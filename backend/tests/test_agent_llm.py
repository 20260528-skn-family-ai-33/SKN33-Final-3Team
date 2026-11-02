import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from backend.app.agents.llm import StructuredClient
from backend.app.agents.state import PlannerOutput


class StructuredClientTest(unittest.TestCase):
    def test_no_implicit_retries_and_structured_response(self):
        factory = MagicMock()
        client = factory.return_value.__enter__.return_value
        client.responses.parse.return_value = SimpleNamespace(status='completed',
            output_parsed={'tasks': [{'intent': 'STATISTICS', 'question': 'q'}]})
        adapter = StructuredClient(SimpleNamespace(values={'OPENAI_API_KEY': 'test-key'}), factory)
        result = adapter.parse(model='gpt-4.1-mini', timeout=10, prompt='p', payload={'question': 'q'},
                               schema=PlannerOutput, max_output_tokens=1000)
        self.assertEqual(result.tasks[0].intent, 'STATISTICS')
        factory.assert_called_once_with(api_key='test-key', timeout=10, max_retries=0)
        self.assertFalse(client.responses.parse.call_args.kwargs['store'])

    def test_incomplete_or_refused_output_is_not_a_plan(self):
        for status, parsed in [('incomplete', {'tasks': []}), ('completed', None)]:
            with self.subTest(status=status):
                factory = MagicMock()
                factory.return_value.__enter__.return_value.responses.parse.return_value = SimpleNamespace(
                    status=status, output_parsed=parsed)
                adapter = StructuredClient(SimpleNamespace(values={'OPENAI_API_KEY': 'test'}), factory)
                with self.assertRaises(ValueError):
                    adapter.parse(model='gpt-4.1-mini', timeout=10, prompt='p', payload={},
                                  schema=PlannerOutput, max_output_tokens=1000)
