import unittest
from backend.app.agents.planner import build_plan, postprocess
from backend.app.agents.state import PlannerOutput
from backend.app.api.common import ApiError


class AgentPlannerTest(unittest.TestCase):
    def test_article_company_stays_in_question_but_not_hs_products(self):
        question = '글로벌 메모리 반도체 수요 증가에…中 YMTC 역대 최대 규모 증설 관련 시장 영향을 알려줘'
        output = PlannerOutput(tasks=[{'intent':'CAUSE','question':question,
            'slots':{'products':['글로벌 메모리 반도체','YMTC']}}])
        task, = postprocess(output,question).tasks
        self.assertEqual(task.slots.products,['글로벌 메모리 반도체'])
        self.assertEqual(task.question,question)
        output = PlannerOutput(tasks=[{'intent':'LOCAL_ISSUES','question':'TSMC NAND 관련 기사',
            'slots':{'products':['TSMC','NAND','DRAM','OLED']}}])
        task, = postprocess(output,output.tasks[0].question).tasks
        self.assertEqual(task.slots.products,['NAND','DRAM','OLED'])
        output = PlannerOutput(tasks=[{'intent':'STATISTICS','question':'YMTC 수출액',
            'slots':{'products':['YMTC']}}])
        task, = postprocess(output,'YMTC 수출액').tasks
        self.assertEqual(task.slots.products,['YMTC'])  # 품목 없는 전체 통계로 바꾸지 않는다.

    def test_vague_request_cannot_repeat_previous_analysis(self):
        for question in ('아무거나', '그냥 아무거나 해줘'):
            output = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question,
                'slots': {'products': ['맥주']}}])
            task, = postprocess(output, question).tasks
            self.assertEqual(task.intent, 'OUT_OF_SCOPE')
            self.assertIsNone(task.slots.products)

    def test_country_followup_preserves_both_directions_even_if_model_echoes_export(self):
        for question, expected in [('미국은?', ['EXPORT', 'IMPORT']), ('미국 수입은?', ['IMPORT'])]:
            output = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question,
                'slots': {'countries': [{'mention': '미국', 'name': '미국'}], 'trade_flow': 'EXPORT'}}])
            result = postprocess(output, question, previous_both_trade_flows=True)
            self.assertEqual([task.slots.trade_flow for task in result.tasks], expected)

    def test_article_product_slot_uses_hs6_but_not_an_amount(self):
        for question, products, expected in (
            ('330499 관련 기사 찾아줘', [], ['330499']),
            ('HS6 코드 330499 관련 기사 찾아줘', ['기초화장품'], ['330499']),
            ('330499달러 관련 기사 찾아줘', None, None),
            ('기초화장품 관련 기사 찾아줘', ['기초화장품'], ['기초화장품']),
        ):
            with self.subTest(question=question):
                output = PlannerOutput(tasks=[{'intent': 'LOCAL_ISSUES', 'question': question,
                    'slots': {'products': products}}])
                task, = postprocess(output, question).tasks
                self.assertEqual(task.slots.products, expected)

    def test_hs_code_followup_switches_product_but_explicit_lookup_stays_lookup(self):
        from backend.app.agents.state import Conditions

        context = Conditions(country_codes=['VN'], trade_flow='IMPORT')
        for question, expected in [('HS6 단위 코드 330499는?', 'STATISTICS'),
                                   ('330499는?', 'STATISTICS'),
                                   ('HS6 단위 코드 330499는 무슨 품목이야?', 'HS_LOOKUP')]:
            with self.subTest(question=question):
                output = PlannerOutput(tasks=[{'intent': 'HS_LOOKUP', 'question': question,
                    'slots': {'products': ['기초화장품']}}])
                task, = postprocess(output, question, previous_conditions=context).tasks
                self.assertEqual(task.intent, expected)
                self.assertEqual(task.slots.products, ['330499'])
                self.assertIsNone(task.slots.countries)
                self.assertIsNone(task.slots.trade_flow)

    def test_same_scope_trend_and_cause_use_shared_query_contract(self):
        question = '2025년 중국 화장품 수출 추이와 변화 원인 알려줘'
        slots = {'products': ['화장품'], 'trade_flow': 'EXPORT'}
        output = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question, 'slots': slots},
            {'intent': 'CAUSE', 'question': question, 'slots': slots, 'source_tasks': [1]}])
        plan, _ = build_plan(postprocess(output, question))
        self.assertFalse(plan.tasks[1].input_bindings)
        queries = [step.args for step in plan.steps() if step.tool == 'query_trade_data']
        self.assertEqual(queries, [{'template': 'TREND'}] * 2)

    def test_previous_result_report_marks_table_reuse_but_not_condition_reuse(self):
        from unittest.mock import Mock
        from backend.app.agents.conditions import check_conditions
        from backend.tests.test_agent_state import state

        for question, reuse in [('방금 그 표로 리포트 만들어줘', True),
                                ('방금 그 조건으로 리포트 만들어줘', False)]:
            output = PlannerOutput(tasks=[{'intent': 'REPORT', 'question': question}])
            plan, _ = build_plan(postprocess(output, question))
            task, = plan.tasks
            self.assertEqual(task.previous_turn_result, reuse)
            self.assertEqual(task.input_bindings, [])
            self.assertEqual(type(plan).model_validate_json(plan.model_dump_json()), plan)
            if reuse:
                current = state()
                current.plan = plan
                store, preview = Mock(), Mock()
                check_conditions(store, current, task.steps[0], confirm_report=preview)
                self.assertEqual(task.steps[0].status, 'FAILED')
                self.assertIn('이전 대화의 결과표', current.results[task.steps[0].step_id].error)
                self.assertEqual(store.mock_calls, [])
                preview.assert_not_called()

    def test_ambiguous_previous_report_asks_table_or_requery(self):
        from unittest.mock import Mock
        from backend.app.agents.clarification import apply_answer
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.state import Conditions
        from backend.tests.test_agent_state import state

        question = '이걸로 리포트 만들어줘'
        output = PlannerOutput(tasks=[{'intent': 'REPORT', 'question': question, 'previous_turn_result': True}])
        plan, _ = build_plan(postprocess(output, question))
        task, = plan.tasks
        self.assertFalse(task.previous_turn_result)
        current = state()
        current.input.question = question
        current.input.context.conditions = Conditions(country_codes=['US'])
        current.plan = plan
        check_conditions(Mock(), current, task.steps[0], confirm_report=Mock())
        self.assertEqual(current.pending.kind, 'REPORT_SOURCE')
        self.assertEqual(current.pending.message, '직전 결과표로 리포트를 만들까요, 아니면 같은 조건으로 다시 조회할까요?')
        apply_answer(current, current.pending.choices[0].patch)
        check_conditions(Mock(), current, task.steps[0], confirm_report=Mock())
        self.assertTrue(task.previous_turn_result)
        self.assertIn('이전 대화의 결과표', current.results[task.steps[0].step_id].error)

    def test_report_links_the_only_preceding_result(self):
        for question, intent, step in [
            ('베트남 화장품 수출 추이 보여주고 이 결과로 보고서 작성해줘', 'STATISTICS', 'sql'),
            ('일본 맥주 수출 보여주고 그걸 리포트로 저장해줘', 'STATISTICS', 'sql'),
            ('영국 위스키 수출 추이를 조회해서 결과를 리포트로 남겨줘', 'STATISTICS', 'sql'),
            ('라면 유망 수출국을 예측한 다음 그 예측 결과로 리포트 만들어 줘', 'PROMISING_MARKET', 'ml'),
        ]:
            with self.subTest(question=question):
                slots = {'products': ['품목'], 'trade_flow': 'EXPORT'}
                output = PlannerOutput(tasks=[{'intent': intent, 'question': '분석', 'slots': slots},
                                              {'intent': 'REPORT', 'question': '리포트', 'slots': slots}])
                plan, _ = build_plan(postprocess(output, question))
                self.assertEqual(plan.tasks[1].steps[0].depends_on, [f't1.{step}'])
                self.assertIsNone(plan.tasks[1].slots.products)
                self.assertEqual(plan.tasks[0].slots.products, ['품목'])
                independent = postprocess(output, '이전 조건으로 리포트 만들어줘')
                self.assertEqual(independent.tasks[-1].source_tasks, [])

    def test_report_does_not_guess_between_multiple_results(self):
        output = PlannerOutput(tasks=[{'intent': intent, 'question': intent}
                                      for intent in ('STATISTICS', 'RANKING', 'REPORT')])
        with self.assertRaisesRegex(ValueError, '선행 분석 결과'):
            postprocess(output, '수출 추이와 순위 보여주고 이 결과로 리포트 만들어줘')

    def test_standalone_report_keeps_the_requested_period_and_scope(self):
        for question, flow, months, compare in [
            ('태국 의류 최근 3개월 수출을 리포트로 정리해줘', 'EXPORT', 3, None),
            ('인도네시아 화장품 수출 리포트를 전년 동기 대비로 만들어줘', 'EXPORT', None, True),
            ('베트남 의류 최근 3개월 수입 리포트 PDF로 만들어줘', 'IMPORT', 3, None),
        ]:
            with self.subTest(question=question):
                slots = {'trade_flow': flow, 'recent_months': months}
                output = PlannerOutput(tasks=[{'intent': intent, 'question': intent, 'slots': slots}
                                              for intent in ('STATISTICS', 'REPORT')])
                report, = postprocess(output, question).tasks
                self.assertEqual((report.intent, report.slots.trade_flow), ('REPORT', flow))
                self.assertEqual((report.slots.recent_months, report.slots.comparison_requested), (months, compare))
                independent = postprocess(output, '수출 추이 보여주고 같은 조건의 수출 리포트 만들어줘')
                self.assertEqual([task.intent for task in independent.tasks], ['STATISTICS', 'REPORT'])

    def test_recent_year_in_report_replaces_invented_dates(self):
        output = PlannerOutput(tasks=[{'intent': 'REPORT', 'question': '리포트',
                                      'slots': {'period': {'start': '2023-06-01', 'end': '2024-05-01'}}}])
        report, = postprocess(output, '최근 1년 대만 반도체 수출 리포트 PDF로 만들어 주세요').tasks
        self.assertEqual(report.slots.recent_months, 12)
        self.assertIsNone(report.slots.period)
        for question in ('2023년 대만 반도체 수출 리포트', '최근 1년 6개월 대만 반도체 수출 리포트'):
            report, = postprocess(output, question).tasks
            self.assertEqual(report.slots, output.tasks[0].slots)

    def test_followup_report_only_keeps_conditions_explicitly_changed(self):
        output = PlannerOutput(tasks=[{'intent': 'REPORT', 'question': '리포트', 'slots': {
            'countries': [{'mention': '중국', 'name': '중국'}], 'products': ['330499'], 'trade_flow': 'EXPORT',
            'period': {'start': '2025-01-01', 'end': '2025-12-01'}}}])
        for question, country, months, flow in [
            ('이 조건으로 리포트 만들어줘', None, None, None),
            ('같은 품목으로 중국 대상 리포트도 만들어줘', '중국', None, None),
            ('기간을 최근 6개월로 바꿔서 리포트 만들어줘', None, 6, None),
            ('방금 조건 그대로 수입 기준 리포트로 만들어줘', None, None, 'IMPORT'),
            ('같은 품목과 국가로 수입 리포트도 만들어 줘', None, None, 'IMPORT'),
        ]:
            with self.subTest(question=question):
                report, = postprocess(output, question).tasks
                slots = report.slots
                self.assertEqual(slots.countries[0].name if slots.countries else None, country)
                self.assertEqual((slots.recent_months, slots.trade_flow), (months, flow))
                self.assertIsNone(slots.products)
                self.assertIsNone(slots.period)
        report, = postprocess(output, '2025년 전체 기간으로 다시 리포트 뽑아줘').tasks
        self.assertIsNone(report.slots.countries)
        self.assertIsNone(report.slots.products)
        self.assertEqual(report.slots.period, output.tasks[0].slots.period)

    def test_market_top_defaults_to_five_and_accepts_requested_count(self):
        for requested, expected in [(None, 5), (8, 8)]:
            plan, _ = self.plan([{'intent': 'PROMISING_MARKET', 'question': '베트남 유망 품목',
                                  'slots': {'ranking_limit': requested}}])
            step = next(step for step in plan.tasks[0].steps if step.tool == 'query_market_model')
            self.assertEqual(step.args['top'], expected)

    def plan(self, tasks):
        return build_plan(PlannerOutput(tasks=tasks))

    def test_four_tasks_are_rejected_without_truncation(self):
        with self.assertRaises(ApiError) as exc:
            self.plan([{'intent': 'STATISTICS', 'question': str(i)} for i in range(4)])
        self.assertEqual(exc.exception.code, 'QUESTION_TOO_COMPLEX')

    def test_over_limit_slots_only_fail_their_task(self):
        plan, warnings = self.plan([
            {'intent': 'STATISTICS', 'question': 'too many', 'slots': {'products': ['a'] * 6}},
            {'intent': 'RANKING', 'question': 'rank'}])
        self.assertEqual(plan.tasks[0].answer_status, 'NO_DATA')
        self.assertEqual(plan.tasks[0].steps, [])
        self.assertTrue(warnings)
        self.assertEqual(plan.tasks[1].steps[1].args, {'template': 'RANKING', 'top': 10})

    def test_trade_term_out_of_scope_becomes_conversation(self):
        from backend.app.agents.planner import rescue_trade_terms
        out = rescue_trade_terms(PlannerOutput(tasks=[{'intent': 'OUT_OF_SCOPE', 'question': 'hs 코드가 뭐야?'},
                                                      {'intent': 'OUT_OF_SCOPE', 'question': '오늘 날씨 어때?'}]))
        self.assertEqual([t.intent for t in out.tasks], ['CONVERSATION', 'OUT_OF_SCOPE'])
        # 플래너가 하위 질문을 고쳐 써 용어가 빠져도 사용자 원문으로 판단한다
        out = rescue_trade_terms(PlannerOutput(tasks=[{'intent': 'OUT_OF_SCOPE', 'question': '그게 뭔지 설명'}]),
                                 original='hs코드가 뭔데')
        self.assertEqual(out.tasks[0].intent, 'CONVERSATION')
        # 영문 용어는 단어 속 글자("this")로 맞추지 않고, "수입"(소득)은 용어를 물을 때만 살린다
        for question, intent in (('what is this weather', 'OUT_OF_SCOPE'), ('내 수입으로 집 살 수 있을까?', 'OUT_OF_SCOPE'),
                                 ('수입이 무슨 뜻이야?', 'CONVERSATION'), ('FTA가 뭐야?', 'CONVERSATION'),
                                 ('what is HS code', 'CONVERSATION'), ('fish sticks', 'OUT_OF_SCOPE')):
            out = rescue_trade_terms(PlannerOutput(tasks=[{'intent': 'OUT_OF_SCOPE', 'question': question}]), original=question)
            self.assertEqual(out.tasks[0].intent, intent, question)
        # 설정 용어에 띄어쓰기가 있어도 원문 띄어쓰기와 상관없이 맞춘다
        for question in ('What is free trade?', 'freetrade 뜻'):
            out = rescue_trade_terms(PlannerOutput(tasks=[{'intent': 'OUT_OF_SCOPE', 'question': question}]),
                                     terms=('free trade',), original=question)
            self.assertEqual(out.tasks[0].intent, 'CONVERSATION', question)

    def test_shared_suffix_product_is_kept_as_one_phrase(self):
        # D-107: 플래너가 "면도 및 탈취 관련 상품"을 ["면도", "탈취 관련 상품"]으로 나누면 원문 표현으로 되돌린다
        from backend.app.agents.planner import merge_product_phrases, merge_shared_suffix
        question = '미국 면도 및 탈취 관련 상품의 수출 추이와 증가 이유를 알려줘'
        out = merge_product_phrases(PlannerOutput(tasks=[
            {'intent': 'STATISTICS', 'question': 'q', 'slots': {'products': ['면도', '탈취 관련 상품']}},
            {'intent': 'CAUSE', 'question': 'q', 'slots': {'products': None}}]), question)
        self.assertEqual([t.slots.products for t in out.tasks], [['면도 및 탈취 관련 상품'], None])
        self.assertEqual(merge_shared_suffix(['의료 관련 제품', '미용 관련 제품'], '의료 및 미용 관련 제품 수출'),
                         ['의료 및 미용 관련 제품'])
        self.assertEqual(merge_shared_suffix(['반도체', '면도', '탈취 관련 상품'], '반도체와 면도 및 탈취 관련 상품'),
                         ['반도체', '면도 및 탈취 관련 상품'])
        # 공통 꼬리말이 없거나 원문에 이어 쓰이지 않았으면 나눈 그대로 둔다
        self.assertEqual(merge_shared_suffix(['화장품', '세제'], '화장품 및 세제 수출'), ['화장품', '세제'])
        self.assertEqual(merge_shared_suffix(['면도', '탈취 관련 상품'], '면도기와 탈취 관련 상품'), ['면도', '탈취 관련 상품'])

    def test_source_failure_is_transitive(self):
        plan, _ = self.plan([
            {'intent': 'OUT_OF_SCOPE', 'question': 'not ready'},
            {'intent': 'STATISTICS', 'question': 'source', 'source_tasks': [1]},
            {'intent': 'REPORT', 'question': 'report', 'source_tasks': [2]}])
        self.assertEqual([t.steps for t in plan.tasks], [[], [], []])
        self.assertEqual([t.answer_status for t in plan.tasks[1:]], ['NO_DATA', 'NO_DATA'])

    def test_selected_source_is_bound_to_check_and_preserves_custom_query(self):
        plan, _ = self.plan([
            {'intent': 'PROMISING_MARKET', 'question': 'markets'},
            {'intent': 'STATISTICS', 'question': 'quantity', 'custom_query': '물량 조회',
             'source_tasks': [1], 'target_selections': [
                 {'source_task': 1, 'dimension': 'COUNTRY', 'limit': 3, 'order_by': 'product_rank'}]}])
        task = plan.tasks[1]
        self.assertEqual(task.steps[0].depends_on, ['t1.ml'])
        self.assertEqual(task.steps[1].args, {'template': 'LLM_SQL'})
        self.assertEqual(task.custom_query, '물량 조회')
        self.assertEqual(task.input_bindings[0].selection.limit, 3)
        self.assertEqual(task.steps[2].args, {})

    def test_cause_uses_sequential_dispatch_order_with_independent_news(self):
        plan, _ = self.plan([{'intent': 'CAUSE', 'question': 'why'}])
        self.assertEqual([s.step_id for s in plan.steps()], ['t1.check', 't1.sql', 't1.news', 't1.chart'])
        self.assertEqual(plan.steps()[2].depends_on, ['t1.check'])


