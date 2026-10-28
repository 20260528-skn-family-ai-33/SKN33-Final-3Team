"""Regression coverage for result-to-report conditions, zero trades and major-issue flags."""
import unittest
from datetime import date
from sqlalchemy import delete
from backend.tests import test_backend_api as fixtures
from backend.app.api import reports
from backend.app.api.common import ApiError, now
from backend.app.api.database import Store
from backend.app.api.worker import run_once


class ReportNewsRegressionTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    post = fixtures.BackendApiTest.post

    def document(self, uid, start='2025-11-01', end='2025-12-31', **extra):
        conditions = {'period': {'start': start, 'end': end}, 'hs_items': [],
                      'country_codes': ['US'], 'sections': ['KPI', 'TOP_COUNTRIES'], **extra}
        with self.engine.begin() as c:
            return reports.build_document(Store(self.app.state.database, c), uid,
                {'conditions_json': conditions, 'report_type': 'RPT_ON_DEMAND'})

    def remove_exports(self, *months):
        t = self.meta.tables['export_summary']
        with self.engine.begin() as c:
            c.execute(delete(t).where(t.c.country_code == 'US', t.c.trade_month.in_(months)))

    def complete_report(self, preview, key):
        response = self.post('reports', {'preview_id': preview['preview_id'], 'confirm_conditions': True,
            'sections': ['KPI', 'TOP_COUNTRIES'], 'formats': ['PDF', 'DOCX']}, **{'Idempotency-Key': key})
        self.assertEqual(response.status_code, 202, response.text)
        rid = response.json()['job']['report_id']
        self.assertTrue(run_once(self.app.state.database, self.settings))
        response = self.client.get(f'/api/v1/reports/{rid}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['report']['status'], 'READY')
        for fmt, signature in [('pdf', b'%PDF'), ('docx', b'PK'), ('xlsx', b'PK')]:
            file = self.client.get(f'/api/v1/reports/{rid}/files/{fmt}')
            self.assertEqual(file.status_code, 200, file.text[:100] if file.status_code != 200 else '')
            self.assertTrue(file.content.startswith(signature), fmt)
        return response.json()

    def test_changes_snapshot_preview_preserves_period_scope_and_comparison(self):
        self.signup()
        for compare, month, prior in [('MOM', '2025-12', '2025-11'), ('YOY', '2025-12', '2024-12')]:
            with self.subTest(compare=compare):
                body = {'compare': compare, 'country_codes': ['US'],
                        'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}]}
                if month: body['month'] = month
                result = self.post('analysis/changes', body)
                self.assertEqual(result.status_code, 200, result.text)
                preview = self.post('reports/preview', {'result_id': result.json()['table']['result_id']})
                self.assertEqual(preview.status_code, 200, preview.text)
                conditions = preview.json()['conditions']
                self.assertEqual(conditions['period'], {'start': '2025-12-01', 'end': '2025-12-31'})
                self.assertEqual(conditions['compare_period']['start'], prior + '-01')
                self.assertEqual(conditions['compare'], compare)
                self.assertEqual(conditions['country_codes'], ['US'])
                self.assertEqual(conditions['hs_items'], body['hs_items'])

    def test_changes_mom_report_finishes_with_mom_kpi(self):
        self.signup()
        result = self.post('analysis/changes', {'month': '2025-12', 'compare': 'MOM', 'country_codes': ['US']})
        preview = self.post('reports/preview', {'result_id': result.json()['table']['result_id']})
        self.assertEqual(preview.status_code, 200, preview.text)
        report = self.complete_report(preview.json(), 'changes-mom')
        kpi = next(s['kpi'] for s in report['sections'] if s['section_code'] == 'KPI')
        self.assertAlmostEqual(kpi['mom_pct'], 10000 / 1220000 * 100, places=6)
        self.assertIsNone(kpi['yoy_pct'])

    def test_zero_trade_report_finishes_and_exports_files(self):
        self.signup()
        self.remove_exports(date(2025, 11, 1), date(2025, 12, 1))
        preview = self.post('reports/preview', {'conditions': {
            'period': {'start': '2025-11-01', 'end': '2025-12-31'}, 'country_codes': ['US']}})
        self.assertEqual(preview.status_code, 200, preview.text)
        report = self.complete_report(preview.json(), 'zero-trades')
        kpi = next(s for s in report['sections'] if s['section_code'] == 'KPI')
        self.assertEqual(kpi['kpi']['amount_usd'], 0)
        self.assertEqual(kpi['kpi']['export_country_count'], 0)
        self.assertEqual(kpi['kpi']['yoy_pct'], -100)
        self.assertIsNone(kpi['kpi']['top_country_share_pct'])
        self.assertEqual(kpi['tables'][1]['rows'], [
            {'month': '2025-11', 'amount_usd': 0}, {'month': '2025-12', 'amount_usd': 0}])

    def test_report_keeps_zero_trade_month_between_observed_months(self):
        uid = int(self.signup())
        self.remove_exports(date(2025, 11, 1))
        doc = self.document(uid, start='2025-10-01')
        kpi = next(s for s in doc['sections'] if s['section_code'] == 'KPI')
        self.assertEqual(kpi['tables'][1]['rows'], [
            {'month': '2025-10', 'amount_usd': 1210000},
            {'month': '2025-11', 'amount_usd': 0}, {'month': '2025-12', 'amount_usd': 1230000}])
        self.assertEqual(kpi['kpi']['amount_usd'], 2440000)

    def mark_unverified(self, start, end):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(), {'source_code': 'CUSTOMS',
                'job_name': 'HS6_AGGREGATION', 'target_table': 'export_summary',
                'period_start': start, 'period_end': end, 'status': 'FAILED', 'detail_json': {}})

    def test_unverified_current_month_is_not_filled_with_zero(self):
        uid = int(self.signup())
        self.mark_unverified(date(2025, 11, 1), date(2025, 11, 30))
        with self.assertRaises(ApiError) as caught:
            self.document(uid)
        self.assertEqual(caught.exception.code, 'DATA_NOT_READY')

    def test_unverified_comparison_remains_null_with_warning(self):
        uid = int(self.signup())
        self.mark_unverified(date(2024, 11, 1), date(2024, 12, 31))
        doc = self.document(uid, compare_period={'start': '2024-11-01', 'end': '2024-12-31'})
        self.assertEqual(doc['meta']['freshness'], 'STALE')
        self.assertTrue(doc['meta']['warnings'])
        kpi = next(s['kpi'] for s in doc['sections'] if s['section_code'] == 'KPI')
        self.assertIsNone(kpi['yoy_pct'])

    def test_major_issue_event_and_category_flags_match_list_and_detail(self):
        self.signup()
        with self.engine.begin() as c:
            for identity, category in [(1, '트렌드'), (2, '통상·규제'), (3, '트렌드')]:
                c.execute(self.meta.tables['article'].insert(), {'article_id': identity,
                    'source_code': 'KOTRA_MARKET', 'external_id': str(identity), 'title': '검증 기사',
                    'published_date': date(2025, 12, 1), 'source_url': 'https://example.org/news',
                    'body_masked': '가상 기사', 'category': category, 'visibility_status': 'VISIBLE',
                    'admin_meta_json': {}})
            for identity, kind, article in [(1, 'MAJOR_ISSUE', 1), (2, 'MAJOR_ISSUE', 1), (3, 'KEYWORD_SPIKE', 3)]:
                c.execute(self.meta.tables['alert_event'].insert(), {'event_id': identity, 'event_type': kind,
                    'article_id': article, 'event_key': 'review-' + str(identity), 'payload_json': {}, 'created_at': now()})
        major = self.client.get('/api/v1/articles?tab=MAJOR_ISSUE')
        self.assertEqual(major.status_code, 200, major.text)
        self.assertEqual({r['article_id']: r['is_major_issue'] for r in major.json()['items']}, {'1': True, '2': True})
        self.assertEqual(len(major.json()['items']), 2)
        all_rows = self.client.get('/api/v1/articles').json()['items']
        self.assertEqual({r['article_id']: r['is_major_issue'] for r in all_rows}, {'1': True, '2': True, '3': False})
        for identity, expected in [(1, True), (2, True), (3, False)]:
            detail = self.client.get(f'/api/v1/articles/{identity}')
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()['article']['is_major_issue'], expected)


if __name__ == '__main__':
    unittest.main()
