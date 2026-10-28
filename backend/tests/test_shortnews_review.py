"""PR 코멘트 회귀 검증: 실행 스위치, HS 제외와 기존 데이터 보존, 인덱스 사용 중 잠금."""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backend.app.db.session import session_scope
from backend.app.models.news import ArticleTag
from backend.app.scheduler import main, shortnews_worker
from backend.app.scheduler.jobs import news_load, shortnews_load
from backend.app.services import shortnews_keywords as keywords
from backend.tests import test_shortnews_load as fixture, test_news_load as market_fixture


class SchedulerSwitchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = Path(self.temp.name) / '.env'
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def jobs(self):
        return {j.id: j for j in main.build_scheduler(env_path=self.env).get_jobs()}

    def test_default_off_and_explicit_off_preserve_other_registered_jobs(self):
        baseline = self.jobs()
        self.assertEqual(len(baseline), 11)
        self.assertIsNone(baseline["trade_news_daily"].next_run_time)
        self.assertFalse(any(k.startswith('shortnews') for k in baseline))
        for value in ('false', '0', 'off', '', 'typo'):
            self.env.write_text('SHORTNEWS_DAILY_ENABLED=' + value, encoding="utf-8")
            self.assertEqual(set(self.jobs()), set(baseline))

    def test_env_file_and_environment_precedence_control_both_jobs(self):
        self.env.write_text('SHORTNEWS_DAILY_ENABLED=true', encoding="utf-8")
        self.assertEqual(len(self.jobs()), 13)
        with patch.dict(os.environ, {'SHORTNEWS_DAILY_ENABLED': 'false'}):
            self.assertEqual(len(self.jobs()), 11)
        self.env.write_text('SHORTNEWS_DAILY_ENABLED=false', encoding="utf-8")
        for value in ('true', '1', 'ON', ' yes '):
            with patch.dict(os.environ, {'SHORTNEWS_DAILY_ENABLED': value}):
                jobs = self.jobs()
                self.assertEqual(len(jobs), 13)
                self.assertIsNone(jobs["trade_news_daily"].next_run_time)
                self.assertIn('shortnews_startup', jobs)
                self.assertIn('shortnews_daily', jobs)

    def test_worker_ignores_integrated_switch_without_starting_job(self):
        with patch.dict(os.environ, {'SHORTNEWS_DAILY_ENABLED': 'false'}), \
                patch.object(shortnews_worker.shortnews_daily, 'run') as run:
            jobs = shortnews_worker.build_scheduler().get_jobs()
        self.assertEqual({j.id for j in jobs}, {'shortnews_daily', 'shortnews_startup'})
        run.assert_not_called()


