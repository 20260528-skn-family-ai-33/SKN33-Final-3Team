import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from pydantic import ValidationError
from backend.app.agents.llm import StructuredClient
from backend.app.agents.state import PlannerOutput


class StructuredClientTest(unittest.TestCase):
    def test_validation_log_includes_safe_fields_reason_and_retry(self):
        import json
        from backend.app.agents.runtime import Services
        from backend.app.api.config import Settings
        from backend.tests.test_agent_state import state
        private='PRIVATE_RESPONSE_DO_NOT_LOG'
        try:
            PlannerOutput.model_validate({'tasks':[{'intent':'STATISTICS','question':private,
                'slots':{'period':{'start':'2025-01-01','end':'2025-03-01'},'recent_months':3},private:private}]})
        except ValidationError as error:
            failure=error
        execution=Mock()
        execution.call.side_effect=[failure,PlannerOutput(tasks=[{'intent':'STATISTICS','question':'ok'}])]
        service=Services(execution,Settings({}),Mock())
        with self.assertLogs('backend.app.agents.runtime',level='WARNING') as logs:
            result=service.structured(state(),'planner','private prompt',{'question':private},PlannerOutput,2000)
        self.assertEqual(result.tasks[0].question,'ok')
        message=logs.records[0].getMessage()
        self.assertIn('attempt=1/2 schema=PlannerOutput',message)
        details=json.loads(message.split(' validation=',1)[1])
        self.assertEqual(details['total'],2)
        self.assertTrue(any(row['field']=='tasks.0.slots' and '동시에' in row['reason'] for row in details['errors']))
        self.assertTrue(any('<unknown>' in row['field'] for row in details['errors']))
        self.assertNotIn(private,message)
        self.assertNotIn('private prompt',message)

    def test_validation_details_bounds_errors_and_redacts_custom_messages(self):
        from pydantic import BaseModel, field_validator
        from backend.app.agents.llm import validation_details
        class Output(BaseModel):
            text: str
            @field_validator('text')
            @classmethod
            def reject(cls,value): raise ValueError('PRIVATE_CUSTOM_MESSAGE '+value)
        with self.assertRaises(ValidationError) as caught:
            Output.model_validate({'text':'PRIVATE_INPUT'})
        details=validation_details(caught.exception,Output)
        self.assertEqual(details['errors'][0]['field'],'text')
        self.assertNotIn('PRIVATE',str(details))
        with self.assertRaises(ValidationError) as caught:
            PlannerOutput.model_validate({'tasks':[{} for _ in range(20)]})
        details=validation_details(caught.exception,PlannerOutput)
        self.assertGreater(details['total'],10)
        self.assertEqual(len(details['errors']),10)
        self.assertTrue(details['truncated'])

    def test_validation_details_keeps_safe_limit_reason_and_failing_model(self):
        from backend.app.agents.llm import validation_details
        from backend.app.agents.state import TaskSlots
        with self.assertRaises(ValidationError) as caught:
            TaskSlots.model_validate({'recent_months':999})  # validate() 단계 모델: 응답 스키마와 다르다
        details=validation_details(caught.exception,PlannerOutput)
        self.assertEqual(details['model'],'TaskSlots')
        self.assertIn('개월까지 조회할 수 있습니다',details['errors'][0]['reason'])

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
