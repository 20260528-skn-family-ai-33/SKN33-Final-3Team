import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from backend.app.agents.execution import invoke_graph
from backend.app.api.common import ApiError

class ResumeRoutingTest(unittest.TestCase):
 def test_dict_answer_targets_interrupt_and_unconsumed_answer_is_not_discarded(self):
  answer={'clarification_id':'c','hs_item':{'hs_code':'3304','hs_ver':'HS2022'}}
  execution=Mock();execution.read.return_value={'job_id':'j','payload_json':{'resume':answer}}
  snapshot=SimpleNamespace(values={'pending':{'clarification_id':'c'}},tasks=[SimpleNamespace(interrupts=[SimpleNamespace(id='interrupt-id')])])
  graph=Mock();graph.get_state.return_value=snapshot
  with self.assertRaises(ApiError) as caught:invoke_graph(graph,execution)
  self.assertEqual(caught.exception.code,'CLARIFICATION_RESUME_FAILED')
  self.assertEqual(graph.invoke.call_args.args[0].resume,{'interrupt-id':answer})
  execution.clear_resume.assert_not_called()

 def test_reply_is_delivered_when_checkpoint_interrupt_writes_are_not_visible(self):
  from unittest.mock import MagicMock,patch
  from langgraph.checkpoint.memory import InMemorySaver
  from backend.tests.test_agent_graph import Services,initial
  from backend.app.agents.graph import build_graph,graph_config
  class ReplyServices(Services):
   def resume(self,state,answer):
    assert answer['choice_ids']==['US']
    super().resume(state,'US')
  services=ReplyServices(ask=True);graph=build_graph(services,InMemorySaver())
  graph.invoke(initial(),graph_config('j'))
  original=graph.get_state;waiting=original(graph_config('j'))
  hidden=waiting._replace(tasks=tuple(task._replace(interrupts=()) for task in waiting.tasks))
  execution=MagicMock();execution.read.return_value={'job_id':'j','payload_json':{'resume':{'clarification_id':'c','choice_ids':['US']}}}
  execution.job={'claim_token':1}
  with patch.object(graph,'get_state',side_effect=[hidden,original(graph_config('j'))]) as get_state:
   # The second read must see the state AFTER invoke, not the old snapshot.
   get_state.side_effect=lambda *args: hidden if get_state.call_count==1 else original(*args)
   with patch('backend.app.agents.execution.jobs.locked_current',return_value=None):
    result=invoke_graph(graph,execution)
  self.assertEqual(result['status'],'SUCCEEDED')
  self.assertEqual(services.events.count('ask_user'),1)
