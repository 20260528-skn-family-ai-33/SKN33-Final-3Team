import unittest
from datetime import date
from decimal import Decimal

from backend.app.agents.charts import build_chart
from backend.app.agents.conditions import check_conditions
from backend.app.agents.context import restore_conditions
from backend.app.agents.multi_year import requested_years, year_comparison_draft
from backend.app.agents.planner import build_plan, postprocess
from backend.app.agents.state import PlannerOutput
from backend.app.agents.templates import run_template
from backend.app.agents.verify import judge_draft, snapshot_evidence
from backend.app.api import results
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.tests.api_support import reference_data, test_engine
from backend.tests.test_agent_state import state


class MultiYearTest(unittest.TestCase):
    def test_discrete_years_and_baseline(self):
        question = '2021년과 2024년을 2025년 수입액 기준으로 비교해줘'
        result = requested_years(question)
        self.assertEqual(result.years, [2021, 2024, 2025])
        self.assertEqual(result.baseline_year, 2025)
        self.assertFalse(result.monthly)
        self.assertIsNone(requested_years('2025년을 기준으로 2024년과 비교해줘'))
        self.assertIsNone(requested_years('2021년 1월, 2024년 2월, 2025년 3월 비교해줘'))
        self.assertIsNone(requested_years('2025년 1월부터 월별 수출을 알려줘', result))

    def test_annual_then_monthly_comparison_with_restored_context(self):
        engine, meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        conn = engine.connect()
        self.addCleanup(conn.close)
        db = Database(engine)
        db.settings = Settings({'API_HS_VECTOR_ENABLED': 'false'})
        store = Store(db, conn)
        store.insert('country', country_code='JP', name_ko='일본', country_role='COUNTRY')
        for table in ['import_summary', 'export_summary']:
            store.insert('batch_run', source_code='CUSTOMS', job_name='HS6_AGGREGATION', target_table=table,
                         period_start=date(2023, 1, 1), period_end=date(2023, 12, 31),
                         status='SUCCESS', detail_json={})
            for year in [2023, 2024, 2025]:
                for month in range(1, 13):
                    store.insert(table, trade_month=date(year, month, 1), hs6_code='330499', hs_ver='HS2022',
                                 country_code='JP', amount_usd=(year - 2022) * 1000 + month)
        s = state()
        for index, question in enumerate([
            '2025년 일본 수출액을 기준으로 2023년과 2024년을 비교해서 분석하고 싶어.',
            '25년을 기준으로 23년 그리고 24년 총 3개년을 월별로 나란히 비교해보자.',
            '25년을 기준으로, 23년 월별, 24년 월별 3개년을 비교해보자.',
        ]):
            with self.subTest(question=question):
                s.input.question = question
                planned = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question,
                    'custom_query': '각 기간 합계 비교',
                    'slots': {'countries': [{'mention':'일본','name':'일본'}] if index == 0 else [],
                              'period': {'start':'2023-01-01','end':'2024-12-01'}}}])
                s.plan, _ = build_plan(postprocess(planned, question, previous_conditions=s.input.context.conditions))
                task, = s.plan.tasks
                check_conditions(store, s, task.steps[0])
                self.assertIsNone(s.pending)
                self.assertEqual(task.conditions.country_codes, ['JP'])
                self.assertEqual(task.conditions.year_comparison.baseline_year, 2025)
                self.assertEqual(task.steps[1].args['template'], 'TREND')
                analysis = run_template(store, 7, task, task.steps[1])
                if index == 0:
                    self.assertEqual([r['year_label'] for r in analysis.rows], ['2023년','2024년','2025년'])
                    self.assertEqual([r['amount_usd'] for r in analysis.rows], [12078,24078,36078])
                    self.assertEqual(analysis.rows[0]['change_pct'],
                                     round(Decimal(-24000)/Decimal(36078)*100, 6))
                else:
                    self.assertEqual([r['month_label'] for r in analysis.rows], [f'{n}월' for n in range(1,13)])
                    self.assertEqual(analysis.rows[-1], {'month_label':'12월','year_2023_usd':1012,
                                                        'year_2024_usd':2012,'year_2025_usd':3012})
                saved = results.result_table(store, 7, analysis.rows, analysis.meta, analysis.conditions,
                                             columns=analysis.columns)
                chart, _, _ = build_chart(store, 7, task, saved['result_id'])
                self.assertEqual(len(chart.chart.series), 3 if index else 1)
                task.answer_status = 'ANSWERED'
                s.results[task.steps[1].step_id] = analysis.result
                s.draft = year_comparison_draft(s)
                self.assertIn('2025년 수출액 대비', s.draft.explanation[0])
                snapshot = results.read_snapshot(store, 7, saved['result_id'], 'RESULT')
                snapshots = {task.steps[1].step_id:snapshot_evidence(snapshot)}
                self.assertEqual(judge_draft(s, snapshots=snapshots).status, 'PASS')
                snapshot['rows'][0]['year_2023_usd' if index else 'amount_usd'] += 999999
                snapshots = {task.steps[1].step_id:snapshot_evidence(snapshot)}
                self.assertEqual(judge_draft(s, snapshots=snapshots).status, 'NEEDS_REVIEW')
                s.input.context.conditions = restore_conditions({'conditions':snapshot['conditions']})

    def test_range_and_explicit_years_keep_the_requested_anchor(self):
        for question in ('2022년부터 2025년까지 각 연도 수출액을 2024년 기준으로 비교해줘',
                         '2024년 기준으로 2022~2025년 수출액을 비교해줘',
                         '22년~25년 일본 수출액을 2024년 기준으로 비교해줘'):
            with self.subTest(question=question):
                parsed = requested_years(question)
                self.assertEqual(parsed.years, [2022, 2023, 2024, 2025])
                self.assertEqual(parsed.baseline_year, 2024)
        self.assertEqual(requested_years('2021년, 2024년을 2025년과 비교해줘').years, [2021, 2024, 2025])
        self.assertIsNone(requested_years('2024년부터 2025년까지 비교해줘'))

    def test_four_year_monthly_average_in_text_uses_full_years(self):
        engine, meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        conn = engine.connect()
        self.addCleanup(conn.close)
        db = Database(engine)
        db.settings = Settings({'API_HS_VECTOR_ENABLED':'false'})
        store = Store(db, conn)
        store.insert('country', country_code='JP', name_ko='일본', country_role='COUNTRY')
        for table in ['import_summary', 'export_summary']:
            store.insert('batch_run', source_code='CUSTOMS', job_name='HS6_AGGREGATION', target_table=table,
                         period_start=date(2022, 1, 1), period_end=date(2025, 12, 31),
                         status='SUCCESS', detail_json={})
        for year in range(2022, 2026):
            for month in range(1, 13):
                store.insert('export_summary', trade_month=date(year, month, 1), hs6_code='330499',
                             hs_ver='HS2022', country_code='JP', amount_usd=(year-2020)*5 + (month == 12))
        s = state()
        q = '2022년부터 2025년까지 일본 수출액을 2025년 기준으로 비교해서 연도별 월평균을 글로만 알려줘'
        s.input.question = q
        planned = PlannerOutput(tasks=[dict(intent='STATISTICS', question=q, custom_query='연도별 월평균',
                                            slots={'countries':[{'mention':'일본','name':'일본'}]})])
        s.plan, _ = build_plan(postprocess(planned, q))
        task, = s.plan.tasks
        self.assertEqual(task.slots.answer_format, 'TEXT')
        self.assertNotIn('build_chart', [step.tool for step in task.steps])
        check_conditions(store, s, task.steps[0])
        query = next(step for step in task.steps if step.tool == 'query_trade_data')
        analysis = run_template(store, 7, task, query)
        self.assertEqual([r['monthly_average'] for r in analysis.rows],
                         [Decimal(x) for x in ('10.08', '15.08', '20.08', '25.08')])
        self.assertEqual([r['amount_usd'] for r in analysis.rows], [121, 181, 241, 301])
        task.answer_status = 'ANSWERED'
        s.results[query.step_id] = analysis.result
        s.draft = year_comparison_draft(s)
        self.assertIn('월평균 수출액', s.draft.turn_summary)
        self.assertIn('2022년 10.08달러', s.draft.turn_summary)
        saved = results.result_table(store, 7, analysis.rows, analysis.meta, analysis.conditions, columns=analysis.columns)
        source = results.read_snapshot(store, 7, saved['result_id'], 'RESULT')
        self.assertEqual(judge_draft(s, snapshots={query.step_id:snapshot_evidence(source)}).status, 'PASS')
        source['rows'][0]['monthly_average'] = 99
        self.assertEqual(judge_draft(s, snapshots={query.step_id:snapshot_evidence(source)}).status, 'NEEDS_REVIEW')

    def test_extra_calculation_is_not_folded_into_amount_comparison(self):
        q = '2022년부터 2025년까지 일본 수출액의 연도별 표준편차를 2025년 기준으로 글로만 비교해줘'
        planned = PlannerOutput(tasks=[dict(intent='STATISTICS', question=q, custom_query='연도별 표준편차',
                                            slots={'countries':[{'mention':'일본','name':'일본'}]})])
        task, = postprocess(planned, q).tasks
        self.assertEqual((task.custom_query, task.slots.answer_format), ('연도별 표준편차', 'TEXT'))
