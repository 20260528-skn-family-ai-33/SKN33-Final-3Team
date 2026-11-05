import unittest
from backend.app.api import jobs
class ReportProtocolTest(unittest.TestCase):
 def test_new_report_protocol_is_separate_from_legacy(self):
  job=jobs._initial(1,'API_REPORT',1,{},'key')
  self.assertEqual(job['version'],jobs.REPORT_QUEUE_VERSION)
  self.assertNotEqual(job['version'],1)
 def test_retry_claim_removes_old_error_without_mutating_previous_record(self):
  job=jobs._initial(1,'API_REPORT',1,{},'key')
  job['result_json']={'step':'PDF','error':{'code':'REPORT_RENDERER_UNAVAILABLE'}}
  claim=jobs._claimed(job)
  self.assertEqual(claim['result_json'],{'step':'PDF'})
  self.assertIn('error',job['result_json'])
  self.assertEqual(claim['attempt_no'],job['attempt_no']+1)
