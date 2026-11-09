import unittest
from backend.app.agents.planner import build_plan
from backend.app.agents.state import PlannerOutput
from backend.app.api.common import ApiError


class AgentPlannerTest(unittest.TestCase):
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
