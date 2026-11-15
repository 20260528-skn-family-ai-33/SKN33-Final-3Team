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

    def test_partner_in_earlier_clause_keeps_llm_flow(self):
        question = '미국이 한국에 부과한 관세 이후 한국 기업의 대미 수출액은 얼마야?'
        [task] = plan(question, [{'intent': 'STATISTICS', 'question': question,
                                  'slots': {'countries': US, 'trade_flow': 'EXPORT'}}])
        self.assertEqual((task.question, task.slots.trade_flow), (question, 'EXPORT'))

    def test_after_clause_and_comma_list(self):
        for question in ('미국이 한국에 관세를 부과한 뒤 대미 수출액은 얼마야?', '미국이 한국에 관세 부과 후 대미 수출액은?'):
            with self.subTest(question=question):   # '뒤·후'로 절이 바뀌고 '대미 수출'은 한국이 주어다
                [task] = plan(question, [{'intent': 'STATISTICS', 'question': question,
                                          'slots': {'countries': US, 'trade_flow': 'EXPORT'}}])
                self.assertEqual(task.slots.trade_flow, 'EXPORT')
        question = '미국이 한국에서 수입, 수출하는 품목을 알려줘'   # 쉼표로 나열한 두 방향은 둘 다 조회한다
        tasks = plan(question, [{'intent': 'RANKING', 'question': question, 'slots': {'countries': US, 'ranking_group': 'HS6'}}])
        self.assertEqual([t.slots.trade_flow for t in tasks], ['EXPORT', 'IMPORT'])
        [task] = plan('미국이 한국에서 후추를 수입하는 규모', [{'intent': 'STATISTICS', 'question': '미국이 한국에서 후추를 수입하는 규모',
                                                          'slots': {'countries': US, 'trade_flow': 'IMPORT'}}])
        self.assertEqual(task.slots.trade_flow, 'EXPORT')   # '후추'의 '후'는 절 경계가 아니다

    def test_noun_after_flow_word_is_not_verb(self):
        question = '미국이 한국에 수출한도를 설정한 뒤 한국산 자동차가 미국으로 얼마나 나갔어?'
        [task] = plan(question, [{'intent': 'STATISTICS', 'question': question, 'slots': {'countries': US, 'trade_flow': 'EXPORT'}}])
        self.assertEqual(task.slots.trade_flow, 'EXPORT')

    def test_passive_and_partner_view_only_keep_planner_flow(self):
        for question, flow in (('미국은 한국에서 수출되는 반도체를 얼마나 받아?', 'EXPORT'),      # 피동형은 상대국이 주체가 아니다
                               ('미국이 한국에서 수입 수출하는 품목 중 수출만 알려줘', 'IMPORT')):  # '수출만'은 미국 기준이다
            with self.subTest(question=question):
                tasks = plan(question, [{'intent': 'STATISTICS', 'question': question,
                                         'slots': {'countries': US, 'trade_flow': flow}}])
                self.assertEqual([(t.question, t.slots.trade_flow) for t in tasks], [(question, flow)])
        [task] = plan('한국의 수출과 수입 중 수출만 알려줘', [{'intent': 'STATISTICS', 'question': '한국의 수출과 수입 중 수출만 알려줘',
                                                        'slots': {'countries': []}}])
        self.assertEqual(task.slots.trade_flow, 'EXPORT')   # 한국 관점 '수출만'은 그대로 한국 기준 수출이다

    def test_spaced_flow_words_split_only_for_partner_shape(self):
        for question, countries, flow, expected in (
                ('미국 기준으로 한국과 수입 수출 중 수출만 알려줘', US, 'IMPORT', ['IMPORT']),     # 허용 모양이 아니다
                ('한국 자동차 수출 수입국 순위 알려줘', [], 'EXPORT', ['EXPORT']),                # '수입국'은 별도 수입이 아니다
                ('미국이 한국에게 대표적으로 수입 수출하는 항목이 뭐야?', US, None, ['EXPORT', 'IMPORT'])):
            with self.subTest(question=question):
                tasks = plan(question, [{'intent': 'RANKING', 'question': question,
                                         'slots': {'countries': countries, 'trade_flow': flow, 'ranking_group': 'COUNTRY'}}])
                self.assertEqual([t.slots.trade_flow for t in tasks], expected)

    def test_partner_both_flows_still_split_after_both_flow_turn(self):
        question = '미국이 한국에게 대표적으로 수입 수출하는 항목이 뭐야?'
        output = PlannerOutput.model_validate({'tasks': [{'intent': 'RANKING', 'question': question,
                                                          'slots': {'countries': US, 'ranking_group': 'HS6'}}]})
        tasks = postprocess(output, question, previous_both_trade_flows=True).tasks
        self.assertEqual([t.slots.trade_flow for t in tasks], ['EXPORT', 'IMPORT'])

    def test_cause_question_is_not_labeled(self):
        # 원인 분석은 질문 문장으로 기사 근거를 고르므로 병기를 붙이지 않는다. 방향은 그대로 한국 기준으로 고친다.
        question = '미국이 한국에서 수입하는 화장품이 왜 늘었어?'
        [task] = plan(question, [{'intent': 'CAUSE', 'question': question, 'slots': {'countries': US, 'trade_flow': 'IMPORT'}}])
        self.assertEqual((task.question, task.slots.trade_flow), (question, 'EXPORT'))

    def test_several_partners_get_country_free_label(self):
        countries = [{'mention': '미국', 'name': '미국'}, {'mention': '중국', 'name': '중국'}]
        question = '미국과 중국이 한국에서 수입하는 품목'
        [task] = plan(question, [{'intent': 'RANKING', 'question': question,
                                  'slots': {'countries': countries, 'ranking_group': 'HS6', 'trade_flow': 'IMPORT'}}])
        self.assertEqual((task.slots.trade_flow, task.question), ('EXPORT', question + ' (한국 기준 수출)'))

    def test_only_allowed_shape_overrides_planner(self):
        cn = [{'mention': '중국', 'name': '중국'}]
        for question, flow, expected in (
                ('중국이 한국에 수출 규제를 하면 한국의 대중 수출은 어떻게 돼?', 'EXPORT', 'EXPORT'),   # '수출 규제'는 동사가 아니다
                ('중국이 한국에서 대중적으로 수입하는 품목', 'IMPORT', 'EXPORT'),                 # '대중적으로'는 부사다
                ('미국이 한국에 수출하는 품목', 'EXPORT', 'IMPORT')):
            with self.subTest(question=question):
                countries = cn if question.startswith('중국') else US
                [task] = plan(question, [{'intent': 'STATISTICS', 'question': question,
                                          'slots': {'countries': countries, 'trade_flow': flow}}])
                self.assertEqual(task.slots.trade_flow, expected)

    def test_sentence_with_another_flow_clause_keeps_planner_flow(self):
        # 허용 모양 뒤에 다른 수출입 절이 있으면 문장 전체가 허용 모양이 아니다. 코드는 고치지 않고 플래너 판단을 둔다.
        for question, flow in (('미국이 한국에서 수입하는 품목과, 한국 수출 1위 품목을 알려줘', 'IMPORT'),
                               ('미국이 한국에 수출하는 것보다 한국이 미국에 수출하는 금액은?', 'EXPORT'),
                               # 앞 문장이 실제 요청이고 허용 모양은 빼라는 거래다
                               ('한국의 대미 수출액만 알려줘. 미국이 한국에 수출하는 금액은 제외해 줘.', 'EXPORT'),
                               ('미국이 한국에 수출하는 금액은 제외하고 알려줘', 'EXPORT')):
            with self.subTest(question=question):
                [task] = plan(question, [{'intent': 'RANKING', 'question': question,
                                          'slots': {'countries': US, 'ranking_group': 'HS6', 'trade_flow': flow}}])
                self.assertEqual((task.question, task.slots.trade_flow), (question, flow))

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

    def test_time_word_alone_keeps_simple_ranking(self):
        for question, simple in (('작년 한국의 수출액 상위 10개국', True), ('전월 수출 상위 품목', True),
                                 ('전년 대비 수출 증가 상위 국가', False), ('작년보다 수출이 늘어난 국가 순위', False)):
            with self.subTest(question=question):
                self.assertEqual(bool(self.ranking_args(question).get('amount_only')), simple)

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

    def test_year_do_range_is_kept(self):
        slots = self.slots('2025년 일본 수출액을 2021년도부터 2024년도까지의 합계와 비교해 줘',
                           {'start': '2021-01-01', 'end': '2024-12-01'})
        self.assertEqual((slots.compare_period.end.year, slots.extra_compare_periods), (2024, None))

    def test_continuous_range_is_kept(self):
        slots = self.slots('2025년 일본 수출을 2023~2024년 합계와 비교해줘', {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertEqual(slots.compare_period.end.year, 2024)
        self.assertIsNone(slots.extra_compare_periods)

    def test_continuous_two_digit_range_is_kept(self):
        slots = self.slots('25년 일본 수출을 23~24년 합계와 비교', {'start': '2023-01-01', 'end': '2024-12-01'})
        self.assertIsNone(slots.extra_compare_periods)

    def test_continuous_month_range_is_kept(self):
        compare = {'start': '2021-01-01', 'end': '2024-12-01'}
        for dates in ('2021년 1월부터 2024년 12월까지의', '2021년 1월~2024년 12월',
                      '2021년 1월에서 2024년 12월까지의'):
            with self.subTest(dates=dates):
                slots = self.slots(f'2025년 일본 수출액을 {dates} 합계와 비교해 줘', compare)
                self.assertEqual(slots.compare_period.model_dump(mode='json'), compare)
                self.assertIsNone(slots.extra_compare_periods)

    def test_month_range_in_base_year_does_not_hide_listed_comparison_years(self):
        slots = self.slots('2025년 1월~12월 일본 수출액을 2021년과 2024년 각각과 비교해 줘',
                           {'start': '2021-01-01', 'end': '2024-12-01'})
        self.assertEqual((slots.compare_period.start.year, [p.start.year for p in slots.extra_compare_periods]), (2021, [2024]))

    def test_extra_periods_need_first_compare_and_limit(self):
        # 플래너가 비교 기간을 모두 extra에 넣어도 첫 기간을 compare_period로 옮겨 받는다(질문 전체를 실패시키지 않는다).
        slots = PlannerSlots(extra_compare_periods=[Y(2023), Y(2024)])
        self.assertEqual((slots.comparison_requested, slots.compare_period.start.year,
                          [p.start.year for p in slots.extra_compare_periods]), (True, 2023, [2024]))
        self.assertIsNone(PlannerSlots(extra_compare_periods=[Y(2023)]).extra_compare_periods)
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

    def test_simple_country_ranking_keeps_requested_zero_trade_country(self):
        self.store.c.execute(self.meta.tables['country'].insert().values(country_code='JP', name_ko='일본', country_role='COUNTRY'))
        for codes, expected in ((['JP', 'US'], [('US', 3_390_000), ('JP', 0)]), (['JP'], [('JP', 0)])):
            with self.subTest(codes=codes):
                conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-03-01'}, country_codes=codes,
                                        ranking_group='COUNTRY')
                analysis = run_template(self.store, 1, SimpleNamespace(conditions=conditions),
                                        SimpleNamespace(args={'template': 'RANKING', 'top': 10, 'amount_only': True}))
                self.assertEqual(analysis.result.status, 'OK')
                self.assertEqual([(r['country_code'], r['export_amount_usd']) for r in analysis.rows], expected)

    def test_simple_ranking_uses_item_edition(self):
        from backend.app.agents.bindings import resolve
        from backend.app.agents.state import ResultBinding
        from backend.app.api.results import result_table
        from backend.tests.test_agent_state import state

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

        conditions.ranking_group = 'HS6'
        consumer = state()
        task = consumer.plan.tasks[0]
        task.question = '상위 1개 품목의 추이'
        task.input_bindings = [ResultBinding(source_task_id='t2', source_step_id='t2.sql',
                                             selection={'dimension': 'HS6', 'limit': 1, 'order_by': 'amount_usd'})]
        analysis = run_template(self.store, consumer.input.user_id, SimpleNamespace(conditions=conditions),
                                SimpleNamespace(args={'template': 'RANKING', 'top': 1, 'amount_only': True}))
        meta = {**analysis.meta, 'completeness': analysis.result.completeness, 'limitations': analysis.result.limitations}
        saved = result_table(self.store, consumer.input.user_id, analysis.rows, meta, analysis.conditions)
        consumer.results['t2.sql'] = analysis.result.model_copy(update={'result_id': saved['result_id']})
        selected = resolve(self.store, consumer, task, Conditions())
        self.assertEqual(selected.hs_items, conditions.hs_items)

    def test_several_targets_with_extra_periods_chart_every_period(self):
        conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-03-01'}, country_codes=['US', 'CN'],
                                compare_period={'start': '2024-01-01', 'end': '2024-03-01'},
                                extra_compare_periods=[{'start': '2024-04-01', 'end': '2024-06-01'}])
        analysis = run_template(self.store, 1, SimpleNamespace(conditions=conditions), SimpleNamespace(args={'template': 'TREND'}))
        task = SimpleNamespace(task_id='t1', conditions=conditions)
        with patch('backend.app.agents.charts.read_snapshot', return_value={'meta': analysis.meta, 'rows': analysis.rows}):
            result, _, _ = build_chart(None, 1, task, 'r')
        self.assertEqual(result.chart.type, 'grouped_bar')
        self.assertEqual([s.name for s in result.chart.series],
                         ['2024-01~2024-03 합계', '2024-04~2024-06 합계', '2025-01~2025-03 합계'])

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

        from backend.app.api.results import result_table
        saved = result_table(self.store, 1, analysis.rows, analysis.meta, analysis.conditions)
        self.assertEqual([c['label'] for c in saved['columns'] if c['key'].endswith('amount_usd')],
                         ['2025-01~2025-03 금액', '2024-01~2024-03 금액', '2024-04~2024-06 금액'])

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
               'monthly_avg_usd': 10, 'compare_total_usd': 108, 'compare_monthly_avg_usd': 9, 'compare_requested_months': 12,
               'change_pct': 11.1,
               'compare2_total_usd': 50, 'compare2_monthly_avg_usd': 8.3, 'compare2_requested_months': 6, 'change2_pct': 140.0}
        keys = [c['key'] for c in display_columns([{'key': k, 'label': k} for k in row], [row], meta)]
        self.assertIn('compare_amount_usd', keys)
        # 한 기간이라도 길이가 다르면 차트는 모든 기간을 월평균으로 그리므로, 길이가 같은 첫 비교 기간의 분모도 보인다.
        self.assertTrue({'compare_total_usd', 'compare_monthly_avg_usd', 'compare_requested_months',
                         'compare2_total_usd', 'compare2_monthly_avg_usd', 'compare2_requested_months',
                         'change2_pct', 'total_usd', 'monthly_avg_usd'} <= set(keys))
        self.assertNotIn('compare2_amount_usd', keys)

    def test_llm_sql_compare_aliases_do_not_prove_a_period(self):
        """생성 SQL은 compare2_total_usd 별칭으로 2025년 금액을 낼 수도 있다. 별칭만으로 2024년 값이라고 검증하지 않는다.
        compose는 확정 조건의 비교 기간을 소스에 덧붙이므로(setdefault) 그 상태로도 확인한다."""
        from backend.app.agents.verify import inspect_sentence
        digest = {'kind': 'LLM_SQL', 'period': {'start': '2025-01', 'end': '2025-12'}, 'trade_flow': 'EXPORT',
                  'compare_period': {'start': '2023-01', 'end': '2023-12'},
                  'extra_compare_periods': [{'start': '2024-01-01', 'end': '2024-12-31'}],
                  'columns': [{'key': k, 'unit': 'USD'} for k in ('total_usd', 'compare_total_usd', 'compare2_total_usd')],
                  'rows': [{'country_name': '일본', 'total_usd': 1200, 'compare_total_usd': 800, 'compare2_total_usd': 1200}]}
        names = {'JP': '일본'}
        for sentence in ('2024년 일본 수출액은 1,200달러입니다.', '2023년 일본 수출액은 800달러입니다.'):
            with self.subTest(sentence=sentence):
                good, _ = inspect_sentence(sentence, [digest], known_names=names, countries=names)
                self.assertFalse(good)
        good, _ = inspect_sentence('2025년 일본 수출액은 1,200달러입니다.', [digest], known_names=names, countries=names)
        self.assertFalse(good)  # 비교 기간이 있으면 SQL이 여러 기간을 읽을 수 있어 조회 기간 열의 기간도 모른다.
        single = {k: v for k, v in digest.items() if k not in ('compare_period', 'extra_compare_periods')}
        good, _ = inspect_sentence('2025년 일본 수출액은 1,200달러입니다.', [single], known_names=names, countries=names)
        self.assertFalse(good)  # 조회 기간 전체를 집계했다는 증명(period_verified)이 없으면 기간을 붙이지 않는다.
        good, _ = inspect_sentence('2025년 일본 수출액은 1,200달러입니다.', [{**single, 'period_verified': True}],
                                   known_names=names, countries=names)
        self.assertTrue(good)
        for sentence in ('조회 기간 일본 수출 총액은 1,200달러입니다.', '올해 일본 수출액은 1,200달러입니다.',
                         '이번 분기 일본 수출 총액은 1,200달러입니다.', '일본 수출 총액은 1,200달러입니다.'):
            with self.subTest(sentence=sentence):  # 기간을 증명하지 못한 수치는 어떤 기간 표현(생략 포함)도 뒷받침하지 않는다.
                good, _ = inspect_sentence(sentence, [digest], known_names=names, countries=names)
                self.assertFalse(good)
        labeled = {**single, 'period_verified': True, 'rows': [{'country_name': '일본', 'month': '2024-01', 'total_usd': 1200}]}
        good, _ = inspect_sentence('2024년 1월 일본 수출액은 1,200달러입니다.', [labeled], known_names=names, countries=names)
        self.assertFalse(good)  # SQL이 붙인 월 표시('2024-01' AS month)는 기간 근거가 아니다.
        monthly = {**digest, 'columns': [{'key': 'compare_amount_usd', 'unit': 'USD'}],
                   'rows': [{'country_name': '일본', 'month': '2025-01', 'compare_month': '2024-01', 'compare_amount_usd': 500}]}
        good, _ = inspect_sentence('2024년 1월 일본 수출액은 500달러입니다.', [monthly], known_names=names, countries=names)
        self.assertFalse(good)  # SQL이 낸 compare_month 값도 기간 근거로 쓰지 않는다.

    def test_llm_sql_period_totals_get_grouped_bar_chart(self):
        meta = {'kind': 'LLM_SQL', 'summary': '일본·미국 연도별 수출 합계'}
        columns = [{'key': 'country_name', 'label': '국가', 'type': 'string', 'unit': None},
                   *({'key': k, 'label': label, 'type': 'number', 'unit': 'USD'}
                     for k, label in (('total_usd', '2025년 합계'), ('compare_total_usd', '2023년 합계'),
                                      ('compare2_total_usd', '2024년 합계')))]
        rows = [{'country_name': '일본', 'total_usd': 3, 'compare_total_usd': 1, 'compare2_total_usd': 2},
                {'country_name': '미국', 'total_usd': 6, 'compare_total_usd': 4, 'compare2_total_usd': 5}]
        task = SimpleNamespace(task_id='t1', conditions=Conditions(period=Y(2025)))
        with patch('backend.app.agents.charts.read_snapshot', return_value={'meta': meta, 'rows': rows, 'columns': columns}):
            result, _, _ = build_chart(None, 1, task, 'r')
        self.assertEqual(result.chart.type, 'grouped_bar')
        self.assertEqual([(s.name, s.values) for s in result.chart.series],
                         [('2025년 합계', [3, 6]), ('2023년 합계', [1, 4]), ('2024년 합계', [2, 5])])


