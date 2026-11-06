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
