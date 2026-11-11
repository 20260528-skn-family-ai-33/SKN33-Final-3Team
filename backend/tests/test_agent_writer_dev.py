import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from backend.scripts.evaluate_writer_dev import DEV, STANDARD_PRICES, dev_state, evaluate, generate_case, measurements
from backend.scripts.writer_dev_dataset import load_dataset, score
from backend.app.agents.compose import COMPOSE_TEXT_PROMPT
from backend.app.agents.state import Draft


class WriterDevTest(unittest.TestCase):
    def test_finished_report_uses_service_outcome_before_compose(self):
        case = next(c for c, _ in load_dataset() if c.case_id == 'WDEV-097')
        case.state.plan.tasks[0].answer_status = None
        state, record = generate_case(case, 'gpt-6-luna', '')
        self.assertEqual(state.plan.tasks[0].answer_status, 'ANSWERED')
        self.assertTrue(record['metrics']['fixed'])
        self.assertIn('리포트가 완성', state.draft.conclusion[0].sentences[0])
        self.assertIsNone(case.state.plan.tasks[0].answer_status)

    def test_comparison_reuses_inputs_and_records_incomplete_retry(self):
        """Exercise the normal SDK request and response hooks for all 100 cases without a network call."""
        output = StringIO()
        summaries = StringIO()
        retried = False

        def respond(request):
            nonlocal retried
            body = json.loads(request.content)
            payload = json.loads(body['input'])
            draft = {'conclusion': [{'task_id': t['task_id'], 'sentences': ['통계를 확인했습니다.']}
                                    for t in payload['tasks']], 'explanation': [], 'turn_summary': '', 'title': None}
            response = {'object': 'response', 'id': 'resp_test', 'created_at': 0, 'model': body['model'],
                        'status': 'completed', 'service_tier': 'default', 'output': [{
                            'type': 'message', 'id': 'msg_test', 'role': 'assistant', 'status': 'completed',
                            'content': [{'type': 'output_text', 'text': json.dumps(draft), 'annotations': []}]}],
                        'usage': {'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 10,
                                  'cache_write_tokens': 0 if body['model'] == 'gpt-4.1-mini' else 20}, 'output_tokens': 12,
                                  'output_tokens_details': {'reasoning_tokens': 7}, 'total_tokens': 112}}
            if body['model'] == 'gpt-5.6-luna' and not retried:
                response.update(status='incomplete', output=[], incomplete_details={'reason': 'max_output_tokens'})
                retried = True
            return httpx.Response(200, json=response)

        real_client = httpx.Client
        def offline_client(**kwargs):
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        with patch('backend.scripts.evaluate_writer_dev.httpx', SimpleNamespace(Client=offline_client)), \
             redirect_stdout(summaries), self.assertLogs('backend.app.agents.runtime', 'WARNING'):
            evaluate(list(STANDARD_PRICES), 'synthetic', output)
        dataset = load_dataset()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(records), 100 * len(STANDARD_PRICES))
        for case, rule in dataset:
            rows = [r for r in records if r['case'] == case.case_id]
            self.assertEqual([r['model'] for r in rows], list(STANDARD_PRICES))
            for row in rows:
                self.assertTrue(row['evaluation']['mode_matches'], case.case_id)
                self.assertTrue(all(t['status_matches'] for t in row['evaluation']['task_checks'].values()), case.case_id)
                self.assertEqual(row['route'], rule.route)
            if rule.mode == 'fixed':
                self.assertTrue(all(not r['attempts'] and not r['metrics']['fallback'] for r in rows))
                continue
            first = rows[0]['attempts'][0]['request']
            self.assertEqual(COMPOSE_TEXT_PROMPT in first['instructions'], rule.route == 'compose_text')
            self.assertNotIn('forbidden_claims', first['input'])
            for row in rows:
                for attempt in row['attempts']:
                    request = attempt['request']
                    self.assertEqual(request['model'], row['model'])
                    for key in ('instructions', 'input', 'text', 'max_output_tokens', 'store'):
                        self.assertEqual(request[key], first[key])
        retry, = [r for r in records if r['metrics']['retries']]
        self.assertEqual(retry['attempts'][0]['incomplete_details']['reason'], 'max_output_tokens')
        self.assertEqual(retry['metrics']['incomplete'], 1)
        self.assertEqual(retry['metrics']['reasoning_tokens'], 14)
        self.assertAlmostEqual(retry['metrics']['estimated_usd'], 2 * (70*.20 + 10*.02 + 20*.25 + 12*1.20)/1e6)
        self.assertFalse(retry['metrics']['first_draft_accepted'])
        self.assertFalse(retry['metrics']['fallback'])
        totals = [json.loads(line) for line in summaries.getvalue().splitlines() if line.startswith('{')]
        self.assertEqual([r['first_draft_accepted'] for r in totals], [94, 93, 94])

    def test_original_six_and_quality_checks(self):
        dataset = load_dataset()
        for (case, _), (name, old) in zip(dataset[:6], DEV.items(), strict=True):
            self.assertEqual(case.case_id, name)
            self.assertEqual(case.state, dev_state(old))
        case, rule = dataset[0]
        state = case.state.model_copy(deep=True)
        state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['수출액은 2,346만 달러입니다.']}], turn_summary='')
        checks = score(state, rule, fixed=False)['task_checks']['t1']
        self.assertEqual(checks['missing_display_amounts'], ['1억 2,346만 달러'])
        state.draft.conclusion[0].sentences = ['수출액은 1억 2,346만 달러입니다.']
        self.assertFalse(score(state, rule, fixed=False)['task_checks']['t1']['missing_display_amounts'])

    def test_missing_usage_does_not_imply_zero_cost_or_reasoning(self):
        result = measurements('gpt-6-luna', [{'accepted': False, 'seconds': 1}, {'accepted': False, 'seconds': 2}])
        self.assertTrue(result['fallback'])
        self.assertIsNone(result['estimated_usd'])
        self.assertIsNone(result['reasoning_tokens'])
