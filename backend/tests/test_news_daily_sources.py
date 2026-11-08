"""일별 집계와 숨김·차단 차감의 출처 범위가 같은지 실제 rebuild_daily로 검증합니다."""
import itertools
import unittest
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.app.api import news
from backend.app.api.database import Store
from backend.app.scheduler.jobs import major_issue_detect as job
from backend.app.scheduler.jobs import news_load, shortnews_load
from backend.app.services.report_context import build_context
from backend.app.api import reports
from backend.tests import test_backend_api as fixtures

MARKET, SHORT, LEGACY = 'KOTRA_MARKET', 'KOTRA_SHORT_NEWS', 'KOTRA_SHORTNEWS'
RECENT_DAY, BASELINE_DAY = date(2025, 12, 21), date(2025, 12, 1)
HS = {'hs_code': '330499', 'hs_ver': 'HS2022'}


class DailySourcesTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    next_id = 1000

    def article(self, source, day, keywords=('관세',), visible='VISIBLE', countries=('US',), hs=None):
        self.next_id += 1
        identity = self.next_id
        tables = self.meta.tables
        with self.engine.begin() as c:
            c.execute(tables['article'].insert(), {'article_id': identity, 'source_code': source, 'external_id': f'{source}-{identity}', 'title': f'기사 {identity}', 'published_date': day,
                'source_url': f'https://example.org/{identity}', 'body_masked': '본문', 'category': '경제', 'visibility_status': visible, 'admin_meta_json': {}})
            rows = [{'keyword_text': k} for k in keywords] + [{'country_code': k} for k in countries] + ([hs] if hs else [])
            for row in rows:  # 태그마다 채우는 열이 달라 한 행씩 넣는다
                c.execute(tables['article_tag'].insert(), {'article_id': identity, **row})
        return identity

    def rebuild(self):
        """실제 적재와 같은 함수로 기사가 있는 날짜의 일별 집계를 만든다."""
        with self.engine.connect() as c:
            days = set(c.scalars(select(self.meta.tables['article'].c.published_date).distinct()))
        with Session(self.engine) as session:
            news_load.rebuild_daily(session, days)
            session.commit()

    def collected(self, end=date(2025, 12, 31)):
        tables = self.meta.tables
        with self.engine.begin() as c:
            for code in (MARKET, SHORT, LEGACY):
                c.execute(tables['service_option'].insert(), {'option_code': code, 'option_type': 'SOURCE', 'label_ko': code, 'enabled': True, 'sort_order': 5, 'value_json': {}})
            for code in (MARKET, SHORT):
                c.execute(tables['batch_run'].insert(), {'source_code': code, 'job_name': 'NEWS', 'target_table': 'article', 'period_start': date(2025, 10, 1), 'period_end': end, 'status': 'SUCCESS', 'detail_json': {}})

    def block(self, uid, *words):
        with self.engine.begin() as c:
            table = self.meta.tables['keyword_topic']
            known = set(c.scalars(select(table.c.keyword_text)))
            new = [{'keyword_text': w, 'status': 'UNCLASSIFIED'} for w in words if w not in known]
            if new:
                c.execute(table.insert(), new)
            store = Store(self.app.state.database, c)
            news.save_blocks(store, uid, list(words), news.blocks(store, uid)['settings']['version'])

    def weekly(self, uid):
        record = {'report_id': 42, 'report_type': 'RPT_WEEKLY', 'conditions_json': {
            'period': {'start': '2025-12-15', 'end': '2025-12-21'}, 'hs_items': [HS], 'country_codes': ['US'], 'compare': 'YOY_PERIOD',
            'compare_period': {'start': '2024-12-15', 'end': '2024-12-31'}, 'sections': ['KPI', 'TOP_COUNTRIES'], 'formats': ['PDF', 'DOCX']}}
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            return build_context(store, uid, record, reports.build_document(store, uid, record))

    def counts(self, function, *args, **kwargs):
        with self.engine.begin() as c:
            return function(Store(self.app.state.database, c), *args, **kwargs)

    def test_counts_equal_public_articles_for_every_source_state_and_window(self):
        """출처·공개 상태·차단·기간의 모든 조합에서 집계-차감 결과가 기사를 직접 센 값과 같아야 한다."""
        uid = int(self.signup())
        self.collected()
        self.block(uid, '차단')
        specs = []
        sources = (MARKET, SHORT, LEGACY, 'HANKYUNG', 'FNNEWS')
        for source, visible, blocked, day in itertools.product(sources, ('VISIBLE', 'HIDDEN'), (False, True), (RECENT_DAY, BASELINE_DAY)):
            keywords = ('관세', '차단') if blocked else ('관세',)
            self.article(source, day, keywords, visible, hs=HS)
            specs.append((source, visible, blocked, day))
        self.rebuild()
        windows = ((date(2025, 12, 15), RECENT_DAY, RECENT_DAY), (date(2025, 10, 20), date(2025, 12, 14), BASELINE_DAY))
        for start, end, day in windows:
            def expected(allowed, ignore_blocks=False):
                return sum(s in allowed and v == 'VISIBLE' and (ignore_blocks or not b) and d == day for s, v, b, d in specs)
            keyword_sources, product_sources = (MARKET, SHORT, LEGACY), (MARKET,)
            with self.subTest(window=start):
                public = self.counts(job.keyword_counts, start, end)
                self.assertEqual(public[('관세', None)], expected(keyword_sources, ignore_blocks=True))
                self.assertEqual(public[('차단', None)], sum(s in keyword_sources and v == 'VISIBLE' and b and d == day for s, v, b, d in specs))
                by_country = self.counts(job.keyword_counts, start, end, ('US',), {'차단'})
                self.assertEqual(by_country, {('관세', 'US'): expected(keyword_sources)})
                products = self.counts(job.product_counts, start, end, {'차단'})
                # HS6 태그 기사는 사전에 있는 상위 HS4(3304)에도 집계된다.
                self.assertEqual(products, {('330499', 'HS2022'): expected(product_sources), ('3304', 'HS2022'): expected(product_sources)})

    def test_hidden_legacy_short_news_is_not_counted_in_report(self):
        uid = int(self.signup())
        self.collected()
        self.article(MARKET, RECENT_DAY)
        for _ in range(3):
            self.article(LEGACY, RECENT_DAY, visible='HIDDEN')
        self.rebuild()
        ctx = self.weekly(uid)
        self.assertEqual(ctx['keyword_spikes'], [])
        self.assertEqual(ctx['summary']['issues'], [])

    def test_user_blocked_legacy_short_news_is_not_counted_in_report(self):
        uid = int(self.signup())
        self.collected()
        self.article(MARKET, RECENT_DAY)
        for _ in range(3):
            self.article(LEGACY, RECENT_DAY, ('관세', '차단'))
        self.rebuild()
        self.block(uid, '차단')
        ctx = self.weekly(uid)
        self.assertEqual(ctx['keyword_spikes'], [])
        self.assertEqual(ctx['summary']['issues'], [])

    def test_hidden_legacy_short_news_leaves_the_baseline_too(self):
        uid = int(self.signup())
        self.collected()
        for _ in range(3):
            self.article(MARKET, RECENT_DAY)
        for _ in range(8):
            self.article(MARKET, BASELINE_DAY)
        for _ in range(8):
            self.article(LEGACY, BASELINE_DAY, visible='HIDDEN')  # 빼지 않으면 주평균 2 → ×1.5
        self.rebuild()
        self.assertEqual(self.weekly(uid)['keyword_spikes'], [{'keyword': '관세', 'country': '미국', 'this_week': 3, 'weekly_avg_8w': 1.0, 'ratio': 3.0}])

    def test_public_legacy_short_news_is_evidence_for_an_issue(self):
        uid = int(self.signup())
        self.collected()
        for _ in range(3):
            self.article(LEGACY, RECENT_DAY, hs=HS)
        self.rebuild()
        job.detect(self.app.state.database, RECENT_DAY)
        issues = self.weekly(uid)['summary']['issues']
        self.assertEqual([(r['kind'], r['title']) for r in issues], [('SPIKE', '‘관세’ 기사 급증')])
        self.assertIn('7일 3건', issues[0]['basis'])
        self.assertEqual(issues[0]['related'], '관심 범위')

    def test_report_counts_and_labels_legacy_short_news_as_short_news(self):
        uid = int(self.signup())
        self.collected()
        self.article(MARKET, RECENT_DAY, hs=HS)
        self.article(SHORT, RECENT_DAY, hs=HS)
        self.article(LEGACY, RECENT_DAY, hs=HS)
        self.rebuild()
        ctx = self.weekly(uid)
        counts = ctx['summary']['counts']
        self.assertEqual((counts['total'], counts['market'], counts['short']), (3, 1, 2))
        self.assertEqual(ctx['short_news']['total'], 2)
        self.assertEqual(len(ctx['short_news']['items']), 2)
        us = next(r for r in ctx['country_counts'] if r['country_code'] == 'US')
        self.assertEqual((us['market'], us['short']), (1, 2))
        self.assertNotIn(LEGACY, ctx['news_source_label'])

    def test_results_are_the_same_before_and_after_source_conversion(self):
        uid = int(self.signup())
        self.collected()
        for _ in range(4):
            self.article(MARKET, RECENT_DAY)
        for _ in range(2):
            self.article(LEGACY, RECENT_DAY)
        for _ in range(3):
            self.article(LEGACY, RECENT_DAY, visible='HIDDEN')
        for _ in range(2):
            self.article(LEGACY, RECENT_DAY, ('관세', '차단'))
        for _ in range(8):
            self.article(LEGACY, BASELINE_DAY)
        for _ in range(4):
            self.article(LEGACY, BASELINE_DAY, visible='HIDDEN')
        self.rebuild()
        self.block(uid, '차단')
        job.detect(self.app.state.database, RECENT_DAY)
        before = self.weekly(uid)
        self.assertEqual(before['keyword_spikes'][0]['this_week'], 6)
        self.assertEqual(before['keyword_spikes'][0]['ratio'], 6.0)
        table = self.meta.tables['article']
        with Session(self.engine) as session:
            days = shortnews_load.migrate_source(session, table)
            news_load.rebuild_daily(session, days)
            session.commit()
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(table.c.article_id).where(table.c.source_code == LEGACY)), None)
        after = self.weekly(uid)
        self.assertEqual(after['keyword_spikes'], before['keyword_spikes'])
        self.assertEqual(after['summary']['issues'], before['summary']['issues'])
        self.assertEqual(after['summary']['counts'], before['summary']['counts'])

    def test_source_conversion_conflict_is_rejected_without_changes(self):
        self.collected()
        identity = self.article(LEGACY, RECENT_DAY)
        table = self.meta.tables['article']
        with self.engine.begin() as c:
            external = c.scalar(select(table.c.external_id).where(table.c.article_id == identity))
        other = self.article(SHORT, RECENT_DAY)
        with self.engine.begin() as c:
            c.execute(update(table).where(table.c.article_id == other).values(external_id=external))
        with Session(self.engine) as session:
            with self.assertRaisesRegex(ValueError, '출처 전환 충돌'):
                shortnews_load.migrate_source(session, table)
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(table.c.source_code).where(table.c.article_id == identity)), LEGACY)


if __name__ == '__main__':
    unittest.main()
