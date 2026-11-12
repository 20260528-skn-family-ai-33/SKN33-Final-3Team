"""이슈 113: 상대국 주어 수출입 방향, 여러 비교 기간을 한 표·차트로."""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from backend.tests.api_support import reference_data, test_engine
from backend.app.agents.charts import build_chart
from backend.app.agents.planner import build_plan, postprocess
from backend.app.agents.state import Conditions, PlannerOutput, PlannerSlots
from backend.app.agents.templates import run_template
from backend.app.agents.verify_claims import NameCatalog
from backend.app.agents.verify_evidence import build_facts
from backend.app.api.database import Store, Database
from backend.app.api.results import display_columns

US = [{'mention': '미국', 'name': '미국'}]
Y = lambda y: {'start': f'{y}-01-01', 'end': f'{y}-12-01'}


def plan(question, tasks):
    return postprocess(PlannerOutput.model_validate({'tasks': tasks}), question).tasks


class PartnerSubjectFlowTest(unittest.TestCase):
    def test_partner_subject_is_flipped_to_korea_basis(self):
        tasks = plan('미국이 한국에게 대표적으로 수입 수출하는 항목이뭐야?', [
            {'intent': 'RANKING', 'question': '미국이 한국에게 대표적으로 수입하는 품목 순위',
             'slots': {'countries': US, 'trade_flow': 'IMPORT', 'ranking_group': 'HS6'}},
            {'intent': 'RANKING', 'question': '미국이 한국에게 대표적으로 수출하는 품목 순위',
             'slots': {'countries': US, 'trade_flow': 'EXPORT', 'ranking_group': 'HS6'}}])
        self.assertEqual([(t.question, t.slots.trade_flow) for t in tasks], [
            ('미국이 한국에게 대표적으로 수입하는 품목 순위 (한국의 대미국 수출)', 'EXPORT'),
            ('미국이 한국에게 대표적으로 수출하는 품목 순위 (한국의 대미국 수입)', 'IMPORT')])

    def test_previous_both_flows_does_not_undo_partner_subject_flow(self):
        question = '미국이 한국에서 수입하는 품목은?'
        output = PlannerOutput.model_validate({'tasks': [{'intent': 'RANKING', 'question': question,
            'slots': {'countries': US, 'trade_flow': 'IMPORT', 'ranking_group': 'HS6'}}]})
        [task] = postprocess(output, question, previous_both_trade_flows=True).tasks
        self.assertEqual(task.slots.trade_flow, 'EXPORT')

    def test_partner_subject_with_both_flows_queries_both_directions(self):
        for question in ('미국이 한국에게 대표적으로 수입 수출하는 항목이 뭐야?', '미국이 한국에 수출입하는 대표 품목은?'):
            with self.subTest(question=question):
                tasks = plan(question, [{'intent': 'RANKING', 'question': question,
                                         'slots': {'countries': US, 'ranking_group': 'HS6'}}])
                self.assertEqual([(t.slots.trade_flow, t.question.endswith(label)) for t, label in
                                  zip(tasks, ('(한국의 대미국 수출)', '(한국의 대미국 수입)'))],
                                 [('EXPORT', True), ('IMPORT', True)])

    def test_korea_as_later_subject_keeps_llm_flow(self):
        question = '미국이 한국에 부과한 관세 이후 한국이 미국에 수출한 품목은?'
        [task] = plan(question, [{'intent': 'RANKING', 'question': question,
                                  'slots': {'countries': US, 'ranking_group': 'HS6', 'trade_flow': 'EXPORT'}}])
        self.assertEqual((task.question, task.slots.trade_flow), (question, 'EXPORT'))

    def test_already_flipped_by_llm_stays_and_korea_subject_unchanged(self):
        [task] = plan('미국은 한국산 화장품을 얼마나 수입해?', [{'intent': 'STATISTICS', 'question': '미국은 한국산 화장품을 얼마나 수입해?',
            'slots': {'countries': US, 'products': ['화장품'], 'trade_flow': 'EXPORT'}}])
        self.assertEqual(task.slots.trade_flow, 'EXPORT')
        [task] = plan('한국이 미국에 수출하는 품목 순위', [{'intent': 'RANKING', 'question': '한국이 미국에 수출하는 품목 순위',
            'slots': {'countries': US, 'trade_flow': 'EXPORT', 'ranking_group': 'HS6'}}])
        self.assertEqual((task.question, task.slots.trade_flow), ('한국이 미국에 수출하는 품목 순위', 'EXPORT'))
        [task] = plan('미국은 한국의 최대 수출국이야?', [{'intent': 'STATISTICS', 'question': '미국은 한국의 최대 수출국이야?',
            'slots': {'countries': US, 'trade_flow': 'EXPORT'}}])
        self.assertEqual(task.question, '미국은 한국의 최대 수출국이야?')


