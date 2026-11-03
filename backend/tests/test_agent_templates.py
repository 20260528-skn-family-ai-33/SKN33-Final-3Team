import unittest
from datetime import date
from types import SimpleNamespace
from sqlalchemy import select, func

from backend.tests.api_support import reference_data, test_engine
from backend.app.agents.templates import run_template
from backend.app.agents.state import Conditions
from backend.app.api.database import Store, Database


class TemplateTest(unittest.TestCase):
    def setUp(self):
        engine, self.meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, self.meta)
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.store = Store(Database(engine), self.connection)

    def query(self, kind, **kwargs):
        conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-03-01'}, **kwargs)
        analysis = run_template(self.store, 1, SimpleNamespace(conditions=conditions),
                                SimpleNamespace(args={'template': kind}))
        self.assertEqual(self.connection.scalar(select(func.count()).select_from(self.meta.tables['api_snapshot'])), 0)
        return analysis

    def test_different_length_comparison_keeps_subtotal_and_null_unverified_total(self):
        analysis = self.query('TREND', country_codes=['US', 'CN'],
                              compare_period={'start': '2023-12-01', 'end': '2024-01-01'})
        self.assertEqual(len(analysis.rows), 6)
        self.assertEqual(analysis.result.completeness, 'PARTIAL')
        by_code = {t['country_code']: t for t in analysis.result.digest['targets']}
        us = by_code['US']
        self.assertIsNone(us['compare_total_usd'])
        self.assertEqual(us['compare_verified_months'], 1)
        self.assertEqual(us['compare_monthly_avg_usd'], 1_000_000)
        self.assertEqual(us['monthly_avg_usd'], 1_130_000)

    def test_country_ranking_uses_full_total(self):
        result = self.query('RANKING', ranking_group='COUNTRY').result.digest
        self.assertEqual([r['code'] for r in result['top']], ['CN', 'US'])
        self.assertEqual(result['total_usd'], sum(row['amount_usd'] for row in result['top']))

    def test_ranking_screen_fields_do_not_leak_into_chat_snapshot(self):
        analysis=self.query('RANKING',ranking_group='HS6')
        self.assertTrue(analysis.rows)
        self.assertNotIn('has_data',analysis.rows[0])
        self.assertNotIn('hs_name',analysis.rows[0])
        self.assertIn('name',analysis.rows[0])

    def test_recent_top_five_question_orders_and_limits_in_one_aggregate(self):
        from backend.app.agents.runtime import Services
        from backend.app.agents.conditions import check_conditions
        from backend.tests.test_agent_state import state
        from backend.app.api.config import Settings
        from unittest.mock import Mock
        from sqlalchemy import event
        from backend.app.agents.planner import explicit_country_ranking
        question='최근 공개된 3개월 동안 한국의 수출액이 가장 큰 국가 5곳을 순서대로 보여줘.'
        current=state()
        current.input.question=question
        provider=Mock()
        Services(None,Settings({}),provider).planner(current)
        provider.parse.assert_not_called()
        self.assertIsNone(explicit_country_ranking(question+' 그리고 그 이유도 설명해줘.'))
        task=current.plan.tasks[0]
        check_conditions(self.store,current,task.steps[0])
        self.assertEqual(task.conditions.period.start,date(2025,10,1))
        self.assertEqual(task.conditions.period.end,date(2025,12,1))
        for code in ('DE','FR','JP','BR','MX'):
            self.connection.execute(self.meta.tables['country'].insert().values(country_code=code,name_ko=code,country_role='COUNTRY'))
            self.connection.execute(self.meta.tables['export_summary'].insert().values(country_code=code,
                trade_month=date(2025,12,1),hs6_code='330499',hs_ver='HS2022',amount_usd=100))
        sent=[]
        event.listen(self.connection,'before_cursor_execute',lambda conn,cur,sql,params,ctx,many:sent.append(sql))
        result=run_template(self.store,1,task,task.steps[1])
        self.assertEqual([r['country_code'] for r in result.rows],['CN','US','BR','DE','FR'])
        self.assertEqual(len(result.sql),1)
        aggregates=[s for s in sent if 'sum(' in s.lower()]
        self.assertEqual(len(aggregates),1)
        self.assertIn('export_summary',aggregates[0])
        self.assertIn('DESC',aggregates[0])
        self.assertIn('LIMIT 5',result.sql[0])

    def test_compound_country_and_product_top_five_limits_rows_and_columns(self):
        from backend.app.agents.planner import explicit_country_ranking, build_plan
        from backend.app.agents.conditions import check_conditions
        from backend.tests.test_agent_state import state
        for i in range(7):
            code=f'9900{i:02}'
            values={'hs_code':code,'hs_ver':'HS2022','name_ko':code,'source_edition':'HS2022'}
            if self.meta.tables['hs_code'].c.depth.computed is None: values['depth']=6
            self.connection.execute(self.meta.tables['hs_code'].insert().values(**values))
            for source in ('export_summary','import_summary'):
                self.connection.execute(self.meta.tables[source].insert().values(country_code='US',
                    trade_month=date(2025,12,1),hs6_code=code,hs_ver='HS2022',amount_usd=100+i))
        for flow,word in (('EXPORT','수출'),('IMPORT','수입')):
            current=state()
            current.input.question=f'한국의 {word}액이 가장 큰 국가와 품목을 각각 5개씩 보여줘'
            current.plan,_=build_plan(explicit_country_ranking(current.input.question))
            self.assertEqual([t.slots.ranking_group for t in current.plan.tasks],['COUNTRY','HS6'])
            for task in current.plan.tasks:
                check_conditions(self.store,current,task.steps[0])
                self.store.queries.clear()
                analysis=run_template(self.store,1,task,task.steps[1])
                self.assertEqual(task.conditions.trade_flow,flow)
                self.assertLessEqual(len(analysis.rows),5)
                self.assertEqual(len(analysis.sql),1)
                self.assertIn('LIMIT 5',analysis.sql[0])
                from unittest.mock import patch
                from backend.app.agents.charts import build_chart
                with patch('backend.app.agents.charts.read_snapshot',return_value={
                    'rows':analysis.rows,'meta':analysis.meta}):
                    chart,_,_=build_chart(self.store,1,task,'test')
                self.assertEqual(len(chart.chart.series[0].values),len(analysis.rows))
                if task.slots.ranking_group=='HS6':
                    self.assertEqual(len(analysis.rows),5)
                    self.assertEqual(set(analysis.rows[0]),{'hs_code','name','rank',
                        'export_amount_usd' if flow=='EXPORT' else 'import_amount_usd'})
                    self.assertEqual(analysis.result.digest['group'],'HS6')

    def test_selected_country_outside_top_ten_keeps_global_denominator(self):
        countries = ['CA','AU','AT','DE','FR','BR','VN','TH','JP','MX','CL']
        for i, code in enumerate(countries):
            self.connection.execute(self.meta.tables['country'].insert().values(country_code=code, name_ko=code, country_role='COUNTRY'))
            self.connection.execute(self.meta.tables['export_summary'].insert().values(country_code=code,
                trade_month=date(2025,1,1), hs6_code='330499', hs_ver='HS2022', amount_usd=100-i))
        result = self.query('SHARE', share_basis='KOREA_TRADE_PARTNER', country_codes=['CL']).result.digest
        self.assertGreater(result['denominator_usd'], 90)
        self.assertEqual(next(r['amount_usd'] for r in result['items'] if r['code']=='CL'), 90)
        self.assertEqual(sum(r['amount_usd'] for r in result['items']), result['denominator_usd'])