class NoHsTest(unittest.TestCase):
    def setUp(self):
        self.db = fixture.ShortnewsLoadTest()
        self.db.setUp()
        self.addCleanup(self.db.doCleanups)
        self.addCleanup(self.db.tearDown)

    def test_new_clean_format_and_old_hs_input_both_load_without_hs(self):
        clean = {'nttSn': '001', 'kw_topic': 'AI', 'country_all': 'US'}
        for extension in ('csv', 'json'):
            for legacy in (False, True):
                with self.subTest(extension=extension, legacy=legacy):
                    import csv
                    import json
                    row = {**clean, **({'hs6': '854143|8541|000000'} if legacy else {})}
                    raw = self.db.root / 'raw.json'
                    raw.write_text(json.dumps([fixture.raw()]), encoding="utf-8")
                    path = self.db.root / ('clean.' + extension)
                    if extension == 'json':
                        path.write_text(json.dumps([row]), encoding="utf-8")
                    else:
                        with path.open('w', newline='', encoding='utf-8') as stream:
                            writer = csv.DictWriter(stream, fieldnames=list(row))
                            writer.writeheader()
                            writer.writerow(row)
                    self.assertEqual(shortnews_load.run(raw, path, self.db.url, self.db.root / 'logs'), 0)
                    self.assertEqual(len(self.db.rows('article')), 1)
                    self.assertEqual(len(self.db.rows('article_tag')), 2)
                    self.assertEqual(self.db.rows('news_product_daily'), [])

    def test_existing_hs_and_market_records_survive_but_only_market_is_counted(self):
        engine = self.db.engine
        market_raw = market_fixture.raw('001', '2026-10-01')
        market_clean = market_fixture.clean('001', '2026-10-01', 'AI', 'US', '854143')
        news_load.load(engine, [market_raw], [market_clean])
        shortnews_load.load(engine, [fixture.raw()], [fixture.clean()])
        short = next(r for r in self.db.rows('article') if r['source_code'] == shortnews_load.SOURCE.code)
        market = next(r for r in self.db.rows('article') if r['source_code'] == 'KOTRA_MARKET')
        # 과거 자동·수동 태그는 관리자 플래그와 관계없이 보존해야 합니다.
        with session_scope(engine) as session:
            session.add(ArticleTag(article_id=short['article_id'], hs_code='854143', hs_ver='HS2022'))
        hs_before = [r for r in self.db.rows('article_tag') if r['hs_code']]
        products_before = self.db.rows('news_product_daily')
        for cleans in ([], [fixture.clean(hs='')], [fixture.clean(hs='854143')]):
            shortnews_load.load(engine, [fixture.raw()], cleans)
            self.assertEqual([r for r in self.db.rows('article_tag') if r['hs_code']], hs_before)
            self.assertEqual(self.db.rows('news_product_daily'), products_before)
            self.assertEqual(next(r for r in self.db.rows('article') if r['source_code'] == 'KOTRA_MARKET'), market)
        # 기존 뉴스 적재기가 다시 집계해도 단신속보의 보존된 HS가 재유입되지 않습니다.
        news_load.load(engine, [market_raw], [market_clean])
        self.assertEqual(self.db.rows('news_product_daily'), products_before)
        self.assertTrue(all(r['day_count'] == 1 for r in products_before))
        self.assertEqual({r['hs_code'] for r in products_before}, {'8541', '854143'})
        self.assertEqual(next(r['day_count'] for r in self.db.rows('news_keyword_daily')
                              if r['country_key'] == '*'), 2)

    def test_old_and_new_dates_remove_stale_shortnews_counts_and_preserve_tags(self):
        shortnews_load.load(self.db.engine, [fixture.raw()], [fixture.clean()])
        article = self.db.rows('article')[0]
        with session_scope(self.db.engine) as session:
            session.add(ArticleTag(article_id=article['article_id'], hs_code='854143', hs_ver='HS2022'))
            from backend.app.models.news import NewsProductDaily
            session.add(NewsProductDaily(metric_date=date(2026, 10, 1), hs_code='854143',
                                        hs_ver='HS2022', country_code='US', day_count=1))
        shortnews_load.load(self.db.engine, [fixture.raw(day='2026-10-03')], [fixture.clean()])
        self.assertEqual(self.db.rows('news_product_daily'), [])
        self.assertEqual([r['hs_code'] for r in self.db.rows('article_tag') if r['hs_code']], ['854143'])

    def test_legacy_shortnews_source_is_excluded_even_before_source_migration(self):
        from backend.app.db.batch_log import ensure_source, Source
        shortnews_load.load(self.db.engine, [fixture.raw()], [fixture.clean()])
        article = self.db.rows('article')[0]
        table = shortnews_load.reflect(self.db.engine, 'article')
        with session_scope(self.db.engine) as session:
            ensure_source(session, Source(shortnews_load.LEGACY_SOURCE_CODE, 'legacy', {}))
            session.execute(table.update().values(source_code=shortnews_load.LEGACY_SOURCE_CODE))
            session.add(ArticleTag(article_id=article['article_id'], hs_code='854143', hs_ver='HS2022'))
        with session_scope(self.db.engine) as session:
            news_load.rebuild_daily(session, {date(2026, 10, 1)})
        self.assertEqual(self.db.rows('news_product_daily'), [])
        self.assertEqual(len(self.db.rows('news_keyword_daily')), 2)


class IndexProcessLockTest(unittest.TestCase):
    def test_same_persistent_store_blocks_other_process_until_reader_finishes(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            client = SimpleNamespace(get_settings=lambda: SimpleNamespace(persist_directory=directory))
            script = '''
import sys
from types import SimpleNamespace
from filelock import Timeout
from backend.app.services.shortnews_keywords import index_lock
client = SimpleNamespace(get_settings=lambda: SimpleNamespace(persist_directory=sys.argv[1]))
try:
    with index_lock(client): print('ACQUIRED')
except Timeout:
    print('BUSY')
'''
            def attempt():
                return subprocess.run([sys.executable, '-c', script, directory],
                                      check=True, capture_output=True, text=True, timeout=15).stdout.strip()
            with keywords.index_lock(client):
                self.assertEqual(attempt(), 'BUSY')
            self.assertEqual(attempt(), 'ACQUIRED')
