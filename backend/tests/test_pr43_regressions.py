"""PR43 contracts: isolated SQLite API tests, no live services or LLM calls."""
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from types import SimpleNamespace
from sqlalchemy import select, update
from sqlalchemy.dialects import mysql
from backend.tests import test_backend_api as fixtures
from backend.tests import test_chat_review_regressions as chat_fixtures
from backend.app.api import trade, reference, reports
from backend.app.api.database import Store
from backend.app.api.common import now
from backend.app.api.results import save_snapshot


class Pr43RegressionTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    post = fixtures.BackendApiTest.post
    ask = chat_fixtures.ChatReviewRegressionTest.ask

    def test_expired_preview_reconfirmation_and_empty_sections(self):
        uid = int(self.signup())
        body = {'conditions': {'period': {'start': '2025-12-01', 'end': '2025-12-31'},
                              'country_codes': ['US']}}
        preview = self.post('reports/preview', body).json()
        t = self.meta.tables['api_snapshot']
        with self.engine.begin() as c:
            c.execute(update(t).where(t.c.snapshot_id == preview['preview_id']).values(expires_at=now()-timedelta(seconds=1)))
        request = {'preview_id': preview['preview_id'], 'confirm_conditions': True, 'sections': [], 'formats': ['PDF']}
        response = self.post('reports', request, **{'Idempotency-Key': 'expired'})
        self.assertEqual((response.status_code, response.json()['code']), (409, 'PREVIEW_EXPIRED'))
        renewed = self.post('reports/preview', body).json()
        response = self.post('reports', {**request, 'preview_id': renewed['preview_id']}, **{'Idempotency-Key': 'renewed'})
        self.assertEqual(response.status_code, 202, response.text)
        with self.engine.begin() as c:
            record = c.execute(select(self.meta.tables['report'])).mappings().one()
            self.assertEqual(record['conditions_json']['sections'], list(reports.TITLES)[1:5])
            result_id = save_snapshot(Store(self.app.state.database, c), uid, 'RESULT', {})
            c.execute(update(t).where(t.c.snapshot_id == result_id).values(expires_at=now()-timedelta(seconds=1)))
        result = self.client.get('/api/v1/results/'+result_id)
        self.assertEqual((result.status_code, result.json()['code']), (410, 'RESULT_EXPIRED'))
        self.assertEqual(self.client.get('/api/v1/results/'+preview['preview_id']).status_code, 404)

    def test_delete_one_subscription_preserves_other_settings(self):
        uid = int(self.signup())
        t = self.meta.tables['report_subscription']
        original = {'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}], 'countries': ['US'],
                    'formats': ['DOCX'], 'topic': '기존 조건', 'enabled_since': '2025-01-03T00:00:00Z',
                    'last_observed_month': '2025-11'}
        with self.engine.begin() as c:
            for code in ('MONTHLY', 'WEEKLY'):
                c.execute(t.insert().values(user_id=uid, period_code=code, enabled=True, filters_json=original))
        items = self.client.get('/api/v1/users/me/subscriptions').json()['items']
        target = next(x for x in items if x['period_code']=='WEEKLY')
        response = self.client.delete('/api/v1/users/me/subscriptions/'+target['subscription_id'], headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 204, response.text)
        with self.engine.connect() as c:
            remaining = c.execute(select(t).where(t.c.period_code=='MONTHLY')).mappings().one()
            self.assertTrue(remaining['enabled'])
            self.assertEqual(remaining['filters_json'], original)

    def test_twelve_month_shares_use_all_countries_and_other(self):
        self.signup()
        with self.engine.begin() as c:
            for i in range(10):
                code = f'X{i}'
                c.execute(self.meta.tables['country'].insert().values(country_code=code, name_ko=code, country_role='COUNTRY'))
                c.execute(self.meta.tables['export_summary'].insert().values(trade_month=date(2025,1,1),
                    country_code=code, hs6_code='330499', hs_ver='HS2022', amount_usd=100+i))
            t = self.meta.tables['export_summary']
            countries = self.meta.tables['country']
            expected = sum(c.execute(select(t.c.amount_usd).join(countries,t.c.country_code==countries.c.country_code).where(
                countries.c.country_role=='COUNTRY', t.c.trade_month>=date(2025,1,1))).scalars())
        response = self.post('analysis/market-shares', {'conditions': {'country_codes': ['US'],
            'period': {'start': '2025-01-01', 'end': '2025-12-31'}}})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['denominator_usd'], expected)
        self.assertEqual(len(data['items']), 11)
        self.assertEqual(data['items'][-1]['label'], '기타')
        self.assertGreater(data['items'][-1]['amount_usd'], 0)
        self.assertAlmostEqual(sum(x['share_pct'] for x in data['items']), 100, places=4)
        self.assertIn('CN', [x['country_code'] for x in data['items']])

    def test_aggregate_hs_prefix_does_not_double_count_or_include_total(self):
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            hs = [{'hs_code': '3304', 'hs_ver': 'HS2022'}, {'hs_code': '330499', 'hs_ver': 'HS2022'}]
            all_rows = trade.aggregate(store, 'export_summary', date(2025,12,1), date(2025,12,1), hs=hs)
            self.assertEqual(sum(x['amount_usd'] for x in all_rows), 3460000)
            us = trade.aggregate(store, 'export_summary', date(2025,12,1), date(2025,12,1), hs=hs, countries=['US'])
            self.assertEqual(us[0]['amount_usd'], 1230000)
            self.assertEqual(len(us), 1)

    def test_query_plan_hint_only_for_unfiltered_mysql_scans(self):
        t = self.meta.tables['export_summary']
        statement = select(t)
        compile_sql = lambda stmt: str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={'literal_binds': True}))
        self.assertIn('FORCE INDEX (PRIMARY)', compile_sql(trade.trade_plan(statement,t,[],[])))
        self.assertNotIn('FORCE INDEX', compile_sql(trade.trade_plan(statement,t,[],['US'])))
        hs = [{'hs_code': '330499', 'hs_ver': 'HS2022'}]
        self.assertNotIn('FORCE INDEX', compile_sql(trade.trade_plan(statement,t,hs,[])))
        sql = compile_sql(statement.where(trade.hs_filter(t,hs)))
        self.assertIn("hs6_code = '330499'", sql)
        self.assertNotIn('LIKE', sql)

    def test_trend_scope_contract_and_selected_period_totals(self):
        self.signup()
        query = '?period_start=2025-10&period_end=2025-12'
        response = self.client.get('/api/v1/trade/trends'+query+'&scope=GLOBAL')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(sum(r['export_amount_usd'] for r in response.json()['items']),10320000)
        saved = self.client.get('/api/v1/users/me/interests').json()['interests']
        response = self.client.put('/api/v1/users/me/interests',json={'hs_items':[], 'country_codes':['US'],
            'expected_version':saved['version']},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,200,response.text)
        response = self.client.get('/api/v1/trade/trends'+query+'&scope=INTERESTED')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(sum(r['export_amount_usd'] for r in response.json()['items']),3660000)
        self.assertEqual(self.client.get('/api/v1/trade/trends'+query+'&scope=INVALID').status_code,400)

    def test_spaced_korean_hs_name_resolves_without_vector_guess(self):
        with self.engine.begin() as c, patch('backend.app.api.hs_vectors.candidates') as vectors:
            result = reference.chat_hs_search(Store(self.app.state.database,c), '기초 화장품')
        self.assertEqual(result['match_type'], 'EXACT')
        self.assertEqual([x['hs_code'] for x in result['items']], ['330499'])
        vectors.assert_not_called()

    def test_aggregate_cache_separates_scope_revision_and_expires(self):
        from backend.app.api.search_cache import SearchCache
        clock = [0]
        db = SimpleNamespace(trade_query_cache=SearchCache(maximum=64,ttl=30,clock=lambda:clock[0]))
        calls = []
        def make_store(revision):
            def rows(stmt):
                calls.append(stmt)
                return [{'amount_usd': 123}]
            return SimpleNamespace(c=SimpleNamespace(dialect=mysql.dialect()), db=db, rows=rows,
                cache={('trade_revision','export_summary'):revision}, queries=[])
        t = self.meta.tables['export_summary']
        statement = select(t).where(t.c.country_code=='US')
        with patch.object(trade,'verified_months'):
            first = trade.query_rows(make_store('v1'),statement,'export_summary')
            first[0]['amount_usd'] = 999
            self.assertEqual(trade.query_rows(make_store('v1'),statement,'export_summary')[0]['amount_usd'],123)
            self.assertEqual(len(calls),1)
            trade.query_rows(make_store('v1'),select(t).where(t.c.country_code=='CN'),'export_summary')
            trade.query_rows(make_store('v2'),statement,'export_summary')
            self.assertEqual(len(calls),3)
            clock[0]=31
            trade.query_rows(make_store('v2'),statement,'export_summary')
            self.assertEqual(len(calls),4)

    def test_chat_comparison_period_matches_prior_year_values(self):
        self.signup()
        _, _, job = self.ask('미국 수출', countries=[{'mention': '미국', 'name': '미국'}])
        answer = job['answer']
        self.assertEqual(answer['answer_status'], 'ANSWERED')
        self.assertEqual(answer['conditions']['compare_period'], {'start':'2024-12-01','end':'2024-12-31'})
        row = answer['tables'][0]['rows'][0]
        self.assertEqual(row['export_amount_usd'], 1230000)
        self.assertEqual(row['previous_year_export_usd'], 1110000)
        self.assertIn('10.81%', answer['text'])


if __name__ == '__main__':
    unittest.main()
