"""Concurrency, retry and incremental durability regressions from PR review."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from filelock import FileLock
from backend.tests import test_s3_storage as fixture, test_s3_review as review
from backend.app.storage.model_cache import ModelCache
from backend.app.storage.objects import Conflict, StorageError
from backend.app.storage.workspace import workspace
from backend.app.storage import sqlite_state


def hold_cached_model(root, state, channel):
    cache = ModelCache(Path(root), None, 3600)
    cache.state, cache.checked = state, time.monotonic()
    try:
        with cache.use() as directory:
            channel.send(str(directory))
            channel.recv()
            channel.send((directory / 'model.joblib').read_bytes())
    finally:
        channel.close()


class PerformanceReviewTests(unittest.TestCase):
    setUp = fixture.S3StorageTests.setUp
    crawler = review.S3ReviewTests.crawler
    save_article = review.S3ReviewTests.save_article

    def publish_model(self, content):
        with workspace(self.root, 'models/monthly', store=self.store):
            (self.root / 'model.joblib').write_bytes(content)

    def cache(self, name='consumer-cache', interval=0):
        return ModelCache(Path(self.temp.name) / name, self.store, interval)

    def test_download_and_load_do_not_hold_global_lock(self):
        self.publish_model(b'old')
        first = self.cache()
        other = self.cache(interval=3600)
        with first.use(): pass
        with other.use(): pass
        self.publish_model(b'new')
        downloading, proceed = threading.Event(), threading.Event()
        errors = []
        verify = self.store.verify
        def blocked(*args, **kwargs):
            downloading.set()
            if not proceed.wait(10):
                raise AssertionError('reader did not progress')
            return verify(*args, **kwargs)
        def update():
            try:
                with first.use() as directory:
                    self.assertEqual((directory / 'model.joblib').read_bytes(), b'new')
                    # The actual model loader runs inside use(), without models.lock.
                    with FileLock(str(first.root / 'models.lock'), timeout=0): pass
            except BaseException as exc:
                errors.append(exc)
        with patch.object(self.store, 'verify', side_effect=blocked):
            worker = threading.Thread(target=update)
            worker.start()
            try:
                self.assertTrue(downloading.wait(5))
                with FileLock(str(first.root / 'models.lock'), timeout=0): pass
                with other.use() as directory:
                    self.assertEqual((directory / 'model.joblib').read_bytes(), b'old')
            finally:
                proceed.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_prune_preserves_live_reader_until_load_finishes(self):
        self.publish_model(b'one')
        cache = self.cache()
        with cache.use() as first:
            for content in (b'two', b'three'):
                self.publish_model(content)
                with self.cache().use(): pass
            self.assertEqual((first / 'model.joblib').read_bytes(), b'one')
        self.assertFalse(first.exists())
        self.assertEqual(len(list((cache.root / 'versions').iterdir())), 2)

    def test_other_process_reader_is_pinned_without_global_lock(self):
        self.publish_model(b'process reader')
        cache = self.cache()
        with cache.use(): pass
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(target=hold_cached_model, args=(str(cache.root), cache.state, child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(20))
            directory = Path(parent.recv())
            with FileLock(str(cache.root / 'models.lock'), timeout=0): pass
            for content in (b'next', b'latest'):
                self.publish_model(content)
                with cache.use(): pass
            self.assertTrue(directory.exists())
            parent.send(True)
            self.assertTrue(parent.poll(20))
            self.assertEqual(parent.recv(), b'process reader')
            process.join(20)
            self.assertEqual(process.exitcode, 0)
            with cache.use(): pass
            self.assertFalse(directory.exists())
        finally:
            parent.close()
            if process.is_alive():
                process.terminate()
                process.join(10)

    def test_retryable_manifest_failure_reuses_verified_cache_after_restart(self):
        self.publish_model(b'valid')
        with self.cache().use(): pass
        # Prior cache versions persisted history + .snapshot.json, not current.json.
        (self.cache().root / 'current.json').unlink()
        with patch.object(self.store, 'get_if_changed', side_effect=StorageError('offline', retryable=True)):
            restarted = self.cache()
            with restarted.use() as directory:
                self.assertEqual((directory / 'model.joblib').read_bytes(), b'valid')
            (directory / 'model.joblib').write_bytes(b'bad')
            with self.assertRaises(StorageError):
                with restarted.use(): pass
            with self.assertRaises(StorageError):
                with self.cache('empty').use(): pass

    def test_prune_removes_crashed_download_but_keeps_active_staging(self):
        self.publish_model(b'valid')
        cache = self.cache()
        with cache.use(): pass
        orphan = cache.root / ('.prepare-' + 'a' * 32)
        orphan.mkdir()
        (orphan / 'partial').write_bytes(b'incomplete')
        with cache.lock():
            pin = cache.pin('b' * 32)
        active = cache.root / ('.prepare-' + pin[0].stem.rsplit('-', 1)[1])
        active.mkdir()
        try:
            with cache.use(): pass
            self.assertFalse(orphan.exists())
            self.assertTrue(active.exists())
        finally:
            with cache.lock(): cache.unpin(pin)
        with cache.use(): pass
        self.assertFalse(active.exists())

    def test_permission_or_invalid_manifest_never_uses_stale_cache(self):
        self.publish_model(b'valid')
        cache = self.cache()
        with cache.use(): pass
        for error in (StorageError('denied'), StorageError('checksum mismatch')):
            with patch.object(self.store, 'get_if_changed', side_effect=error), self.assertRaises(StorageError):
                with cache.use(): pass
        with patch.object(self.store, 'get_if_changed', return_value=(b'{"schema":0}', 'etag')), self.assertRaises(StorageError):
            with cache.use(): pass

    def test_outage_fallback_uses_other_workers_latest_complete_cache(self):
        self.publish_model(b'old')
        cache = self.cache()
        with cache.use() as old: pass
        for content in (b'next', b'latest'):
            self.publish_model(content)
            with self.cache().use(): pass
        self.assertFalse(old.exists())
        with patch.object(self.store, 'get_if_changed', side_effect=StorageError('offline', retryable=True)):
            with cache.use() as current:
                self.assertEqual((current / 'model.joblib').read_bytes(), b'latest')

    def test_workspace_reuses_hash_and_rechecks_replaced_file(self):
        with workspace(self.root, 'raw/un-data/runtime', store=self.store):
            (self.root / 'raw.json').write_text('saved')
        with workspace(self.root, 'raw/un-data/runtime', write=False, store=self.store): pass
        with patch('backend.app.storage.workspace.file_digest', side_effect=AssertionError('unchanged file hashed')):
            with workspace(self.root, 'raw/un-data/runtime', write=False, store=self.store): pass
        other = self.root / '.replacement'
        other.write_text('wrong')
        other.replace(self.root / 'raw.json')
        with workspace(self.root, 'raw/un-data/runtime', write=False, store=self.store):
            self.assertEqual((self.root / 'raw.json').read_text(), 'saved')

    def test_article_commits_journal_changed_rows_and_restore_all_databases(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        try:
            self.save_article(crawler)
            with patch.object(sqlite_state, 'partition', wraps=sqlite_state.partition) as partition:
                crawler.reserve('hankyung', 100, 60)
                self.save_article(crawler, 2)
                self.save_article(crawler, 3)
                self.assertNotIn('articles', [call.args[1] for call in partition.call_args_list])
            pointer = json.loads(self.store.get(sqlite_state.POINTER)[0])
            self.assertEqual(pointer['schema'], 3)
            self.assertEqual(pointer['parts']['articles']['schema'], 2)
        finally:
            crawler.close()
        restored = Store(self.root / 'lost-cache')
        try:
            self.assertEqual(restored.used('hankyung'), 1)
            self.assertEqual(restored.count_articles('hankyung'), 3)
            from news.project_articles import select_prepared_articles
            with sqlite3.connect(restored.root / 'prepared.sqlite3') as prepared:
                selected = select_prepared_articles(restored, prepared)
            prepared.close()
            self.assertEqual(len(selected['articles']), 3)
            self.assertEqual(selected['rejected'], [])
        finally:
            restored.close()

    def test_deltas_cover_replace_delete_rollback_and_blob_values(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        try:
            with crawler.db:
                crawler.db.execute('CREATE TABLE assets(name TEXT UNIQUE, value BLOB)')
                crawler.db.execute('INSERT INTO assets VALUES(?,?)', ('a', b'first'))
            with crawler.db:
                crawler.db.execute('INSERT OR REPLACE INTO assets VALUES(?,?)', ('a', b'second'))
                crawler.db.execute('INSERT INTO assets VALUES(?,?)', ('b', b'delete'))
            with crawler.db:
                crawler.db.execute("DELETE FROM assets WHERE name='b'")
            with self.assertRaises(ValueError):
                with crawler.db:
                    crawler.db.execute("UPDATE assets SET value='rollback'")
                    raise ValueError('rollback')
            crawler.reserve('hankyung', 100, 60)
        finally:
            crawler.close()
        target = self.root / 'restored/articles.sqlite3'
        sqlite_state.restore(target, self.store)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute('SELECT name,value FROM assets').fetchall(), [('a', b'second')])
        db.close()

    def test_journal_compacts_at_bounded_commit_count(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        try:
            self.save_article(crawler)
            with patch('backend.app.storage.article_journal.COMPACT_COMMITS', 2), \
                    patch.object(sqlite_state, 'partition', wraps=sqlite_state.partition) as partition:
                for number in range(2, 7): self.save_article(crawler, number)
                self.assertGreater([c.args[1] for c in partition.call_args_list].count('articles'), 0)
            pointer = json.loads(self.store.get(sqlite_state.POINTER)[0])
            self.assertLessEqual(len(pointer['parts']['articles'].get('deltas', [])), 2)
        finally:
            crawler.close()
        restored = Store(self.root / 'compacted')
        self.assertEqual(restored.count_articles('hankyung'), 6)
        restored.close()

    def test_cleanup_preserves_current_article_deltas(self):
        from backend.app.storage.crawler_gc import collect, referenced
        Store, _ = self.crawler()
        crawler = Store(self.root)
        try:
            self.save_article(crawler)
            self.save_article(crawler, 2)
        finally:
            crawler.close()
        pointer = json.loads(self.store.get(sqlite_state.POINTER)[0])
        self.assertTrue(pointer['parts']['articles']['deltas'])
        current = referenced(pointer)
        now = datetime.now(timezone.utc)
        self.client.list_objects_v2 = Mock(return_value={'Contents': [
            {'Key': key, 'LastModified': now - timedelta(days=10)}
            for key in self.client.data if '/_chunks/' in key]})
        self.client.delete_object = Mock(side_effect=lambda **kw: self.client.data.pop(kw['Key']))
        deleted = collect(self.store, apply=True, writers_stopped=True, now=now)
        self.assertFalse(current.intersection(deleted['candidates']))
        restored = Store(self.root / 'after-gc')
        self.assertEqual(restored.count_articles('hankyung'), 2)
        restored.close()

    def test_final_publish_conflict_is_failed_but_initial_conflict_is_busy(self):
        from backend.app.scheduler.jobs import trade_news_daily as job
        report = {'execution_healthy': True, 'counts': {}, 'operational': {'sites': {
            site: {'status': 'available_candidates_processed'} for site in job.SITES}}}
        def run(command, **options):
            if '--result-file' in command:
                current = json.loads((self.root / 'current_job.json').read_text())
                Path(command[command.index('--result-file') + 1]).write_text(json.dumps({
                    'run_id': current['run_id'], 'target_date': current['target_date'],
                    'sites': list(job.SITES), 'mode': 'applied', 'status': 'complete'}))
            return SimpleNamespace(returncode=0)
        for stage in ('start', 'finish'):
            @contextmanager
            def failing(*args, **kwargs):
                if stage == 'start': raise Conflict('busy')
                yield
                raise Conflict('expired while publishing')
            with patch('backend.app.storage.objects.enabled', return_value=True), \
                    patch.object(job, 'workspace', failing), patch.object(job, 'settings', return_value={}), \
                    patch.object(job, 'data_settings', return_value=(self.root, {})), \
                    patch.object(job, 'CONFIGURATION_ERRORS', self.root / 'diagnostics'), \
                    patch.object(job, 'collection_result', return_value=(report, 'test.json')):
                result = job.run(self.root, runner=run, now=datetime(2026, 11, 6, tzinfo=job.KST))
            self.assertEqual(result['status'], 'already_running' if stage == 'start' else 'failed')
            if stage == 'finish':
                self.assertEqual(result['stage'], 'storage')
                receipt = self.root / 'diagnostics' / ('job_' + result['run_id'] + '_storage_failure.json')
                self.assertFalse(json.loads(receipt.read_text())['storage_confirmed'])