class PeriodCoveredTest(unittest.TestCase):
    """생성 SQL 수치에 조회 기간을 붙이는 증명(sql.period_covered)은 보수적이어야 한다."""
    def test_only_full_period_unfiltered_aggregates_are_proven(self):
        from backend.app.agents.sql import period_covered
        c = Conditions(period=Y(2025), country_codes=['JP'])
        base = ("FROM export_summary e WHERE e.country_code='JP' AND e.hs_ver='HS2022' "
                "AND e.trade_month BETWEEN '2025-01-01' AND '2025-12-01'")
        proven = {'total': f"SELECT SUM(e.amount_usd) AS v {base}",
                  'by_product_top5': f"SELECT e.hs6_code, SUM(e.amount_usd) AS v {base} GROUP BY e.hs6_code ORDER BY v DESC LIMIT 5"}
        unproven = {
            'part_of_period': base.replace("'2025-12-01'", "'2025-01-01'"),
            'month_function': f"{base} AND MONTH(e.trade_month)=1",
            'grouped_by_month': f"{base} GROUP BY e.trade_month",
            'case_on_month': None,
            'cte_limit_before_sum': None,
            'amount_filter': f"{base} AND e.amount_usd > 100",
            'cte_having_before_sum': None,
            'not_equal_month': f"{base} AND e.trade_month <> '2025-03-01'"}
        unproven = {k: (f"SELECT SUM(e.amount_usd) AS v {v}" if v else v) for k, v in unproven.items()}
        unproven['grouped_by_month'] = f"SELECT e.trade_month AS m, SUM(e.amount_usd) AS v {base} GROUP BY e.trade_month"
        unproven['case_on_month'] = f"SELECT SUM(CASE WHEN e.trade_month='2025-01-01' THEN e.amount_usd END) AS v {base}"
        unproven['cte_limit_before_sum'] = (f"WITH t AS (SELECT e.amount_usd {base} ORDER BY e.amount_usd DESC LIMIT 1) "
                                            "SELECT SUM(amount_usd) AS v FROM t")
        unproven['cte_having_before_sum'] = (f"WITH t AS (SELECT e.hs6_code, SUM(e.amount_usd) AS a {base} GROUP BY e.hs6_code "
                                             "HAVING SUM(e.amount_usd) > 9) SELECT SUM(a) AS v FROM t")
        for name, sql in proven.items():
            with self.subTest(name=name):
                self.assertTrue(period_covered(sql, c))
        for name, sql in unproven.items():
            with self.subTest(name=name):
                self.assertFalse(period_covered(sql, c))
        self.assertFalse(period_covered(proven['total'], Conditions(period=Y(2025), compare_period=Y(2024))))
        # 허용 모양만 받는다: OR로 국가마다 다른 기간, 수출입 표가 아닌 표, 행 단위 금액, 윈도 함수, 키가 빠진 조인은 실패
        tail = "e.hs_ver='HS2022' AND e.trade_month BETWEEN '2025-01-01' AND '2025-12-01'"
        for name, sql in {
            'or_per_country': ("SELECT e.country_code AS c, SUM(e.amount_usd) AS v FROM export_summary e WHERE e.hs_ver='HS2022' "
                               "AND ((e.country_code='JP' AND e.trade_month='2025-01-01') OR "
                               "(e.country_code='US' AND e.trade_month BETWEEN '2025-02-01' AND '2025-12-01')) GROUP BY e.country_code"),
            'other_table': "SELECT SUM(u.korea_import_usd) AS v FROM un_data u",
            'row_level': f"SELECT e.hs6_code, e.amount_usd AS v FROM export_summary e WHERE {tail}",
            'window': f"SELECT e.hs6_code, SUM(e.amount_usd) OVER (PARTITION BY e.hs6_code) AS v FROM export_summary e WHERE {tail}",
            'join_without_edition': (f"SELECT h.name_ko, SUM(e.amount_usd) AS v FROM export_summary e JOIN hs_code h "
                                     f"ON h.hs_code=e.hs6_code WHERE {tail} GROUP BY h.name_ko"),
            'select_subquery': (f"SELECT (SELECT MAX(u.korea_import_usd) FROM un_data u) AS v, SUM(e.amount_usd) AS t "
                                f"FROM export_summary e WHERE {tail}"),
            'case_in_sum': f"SELECT SUM(CASE WHEN e.amount_usd > 100 THEN e.amount_usd END) AS v FROM export_summary e WHERE {tail}",
            'max_is_one_month': f"SELECT MAX(e.amount_usd) AS v FROM export_summary e WHERE {tail}",
            'sum_distinct': f"SELECT SUM(DISTINCT e.amount_usd) AS v FROM export_summary e WHERE {tail}",
            'group_by_amount': (f"SELECT e.amount_usd > 100 AS big, SUM(e.amount_usd) AS v FROM export_summary e WHERE {tail} "
                                "GROUP BY e.amount_usd > 100"),
            'least_caps_value': f"SELECT LEAST(SUM(e.amount_usd), 1200) AS v FROM export_summary e WHERE {tail}",
            'constant_value': f"SELECT SUM(e.amount_usd)*0+1200 AS v FROM export_summary e WHERE {tail}",
            'coalesce_constant': f"SELECT COALESCE(SUM(e.amount_usd), 900) AS v FROM export_summary e WHERE {tail}",
            'depth_filter_drops_rows': (f"SELECT SUM(e.amount_usd) AS v FROM export_summary e JOIN hs_code h ON h.hs_code=e.hs6_code "
                                        f"AND h.hs_ver=e.hs_ver WHERE h.depth = 4 AND {tail}")}.items():
            with self.subTest(name=name):
                self.assertFalse(period_covered(sql, c))
        joined = (f"SELECT cn.name_ko, SUM(e.amount_usd) AS v FROM export_summary e INNER JOIN country cn "
                  f"ON cn.country_code=e.country_code WHERE cn.country_role='COUNTRY' AND {tail} GROUP BY cn.name_ko")
        self.assertTrue(period_covered(joined, c))
        ratio = (f"SELECT e.hs6_code, ROUND(SUM(e.amount_usd) / NULLIF(SUM(e.weight_kg), 0), 1) AS v "
                 f"FROM export_summary e WHERE {tail} GROUP BY e.hs6_code")
        self.assertTrue(period_covered(ratio, c))   # 합계끼리의 계산(단가)은 기간 전체 값이다.


