import unittest
from backend.app.agents.compose import compose_input, assemble
from backend.app.agents.planner import build_plan
from backend.app.agents.state import AgentState, PlannerOutput, ToolResult, Draft
from backend.tests.test_agent_graph import initial


def describe(conditions):
    return {'countries': [{'name': '미국'}], 'products': [{'name': '화장품'}]}


class ComposeTest(unittest.TestCase):
    def state(self, intents):
        state = AgentState.model_validate(initial())
        state.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': i, 'question': 'q'} for i in intents]))
        return state

    def test_all_fixed_needs_no_generated_response(self):
        state = self.state(['MARKET_TYPE', 'OUT_OF_SCOPE'])
        for task in state.plan.tasks:
            task.answer_status = state.task_outcome(task.task_id)
        payload, fixed = compose_input(state, describe, [])
        self.assertEqual(payload['tasks'], [])
        draft = assemble(state, None, fixed, describe)
        self.assertEqual([c.task_id for c in draft.conclusion], ['t1', 't2'])
        self.assertEqual(draft.turn_summary, '')

    def test_report_success_is_fixed_and_keeps_condition_summary(self):
        state = self.state(['REPORT'])
        state.input.first_question = True
        state.plan.tasks[0].answer_status = 'ANSWERED'
        payload, fixed = compose_input(state, describe, [])
        self.assertEqual(payload['tasks'], [])
        draft = assemble(state, None, fixed, describe)
        self.assertEqual(draft.turn_summary, '미국 화장품 리포트 생성 완료')
        self.assertEqual(draft.title, '미국 화장품 리포트')
        self.assertIn('완성되었습니다', draft.conclusion[0].sentences[0])

    def test_mixed_fixed_generated_order_and_primary_failure_summary(self):
        state = self.state(['MARKET_TYPE', 'STATISTICS'])
        state.plan.tasks[0].answer_status = 'NO_DATA'
        state.plan.tasks[1].answer_status = 'ANSWERED'
        step = next(s for s in state.plan.tasks[1].steps if s.tool == 'query_trade_data')
        state.results[step.step_id] = ToolResult(status='OK', result_id='r', digest={'total': 10})
        payload, fixed = compose_input(state, describe, [])
        self.assertEqual([t['task_id'] for t in payload['tasks']], ['t2'])
        generated = Draft(conclusion=[{'task_id': 't2', 'sentences': ['수출액은 10 USD입니다.']}], turn_summary='10 USD')
        result = assemble(state, generated, fixed, describe)
        self.assertEqual([c.task_id for c in result.conclusion], ['t1', 't2'])
        self.assertEqual(result.turn_summary, '')
        generated.conclusion[0].task_id = 't1'
        with self.assertRaises(ValueError): assemble(state, generated, fixed, describe)

    def test_article_body_and_insufficient_evidence_never_enter_prompt(self):
        state = self.state(['OUT_OF_SCOPE'])
        article = {'n': 1, 'task_id': 't1', 'title': '기사', 'published_date': '2026-11-01',
                   'summary': ['요약'], 'body': 'private body', 'evidence_sufficient': True}
        payload, _ = compose_input(state, describe, [article, dict(article, n=2, evidence_sufficient=False)])
        self.assertEqual(len(payload['articles']), 1)
        self.assertNotIn('body', payload['articles'][0])
