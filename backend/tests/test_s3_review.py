"""Regression cases from the S3 PR review; no cloud/database/network services."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from functools import partial
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.tests import test_s3_storage as fixture
from backend.app.storage import sqlite_state
from backend.app.storage.objects import Conflict, StorageError, digest, json_bytes
from backend.app.storage.workspace import Workspace, workspace


class S3ReviewTests(unittest.TestCase):
    setUp = fixture.S3StorageTests.setUp

    def test_un_process_and_remote_lock_conflicts_are_normal_results(self):
        from backend.app.scheduler import main
        with patch.object(main, 'enabled', return_value=True), patch.object(main, 'workspace') as opened:
            main.UN_RUN_LOCK.acquire()
            try:
                self.assertEqual(main.un_refresh_job(self.root), 'ALREADY_RUNNING')
                self.assertEqual(main.un_resume_job(self.root), 'ALREADY_RUNNING')
                opened.assert_not_called()
            finally:
                main.UN_RUN_LOCK.release()
        with workspace(self.root, 'raw/un-data/runtime', store=self.store):
            pass
        remote = Workspace(self.root, 'raw/un-data/runtime', store=self.store)
        remote.claim()
        with patch.object(main, 'enabled', return_value=True), patch.object(main, 'workspace', partial(workspace, store=self.store)):
            self.assertEqual(main.un_resume_job(self.root), 'ALREADY_RUNNING')
            self.assertEqual(main.un_refresh_job(self.root), 'ALREADY_RUNNING')
        remote.release()

    def test_un_deferred_checks_do_not_write_and_due_job_releases_reader(self):
        from backend.app.scheduler import main
        with workspace(self.root, 'raw/un-data/runtime', store=self.store):
            (self.root / 'checkpoint.json').write_text('{}')
        with patch.object(main, 'enabled', return_value=True), \
                patch.object(main, 'workspace', partial(workspace, store=self.store)), \
                patch.object(main, '_un_resume_status', return_value='DEFERRED') as status, \
                patch.object(self.client, 'put_object', wraps=self.client.put_object) as put:
            self.assertEqual(main.un_resume_job(self.root), 'DEFERRED')
            self.assertEqual(main.un_resume_job(self.root), 'DEFERRED')
            put.assert_not_called()
            status.return_value = 'RUN'
            def run(*args):
                self.assertTrue(main.UN_RUN_LOCK.acquire(blocking=False))
                main.UN_RUN_LOCK.release()
                with workspace(self.root, 'raw/un-data/runtime', store=self.store):
                    return 'SUCCESS'
            with patch.object(main, 'un_refresh_job', side_effect=run):
                self.assertEqual(main.un_resume_job(self.root), 'SUCCESS')
        with patch.object(main, 'enabled', return_value=True), patch.object(main, 'workspace', side_effect=StorageError('offline')):
            self.assertEqual(main.un_resume_job(self.root), 'FAILED')
            self.assertEqual(main.un_refresh_job(self.root), 'FAILED')
        self.assertTrue(main.UN_RUN_LOCK.acquire(blocking=False))
        main.UN_RUN_LOCK.release()

    def test_input_restore_preserves_legacy_logs_and_default_logs_live_outside(self):
        from backend.app.scheduler.jobs import news_load
        self.assertFalse(news_load.LOG_DIR.resolve().is_relative_to(news_load.RAW_DIR.resolve()))
        with workspace(self.root, 'raw/news-history', store=self.store):
            (self.root / 'raw.csv').write_text('input')
        (self.root / 'logs').mkdir()
        (self.root / 'logs/previous.log').write_text('diagnostic')
        remote = Workspace(self.root, 'raw/news-history', store=self.store)
        remote.read_manifest()
        older = self.store.immutable('raw/news-history/_blobs/' + digest(b'old log'), b'old log')
        remote._put({**remote.state, 'files': {**remote.state['files'], 'logs/previous.log': older}})
        with workspace(self.root, 'raw/news-history', write=False, store=self.store):
            pass
        self.assertEqual((self.root / 'logs/previous.log').read_text(), 'diagnostic')

    def test_model_cache_deduplicates_prunes_and_does_not_rehash_unchanged_requests(self):
        from backend.app.storage.model_cache import ModelCache
        cache = ModelCache(Path(self.temp.name) / 'models', self.store, 30)
        with workspace(self.root, 'models/monthly', store=self.store):
            (self.root / 'common.joblib').write_bytes(b'common')
            (self.root / 'model.joblib').write_bytes(b'one')
        with patch('backend.app.storage.model_cache.time.monotonic', return_value=100):
            with cache.use() as first:
                self.assertEqual((first / 'model.joblib').read_bytes(), b'one')
            with patch.object(self.client, 'get_object', wraps=self.client.get_object) as get, \
                    patch('backend.app.storage.model_cache.file_digest', side_effect=AssertionError('request rehashed model')):
                with cache.use(): pass
                with cache.use(): pass
                get.assert_not_called()
        with patch('backend.app.storage.model_cache.time.monotonic', return_value=131), \
                patch.object(self.client, 'get_object', wraps=self.client.get_object) as get:
            with cache.use(): pass
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.kwargs['IfNoneMatch'], cache.etag)
        for index, content in enumerate((b'two', b'three')):
            with workspace(self.root, 'models/monthly', store=self.store):
                (self.root / 'model.joblib').write_bytes(content)
            with patch('backend.app.storage.model_cache.time.monotonic', return_value=200 + index * 40):
                with cache.use() as current:
                    self.assertTrue((current / 'common.joblib').samefile(cache.root / 'blobs' / digest(b'common')))
        self.assertFalse(first.exists())
        self.assertEqual(len(list((cache.root / 'versions').iterdir())), 2)
        self.assertEqual(len(list((cache.root / 'blobs').iterdir())), 3)

    def test_report_storage_reuses_client_and_isolates_bucket_settings(self):
        from backend.app.api.report_storage import report_storage, _s3_report_storage
        from backend.app.api.config import Settings
        _s3_report_storage.cache_clear()
        self.addCleanup(_s3_report_storage.cache_clear)
        with patch('backend.app.api.report_storage.S3Objects', return_value=self.store) as factory:
            first = report_storage(self.settings)
            self.assertIs(first, report_storage(Settings(dict(self.settings.values))))
            factory.assert_called_once()
            other = Settings({**self.settings.values, 'ARTIFACT_S3_BUCKET': 'other-bucket'})
            self.assertIsNot(first, report_storage(other))

    def test_market_requests_reuse_manifest_and_model_selection_until_refresh(self):
        from backend.app.agents import market
        from backend.app.storage.model_cache import ModelCache
        cache = ModelCache(Path(self.temp.name) / 'market-cache', self.store, 30)
        market._published_model_paths.cache_clear()
        market._published_growth_path.cache_clear()
        self.addCleanup(market._published_model_paths.cache_clear)
        self.addCleanup(market._published_growth_path.cache_clear)
        with workspace(self.root, 'models/monthly', store=self.store):
            (self.root / 'model_a_two_stage_202610_r1.joblib').write_bytes(b'model a')
            (self.root / 'model_a_two_stage_202610_r1.registry.json').write_text(json.dumps({
                'model_key': 'model_a', 'status': 'READY', 'data_info': {'trade_month': '2026-10'},
                'created_at': '2026-11-01', 'artifact_path': 'model_a_two_stage_202610_r1.joblib',
                'artifact_sha256': digest(b'model a')}))
            (self.root / 'model_b_growth_202610_r1.joblib').write_bytes(b'model b')
        with patch('backend.app.storage.model_cache.configured', return_value=cache), \
                patch('backend.app.storage.model_cache.time.monotonic', return_value=100), \
                patch.object(market, '_load', return_value='loaded'), \
                patch.object(market, '_load_growth', return_value=({}, 'model b')), \
                patch.object(market, '_registry_model_paths', wraps=market._registry_model_paths) as select_a, \
                patch.object(market, '_select_growth_path', wraps=market._select_growth_path) as select_b:
            self.assertEqual(market.loaded(self.settings), 'loaded')
            self.assertEqual(market.growth(self.settings, '2026-10'), ({}, 'model b', None))
            with patch.object(self.client, 'get_object', wraps=self.client.get_object) as get, \
                    patch('backend.app.storage.model_cache.file_digest', side_effect=AssertionError('rehashed')):
                self.assertEqual(market.loaded(self.settings), 'loaded')
                self.assertEqual(market.growth(self.settings, '2026-10'), ({}, 'model b', None))
                get.assert_not_called()
            self.assertEqual(select_a.call_count, 1)
            self.assertEqual(select_b.call_count, 1)

    def crawler(self):
        from backend.tests.trade_news import helpers
        from news.save import Store
        with patch.object(sqlite_state, 'enabled', return_value=False):
            initial = Store(self.root)
            initial.close()
        sqlite_state.migrate_database(self.root / 'articles.sqlite3', self.store)
        stack = ExitStack()
        stack.enter_context(patch.object(sqlite_state, 'enabled', return_value=True))
        stack.enter_context(patch.object(sqlite_state, 'S3Objects', return_value=self.store))
        self.addCleanup(stack.close)
        return Store, helpers

    def save_article(self, store, number=1):
        from backend.tests.trade_news.helpers import article
        from news.preparation import prepare_article
        from news.staging import PreparationStore
        raw = article(number)
        prepared = prepare_article(raw)
        staging = PreparationStore(self.root / 'prepared.sqlite3')
        try:
            staging.save(raw, prepared)
        finally:
            staging.close()
        saved = {**raw, 'body_masked': prepared['body_masked'],
                 'preparation': {k: prepared[k] for k in ('quality', 'focus', 'cleaning', 'tagging')}}
        store.save_article(saved)
        return saved

    def test_article_inspection_and_budget_restore_after_parent_checkpoint_crash(self):
        for lose_cache in (False, True):
            with self.subTest(lose_cache=lose_cache):
                # Separate fixture trees for each interrupted crawl.
                if lose_cache:
                    self.root = Path(self.temp.name) / 'lost-cache'
                    self.root.mkdir()
                    self.client.data.clear()
                Store, helpers = self.crawler()
                with workspace(self.root, 'raw/trade-news', store=self.store):
                    pass
                with self.assertRaisesRegex(RuntimeError, 'crash before parent checkpoint'):
                    with workspace(self.root, 'raw/trade-news', store=self.store):
                        crawler = Store(self.root)
                        try:
                            self.assertTrue(crawler.reserve('hankyung', 100, 60))
                            article = self.save_article(crawler)
                        finally:
                            crawler.close()
                        raise RuntimeError('crash before parent checkpoint')
                if lose_cache:
                    self.assertTrue(self.root.resolve().is_relative_to(Path(self.temp.name).resolve()))
                    shutil.rmtree(self.root)
                with workspace(self.root, 'raw/trade-news', write=False, store=self.store):
                    pass
                crawler = Store(self.root)
                try:
                    self.assertEqual(crawler.used('hankyung'), 1)
                    self.assertTrue(crawler.exists(article['source_url']))
                    from news.spiders.articles import ArticleSpider
                    spider = ArticleSpider(crawler, ['hankyung'])
                    spider.crawler = SimpleNamespace(stats=Mock())
                    self.assertIsNone(spider.request_article(article['source_url']))
                    from news.project_articles import select_prepared_articles
                    with sqlite3.connect(self.root / 'prepared.sqlite3') as prepared:
                        selected = select_prepared_articles(crawler, prepared)
                    prepared.close()
                    self.assertEqual(selected['rejected'], [])
                    self.assertEqual(len(selected['articles']), 1)
                    from news.mysql_store import MySQLArticleStore
                    engine = helpers.engine(Path(self.temp.name) / ('project-' + str(lose_cache) + '.sqlite3'))
                    try:
                        with engine.connect() as conn:
                            MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'}).save(selected['articles'][0])
                    finally:
                        engine.dispose()
                finally:
                    crawler.close()

    def test_budget_commit_does_not_serialize_or_scan_article_partition(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        try:
            self.save_article(crawler)
            with patch.object(sqlite_state.DurableConnection, 'serialize', side_effect=AssertionError('full DB serialized')), \
                    patch.object(sqlite_state, 'partition', wraps=sqlite_state.partition) as part:
                self.assertTrue(crawler.reserve('hankyung', 100, 60))
                self.assertEqual([call.args[1] for call in part.call_args_list], ['state'])
        finally:
            crawler.close()

    def test_failed_article_pointer_never_exposes_only_one_database(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        self.assertTrue(crawler.reserve('hankyung', 100, 60))
        self.client.fail = lambda key: key == sqlite_state.POINTER
        with self.assertRaises(StorageError):
            self.save_article(crawler)
        crawler.close()
        self.client.fail = None
        crawler = Store(self.root)
        try:
            self.assertEqual(crawler.used('hankyung'), 1)
            self.assertEqual(crawler.count_articles('hankyung'), 0)
            with sqlite3.connect(self.root / 'prepared.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM prepared_article').fetchone()[0], 0)
            db.close()
        finally:
            crawler.close()

    def test_restore_discards_unconfirmed_sqlite_journals(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        crawler.reserve('hankyung', 100, 60)
        self.save_article(crawler)
        crawler.close()
        for database in ('articles.sqlite3', 'prepared.sqlite3'):
            for suffix in ('-wal', '-shm', '-journal'):
                (self.root / (database + suffix)).write_bytes(b'unconfirmed journal')
        sqlite_state.restore(self.root / 'articles.sqlite3', self.store)
        for database in ('articles.sqlite3', 'prepared.sqlite3'):
            for suffix in ('-wal', '-shm', '-journal'):
                self.assertFalse((self.root / (database + suffix)).exists())
        crawler = Store(self.root)
        self.assertEqual(crawler.used('hankyung'), 1)
        self.assertEqual(crawler.count_articles('hankyung'), 1)
        crawler.close()

    def test_legacy_missing_preparation_recovers_without_http_and_unknown_policy_stays_blocked(self):
        Store, _ = self.crawler()
        crawler = Store(self.root)
        self.save_article(crawler)
        # Simulate the old v1 writer: current article DB, missing inspection DB.
        content = crawler.db.serialize()
        crawler.close()
        meta = sqlite_state.snapshot(content, self.store)
        _, etag = self.store.get(sqlite_state.POINTER)
        self.store.put(sqlite_state.POINTER, json_bytes(meta), etag=etag)
        (self.root / 'prepared.sqlite3').unlink()
        crawler = Store(self.root)
        try:
            with sqlite3.connect(self.root / 'prepared.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM prepared_article').fetchone()[0], 1)
                damaged = json.loads(db.execute('SELECT payload FROM prepared_article').fetchone()[0])
                damaged['published_date'] = '2000-01-01'
                db.execute('UPDATE prepared_article SET payload=?', (json.dumps(damaged),))
            db.close()
            from news.recovery import recover_prepared
            self.assertEqual(recover_prepared(crawler.db, self.root)['repaired'], 1)
            with crawler.db:
                row = crawler.db.execute('SELECT url,payload FROM articles').fetchone()
                item = json.loads(row['payload'])
                item['preparation']['focus']['version'] = 'unavailable-policy'
                crawler.db.execute('UPDATE articles SET payload=? WHERE url=?', (json.dumps(item), row['url']))
        finally:
            crawler.close()
        crawler = Store(self.root)
        try:
            from news.project_articles import select_prepared_articles
            with sqlite3.connect(self.root / 'prepared.sqlite3') as db:
                result = select_prepared_articles(crawler, db)
            db.close()
            self.assertEqual(result['articles'], [])
            self.assertIn('RECOVERY_REQUIRED', result['rejected'][0]['reason'])
        finally:
            crawler.close()

    def test_chunk_cleanup_preserves_live_and_recent_chunks_and_fences_old_writers(self):
        from backend.app.storage.crawler_gc import collect
        Store, _ = self.crawler()
        crawler = Store(self.root)
        orphan = self.store.immutable(sqlite_state.PREFIX + '/_chunks/' + digest(b'orphan'), b'orphan')['key']
        recent = self.store.immutable(sqlite_state.PREFIX + '/_chunks/' + digest(b'recent'), b'recent')['key']
        now = datetime.now(timezone.utc)
        self.client.list_objects_v2 = Mock(return_value={'Contents': [
            {'Key': key, 'LastModified': now if key == recent else now - timedelta(days=10)}
            for key in self.client.data if '/_chunks/' in key]})
        def delete(**kwargs):
            self.client.data.pop(kwargs['Key'])
            with self.assertRaises(Conflict):
                crawler.reserve('hankyung', 100, 60)
        self.client.delete_object = Mock(side_effect=delete)
        try:
            plan = collect(self.store, now=now)['candidates']
            self.assertIn(orphan, plan)
            self.assertNotIn(recent, plan)
            self.assertEqual(collect(self.store, now=now, apply=True, writers_stopped=True)['deleted'], len(plan))
            self.assertNotIn(orphan, self.client.data)
            self.assertIn(recent, self.client.data)
            self.assertNotIn('maintenance', json.loads(self.store.get(sqlite_state.POINTER)[0]))
        finally:
            crawler.close()