class ComparePeriodDedupeTest(unittest.TestCase):
    def setUp(self):
        from backend.tests.test_agent_state import state
        from backend.app.api.config import Settings
        engine, meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        db = Database(engine)
        db.settings = Settings({'API_HS_VECTOR_ENABLED': 'false'})
        connection = engine.connect()
        self.addCleanup(connection.close)
        self.store = Store(db, connection)
        self.state = state()
        self.task = self.state.plan.tasks[0]

    def test_compare_period_equal_to_base_is_dropped(self):
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import TaskSlots
        self.task.intent = 'STATISTICS'
        self.task.slots = TaskSlots(period=Y(2025), comparison_requested=True, compare_period=Y(2025),
                                    extra_compare_periods=[Y(2024)])
        check_conditions(self.store, self.state, self.task.steps[0])
        self.assertEqual(self.task.conditions.compare_period.start.year, 2024)
        self.assertEqual(self.task.conditions.extra_compare_periods, [])

    def test_compare_equal_to_base_alone_adds_no_prior_year(self):
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import TaskSlots
        self.task.intent = 'STATISTICS'
        self.task.slots = TaskSlots(period=Y(2025), comparison_requested=True, compare_period=Y(2025))
        check_conditions(self.store, self.state, self.task.steps[0])
        self.assertIsNone(self.task.conditions.compare_period)

    def test_extra_periods_dropped_outside_statistics_are_announced(self):
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import TaskSlots
        self.task.intent = 'RANKING'
        self.task.slots = TaskSlots(period=Y(2025), comparison_requested=True, compare_period=Y(2023),
                                    extra_compare_periods=[Y(2024)], ranking_group='COUNTRY')
        check_conditions(self.store, self.state, self.task.steps[0])
        self.assertEqual((self.task.conditions.compare_period.start.year, self.task.conditions.extra_compare_periods), (2023, []))
        # 순위는 지정 비교 기간을 쓰지 않으므로 "첫 기간만 비교했다"가 아니라 비교하지 않았다고 안내한다.
        self.assertIn('순위는 지정한 비교 기간으로 비교하지 않습니다.', self.task.applied_defaults)
        self.assertFalse(any('첫 비교 기간만' in note for note in self.task.applied_defaults))

    def test_cause_uses_extra_period_left_after_dedupe_without_notice(self):
        # 기준 기간과 같은 첫 비교 기간(2025년)을 뺀 뒤 남은 2024년 하나만 비교한다. 버린 기간이 없으니 안내도 없다.
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import TaskSlots
        self.task.intent = 'CAUSE'
        self.task.slots = TaskSlots(period=Y(2025), comparison_requested=True, compare_period=Y(2025),
                                    extra_compare_periods=[Y(2024)])
        check_conditions(self.store, self.state, self.task.steps[0])
        self.assertEqual((self.task.conditions.compare_period.start.year, self.task.conditions.extra_compare_periods), (2024, []))
        self.assertFalse(any('첫 비교 기간만' in note or '순위는 지정한' in note for note in self.task.applied_defaults))

    def test_generated_sql_ranking_gets_no_ranking_notice(self):
        # 생성 SQL 순위는 확정 비교 기간을 SQL 조건으로 쓰므로 "비교하지 않습니다"라고 안내하지 않는다.
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import TaskSlots
        self.task.intent = 'RANKING'
        self.task.custom_query = '2025년 국가별 수출액과 2023년 대비 증감률 순위'
        self.task.slots = TaskSlots(period=Y(2025), comparison_requested=True, compare_period=Y(2023),
                                    ranking_group='COUNTRY')
        check_conditions(self.store, self.state, self.task.steps[0])
        self.assertEqual(self.task.conditions.compare_period.start.year, 2023)
        self.assertNotIn('순위는 지정한 비교 기간으로 비교하지 않습니다.', self.task.applied_defaults)

    def test_share_comparison_conditions_follow_the_template(self):
        from backend.app.agents.conditions import check_conditions

        for group, template in ((None, 'SHARE'), ('HS6', 'LLM_SQL')):
            with self.subTest(group=group):
                self.state.plan, _ = build_plan(PlannerOutput.model_validate({'tasks': [{
                    'intent': 'MARKET_SHARE', 'question': '2025년 교역 비중을 2023년과 2024년과 비교', 'slots': {
                        'period': Y(2025), 'comparison_requested': True, 'compare_period': Y(2023),
                        'extra_compare_periods': [Y(2024)], 'share_basis': 'KOREA_TRADE_PARTNER', 'ranking_group': group}}]}))
                [task] = self.state.plan.tasks
                check_conditions(self.store, self.state, task.steps[0])
                sql = next(step for step in task.steps if step.tool == 'query_trade_data')
                self.assertEqual(sql.args['template'], template)
                self.assertEqual(task.conditions.extra_compare_periods, [])
                if group is None:
                    self.assertIsNone(task.conditions.compare_period)
                    self.assertIn('국가별 교역 비중은 조회 기간 기준으로만 계산했습니다.', task.applied_defaults)
                    self.assertFalse(any('첫 비교 기간만' in note for note in task.applied_defaults))
                    analysis = run_template(self.store, self.state.input.user_id, task, sql)
                    self.assertEqual(analysis.conditions['compare'], 'NONE')
                else:
                    self.assertEqual(task.conditions.compare_period.start.year, 2023)

    def test_planner_split_skips_base_year(self):
        question = '2025년을 기준으로 2025년과 2024년 수출액을 비교'
        [task] = plan(question, [{'intent': 'STATISTICS', 'question': question, 'slots': {
            'period': Y(2025), 'comparison_requested': True, 'compare_period': {'start': '2024-01-01', 'end': '2025-12-01'}}}])
        self.assertEqual((task.slots.compare_period.start.year, task.slots.extra_compare_periods), (2024, []))