class SimpleRankingTableTest(unittest.TestCase):
    def ranking_args(self, question, **slots):
        output = PlannerOutput.model_validate({'tasks': [{'intent': 'RANKING', 'question': question,
            'slots': {'countries': US, 'ranking_group': 'HS6', **slots}}]})
        plan, _ = build_plan(output)
        return plan.tasks[0].steps[1].args

    def test_ranking_without_count_uses_simple_table(self):
        self.assertEqual(self.ranking_args('미국이 한국에게 대표적으로 수입하는 품목 순위 (한국의 대미국 수출)'),
                         {'template': 'RANKING', 'top': 10, 'amount_only': True})
        self.assertNotIn('amount_only', self.ranking_args('미국 수출 품목 순위와 전년 대비 증감률'))
        self.assertNotIn('amount_only', self.ranking_args('미국 수출 품목 순위 변화', comparison_requested=True))

    def test_ranking_group_asked_later_still_uses_simple_table(self):
        output = PlannerOutput.model_validate({'tasks': [{'intent': 'RANKING', 'question': '수출 순위', 'slots': {}}]})
        plan_, _ = build_plan(output)
        self.assertTrue(plan_.tasks[0].steps[1].args.get('amount_only'))

    def test_two_trade_tables_accept_unqualified_not_in_subquery(self):
        from backend.app.agents.sql import validate_sql
        from backend.app.agents.state import SqlDraft
        sub = "NOT IN (SELECT country_code FROM country WHERE country_role <> 'COUNTRY')"
        def draft(sql):
            return SqlDraft(sql=sql, summary='s', unsupported=None,
                            columns=[{'key': 'value', 'label': 'v', 'type': 'number', 'unit': 'USD'}])
        head = ("SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN import_summary i "
                "ON i.hs6_code = e.hs6_code AND i.trade_month = e.trade_month WHERE e.hs_ver = 'HS2022' AND i.hs_ver = 'HS2022' ")
        self.assertTrue(validate_sql(draft(head + f"AND e.country_code {sub} AND i.country_code {sub}")))
        with self.assertRaises(ValueError):  # 한쪽 표만 거르면 여전히 거부한다.
            validate_sql(draft(head + f"AND e.country_code {sub}"))


