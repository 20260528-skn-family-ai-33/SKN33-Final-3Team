"""Real-data context, renderer routing, and optional Chromium/download integration."""
import io
import os
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import delete, select, update

from backend.tests import test_backend_api as fixtures
from backend.app.api import files, jobs, news, reports
from backend.app.api.common import ApiError, now
from backend.app.api.config import Settings
from backend.app.api.database import Store
from backend.app.api.worker import run_once
from backend.app.scheduler.jobs import major_issue_detect as job
from backend.app.services.report_context import build_context, safe_url, weekly_highlights
from backend.app.services.report_templates import render


class PeriodicContextTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup

    def record(self, kind='RPT_MONTHLY', **extra):
        start = '2025-10-01' if kind == 'RPT_QUARTERLY' else '2025-12-01'
        end = '2025-12-31'
        if kind == 'RPT_WEEKLY':
            start, end = '2025-12-15', '2025-12-21'
        return {'report_id': 42, 'report_type': kind, 'conditions_json': {
            'period': {'start': start, 'end': end},
            'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}],
            'country_codes': ['US'], 'compare': 'YOY_PERIOD',
            'compare_period': {'start': start.replace('2025', '2024'), 'end': '2024-12-31'},
            'sections': ['KPI', 'TOP_COUNTRIES'], 'formats': ['PDF', 'DOCX'], **extra}}

    def context(self, uid, record):
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            doc = reports.build_document(store, uid, record)
            return build_context(store, uid, record, doc), doc, list(store.queries)

    def test_monthly_values_units_scope_and_legacy_document_preserved(self):
        uid = int(self.signup())
        ctx, doc, sql = self.context(uid, self.record())
        item = ctx['items'][0]
        self.assertEqual(item['amount_usd'], 1230000)
        self.assertEqual(item['amount_usd'], doc['sections'][1]['kpi']['amount_usd'])
        self.assertAlmostEqual(item['mom_pct'], 10000 / 1220000 * 100, places=5)
        self.assertAlmostEqual(item['yoy_pct'], 120000 / 1110000 * 100, places=5)
        self.assertEqual(item['country_count'], 1)
        self.assertEqual(item['top_country'], {'name': '미국', 'share_pct': 100})
        self.assertEqual(item['trend']['current'][-1], 1230000)
        self.assertEqual(len(item['trend']['current']), 13)
        self.assertIsNone(item['trend']['previous'][0])  # December 2023 is not verified.
        self.assertEqual(ctx['interest_overview'][0]['values'][0]['amount_usd'], 1230000)
        self.assertFalse(ctx['report']['is_sample'])
        self.assertTrue(ctx['report']['published_at'].endswith('+09:00'))
        self.assertIsNone(ctx['verification'])
        self.assertNotIn('template_pdf', doc)
        self.assertIn('기초화장품', render('RPT_MONTHLY', ctx))
        self.assertEqual(sum('GROUP BY export_summary.trade_month, export_summary.country_code' in q for q in sql), 1)

    def test_quarterly_sums_and_previous_year_comparison(self):
        uid = int(self.signup())
        ctx, doc, _ = self.context(uid, self.record('RPT_QUARTERLY'))
        item = ctx['items'][0]
        self.assertEqual(item['amount_usd'], 3660000)
        self.assertEqual(item['prev_amount_usd'], 3300000)
        self.assertEqual(sum(r['amount_usd'] for r in item['months']), item['amount_usd'])
        self.assertEqual(item['quarters']['values'][-1], item['amount_usd'])
        self.assertEqual(item['quarters']['values'][3], item['prev_amount_usd'])
        self.assertEqual(len(item['quarters']['values']), 8)
        self.assertEqual(item['country_quarters'][0]['values'][-1], item['amount_usd'])
        self.assertEqual(item['amount_usd'], doc['sections'][1]['kpi']['amount_usd'])
        self.assertIn('분기·월별 추이', render('RPT_QUARTERLY', ctx))

    def test_country_only_and_all_country_scopes(self):
        uid = int(self.signup())
        for hs, countries, expected in [([], ['US'], 1230000),
                                       ([{'hs_code': '330499', 'hs_ver': 'HS2022'}], [], 3460000)]:
            with self.subTest(hs=hs, countries=countries):
                ctx, _, _ = self.context(uid, self.record(hs_items=hs, country_codes=countries))
                self.assertEqual(ctx['items'][0]['amount_usd'], expected)
                self.assertEqual(sum(r['amount_usd'] for r in ctx['items'][0]['top_countries']), expected)
                self.assertIn('전체 품목' if not hs else '기초화장품', render('RPT_MONTHLY', ctx))

    def test_unverified_history_is_null_but_verified_zero_is_zero(self):
        uid = int(self.signup())
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(), {
                'source_code': 'CUSTOMS', 'job_name': 'HS6_AGGREGATION', 'target_table': 'export_summary',
                'period_start': date(2024, 11, 1), 'period_end': date(2024, 11, 30), 'status': 'FAILED',
                'detail_json': {'schema_version': 1}})
            t = self.meta.tables['export_summary']
            c.execute(delete(t).where(t.c.country_code == 'US', t.c.trade_month == date(2025, 12, 1)))
        ctx, _, _ = self.context(uid, self.record())
        item = ctx['items'][0]
        self.assertEqual(item['amount_usd'], 0)
        self.assertEqual(item['country_count'], 0)
        self.assertIsNone(item['trend']['previous'][-2])
        self.assertEqual(item['trend']['current'][-1], 0)
        self.assertIn('수출 실적이 없습니다', render('RPT_MONTHLY', ctx))

    def seed_news(self):
        with self.engine.begin() as c:
            for i in range(1, 26):
                code = 'KOTRA_SHORT_NEWS' if i <= 3 else 'HANKYUNG' if i % 2 else 'FNNEWS'
                c.execute(self.meta.tables['article'].insert(), {
                    'article_id': i, 'source_code': code, 'external_id': f'periodic-{i}',
                    'title': f'검증 기사 {i} <script>bad()</script>', 'published_date': date(2025, 12, 15),
                    'source_url': f'https://example.org/news/{i}', 'body_masked': '화장품 수출 관련 본문 발췌',
                    'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
                c.execute(self.meta.tables['article_tag'].insert(), {
                    'article_id': i, 'country_code': 'US', 'hs_code': '330499', 'hs_ver': 'HS2022', 'weight': 1})

    def test_weekly_full_counts_not_capped_cards_and_real_publishers(self):
        uid = int(self.signup())
        self.seed_news()
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['summary']['counts'], {'total': 25, 'market': 22, 'short': 3, 'spike_keywords': None})
        self.assertEqual(len(ctx['articles']), 20)
        self.assertEqual(ctx['short_news']['total'], 3)
        self.assertEqual(len(ctx['short_news']['items']), 3)
        self.assertEqual(ctx['country_counts'][0]['market'], 22)
        self.assertEqual(ctx['country_counts'][0]['short'], 3)
        html = render('RPT_WEEKLY', ctx)
        self.assertIn('한국경제', html)
        self.assertIn('파이낸셜뉴스', html)
        self.assertNotIn('<script>bad()', html)
        self.assertIn('직전 8주와 비교하지 않았습니다', html)  # 수집 기록이 없으면 0건으로 보이지 않는다
        self.assertNotIn('기사 저작권은 KOTRA', html)

    def collected_news(self, end=date(2025, 12, 31)):
        with self.engine.begin() as c:
            for code in ('KOTRA_MARKET', 'KOTRA_SHORT_NEWS'):
                c.execute(self.meta.tables['service_option'].insert(), {'option_code': code, 'option_type': 'SOURCE', 'label_ko': code, 'enabled': True, 'sort_order': 5, 'value_json': {}})
                c.execute(self.meta.tables['batch_run'].insert(), {'source_code': code, 'job_name': 'NEWS', 'target_table': 'article', 'period_start': date(2025, 10, 1), 'period_end': end, 'status': 'SUCCESS', 'detail_json': {}})

    def test_weekly_connects_keyword_spikes_in_subscribed_country(self):
        uid = int(self.signup())
        self.seed_news()
        self.collected_news()
        table = self.meta.tables['news_keyword_daily']
        with self.engine.begin() as c:
            for day, keyword, country, count in [(date(2025, 12, 21), '관세', 'US', 24), (date(2025, 12, 14), '관세', 'US', 64),
                                                 (date(2025, 12, 21), '평이', 'US', 23), (date(2025, 12, 14), '평이', 'US', 64),
                                                 (date(2025, 12, 21), '중국만', 'CN', 50)]:
                c.execute(table.insert(), {'metric_date': day, 'keyword_text': keyword, 'country_code': country, 'day_count': count})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['keyword_spikes'], [{'keyword': '관세', 'country': '미국', 'this_week': 24, 'weekly_avg_8w': 8.0, 'ratio': 3.0}])
        self.assertEqual((ctx['summary']['counts']['spike_keywords'], ctx['summary']['spike_ratio']), (1, 3))
        self.assertIn('관세', render('RPT_WEEKLY', ctx))
        self.assertIn('급증 키워드: 관세(미국) ×3.0', weekly_highlights(ctx))

    def test_weekly_total_counts_all_spikes_but_lists_ten(self):
        uid = int(self.signup())
        self.seed_news()
        self.collected_news()
        table = self.meta.tables['news_keyword_daily']
        with self.engine.begin() as c:
            for i in range(11):
                c.execute(table.insert(), {'metric_date': date(2025, 12, 21), 'keyword_text': f'키워드{i}', 'country_code': 'US', 'day_count': 5})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['summary']['counts']['spike_keywords'], 11)
        self.assertEqual(len(ctx['keyword_spikes']), 10)

    def market_article(self, identity, day=date(2025, 12, 21), visible='VISIBLE', **tag):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article'].insert(), {'article_id': identity, 'source_code': 'KOTRA_MARKET', 'external_id': f'kw-{identity}', 'title': f'기사 {identity}', 'published_date': day,
                'source_url': f'https://example.org/{identity}', 'body_masked': '본문', 'category': '경제', 'visibility_status': visible, 'admin_meta_json': {}})
            for values in tag.get('tags', ()):
                c.execute(self.meta.tables['article_tag'].insert(), {'article_id': identity, **values})

    def test_weekly_keyword_from_hidden_articles_only_is_not_listed(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news()
        for identity in (201, 202, 203):
            self.market_article(identity, visible='HIDDEN', tags=[{'keyword_text': '숨김'}, {'country_code': 'US'}])
        with self.engine.begin() as c:
            c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': date(2025, 12, 21), 'keyword_text': '숨김', 'country_code': 'US', 'day_count': 3})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['keyword_spikes'], [])

    def test_weekly_interest_uses_all_issue_articles_not_top_five(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news()
        # 미국 기사(가장 오래됨)는 대표 5건에 들지 않지만 관심 범위 판정에는 포함된다
        self.market_article(301, date(2025, 12, 15), tags=[{'keyword_text': '관세'}, {'country_code': 'US'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}])
        for identity in range(302, 307):
            self.market_article(identity, tags=[{'keyword_text': '관세'}, {'country_code': 'CN'}])
        with self.engine.begin() as c:
            c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': date(2025, 12, 21), 'keyword_text': '관세', 'country_code': None, 'day_count': 6})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        issue = next(r for r in ctx['summary']['issues'] if '관세' in r['title'])
        self.assertEqual(issue['related'], '관심 범위')

    def detected_spike_on_dec_15(self, keyword='관세', count=10):
        """실제 판정기로 12월 15일 급증 사건을 만든다. 수집이 15일까지만 끝나 보고서 마지막 날 기준 직접 계산은 돌지 않는다."""
        self.collected_news(end=date(2025, 12, 15))
        for identity in range(501, 501 + count):
            self.market_article(identity, date(2025, 12, 15), tags=[{'keyword_text': keyword}])
        self.keyword_day(date(2025, 12, 15), count, keyword)
        job.detect(self.app.state.database, date(2025, 12, 15))
        with self.engine.begin() as c:
            event = c.execute(select(self.meta.tables['alert_event'])).mappings().one()
        return event['payload_json']['article_ids']

    def set_hidden(self, ids):
        table = self.meta.tables['article']
        with self.engine.begin() as c:
            c.execute(update(table).where(table.c.article_id.in_(ids)).values(visibility_status='HIDDEN'))

    def test_spike_stays_when_only_saved_representatives_are_hidden(self):
        uid = int(self.signup())
        self.set_hidden(self.detected_spike_on_dec_15())
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual([issue['title'] for issue in ctx['summary']['issues']], ['‘관세’ 기사 급증'])
        self.assertIn('7일 5건', ctx['summary']['issues'][0]['basis'])

    def test_spike_is_dropped_when_every_evidence_article_is_hidden(self):
        uid = int(self.signup())
        self.detected_spike_on_dec_15()
        self.set_hidden(range(501, 511))
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['summary']['issues'], [])

    def test_spike_is_dropped_when_user_blocks_it_or_all_its_public_articles(self):
        uid = int(self.signup())
        representatives = self.detected_spike_on_dec_15()
        self.set_hidden(representatives)
        rest = [identity for identity in range(501, 511) if identity not in representatives]
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            c.execute(self.meta.tables['keyword_topic'].insert(), [{'keyword_text': word, 'status': 'UNCLASSIFIED'} for word in ('관세', '차단')])
            news.save_blocks(store, uid, ['관세'], news.blocks(store, uid)['settings']['version'])
        self.assertEqual(self.context(uid, self.record('RPT_WEEKLY'))[0]['summary']['issues'], [])
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            news.save_blocks(store, uid, ['차단'], news.blocks(store, uid)['settings']['version'])
            for identity in rest:
                c.execute(self.meta.tables['article_tag'].insert(), {'article_id': identity, 'keyword_text': '차단'})
        self.assertEqual(self.context(uid, self.record('RPT_WEEKLY'))[0]['summary']['issues'], [])

    def block(self, uid, *words, synonyms=()):
        """회원의 차단 키워드를 저장한다. synonyms는 (동의어, 대표어) 쌍이다."""
        with self.engine.begin() as c:
            table = self.meta.tables['keyword_topic']
            known = set(c.scalars(select(table.c.keyword_text)))
            rows = [{'keyword_text': w, 'status': 'UNCLASSIFIED'} for w in words if w not in known]
            rows += [{'keyword_text': s, 'canonical_text': w, 'status': 'UNCLASSIFIED'} for s, w in synonyms if s not in known]
            if rows:
                c.execute(table.insert(), [dict({'canonical_text': None}, **row) for row in rows])
            store = Store(self.app.state.database, c)
            news.save_blocks(store, uid, list(words), news.blocks(store, uid)['settings']['version'])

    def keyword_articles(self, first, count, day, *keywords, country='US'):
        """키워드 태그가 붙은 기사를 만들고 일별 집계(국가별·전체)에도 같은 건수를 더한다."""
        for identity in range(first, first + count):
            self.market_article(identity, day, tags=[*({'keyword_text': k} for k in keywords), {'country_code': country}])
        with self.engine.begin() as c:
            for keyword in keywords:
                for scope in (country, None):
                    c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': day, 'keyword_text': keyword, 'country_code': scope, 'day_count': count})

    def weekly_for_us(self, uid):
        return self.context(uid, self.record('RPT_WEEKLY'))[0]  # 리포트 조건의 국가는 미국이다

    def test_keyword_on_blocked_articles_is_not_a_spike(self):
        uid = int(self.signup())
        self.collected_news()
        self.keyword_articles(801, 3, date(2025, 12, 21), '관세', '차단')
        self.block(uid, '차단')
        ctx = self.weekly_for_us(uid)
        self.assertEqual(ctx['keyword_spikes'], [])
        self.assertEqual(ctx['summary']['counts']['spike_keywords'], 0)

    def test_blocked_articles_leave_both_recent_and_baseline_counts(self):
        uid = int(self.signup())
        self.collected_news()
        self.keyword_articles(811, 3, date(2025, 12, 21), '관세')
        self.keyword_articles(821, 8, date(2025, 12, 1), '관세')
        self.keyword_articles(831, 8, date(2025, 12, 2), '관세', '차단')  # 차단 전 주평균 2 → ×1.5
        self.block(uid, '차단')
        ctx = self.weekly_for_us(uid)  # 차단 후 주평균 1 → ×3.0
        self.assertEqual(ctx['keyword_spikes'], [{'keyword': '관세', 'country': '미국', 'this_week': 3, 'weekly_avg_8w': 1.0, 'ratio': 3.0}])

    def test_partly_blocked_keyword_shows_the_remaining_count(self):
        uid = int(self.signup())
        self.collected_news()
        self.keyword_articles(841, 3, date(2025, 12, 21), '관세')
        self.keyword_articles(851, 2, date(2025, 12, 20), '관세', '차단')
        self.block(uid, '차단')
        self.assertEqual(self.weekly_for_us(uid)['keyword_spikes'][0]['this_week'], 3)

    def test_synonym_of_blocked_topic_also_removes_the_article(self):
        uid = int(self.signup())
        self.collected_news()
        self.keyword_articles(861, 3, date(2025, 12, 21), '관세', '동의어')
        self.block(uid, '대표어', synonyms=[('동의어', '대표어')])
        self.assertEqual(self.weekly_for_us(uid)['keyword_spikes'], [])

    def test_blocks_apply_only_to_the_user_who_set_them(self):
        uid = int(self.signup())
        other = int(self.signup('other@example.org'))
        self.collected_news()
        self.keyword_articles(871, 3, date(2025, 12, 21), '관세', '차단')
        self.block(uid, '차단')
        self.assertEqual([r['keyword'] for r in self.weekly_for_us(other)['keyword_spikes']], ['관세', '차단'])

    def test_stored_spike_uses_counts_without_the_users_blocked_articles(self):
        uid = int(self.signup())
        self.collected_news(end=date(2025, 12, 15))
        self.keyword_articles(881, 4, date(2025, 12, 15), '관세')
        self.keyword_articles(891, 2, date(2025, 12, 14), '관세', '차단')
        job.detect(self.app.state.database, date(2025, 12, 15))
        self.block(uid, '차단')
        ctx = self.weekly_for_us(uid)
        issue = next(r for r in ctx['summary']['issues'] if '관세' in r['title'])
        self.assertIn('7일 4건', issue['basis'])

    def test_stored_spike_is_dropped_when_blocks_take_it_below_threshold(self):
        uid = int(self.signup())
        self.collected_news(end=date(2025, 12, 15))
        self.keyword_articles(901, 2, date(2025, 12, 15), '관세')
        self.keyword_articles(911, 2, date(2025, 12, 14), '관세', '차단')
        job.detect(self.app.state.database, date(2025, 12, 15))
        self.block(uid, '차단')
        self.assertEqual(self.weekly_for_us(uid)['summary']['issues'], [])

    def test_stored_product_spike_uses_counts_without_the_users_blocked_articles(self):
        uid = int(self.signup())
        self.collected_news(end=date(2025, 12, 15))
        hs = {'hs_code': '330499', 'hs_ver': 'HS2022'}
        for identity in range(921, 925):
            self.market_article(identity, date(2025, 12, 15), tags=[hs])
        for identity in range(931, 933):
            self.market_article(identity, date(2025, 12, 14), tags=[hs, {'keyword_text': '차단'}])
        with self.engine.begin() as c:
            c.execute(self.meta.tables['news_product_daily'].insert(), {'metric_date': date(2025, 12, 15), 'hs_code': '330499', 'hs_ver': 'HS2022', 'country_code': None, 'day_count': 6})
        job.detect(self.app.state.database, date(2025, 12, 15))
        self.block(uid, '차단')
        issue = next(r for r in self.weekly_for_us(uid)['summary']['issues'] if r['kind'] == 'SPIKE')
        self.assertIn('7일 4건', issue['basis'])

    def consent(self):
        r = self.client.post('/api/v1/users/me/consents', headers={'X-CSRF-Token': self.csrf},
                             json={'terms_type': 'NOTIFICATION', 'terms_version': 'TERMS_V1', 'action': 'AGREE', 'enable_notifications': True})
        self.assertEqual(r.status_code, 200, r.text)

    def short_news_run(self, start, end, status):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(), {'source_code': 'KOTRA_SHORT_NEWS', 'job_name': 'NEWS', 'target_table': 'article',
                                                               'period_start': start, 'period_end': end, 'status': status, 'detail_json': {}})

    def test_member_gets_spike_alert_from_viewable_evidence_when_all_representatives_are_blocked(self):
        """관세 기사 8건 중 최신 5건(대표 기사)만 차단해도 남은 3건으로 기준을 넘으면 알림을 받고, 어디서나 볼 수 있는 기사로 연다."""
        uid = int(self.signup()); self.consent()
        self.collected_news()
        self.keyword_articles(1001, 3, date(2025, 12, 15), '관세')
        self.keyword_articles(1011, 5, date(2025, 12, 21), '관세', '차단')
        self.block(uid, '차단')
        result = job.detect(self.app.state.database, date(2025, 12, 21))
        self.assertEqual(result['events'], 2)  # '관세'와 '차단' 키워드가 각각 급증. '차단' 사건은 차단한 회원에게 가지 않는다.
        self.assertEqual(result['deliveries'], 1)
        visible = {'1001', '1002', '1003'}
        items = self.client.get('/api/v1/notifications').json()['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['target_type'], 'ARTICLE')
        self.assertIn(items[0]['target_id'], visible)
        self.assertTrue({a['article_id'] for a in items[0]['issue']['articles']} <= visible)
        self.assertTrue(items[0]['issue']['articles'])
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 1)
        groups = self.client.get('/api/v1/notifications/popups').json()['groups']
        popup = [n for g in groups for n in g['notifications']]
        self.assertEqual([(n['target_type'], n['target_id'] in visible) for n in popup], [('ARTICLE', True)])
        major = self.client.get('/api/v1/issues/major').json()['items']
        self.assertEqual(len(major), 1)
        self.assertTrue(major[0]['articles'] and {a['article_id'] for a in major[0]['articles']} <= visible)
        issues = self.weekly_for_us(uid)['summary']['issues']
        self.assertEqual([r['title'] for r in issues], ['‘관세’ 기사 급증'])
        self.assertIn('7일 3건', issues[0]['basis'])

    def test_inbox_checks_evidence_of_each_spike_event_separately(self):
        """알림함 조건은 사건마다 자기 대상의 근거를 본다. 다른 사건의 근거 기사 때문에 통과하면 안 된다."""
        uid = int(self.signup()); self.consent()
        self.collected_news()
        self.keyword_articles(1061, 3, date(2025, 12, 21), '관세')
        self.keyword_articles(1071, 3, date(2025, 12, 21), '무역', '차단')
        job.detect(self.app.state.database, date(2025, 12, 21))
        labels = lambda: sorted(n['issue']['label'] for n in self.client.get('/api/v1/notifications').json()['items'])
        self.assertEqual(labels(), ['관세', '무역', '차단'])
        self.block(uid, '차단')  # 전달 뒤 차단: '무역' 기사는 모두 '차단' 태그가 있어 더는 열 수 없다
        self.assertEqual(labels(), ['관세'])
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 1)

    def test_alert_skips_a_representative_that_no_longer_matches_the_judgment(self):
        """대표 기사의 태그가 바뀌면(재적재) 근거 목록에서 빠지고, 클릭은 남은 같은 판정 기사로 간다."""
        self.signup(); self.consent()
        self.collected_news()
        for i, identity in enumerate(range(1101, 1109)):
            self.market_article(identity, date(2025, 12, 14 + i % 7), tags=[{'keyword_text': '관세'}, {'country_code': 'US'}])
        self.keyword_day(date(2025, 12, 21), 8)
        job.detect(self.app.state.database, date(2025, 12, 21))
        item = self.client.get('/api/v1/notifications').json()['items'][0]
        moved = item['target_id']
        tags = self.meta.tables['article_tag']
        with self.engine.begin() as c:
            c.execute(tags.update().where(tags.c.article_id == int(moved), tags.c.keyword_text == '관세').values(keyword_text='물류'))
        remaining = {str(i) for i in range(1101, 1109)} - {moved}
        item = self.client.get('/api/v1/notifications').json()['items'][0]
        self.assertIn(item['target_id'], remaining)
        self.assertNotIn(moved, {a['article_id'] for a in item['issue']['articles']})
        major = self.client.get('/api/v1/issues/major').json()['items'][0]
        self.assertNotIn(moved, {a['article_id'] for a in major['articles']})
        popup = [n for g in self.client.get('/api/v1/notifications/popups').json()['groups'] for n in g['notifications']][0]
        self.assertIn(popup['target_id'], remaining)

    def weekly_document(self, uid):
        """작업자가 만드는 것과 같은 주간 문서: 웹 본문·DOCX가 쓰는 문서와 PDF용 맥락을 함께 만든다."""
        record = self.record('RPT_WEEKLY')
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            doc = reports.build_document(store, uid, record)
            doc['template_pdf'] = {'version': 1, 'report_type': 'RPT_WEEKLY', 'context': build_context(store, uid, record, doc)}
            reports.attach_weekly_summary(doc)
        return doc

    def docx_sources(self, doc):
        from docx import Document
        word = Document(io.BytesIO(files.report_docx(doc, self.app.state.settings)))
        return next(row.cells[1].text for table in word.tables for row in table.rows if row.cells[0].text == '기사')

    def test_report_sources_count_legacy_and_current_short_news_once(self):
        uid = int(self.signup())
        self.collected_news()
        for identity, code in ((1201, 'KOTRA_SHORTNEWS'), (1202, 'KOTRA_SHORT_NEWS')):
            with self.engine.begin() as c:
                c.execute(self.meta.tables['article'].insert(), {'article_id': identity, 'source_code': code, 'external_id': f's-{identity}', 'title': f'단신 {identity}',
                    'published_date': date(2025, 12, 20), 'source_url': f'https://example.org/{identity}', 'body_masked': '본문', 'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
                for tag in ({'country_code': 'US'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}):
                    c.execute(self.meta.tables['article_tag'].insert(), {'article_id': identity, **tag})
        doc = self.weekly_document(uid)
        self.assertEqual(doc['template_pdf']['context']['summary']['counts']['short'], 2)
        labels = [s['label'] for s in doc['meta']['sources']]
        self.assertEqual(labels.count('KOTRA 단신속보'), 1)
        self.assertEqual(self.docx_sources(doc).count('KOTRA 단신속보'), 1)

    def test_report_sources_include_articles_used_for_spikes_beyond_the_20_cards(self):
        uid = int(self.signup())
        self.collected_news()
        for identity in range(1301, 1304):  # 단신 운임 3건 → 운임(미국) ×3.0
            with self.engine.begin() as c:
                c.execute(self.meta.tables['article'].insert(), {'article_id': identity, 'source_code': 'KOTRA_SHORT_NEWS', 'external_id': f'f-{identity}', 'title': f'운임 {identity}',
                    'published_date': date(2025, 12, 18), 'source_url': f'https://example.org/{identity}', 'body_masked': '본문', 'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
                for tag in ({'keyword_text': '운임'}, {'country_code': 'US'}):
                    c.execute(self.meta.tables['article_tag'].insert(), {'article_id': identity, **tag})
        with self.engine.begin() as c:
            for scope in ('US', None):
                c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': date(2025, 12, 18), 'keyword_text': '운임', 'country_code': scope, 'day_count': 3})
        for identity in range(1401, 1421):  # 본문 카드 20건을 채우는 최신 해외시장뉴스
            self.market_article(identity, date(2025, 12, 21), tags=[{'keyword_text': '관세'}, {'country_code': 'US'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}])
        doc = self.weekly_document(uid)
        ctx = doc['template_pdf']['context']
        self.assertEqual([r['keyword'] for r in ctx['keyword_spikes']], ['운임'])
        self.assertEqual(len(doc['sections'][0]['articles']), 20)
        labels = [s['label'] for s in doc['meta']['sources']]
        self.assertEqual(sorted(labels), ['KOTRA 단신속보', 'KOTRA 해외시장뉴스'])
        text = self.docx_sources(doc)
        self.assertEqual((text.count('KOTRA 단신속보'), text.count('KOTRA 해외시장뉴스')), (1, 1))
        self.assertEqual(ctx['news_source_label'], 'KOTRA 해외시장뉴스, KOTRA 단신속보')

    def test_member_without_any_viewable_evidence_gets_no_spike_alert(self):
        uid = int(self.signup()); self.consent()
        self.collected_news()
        self.keyword_articles(1021, 3, date(2025, 12, 21), '관세', '차단')
        self.block(uid, '차단')
        self.assertEqual(job.detect(self.app.state.database, date(2025, 12, 21))['deliveries'], 0)
        self.assertEqual(self.client.get('/api/v1/notifications').json()['items'], [])
        self.assertEqual(self.client.get('/api/v1/issues/major').json()['items'], [])

    def test_stored_spike_is_excluded_when_its_own_window_is_not_fully_collected(self):
        """판정 후 그 판정의 8주 비교 기간 중 하루가 재수집 실패로 바뀌면 그 판정은 쓰지 않는다."""
        uid = int(self.signup())
        self.collected_news()
        self.keyword_articles(1031, 3, date(2025, 12, 20), '관세')
        job.detect(self.app.state.database, date(2025, 12, 20))
        self.short_news_run(date(2025, 11, 10), date(2025, 11, 10), 'FAILED')
        summary = self.weekly_for_us(uid)['summary']
        self.assertEqual(summary['issues'], [])
        self.assertEqual(summary['spike_gap_note'], '12월 21일 기준 단신 수집이 완료되지 않아 키워드 급증은 판단하지 않았습니다.')

    def test_stored_spike_before_a_failed_day_keeps_its_own_date(self):
        """20일(토)까지 수집 완료 후 21일(일) 단신만 실패하면 20일 판정은 유효하고 안내는 21일 기준이다."""
        uid = int(self.signup())
        self.collected_news(end=date(2025, 12, 20))
        with self.engine.begin() as c:  # 시장뉴스는 21일까지 끝났다
            c.execute(self.meta.tables['batch_run'].insert(), {'source_code': 'KOTRA_MARKET', 'job_name': 'NEWS', 'target_table': 'article',
                                                               'period_start': date(2025, 12, 21), 'period_end': date(2025, 12, 21), 'status': 'SUCCESS', 'detail_json': {}})
        self.keyword_articles(1041, 3, date(2025, 12, 20), '관세')
        job.detect(self.app.state.database, date(2025, 12, 20))
        self.short_news_run(date(2025, 12, 21), date(2025, 12, 21), 'FAILED')
        summary = self.weekly_for_us(uid)['summary']
        self.assertEqual([r['title'] for r in summary['issues']], ['‘관세’ 기사 급증'])
        self.assertTrue(summary['issues'][0]['basis'].startswith('12월 20일 기준 · 7일 3건'))
        self.assertEqual(summary['spike_gap_note'], '12월 21일 기준 단신 수집이 완료되지 않아 키워드 급증은 판단하지 않았습니다.')

    def test_spike_shows_once_after_a_canonical_keyword_is_assigned(self):
        uid = int(self.signup())
        self.collected_news()
        self.keyword_articles(1051, 3, date(2025, 12, 21), '관세')
        job.detect(self.app.state.database, date(2025, 12, 21))
        with self.engine.begin() as c:
            c.execute(self.meta.tables['keyword_topic'].insert(), {'keyword_text': '관세', 'canonical_text': '관세정책', 'status': 'UNCLASSIFIED'})
        issues = self.weekly_for_us(uid)['summary']['issues']
        self.assertEqual([r['title'] for r in issues], ['‘관세’ 기사 급증'])

    def test_weekly_uses_latest_metric_date_when_old_date_is_reprocessed(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news()  # 저장된 판정은 수집이 끝난 기간에서만 쓴다
        self.market_article(601, date(2025, 12, 18), tags=[{'keyword_text': '관세'}])
        table = self.meta.tables['alert_event']
        with self.engine.begin() as c:
            for key, metric, count in [('a', '2025-12-20', 24), ('b', '2025-12-18', 6)]:  # 18일을 나중에 재처리
                c.execute(table.insert(), {'event_type': 'MAJOR_ISSUE', 'event_key': key * 64, 'created_at': datetime(2025, 12, 22), 'payload_json': {
                    'reason': 'WEEKLY_NEWS_SPIKE', 'label': '관세', 'metric_date': metric, 'recent_week_count': count, 'baseline_weekly': 2.0, 'ratio': 12.0, 'target': {'keyword_text': '관세'}}})
            for day, count in ((date(2025, 12, 18), 6), (date(2025, 12, 20), 18)):
                c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': day, 'keyword_text': '관세', 'country_code': None, 'day_count': count})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertIn('24건', ctx['summary']['issues'][0]['basis'])

    def stored_spike(self, key, metric, count, keyword='관세', **extra):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['alert_event'].insert(), {'event_type': 'MAJOR_ISSUE', 'event_key': key * 64, 'created_at': datetime(2025, 12, 22), 'payload_json': {
                'reason': 'WEEKLY_NEWS_SPIKE', 'label': keyword, 'metric_date': metric, 'recent_week_count': count, 'baseline_weekly': 0.0, 'ratio': float(count), 'target': {'keyword_text': keyword}, **extra}})

    def keyword_day(self, day, count, keyword='관세'):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': day, 'keyword_text': keyword, 'country_code': None, 'day_count': count})

    def test_stored_spike_is_dropped_when_public_count_falls_below_threshold(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news()  # 저장된 판정은 수집이 끝난 기간에서만 쓴다
        self.market_article(401, visible='HIDDEN', tags=[{'keyword_text': '관세'}])
        for identity in (402, 403):
            self.market_article(identity, tags=[{'keyword_text': '관세'}])
        self.keyword_day(date(2025, 12, 21), 3)
        self.stored_spike('a', '2025-12-21', 3)
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['summary']['issues'], [])

    def test_stored_spike_shows_recalculated_public_count(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news()  # 저장된 판정은 수집이 끝난 기간에서만 쓴다
        self.market_article(401, visible='HIDDEN', tags=[{'keyword_text': '관세'}])
        self.market_article(402, tags=[{'keyword_text': '관세'}])
        self.keyword_day(date(2025, 12, 21), 5)
        self.stored_spike('a', '2025-12-21', 5)
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertIn('7일 4건', ctx['summary']['issues'][0]['basis'])

    def test_weekly_says_spike_was_not_judged_without_collection(self):
        uid = int(self.signup())
        self.seed_news()
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertIsNone(ctx['summary']['issue_spike_ratio'])
        self.assertIn('시장뉴스 수집이 완료되지 않아 급증 이슈는 판단하지 않았습니다.', weekly_highlights(ctx))

    def test_weekly_says_only_keyword_spike_was_not_judged_when_short_news_is_missing(self):
        uid = int(self.signup())
        self.seed_news()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['service_option'].insert(), {'option_code': 'KOTRA_MARKET', 'option_type': 'SOURCE', 'label_ko': 'KOTRA', 'enabled': True, 'sort_order': 5, 'value_json': {}})
            c.execute(self.meta.tables['batch_run'].insert(), {'source_code': 'KOTRA_MARKET', 'job_name': 'NEWS', 'target_table': 'article', 'period_start': date(2025, 10, 1), 'period_end': date(2025, 12, 31), 'status': 'SUCCESS', 'detail_json': {}})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        summary = ctx['summary']
        self.assertEqual(summary['issue_spike_ratio'], 3)
        self.assertEqual(summary['spike_gap_note'], '12월 21일 기준 단신 수집이 완료되지 않아 키워드 급증은 판단하지 않았습니다.')
        self.assertIn('단신 수집이 완료되지 않아', summary['issues_note'])
        self.assertNotIn('이번 주 기준에 해당하는', summary['issues_note'])
        self.assertIn('단신 수집이 완료되지 않아', render('RPT_WEEKLY', ctx))

    def test_interest_ignores_articles_outside_judgment_window(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news(end=date(2025, 12, 15))  # 15일 저장 판정만 쓰이도록 수집은 15일까지
        # 15일 판정에 들어간 기사는 중국뿐이고, 같은 키워드의 미국 기사는 18일에 나왔다
        for identity in (501, 502, 503):
            self.market_article(identity, date(2025, 12, 15), tags=[{'keyword_text': '관세'}, {'country_code': 'CN'}])
        self.market_article(504, date(2025, 12, 18), tags=[{'keyword_text': '관세'}, {'country_code': 'US'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}])
        self.keyword_day(date(2025, 12, 15), 3)
        self.stored_spike('a', '2025-12-15', 3, window_start='2025-12-09', window_end='2025-12-15', article_ids=[501, 502, 503])
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['summary']['issues'][0]['related'], '—')

    def test_interest_counts_contributing_article_before_report_week(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news(end=date(2025, 12, 15))  # 15일 저장 판정만 쓰이도록 수집은 15일까지
        self.market_article(505, date(2025, 12, 10), tags=[{'keyword_text': '관세'}, {'country_code': 'US'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}])
        for identity in (506, 507):
            self.market_article(identity, date(2025, 12, 15), tags=[{'keyword_text': '관세'}, {'country_code': 'CN'}])
        self.keyword_day(date(2025, 12, 10), 1); self.keyword_day(date(2025, 12, 15), 2)
        self.stored_spike('a', '2025-12-15', 3, window_start='2025-12-09', window_end='2025-12-15', article_ids=[506, 507, 505])
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual(ctx['summary']['issues'][0]['related'], '관심 범위')

    def test_weekly_includes_sunday_regulation_before_judgment_and_keeps_same_titles(self):
        uid = int(self.signup())
        self.seed_news()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['service_option'].insert(), {'option_code': 'KOTRA_MARKET', 'option_type': 'SOURCE', 'label_ko': 'KOTRA', 'enabled': True, 'sort_order': 5, 'value_json': {}})
            for identity in (101, 102):  # 판정 작업이 돌기 전 일요일 기사, 제목이 같아도 별개 이슈
                c.execute(self.meta.tables['article'].insert(), {'article_id': identity, 'source_code': 'KOTRA_MARKET', 'external_id': f'reg-{identity}', 'title': '같은 제목 규제', 'published_date': date(2025, 12, 21),
                    'source_url': f'https://example.org/{identity}', 'body_masked': '본문', 'category': '통상·규제', 'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        self.assertEqual([r['title'] for r in ctx['summary']['issues']], ['같은 제목 규제', '같은 제목 규제'])

    def test_weekly_highlights_explain_missing_collection_and_empty_issues(self):
        uid = int(self.signup())
        self.seed_news()
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        text = weekly_highlights(ctx)
        self.assertIn('직전 8주와 비교하지 않았습니다', text)
        self.assertIn('주요 이슈: 12월 21일 기준 시장뉴스 수집이 완료되지 않아 급증 이슈는 판단하지 않았습니다. 확인한 범위에는 주요 이슈가 없습니다.', text)

    def test_weekly_lists_major_issues_of_that_week_once_and_marks_interest(self):
        uid = int(self.signup())
        self.seed_news(); self.collected_news()  # 저장된 판정은 수집이 끝난 기간에서만 쓴다
        self.market_article(701, tags=[{'keyword_text': '관세'}])
        table = self.meta.tables['alert_event']
        article_id = 1
        with self.engine.begin() as c:
            for key, metric, label, count in [('a', '2025-12-20', '관세', 20), ('b', '2025-12-21', '관세', 24), ('c', '2025-12-30', '다음주', 9)]:
                c.execute(table.insert(), {'event_type': 'MAJOR_ISSUE', 'event_key': key * 64, 'created_at': datetime(2025, 12, 22), 'payload_json': {
                    'reason': 'WEEKLY_NEWS_SPIKE', 'label': label, 'metric_date': metric, 'recent_week_count': count, 'baseline_weekly': 8.0, 'ratio': 3.0, 'target': {'keyword_text': label}}})
            c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': date(2025, 12, 21), 'keyword_text': '관세', 'country_code': None, 'day_count': 24})
            c.execute(table.insert(), {'event_type': 'MAJOR_ISSUE', 'event_key': 'd' * 64, 'article_id': article_id, 'created_at': datetime(2025, 12, 22), 'payload_json': {
                'reason': 'KOTRA_TRADE_REGULATION', 'label': '규제 강화', 'metric_date': '2025-12-21', 'article_ids': [article_id], 'target': {}}})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY'))
        issues = {r['title']: r for r in ctx['summary']['issues']}
        self.assertEqual(set(issues), {'‘관세’ 기사 급증', '규제 강화'})
        self.assertIn('24건', issues['‘관세’ 기사 급증']['basis'])
        self.assertEqual(issues['규제 강화']['related'], '관심 범위')
        self.assertIn('규제 강화', render('RPT_WEEKLY', ctx))

    def test_unsafe_links_are_removed(self):
        self.assertIsNone(safe_url('javascript:alert(1)'))
        self.assertIsNone(safe_url('file:///C:/Windows/win.ini'))
        self.assertEqual(safe_url('https://example.org/a'), 'https://example.org/a')

    def test_multiple_items_remain_separate_and_sum_to_existing_total(self):
        uid = int(self.signup())
        with self.engine.begin() as c:
            c.execute(self.meta.tables['hs_code'].insert(), {
                'hs_code': '330510', 'hs_ver': 'HS2022', 'name_ko': '샴푸',
                'source_edition': 'HS2022', 'depth': 6})
            c.execute(self.meta.tables['export_summary'].insert(), {
                'trade_month': date(2025, 12, 1), 'hs6_code': '330510', 'hs_ver': 'HS2022',
                'country_code': 'US', 'amount_usd': 700000})
        hs = [{'hs_code': code, 'hs_ver': 'HS2022'} for code in ('330499', '330510')]
        ctx, doc, sql = self.context(uid, self.record(hs_items=hs))
        self.assertEqual([r['amount_usd'] for r in ctx['items']], [1230000, 700000])
        self.assertEqual(sum(r['amount_usd'] for r in ctx['items']), doc['sections'][1]['kpi']['amount_usd'])
        self.assertEqual(ctx['items'][1]['yoy_pct'], None)  # Verified previous zero, not a fabricated growth rate.
        self.assertTrue(ctx['items'][1]['top_countries'][0]['is_new_yoy'])
        self.assertIn('샴푸', render('RPT_MONTHLY', ctx))

    def test_incomplete_comparison_quarter_is_not_a_partial_sum(self):
        uid = int(self.signup())
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(), {
                'source_code': 'CUSTOMS', 'job_name': 'HS6_AGGREGATION', 'target_table': 'export_summary',
                'period_start': date(2024, 11, 1), 'period_end': date(2024, 11, 30), 'status': 'FAILED',
                'detail_json': {'schema_version': 1}})
        ctx, _, _ = self.context(uid, self.record('RPT_QUARTERLY'))
        item = ctx['items'][0]
        self.assertIsNone(item['prev_amount_usd'])
        self.assertIsNone(item['yoy_pct'])
        self.assertIsNone(item['quarters']['values'][3])
        self.assertIsNone(item['country_count_diff'])

    def test_country_counts_deduplicate_tags_but_explain_multicountry_articles(self):
        uid = int(self.signup())
        self.seed_news()
        with self.engine.begin() as c:
            t = self.meta.tables['article_tag']
            c.execute(t.insert(), {'article_id': 25, 'country_code': 'CN', 'hs_code': '330499', 'hs_ver': 'HS2022', 'weight': 1})
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY', country_codes=[]))
        self.assertEqual(ctx['summary']['counts']['total'], 25)
        self.assertEqual(sum(r['market'] + r['short'] for r in ctx['country_counts']), 26)
        self.assertIn('다국가', ctx['country_count_note'])

    def test_other_countries_include_dropped_out_countries_in_comparison(self):
        uid = int(self.signup())
        with self.engine.begin() as c:
            t = self.meta.tables['export_summary']
            c.execute(delete(t).where(t.c.country_code == 'CN', t.c.trade_month == date(2025, 12, 1)))
        ctx, _, _ = self.context(uid, self.record(country_codes=[]))
        other = ctx['items'][0]['others']
        self.assertEqual(other['amount_usd'], 0)
        self.assertEqual(other['yoy_pct'], -100)

    def test_weekly_empty_scope_is_a_valid_empty_report(self):
        uid = int(self.signup())
        self.seed_news()
        ctx, _, _ = self.context(uid, self.record('RPT_WEEKLY', country_codes=['CN']))
        self.assertEqual(ctx['summary']['counts']['total'], 0)
        self.assertEqual(ctx['articles'], [])
        self.assertIn('조건에 맞는 관련 기사가 없습니다', render('RPT_WEEKLY', ctx))

    def test_legacy_snapshot_pdf_and_docx_do_not_use_html_renderer(self):
        uid = int(self.signup())
        _, document, _ = self.context(uid, self.record())
        with patch('backend.app.services.report_pdf.template_pdf', side_effect=AssertionError('unexpected HTML')):
            self.assertTrue(files.report_pdf(document, self.settings).startswith(b'%PDF'))
            self.assertTrue(files.report_docx(document, self.settings).startswith(b'PK'))

    def test_missing_font_is_explicit_not_a_silent_legacy_fallback(self):
        from backend.app.services.report_pdf import template_pdf
        with self.assertRaises(ApiError) as caught:
            template_pdf('RPT_MONTHLY', {}, Settings(values={'API_REPORT_FONT': 'missing-report-font.ttf'}))
        self.assertEqual(caught.exception.code, 'REPORT_FONT_UNAVAILABLE')

    def test_browser_failure_keeps_worker_retry_policy_without_legacy_fallback(self):
        from playwright.sync_api import Error
        uid = int(self.signup())
        ctx, document, _ = self.context(uid, self.record())
        document['template_pdf'] = {'report_type': 'RPT_MONTHLY', 'context': ctx}
        with patch('playwright.sync_api.sync_playwright', side_effect=Error('browser unavailable')), \
             patch('backend.app.api.files.reportlab_pdf', side_effect=AssertionError('legacy fallback')):
            with self.assertRaises(ApiError) as caught:
                files.report_pdf(document, self.settings)
        self.assertEqual(caught.exception.code, 'REPORT_RENDERER_UNAVAILABLE')
        self.assertTrue(caught.exception.retryable)

    @unittest.skipUnless(os.environ.get('RUN_REPORT_PDF_TESTS') == '1', 'Requires installed Playwright Chromium')
    def test_three_kinds_worker_pdf_docx_download_and_frozen_snapshot(self):
        from pypdf import PdfReader
        uid = int(self.signup())
        self.seed_news()
        for kind in ('RPT_MONTHLY', 'RPT_WEEKLY', 'RPT_QUARTERLY'):
            with self.subTest(kind=kind):
                record = self.record(kind, sections=['KPI', 'TOP_COUNTRIES', 'LOCAL_ISSUES'])
                with self.engine.begin() as c:
                    store = Store(self.app.state.database, c)
                    rid = store.insert('report', user_id=uid, report_type=kind, generation_key=kind,
                        conditions_json=record['conditions_json'], status='QUEUED', retry_count=0, created_at=now())
                    jobs.enqueue(store, uid, 'API_REPORT', rid, {'report_id': str(rid)}, kind)
                self.assertTrue(run_once(self.app.state.database, self.settings))
                response = self.client.get(f'/api/v1/reports/{rid}')
                self.assertEqual(response.status_code, 200, response.text)
                data = self.client.get(f'/api/v1/reports/{rid}/files/pdf')
                self.assertEqual(data.status_code, 200, data.text[:100] if data.status_code != 200 else '')
                pdf = PdfReader(io.BytesIO(data.content))
                text = '\n'.join(p.extract_text() for p in pdf.pages)
                self.assertIn('BORDERLESSSELLERS', ''.join(text.split()))
                self.assertIn('목차', text)
                self.assertIn('한국경제', text)
                self.assertNotIn('템플릿 예시', text)
                self.assertNotIn('\ufffd', text)
                self.assertGreaterEqual(len(pdf.pages), 5)
                for page in pdf.pages:
                    self.assertAlmostEqual(float(page.mediabox.width), 595.28, delta=2)
                    self.assertAlmostEqual(float(page.mediabox.height), 841.89, delta=2)
                docx = self.client.get(f'/api/v1/reports/{rid}/files/docx')
                self.assertEqual(docx.status_code, 200)
                self.assertTrue(docx.content.startswith(b'PK'))
                self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/files/xlsx').status_code, 200)
                with self.engine.begin() as c:
                    table = self.meta.tables['report']
                    saved = c.execute(select(table.c.content_json).where(table.c.report_id == rid)).scalar_one()
                    self.assertEqual(saved['template_pdf']['report_type'], kind)
                    self.assertIn('sections', saved)
                with patch('backend.app.api.reports.build_document', side_effect=AssertionError('must not rebuild')):
                    self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/files/pdf').content, data.content)
                output = os.environ.get('REPORT_PDF_QA_DIRECTORY')
                if output:
                    folder = Path(output)
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / (kind + '.pdf')).write_bytes(data.content)
                    (folder / (kind + '.html')).write_text(render(kind, saved['template_pdf']['context']), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