class UnverifiedLlmSqlOutputTest(unittest.TestCase):
    """기간을 증명하지 못한 생성 SQL 수치는 대체 결론·리포트에서도 기간 미확인으로 다룬다."""
    TABLE = {'result_id': 'r', 'kind': 'LLM_SQL', 'rows': [{'v': 1200}], 'total_rows': 1,
             'columns': [{'key': 'v', 'label': '2024년 수출 합계', 'type': 'number', 'unit': 'USD'}]}

    def test_fallback_conclusion_does_not_quote_unverified_number(self):
        from backend.app.agents.compose import _trade_fact
        digest = {'kind': 'LLM_SQL', 'trade_flow': 'EXPORT', 'rows': [{'v': 1200}],
                  'columns': [{'key': 'v', 'label': '2024년 수출 합계', 'unit': 'USD'}]}
        self.assertIsNone(_trade_fact(digest))
        self.assertIn('1,200', _trade_fact({**digest, 'period_verified': True}))

    def test_report_keeps_period_limitation(self):
        from datetime import date
        from backend.app.api.common import LLM_SQL_PERIOD_WARNING
        from backend.app.api.reports import build_document
        from backend.app.services.report_ondemand_context import populate_results
        for verified in (False, True):
            with self.subTest(verified=verified):
                table = {**self.TABLE, 'meta': {'kind': 'LLM_SQL', 'period_verified': verified}}
                content = {'source_tables': [table], 'source_results': [{'result_id': 'r', 'limitations': [], 'conditions': {}}]}
                record = {'report_type': 'RPT_ON_DEMAND', 'content_json': content, 'conditions_json': {
                    'period': {'start': '2025-01-01', 'end': '2025-12-31'}, 'sections': ['KPI'], 'hs_items': [], 'country_codes': []}}
                warnings = build_document(SimpleNamespace(queries=[]), 1, record)['meta']['warnings']
                self.assertEqual(LLM_SQL_PERIOD_WARNING in warnings, not verified)   # 웹·DOCX 리포트
                ctx = {'conditions': {}, 'meta': {}}
                populate_results({'sections': ['KPI']}, ctx, date(2025, 1, 1), date(2025, 12, 1), content)   # PDF 리포트
                self.assertEqual('집계 기간 미확인' in ctx['results'][0]['note'], not verified)
                self.assertEqual(any(c['content'] == LLM_SQL_PERIOD_WARNING for c in ctx['summary']['check_points']),
                                 not verified)


if __name__ == '__main__':
    unittest.main()
