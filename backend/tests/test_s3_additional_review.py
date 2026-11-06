"""Additional S3 boundary failures found after the initial eight review fixes."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from filelock import FileLock
from backend.tests import test_s3_review as review
from backend.tests import test_s3_storage as fixture
from backend.app.storage import sqlite_state
from backend.app.storage.objects import StorageError
from backend.app.storage.workspace import workspace


class AdditionalS3ReviewTests(unittest.TestCase):
    setUp = fixture.S3StorageTests.setUp
    crawler = review.S3ReviewTests.crawler
    save_article = review.S3ReviewTests.save_article

    def test_live_crawler_child_prevents_parent_restore_and_publication(self):
        from backend.app.scheduler.jobs import trade_news_daily as job
        with workspace(self.root, 'raw/trade-news', store=self.store):
            (self.root / 'current_job.json').write_text(json.dumps({'run_id': 'a' * 32, 'status': 'running'}))
        live = self.root / 'child-progress.json'
        live.write_text('in-flight child state')
        run = Mock(side_effect=AssertionError('must not launch another child'))
        with FileLock(str(self.root / 'crawler.lock'), timeout=0), \
                patch('backend.app.storage.objects.enabled', return_value=True), \
                patch('backend.app.storage.workspace.enabled', return_value=True), \
                patch('backend.app.storage.workspace.S3Objects', return_value=self.store), \
                patch.object(job, 'settings', return_value={}), \
                patch.object(self.client, 'get_object', wraps=self.client.get_object) as get, \
                patch.object(self.client, 'put_object', wraps=self.client.put_object) as put:
            result = job.run(self.root, now=datetime(2026, 11, 6, tzinfo=ZoneInfo('Asia/Seoul')), runner=run)
            self.assertTrue(live.exists(), 'S3 restore removed files belonging to the live child')
            self.assertEqual(result['status'], 'already_running')
            get.assert_not_called()
            put.assert_not_called()
        run.assert_not_called()

    def test_repeat_migration_rejects_changed_inspection_database(self):
        path = self.root / 'articles.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE budget(count INTEGER)')
            db.execute('INSERT INTO budget VALUES(1)')
        db.close()
        prepared = self.root / 'prepared.sqlite3'
        with sqlite3.connect(prepared) as db:
            db.execute('CREATE TABLE inspection(value TEXT)')
            db.execute("INSERT INTO inspection VALUES('reviewed')")
        db.close()
        sqlite_state.migrate_database(path, self.store)
        with sqlite3.connect(prepared) as db:
            db.execute("UPDATE inspection SET value='changed'")
        db.close()
        with self.assertRaises(StorageError):
            sqlite_state.migrate_database(path, self.store)

    def test_changed_source_during_migration_is_not_published(self):
        path = self.root / 'articles.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE budget(count INTEGER)')
        db.close()
        prepared = self.root / 'prepared.sqlite3'
        with sqlite3.connect(prepared) as db:
            db.execute('CREATE TABLE inspection(value INTEGER)')
        db.close()
        original = self.store.immutable
        def upload(*args, **kwargs):
            with sqlite3.connect(prepared) as db:
                db.execute('INSERT INTO inspection VALUES(1)')
            db.close()
            return original(*args, **kwargs)
        with patch.object(self.store, 'immutable', side_effect=upload), self.assertRaises(StorageError):
            sqlite_state.migrate_database(path, self.store)
        with self.assertRaises(FileNotFoundError):
            self.store.get(sqlite_state.POINTER)

    def test_parent_restores_empty_cache_releases_child_lock_and_publishes_result(self):
        from backend.app.scheduler.jobs import trade_news_daily as job
        path = self.root / 'articles.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE budget(day TEXT,site TEXT,count INTEGER)')
            db.execute("INSERT INTO budget VALUES('2026-11-05','hankyung',7)")
        db.close()
        sqlite_state.migrate_database(path, self.store)
        with workspace(self.root, 'raw/trade-news', store=self.store):
            (self.root / 'source.json').write_text('durable source')
        cache = self.root / 'empty-cache'
        calls = []
        def run(command, **options):
            with FileLock(str(cache / 'crawler.lock'), timeout=0):
                current = json.loads((cache / 'current_job.json').read_text())
                self.assertEqual(current['run_id'], options['env']['TRADE_NEWS_JOB_RUN_ID'])
                self.assertEqual((cache / 'source.json').read_text(), 'durable source')
                if '--result-file' in command:
                    output = Path(command[command.index('--result-file') + 1])
                    output.write_text(json.dumps({'run_id': current['run_id'], 'target_date': current['target_date'],
                        'sites': list(job.SITES), 'mode': 'applied', 'status': 'complete'}))
                calls.append(command)
            return SimpleNamespace(returncode=0)
        report = {'execution_healthy': True, 'counts': {}, 'operational': {'sites': {
            site: {'status': 'available_candidates_processed'} for site in job.SITES}}}
        with patch('backend.app.storage.objects.enabled', return_value=True), \
                patch('backend.app.storage.workspace.enabled', return_value=True), \
                patch('backend.app.storage.workspace.S3Objects', return_value=self.store), \
                patch.object(sqlite_state, 'S3Objects', return_value=self.store), \
                patch.object(job, 'settings', return_value={}), \
                patch.object(job, 'collection_result', return_value=(report, 'synthetic.json')):
            result = job.run(cache, now=datetime(2026, 11, 6, tzinfo=ZoneInfo('Asia/Seoul')), runner=run)
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(len(calls), 2)
        exported = self.root / 'exported-cache'
        with workspace(exported, 'raw/trade-news', write=False, store=self.store):
            self.assertEqual(json.loads((exported / 'current_job.json').read_text()), result)
        with sqlite3.connect(cache / 'articles.sqlite3') as db:
            self.assertEqual(db.execute('SELECT count FROM budget').fetchone()[0], 7)
        db.close()

    def test_migration_reads_committed_wal_rows(self):
        path = self.root / 'articles.sqlite3'
        writer = sqlite3.connect(path)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            with writer:
                writer.execute('CREATE TABLE budget(count INTEGER)')
                writer.execute('INSERT INTO budget VALUES(7)')
            sqlite_state.migrate_database(path, self.store)
        finally:
            writer.close()
        target = self.root / 'restored/articles.sqlite3'
        sqlite_state.restore(target, self.store)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('SELECT count FROM budget').fetchone()[0], 7)
        db.close()

    def test_trade_news_inventory_includes_authoritative_databases(self):
        from backend.app.storage import migrate
        for name in ('articles.sqlite3', 'prepared.sqlite3'):
            with sqlite3.connect(self.root / name) as db:
                db.execute('CREATE TABLE sample(value INTEGER)')
            db.close()
        output = io.StringIO()
        with patch('sys.argv', ['migrate', '--dataset', 'trade-news', '--source', str(self.root)]), \
                patch.object(migrate, 'S3Objects', side_effect=AssertionError('dry run contacted AWS')), redirect_stdout(output):
            migrate.main()
        inventory = json.loads(output.getvalue())
        self.assertTrue({'articles.sqlite3', 'prepared.sqlite3'} <= {item['file'] for item in inventory['files']})

    def test_malformed_inspection_json_is_rebuilt_without_stopping_crawler(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        self.save_article(crawler)
        with sqlite3.connect(self.root / 'prepared.sqlite3') as db:
            db.execute("UPDATE prepared_article SET payload='[]'")
        db.close()
        crawler.db.persist()
        crawler.close()
        restored = Store(self.root)
        try:
            from news.project_articles import select_prepared_articles
            with sqlite3.connect(self.root / 'prepared.sqlite3') as db:
                result = select_prepared_articles(restored, db)
            db.close()
            self.assertEqual(result['rejected'], [])
            self.assertEqual(len(result['articles']), 1)
        finally:
            restored.close()
