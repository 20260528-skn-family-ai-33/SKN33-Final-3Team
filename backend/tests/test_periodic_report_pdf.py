"""Real-data context, renderer routing, and optional Chromium/download integration."""
import io
import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import delete, select, update

from backend.tests import test_backend_api as fixtures
from backend.app.api import files, jobs, reports
from backend.app.api.common import ApiError, now
from backend.app.api.config import Settings
from backend.app.api.database import Store
from backend.app.api.worker import run_once
from backend.app.services.report_context import build_context, safe_url
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
        self.assertIn('아직 연결되지 않았습니다', html)
        self.assertNotIn('기사 저작권은 KOTRA', html)

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

    def test_requested_pdf_and_docx_do_not_use_html_renderer(self):
        uid = int(self.signup())
        _, document, _ = self.context(uid, self.record())
        with patch('backend.app.services.report_pdf.template_pdf', side_effect=AssertionError('unexpected HTML')):
            self.assertTrue(files.report_pdf(document, self.settings).startswith(b'%PDF'))
            # Even a periodic context attached to the snapshot must not alter DOCX rendering.
            document['template_pdf'] = {'context': {}, 'report_type': 'RPT_MONTHLY'}
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
                self.assertIn('BORDERLESS SELLERS', text)
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
