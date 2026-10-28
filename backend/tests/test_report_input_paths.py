"""Report comparison semantics and snapshot reuse across API input paths."""
import unittest
from backend.tests import test_backend_api as fixtures
from backend.tests import test_report_news_regressions as report_fixtures
from backend.app.api import reports
from backend.app.api.database import Store
from backend.app.api.results import save_snapshot, read_snapshot
from backend.app.api.schemas import Conditions


class ReportInputPathsTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    post = fixtures.BackendApiTest.post
    chat_answer = fixtures.BackendApiTest.chat_answer
    complete_report = report_fixtures.ReportNewsRegressionTest.complete_report

    def conditions(self, **extra):
        return {'period': {'start': '2025-12-01', 'end': '2025-12-31'},
                'country_codes': ['US'], 'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}], **extra}

    def preview(self, **extra):
        return self.post('reports/preview', {'conditions': self.conditions(**extra)})

    def test_direct_comparison_modes_generate_correct_periods(self):
        self.signup()
        for mode, expected in [('MOM', {'start': '2025-11-01', 'end': '2025-11-30'}),
                               ('YOY', {'start': '2024-12-01', 'end': '2024-12-31'}),
                               ('YOY_PERIOD', {'start': '2024-12-01', 'end': '2024-12-31'}),
                               ('NONE', None)]:
            with self.subTest(mode=mode):
                response = self.preview(compare=mode)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['conditions']['compare_period'], expected)

    def test_direct_comparison_modes_drive_correct_report_kpis_and_text(self):
        uid = int(self.signup())
        custom = {'start': '2025-10-01', 'end': '2025-10-31'}
        for mode, expected in [('MOM', 0.819672), ('YOY', 10.810811), ('NONE', None), ('CUSTOM', None)]:
            with self.subTest(mode=mode):
                response = self.preview(compare=mode, **({'compare_period': custom} if mode == 'CUSTOM' else {}))
                self.assertEqual(response.status_code, 200, response.text)
                conditions = {**response.json()['conditions'], 'sections': ['KPI']}
                with self.engine.begin() as c:
                    doc = reports.build_document(Store(self.app.state.database, c), uid,
                        {'conditions_json': conditions, 'report_type': 'RPT_ON_DEMAND'})
                kpi = doc['sections'][1]['kpi']
                self.assertEqual(kpi['mom_pct'], expected if mode == 'MOM' else None)
                self.assertEqual(kpi['yoy_pct'], expected if mode == 'YOY' else None)
                if mode == 'NONE': self.assertNotIn('증감률', doc['sections'][0]['text'])
                if mode == 'CUSTOM': self.assertIn('1.65%', doc['sections'][0]['text'])

    def test_invalid_comparison_inputs_are_rejected(self):
        self.signup()
        current = self.conditions()['period']
        previous_year = {'start': '2024-12-01', 'end': '2024-12-31'}
        for extra in [
            {'compare': 'NONE', 'compare_period': previous_year},
            {'compare': 'CUSTOM'},
            {'compare': 'CUSTOM', 'compare_period': current},
            {'compare': 'MOM', 'compare_period': previous_year},
            {'compare': 'YOY', 'compare_period': {'start': '2025-11-01', 'end': '2025-11-30'}},
        ]:
            with self.subTest(extra=extra):
                response = self.preview(**extra)
                self.assertEqual(response.status_code, 400, response.text)

    def test_explicit_matching_comparison_and_leap_year_month_boundaries(self):
        self.signup()
        response = self.preview(compare='MOM', period={'start': '2024-03-07', 'end': '2024-03-20'},
            compare_period={'start': '2024-02-10', 'end': '2024-02-20'})
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()['conditions']
        self.assertEqual(value['period'], {'start': '2024-03-01', 'end': '2024-03-31'})
        self.assertEqual(value['compare_period'], {'start': '2024-02-01', 'end': '2024-02-29'})
        self.assertTrue(value['applied_defaults'])

    def test_multi_month_mom_is_explicitly_rejected(self):
        self.signup()
        response = self.preview(compare='MOM', period={'start': '2025-11-01', 'end': '2025-12-31'})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['code'], 'UNSUPPORTED_COMPARISON')

    def test_long_yoy_period_and_latest_month_adjustment(self):
        self.signup()
        response = self.preview(compare='YOY_PERIOD', period={'start': '2024-01-01', 'end': '2025-12-31'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['conditions']['compare_period'], {'start': '2023-01-01', 'end': '2024-12-31'})
        response = self.preview(compare='YOY_PERIOD', period={'start': '2025-12-01', 'end': '2026-01-31'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['conditions']['compare_period'], {'start': '2024-12-01', 'end': '2024-12-31'})
        self.assertTrue(response.json()['conditions']['applied_defaults'])

    def test_result_reuse_from_kpis_trends_rankings_changes_and_market_shares(self):
        self.signup()
        requests = [
            ('kpis', lambda: self.client.get('/api/v1/trade/kpis?month=2025-12')),
            ('trends', lambda: self.client.get('/api/v1/trade/trends?period_start=2025-12&period_end=2025-12&trade_flow=EXPORT')),
            ('rankings', lambda: self.client.get('/api/v1/trade/rankings?period_start=2025-12&period_end=2025-12')),
            ('changes', lambda: self.post('analysis/changes', {'month': '2025-12', 'compare': 'MOM'})),
            ('shares', lambda: self.post('analysis/market-shares', {'conditions': self.conditions()})),
        ]
        for label, request in requests:
            with self.subTest(path=label):
                result = request()
                self.assertEqual(result.status_code, 200, result.text)
                payload = result.json()
                identity = payload.get('result_id') or payload['table']['result_id']
                response = self.post('reports/preview', {'result_id': identity})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['conditions']['period'], self.conditions()['period'])

    def test_chat_result_conditions_can_be_reused(self):
        self.signup()
        answer = self.chat_answer('2025년 12월 미국 전체 품목 수출 추이', 'report-path')
        self.assertEqual(answer['answer_status'], 'ANSWERED')
        response = self.post('reports/preview', {'result_id': answer['tables'][0]['result_id']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['conditions']['country_codes'], ['US'])
        self.assertEqual(response.json()['conditions']['period'], self.conditions()['period'])

    def test_default_24_month_export_trend_can_be_reused(self):
        self.signup()
        result = self.client.get('/api/v1/trade/trends?trade_flow=EXPORT')
        self.assertEqual(result.status_code, 200, result.text)
        response = self.post('reports/preview', {'result_id': result.json()['result_id']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['conditions']['period'], {'start': '2024-01-01', 'end': '2025-12-31'})

    def test_both_and_import_snapshots_are_not_silently_changed_to_export(self):
        uid = int(self.signup())
        for flow in ('BOTH', 'IMPORT'):
            with self.subTest(flow=flow):
                result = self.client.get('/api/v1/trade/trends', params={
                    'period_start': '2025-12', 'period_end': '2025-12', 'trade_flow': flow})
                self.assertEqual(result.status_code, 200, result.text)
                identity = result.json()['result_id']
                response = self.post('reports/preview', {'result_id': identity})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['code'], 'UNSUPPORTED_TRADE_FLOW')
                with self.engine.begin() as c:
                    snapshot = read_snapshot(Store(self.app.state.database, c), uid, identity, 'RESULT')
                self.assertEqual(snapshot['conditions']['trade_flow'], flow)

    def test_report_tables_store_only_analysis_conditions_and_can_be_reused(self):
        uid = int(self.signup())
        preview = self.preview(compare='NONE')
        self.assertEqual(preview.status_code, 200, preview.text)
        report = self.complete_report(preview.json(), 'reusable-report')
        identities = [table['result_id'] for section in report['sections'] for table in section['tables']]
        self.assertTrue(identities)
        for identity in identities:
            with self.subTest(identity=identity):
                with self.engine.begin() as c:
                    snapshot = read_snapshot(Store(self.app.state.database, c), uid, identity, 'RESULT')
                self.assertLessEqual(set(snapshot['conditions']), set(Conditions.model_fields))
                response = self.post('reports/preview', {'result_id': identity})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIsNone(response.json()['conditions']['compare_period'])

    def test_legacy_snapshot_internal_fields_are_ignored_but_direct_input_is_strict(self):
        uid = int(self.signup())
        legacy = {**self.conditions(compare='NONE'), 'schema_version': 1, 'sections': ['KPI'],
                  'formats': ['PDF'], 'session_id': None, 'request_hash': 'internal',
                  'applied_defaults': [], 'expanded_regions': []}
        with self.engine.begin() as c:
            identity = save_snapshot(Store(self.app.state.database, c), uid, 'RESULT', {'conditions': legacy})
        response = self.post('reports/preview', {'result_id': identity})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.post('reports/preview', {'conditions': legacy}).status_code, 400)

    def test_snapshot_ownership_expiry_and_kind_checks_remain_enforced(self):
        uid = int(self.signup())
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            valid = save_snapshot(store, uid, 'RESULT', {'conditions': self.conditions()})
            expired = save_snapshot(store, uid, 'RESULT', {'conditions': self.conditions()}, -10)
            wrong = save_snapshot(store, uid, 'PREVIEW', {'conditions': self.conditions()})
        self.assertEqual(self.post('reports/preview', {'result_id': expired}).status_code, 410)
        self.assertEqual(self.post('reports/preview', {'result_id': wrong}).status_code, 404)
        self.signup('bob@example.org')
        self.assertEqual(self.post('reports/preview', {'result_id': valid}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
