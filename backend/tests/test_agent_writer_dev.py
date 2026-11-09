import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from backend.scripts.evaluate_writer_dev import DEV, STANDARD_PRICES, evaluate, measurements


class WriterDevTest(unittest.TestCase):
    def test_comparison_reuses_inputs_and_records_incomplete_retry(self):
        """Exercise the normal SDK request and response hooks for all six cases without a network call."""
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
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(records), len(DEV) * len(STANDARD_PRICES))
        for case in DEV:
            rows = [r for r in records if r['case'] == case]
            self.assertEqual([r['model'] for r in rows], list(STANDARD_PRICES))
            first = rows[0]['attempts'][0]['request']
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
        self.assertEqual([r['first_draft_accepted'] for r in totals], [6, 5, 6])

    def test_missing_usage_does_not_imply_zero_cost_or_reasoning(self):
        result = measurements('gpt-6-luna', [{'accepted': False, 'seconds': 1}, {'accepted': False, 'seconds': 2}])
        self.assertTrue(result['fallback'])
        self.assertIsNone(result['estimated_usd'])
        self.assertIsNone(result['reasoning_tokens'])
