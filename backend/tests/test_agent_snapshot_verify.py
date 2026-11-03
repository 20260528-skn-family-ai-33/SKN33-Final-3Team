import unittest
from backend.tests.test_agent_compose import ComposeTest
from backend.app.agents.state import ToolResult,Draft
from backend.app.agents.verify import verify,snapshot_evidence

class SnapshotVerificationTest(unittest.TestCase):
 def prepare(self,sentence):
  state=ComposeTest().state(['STATISTICS']);task=state.plan.tasks[0];task.answer_status='ANSWERED'
  step=next(s for s in task.steps if s.tool=='query_trade_data')
  state.results[step.step_id]=ToolResult(status='OK',result_id='r',digest={'rows':[{'country_name':'중국','amount_usd':999}]})
  state.draft=Draft(conclusion=[{'task_id':'t1','sentences':[sentence]}],turn_summary='')
  return state,step.step_id
 def source(self,rows):
  return snapshot_evidence({'conditions':{'period':{'start':'2026-01-01','end':'2026-08-01'},'trade_flow':'EXPORT'},'rows':rows})
 def test_full_saved_rows_not_truncated_or_rounded_digest(self):
  state,step=self.prepare('중국의 2026년 1월 수출액은 123.45 USD입니다.')
  rows=[{'country_name':'중국','month':'2026-01','amount_usd':123.45}]+[{'country_name':'중국','month':'2026-08','amount_usd':1}]*80
  verify(state,snapshots={step:self.source(rows)})
  check=state.verification.checks[0]
  self.assertEqual((check.status,check.basis,check.reason),('PASS','SNAPSHOT','MATCH'))
 def test_wrong_digest_cannot_override_saved_result(self):
  state,step=self.prepare('중국의 수출액은 999 USD입니다.')
  verify(state,snapshots={step:self.source([{'country_name':'중국','amount_usd':123}])})
  self.assertEqual(state.verification.checks[0].status,'MISMATCH')
 def test_other_country_same_number_cannot_validate_claim(self):
  state,step=self.prepare('중국의 수출액은 123 USD입니다.')
  verify(state,known_names={'중국','미국'},snapshots={step:self.source([{'country_name':'미국','amount_usd':123}])})
  self.assertEqual(state.verification.checks[0].status,'MISMATCH')
 def test_other_month_same_number_cannot_validate_claim(self):
  state,step=self.prepare('중국의 2026년 1월 수출액은 123 USD입니다.')
  verify(state,snapshots={step:self.source([{'country_name':'중국','month':'2026-08','amount_usd':123}])})
  self.assertEqual(state.verification.checks[0].status,'MISMATCH')
 def test_expired_source_does_not_pass_using_old_digest(self):
  state,step=self.prepare('중국의 수출액은 999 USD입니다.')
  verify(state,known_names={'중국'},snapshots={step:{'_verification_source':'SNAPSHOT','_source_unavailable':True,'rows':[]}})
  check=state.verification.checks[0]
  self.assertEqual((check.status,check.reason),('MISMATCH','SOURCE_UNAVAILABLE'))

 def test_import_amount_cannot_validate_export_claim(self):
  state,step=self.prepare('중국의 수출액은 123 USD입니다.')
  verify(state,snapshots={step:self.source([{'country_name':'중국','export_amount_usd':456,'import_amount_usd':123}])})
  self.assertEqual(state.verification.checks[0].status,'MISMATCH')
 def test_preaggregated_total_passes_but_single_month_is_not_period_total(self):
  for monthly,expected in ((False,'PASS'),(True,'MISMATCH')):
   state,step=self.prepare('중국의 수출 총액은 123 USD입니다.')
   row={'country_name':'중국','amount_usd':123}
   if monthly:row['month']='2026-01'
   verify(state,snapshots={step:self.source([row])})
   self.assertEqual(state.verification.checks[0].status,expected)