class MarketSourcePeriodTest(unittest.TestCase):
    def test_market_task_drops_period_copied_from_source_ranking(self):
        question = '2025년 화장품 수출 상위 국가 3곳을 뽑고, 그 3곳 각각에서 전체 품목 중 유망 품목을 알려줘'
        period = {'start': '2025-01-01', 'end': '2025-12-01'}
        output = PlannerOutput(tasks=[
            {'intent': 'RANKING', 'question': 'q1', 'slots': {'products': ['화장품'], 'period': period,
             'trade_flow': 'EXPORT', 'ranking_group': 'COUNTRY', 'ranking_limit': 3}},
            {'intent': 'PROMISING_MARKET', 'question': 'q2', 'slots': {'products': [], 'period': period},
             'source_tasks': [1], 'target_selections': [{'source_task': 1, 'dimension': 'COUNTRY', 'limit': 3}]}])
        ranking, market = postprocess(output, question).tasks
        self.assertEqual(ranking.slots.period.start.isoformat(), '2025-01-01')
        self.assertIsNone(market.slots.period)


class YearMonthFollowupTest(unittest.TestCase):
    def test_six_digit_year_month_is_not_an_hs6_followup(self):
        from backend.app.agents.state import Conditions
        previous = Conditions(country_codes=['VN'], hs_items=[{'hs_code': '330499', 'hs_ver': 'HS2022'}])
        for question, product in [('202401은?', None), ('HS 190230은?', ['190230']), ('190230은?', ['190230'])]:
            with self.subTest(question=question):
                output = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question}])
                task, = postprocess(output, question, previous_conditions=previous).tasks
                self.assertEqual(task.slots.products, product)