class CompareYearsSplitTest(unittest.TestCase):
    def slots(self, question, compare):
        [task] = plan(question, [{'intent': 'STATISTICS', 'question': question, 'slots': {
            'countries': [{'mention': '일본', 'name': '일본'}], 'trade_flow': 'EXPORT', 'period': Y(2025),
            'comparison_requested': True, 'compare_period': compare}}])
        return task.slots

    def test_merged_years_are_split_in_mention_order(self):
        slots = self.slots('2025년 일본 수출액을 기준으로 2023년과 2024년 을 비교해서 분석하고 싶어.',
                           {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertEqual(slots.compare_period.model_dump(mode='json'), {'start': '2023-01-01', 'end': '2023-12-01'})
        self.assertEqual([p.model_dump(mode='json') for p in slots.extra_compare_periods],
                         [{'start': '2024-01-01', 'end': '2024-12-01'}])

    def test_two_digit_years_are_split(self):
        slots = self.slots('25년을 기준으로 23년 그리고 24년 총 3개년을 월별로 나란히 비교해보자.',
                           {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertEqual((slots.compare_period.start.year, [p.start.year for p in slots.extra_compare_periods]), (2023, [2024]))
        slots = self.slots('2025년에서 2024년과 2023년을 비교해줘', {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertEqual((slots.compare_period.start.year, [p.start.year for p in slots.extra_compare_periods]), (2024, [2023]))

    def test_listed_years_with_gap_keep_only_named_years(self):
        slots = self.slots('2025년 일본 수출을 2021년과 2024년 각각과 비교해 줘', {'start': '2021-01-01', 'end': '2024-12-01'})
        self.assertEqual((slots.compare_period.start.year, [p.start.year for p in slots.extra_compare_periods]), (2021, [2024]))
        slots = self.slots('2025년 일본 수출을 2021년과 2024년 사이 합계와 비교', {'start': '2021-01-01', 'end': '2024-12-01'})
        self.assertIsNone(slots.extra_compare_periods)

    def test_continuous_range_is_kept(self):
        slots = self.slots('2025년 일본 수출을 2023~2024년 합계와 비교해줘', {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertEqual(slots.compare_period.end.year, 2024)
        self.assertIsNone(slots.extra_compare_periods)

    def test_continuous_two_digit_range_is_kept(self):
        slots = self.slots('25년 일본 수출을 23~24년 합계와 비교', {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertIsNone(slots.extra_compare_periods)

    def test_extra_periods_need_first_compare_and_limit(self):
        with self.assertRaises(ValueError):
            PlannerSlots(comparison_requested=True, extra_compare_periods=[Y(2023)])
        with self.assertRaises(ValueError):
            Conditions(period=Y(2025), extra_compare_periods=[Y(2023)])
        slots = PlannerSlots(comparison_requested=True, compare_period=Y(2024),
                             extra_compare_periods=[Y(2020), Y(2021), Y(2022), Y(2023)])
        self.assertTrue(slots.limit_errors())


class ExtraCompareTrendTest(unittest.TestCase):
    def setUp(self):
        engine, self.meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, self.meta)
        connection = engine.connect()
        self.addCleanup(connection.close)
        self.store = Store(Database(engine), connection)

    def test_single_country_product_ranking_names_the_country(self):
        conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-03-01'}, country_codes=['US'], ranking_group='HS6')
        analysis = run_template(self.store, 1, SimpleNamespace(conditions=conditions),
                                SimpleNamespace(args={'template': 'RANKING', 'top': 10, 'amount_only': True}))
        columns = display_columns([{'key': k, 'label': k} for k in analysis.rows[0]], analysis.rows, analysis.meta)
        self.assertEqual([c['label'] for c in columns], ['순위', '국가', 'HS 코드', '품목', '수출액'])
        self.assertEqual(analysis.rows[0]['country_name'], '미국')
        self.assertNotIn('country_code', analysis.rows[0])

    def test_simple_ranking_uses_item_edition(self):
        # 품목을 HS2017로 확정하면 그 판본 행만 읽는다. 판본을 HS2022로 고정하면 결과가 빈다.
        hs = self.meta.tables['hs_code']
        row = dict(self.store.c.execute(hs.select().where(hs.c.hs_code == '330499')).mappings().first())
        if hs.c.depth.computed is not None:
            row.pop('depth')
        self.store.c.execute(hs.insert().values({**row, 'hs_ver': 'HS2017'}))
        self.store.c.execute(self.meta.tables['export_summary'].insert().values(
            trade_month=date(2025, 1, 1), country_code='US', hs6_code='330499', hs_ver='HS2017', amount_usd=7))
        conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-03-01'}, ranking_group='COUNTRY',
                                hs_items=[{'hs_code': '330499', 'hs_ver': 'HS2017'}])
        analysis = run_template(self.store, 1, SimpleNamespace(conditions=conditions),
                                SimpleNamespace(args={'template': 'RANKING', 'top': 10, 'amount_only': True}))
        self.assertEqual([(r['country_code'], r['export_amount_usd']) for r in analysis.rows], [('US', 7)])

    def test_trend_puts_each_compare_period_side_by_side(self):
        conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-03-01'}, country_codes=['US'],
                                compare_period={'start': '2024-01-01', 'end': '2024-03-01'},
                                extra_compare_periods=[{'start': '2024-04-01', 'end': '2024-06-01'}])
        analysis = run_template(self.store, 1, SimpleNamespace(conditions=conditions), SimpleNamespace(args={'template': 'TREND'}))
        first = analysis.rows[0]
        # 픽스처의 미국 월 금액은 2024-01부터 1,000,000 + 10,000 × 경과 월이다.
        self.assertEqual((first['month'], first['amount_usd'], first['compare_amount_usd'],
                          first['compare2_month'], first['compare2_amount_usd']),
                         ('2025-01', 1_120_000, 1_000_000, '2024-04', 1_030_000))
        self.assertEqual((first['total_usd'], first['compare_total_usd'], first['compare2_total_usd']),
                         (3_390_000, 3_030_000, 3_120_000))
        self.assertAlmostEqual(float(first['change2_pct']), 270_000 / 3_120_000 * 100, places=4)
        digest = analysis.result.digest
        self.assertEqual(digest['extra_compare_periods'], [{'start': '2024-04-01', 'end': '2024-06-30'}])
        self.assertNotIn('extra_compare_periods', analysis.conditions)  # 기존 API 조건 형식은 그대로다.

        facts = [f for f in build_facts([digest], NameCatalog([digest])) if f.path[-1].startswith('compare2_')]
        self.assertEqual({f.period for f in facts if f.path[-1] == 'compare2_total_usd'}, {('2024-04', '2024-06')})
        self.assertEqual(sorted((f.month, f.value) for f in facts if f.path[-1] == 'compare2_amount_usd'),
                         [('2024-04', 1_030_000), ('2024-05', 1_040_000), ('2024-06', 1_050_000)])

        columns = display_columns([{'key': k, 'label': k} for k in analysis.rows[0]], analysis.rows, analysis.meta)
        labels = {c['key']: c['label'] for c in columns}
        self.assertEqual(labels['compare2_amount_usd'], '2024-04~2024-06 금액')
        self.assertNotIn('compare2_total_usd', labels)  # 월별 비교가 있으면 기간 합계 열은 숨긴다(첫 비교 기간과 같다).

        task = SimpleNamespace(task_id='t1', conditions=conditions)
        with patch('backend.app.agents.charts.read_snapshot', return_value={'meta': analysis.meta, 'rows': analysis.rows}):
            result, _, _ = build_chart(None, 1, task, 'r')
        self.assertEqual(result.chart.type, 'line')
        self.assertEqual([(s.name, s.values[0]) for s in result.chart.series],
                         [('2025-01~2025-03', 1_120_000), ('2024-01~2024-03', 1_000_000), ('2024-04~2024-06', 1_030_000)])



class ExtraCompareDisplayTest(unittest.TestCase):
    def test_unequal_extra_period_keeps_its_totals_in_table(self):
        meta = {'period': {'start': '2025-01-01', 'end': '2025-12-31'}, 'compare_period': {'start': '2024-01-01', 'end': '2024-12-31'},
                'extra_compare_periods': [{'start': '2023-01-01', 'end': '2023-06-30'}]}
        row = {'country_name': '일본', 'month': '2025-01', 'amount_usd': 10, 'compare_month': '2024-01', 'compare_amount_usd': 9,
               'compare2_month': None, 'compare2_amount_usd': None, 'total_usd': 120, 'requested_months': 12, 'verified_months': 12,
               'compare_total_usd': 108, 'compare_requested_months': 12, 'change_pct': 11.1,
               'compare2_total_usd': 50, 'compare2_monthly_avg_usd': 8.3, 'compare2_requested_months': 6, 'change2_pct': 140.0}
        keys = [c['key'] for c in display_columns([{'key': k, 'label': k} for k in row], [row], meta)]
        self.assertIn('compare_amount_usd', keys)
        self.assertNotIn('compare_total_usd', keys)
        self.assertTrue({'compare2_total_usd', 'compare2_monthly_avg_usd', 'change2_pct', 'total_usd'} <= set(keys))
        self.assertNotIn('compare2_amount_usd', keys)

    def test_llm_sql_extra_period_columns_are_verified_against_their_period(self):
        from backend.app.agents.verify import inspect_sentence
        digest = {'kind': 'LLM_SQL', 'period': {'start': '2025-01', 'end': '2025-12'}, 'trade_flow': 'EXPORT',
                  'compare_period': {'start': '2023-01', 'end': '2023-12'},
                  'extra_compare_periods': [{'start': '2024-01-01', 'end': '2024-12-31'}],
                  'columns': [{'key': k, 'unit': 'USD'} for k in ('total_usd', 'compare_total_usd', 'compare2_total_usd')],
                  'rows': [{'country_name': '일본', 'total_usd': 28_302_297_296, 'compare_total_usd': 28_997_027_212,
                            'compare2_total_usd': 29_603_178_247}]}
        names = {'JP': '일본'}
        good, _ = inspect_sentence('2024년 일본 수출액은 296억 318만 달러입니다.', [digest], known_names=names, countries=names)
        self.assertTrue(good)
        good, _ = inspect_sentence('2023년 일본 수출액은 296억 318만 달러입니다.', [digest], known_names=names, countries=names)
        self.assertFalse(good)


if __name__ == '__main__':
    unittest.main()
