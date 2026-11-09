import json
import unittest
from unittest.mock import MagicMock, patch
import httpx
from openai import OpenAI
from backend.app.agents.compose import compose_input, assemble
from backend.app.agents.llm import StructuredClient
from backend.app.agents.planner import build_plan
from backend.app.agents.runtime import Services
from backend.app.agents.state import AgentState, PlannerOutput, ToolResult, Draft
from backend.app.api.config import Settings
from backend.tests.test_agent_graph import initial


def describe(conditions):
    return {'countries': [{'name': '미국'}], 'products': [{'name': '화장품'}]}


class ComposeTest(unittest.TestCase):
    def state(self, intents):
        state = AgentState.model_validate(initial())
        state.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': i, 'question': 'q'} for i in intents]))
        return state

    def test_compose_model_selection_on_responses_api(self):
        for model in (None, 'gpt-4.1-mini', 'gpt-5.6-luna', 'gpt-6-luna'):
            overrides = {'API_COMPOSE_MODEL': model} if model else {}
            expected_model = model or 'gpt-6-luna'
            with self.subTest(overrides=overrides):
                state = self.state(['STATISTICS'])
                task = state.plan.tasks[0]
                task.answer_status = 'ANSWERED'
                step = next(s for s in task.steps if s.tool == 'query_trade_data')
                state.results[step.step_id] = ToolResult(status='OK', digest={'total': 10})
                draft = Draft(conclusion=[{'task_id': task.task_id, 'sentences': ['수출액은 10 USD입니다.']}],
                              turn_summary='수출액 10 USD')
                requests = []

                def respond(request):
                    requests.append(request)
                    return httpx.Response(200, json={
                        'object': 'response', 'id': 'resp_test', 'created_at': 0, 'model': expected_model,
                        'status': 'completed', 'output': [{'type': 'message', 'id': 'msg_test',
                            'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text',
                                'text': draft.model_dump_json(), 'annotations': []}]}]})

                def client_factory(**kwargs):
                    return OpenAI(**kwargs, base_url='https://test.invalid/v1',
                                  http_client=httpx.Client(transport=httpx.MockTransport(respond)))

                settings = Settings({'OPENAI_API_KEY': 'test', **overrides})
                execution = MagicMock()
                execution.call.side_effect = lambda node, timeout, call, **kwargs: call(timeout)
                services = Services(execution, settings, StructuredClient(settings, client_factory))
                with patch.object(services, 'read_store'), patch('backend.app.agents.templates._names', return_value=({}, {})):
                    services.compose(state)

                self.assertEqual(state.draft, draft)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].url.path, '/v1/responses')
                body = json.loads(requests[0].content)
                self.assertEqual(body['model'], expected_model)
                self.assertEqual(body['text']['format']['name'], 'Draft')
                self.assertTrue(body['text']['format']['strict'])
                self.assertEqual(body['max_output_tokens'], 1500)
                self.assertFalse(body['store'])
                if expected_model == 'gpt-4.1-mini':
                    self.assertEqual(body['temperature'], 0)
                    self.assertNotIn('reasoning', body)
                else:
                    self.assertNotIn('temperature', body)
                    self.assertEqual(body['reasoning'], {'effort': 'low'})

    def test_all_fixed_needs_no_generated_response(self):
        state = self.state(['OUT_OF_SCOPE', 'OUT_OF_SCOPE'])
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
