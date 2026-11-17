"""Dashboard and full trade APIs share amounts, but dashboard reads never save results."""
import time
import unittest
from unittest.mock import patch
from sqlalchemy import select, func, event
from backend.tests import test_backend_api as fixtures
from backend.app.api import trade


class DashboardPerformanceTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup
    post = fixtures.BackendApiTest.post

    def get(self, path, **params):
        response = self.client.get('/api/v1/trade/'+path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_period_amounts_and_query_cost_match_full_api(self):
        self.signup()
        # Five products, two countries, 24 months; all synthetic SQLite fixtures.
        with self.engine.begin() as c:
            hs = self.meta.tables['hs_code']
            original = dict(c.execute(select(hs).where(hs.c.hs_code=='330499')).mappings().one())
            for code in ('330410','330420','330430','330491'):
                row = {**original, 'hs_code':code, 'name_ko':code}
                if hs.c.depth.computed is not None: row.pop('depth',None)
                c.execute(hs.insert(),row)
            for name in ('export_summary','import_summary'):
                table = self.meta.tables[name]
                rows = list(c.execute(select(table)).mappings())
                for code in ('330410','330420','330430','330491'):
                    c.execute(table.insert(),[{**r,'hs6_code':code} for r in rows])
        # Warm schema reflection before comparing both paths under the same conditions.
        self.get('rankings',period_start='2025-10',period_end='2025-12',group_by='HS6')
        for start in ('2025-10','2025-07','2025-01'):
            with self.subTest(start=start):
                params={'period_start':start,'period_end':'2025-12','group_by':'HS6','page_size':5}
                results=[]
                for view in ('FULL','DASHBOARD'):
                    sql=[]; sql_times=[]; sql_started=[0]
                    def capture(conn,cursor,statement,parameters,context,many):
                        sql.append(statement); sql_started[0]=time.perf_counter()
                    def finished(conn,cursor,statement,parameters,context,many):
                        sql_times.append(time.perf_counter()-sql_started[0])
                    event.listen(self.engine,'before_cursor_execute',capture)
                    event.listen(self.engine,'after_cursor_execute',finished)
                    began=time.perf_counter()
                    try:
                        with patch.object(trade,'aggregate',wraps=trade.aggregate) as aggregates, patch.object(trade,'anomaly_map',wraps=trade.anomaly_map) as anomalies:
                            ranking=self.get('rankings',**params,view=view)
                            series=[self.get('trends',period_start=start,period_end='2025-12',hs_code=r['hs_item']['hs_code'],hs_ver='HS2022',trade_flow='EXPORT',view='FULL' if view=='FULL' else 'SPARKLINE') for r in ranking['items']]
                            self.assertEqual(aggregates.call_count,22 if view=='FULL' else 7)
                            self.assertEqual(anomalies.call_count,1 if view=='FULL' else 0)
                        writes=sum('INSERT INTO api_snapshot' in statement for statement in sql)
                        self.assertEqual(writes,6 if view=='FULL' else 0)
                    finally:
                        event.remove(self.engine,'before_cursor_execute',capture)
                        event.remove(self.engine,'after_cursor_execute',finished)
                    elapsed=(time.perf_counter()-began)*1000
                    print(f'DASHBOARD_BENCH start={start} view={view} sql={len(sql)} snapshots={writes} sql_ms={sum(sql_times)*1000:.2f} api_ms={elapsed:.1f}')
                    results.append((ranking,series))
                full,light=results
                for a,b in zip(full[0]['items'],light[0]['items']):
                    self.assertEqual(b,{k:v for k,v in a.items() if k not in ('one_off','rank_change','rank_status','mom_pct','yoy_pct')})
                self.assertEqual(full[0]['page'],light[0]['page'])
                for a,b in zip(full[1],light[1]):
                    self.assertEqual(b['items'],[{k:r[k] for k in ('month','export_amount_usd')} for r in a['items']])
                    self.assertNotIn('result_id',b)

    def test_dashboard_kpis_keep_import_and_previous_year_without_snapshot(self):
        self.signup()
        params={'period_start':'2025-01','period_end':'2025-12'}
        full=self.get('trends',**params)
        dashboard=self.get('trends',**params,view='DASHBOARD')
        self.assertEqual(full['items'],dashboard['items'])
        self.assertIn('result_id',full)
        self.assertNotIn('result_id',dashboard)
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(self.meta.tables['api_snapshot'])),1)

    def test_interest_scope_pagination_and_empty_results(self):
        self.signup()
        saved=self.client.get('/api/v1/users/me/interests').json()['interests']
        response=self.client.put('/api/v1/users/me/interests',headers={'X-CSRF-Token':self.csrf},json={
            'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}], 'country_codes':['US'],
            'landing_page':'INTERESTED','expected_version':saved['version']})
        self.assertEqual(response.status_code,200,response.text)
        for group in ('HS6','COUNTRY'):
            for page in (1,2):
                params={'period_start':'2025-12','period_end':'2025-12','scope':'INTERESTED','group_by':group,'page':page,'page_size':1}
                full=self.get('rankings',**params)
                light=self.get('rankings',**params,view='DASHBOARD')
                self.assertEqual(full['conditions'],light['conditions'])
                self.assertEqual(full['page'],light['page'])
                self.assertEqual([r['export_amount_usd'] for r in full['items']],[r['export_amount_usd'] for r in light['items']])
        full=self.get('trends',scope='INTERESTED',hs_code='330499',hs_ver='HS2022',period_start='2025-10',period_end='2025-12')
        light=self.get('trends',scope='INTERESTED',hs_code='330499',hs_ver='HS2022',period_start='2025-10',period_end='2025-12',view='SPARKLINE',trade_flow='EXPORT')
        self.assertEqual(full['conditions'],light['conditions'])
        self.assertEqual([r['export_amount_usd'] for r in full['items']],[r['export_amount_usd'] for r in light['items']])

    def test_view_validation_and_authentication(self):
        self.assertEqual(self.client.get('/api/v1/trade/rankings?view=DASHBOARD').status_code,401)
        self.signup()
        for path,params in [('rankings',{'view':'SPARKLINE'}),('trends',{'view':'unknown'}),('trends',{'view':'SPARKLINE','trade_flow':'IMPORT'}),('trends',{'view':'SPARKLINE'})]:
            self.assertEqual(self.client.get('/api/v1/trade/'+path,params=params).status_code,400)

    def test_country_only_interests_use_resolved_ranking_scope_for_sparklines(self):
        self.signup()
        saved=self.client.get('/api/v1/users/me/interests').json()['interests']
        response=self.client.put('/api/v1/users/me/interests',headers={'X-CSRF-Token':self.csrf},json={
            'hs_items':[], 'country_codes':['US'], 'landing_page':'INTERESTED',
            'expected_version':saved['version']})
        self.assertEqual(response.status_code,200,response.text)
        params={'period_start':'2025-10','period_end':'2025-12'}
        ranking=self.get('rankings',**params,scope='INTERESTED',group_by='HS6',view='DASHBOARD')
        self.assertTrue(ranking['items'])
        self.assertEqual(ranking['conditions']['country_codes'],['US'])
        for row in ranking['items']:
            series=self.get('trends',**params,**row['hs_item'],country_codes=ranking['conditions']['country_codes'],view='SPARKLINE',trade_flow='EXPORT')
            self.assertEqual(sum(month['export_amount_usd'] for month in series['items']),row['export_amount_usd'])


if __name__=='__main__':
    unittest.main()
