import json,tempfile,unittest
from pathlib import Path
from unittest import mock
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
from backend.app.scheduler.collectors.un_data.api import Client,ApiRequestError,retry_details

class RetryAfterTest(unittest.TestCase):
    def response(self,status,headers=None):
        return mock.Mock(status_code=status,headers=headers or {},text='{"data":[],"count":0}')

    def test_recovery_records_retry_without_secrets(self):
        session=mock.Mock();session.get.side_effect=[self.response(429,{'Retry-After':'1','x-ratelimit-limit':'1','Secret':'KEY'}),self.response(200)]
        with tempfile.TemporaryDirectory() as tmp,mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep') as sleep:
            path=Path(tmp)/'response.json';client=Client('KEY',session)
            self.assertEqual(client.interval,2)
            self.assertEqual(client.get('get/C/M/HS',{},path),[])
            info=json.loads(Path(str(path)+'.retry.jsonl').read_text())
            self.assertEqual(info['wait_seconds'],2);self.assertTrue(info['will_retry'])
            self.assertNotIn('KEY',json.dumps(info));sleep.assert_any_call(2)
            self.assertEqual(client.request_count,2)

    def test_exhaustion_and_long_wait(self):
        for header,calls in [('1',3),('120',1),('invalid',3)]:
            with self.subTest(header=header),tempfile.TemporaryDirectory() as tmp,mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
                session=mock.Mock();session.get.return_value=self.response(429,{'Retry-After':header})
                with self.assertRaises(ApiRequestError) as error:
                    Client(session=session).get('get/C/M/HS',{},Path(tmp)/'response.json')
                self.assertEqual(session.get.call_count,calls)
                self.assertFalse(error.exception.retry_info['will_retry'])
                self.assertEqual(error.exception.attempts,calls)

    def test_http_date_and_invalid_values(self):
        future=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=20),usegmt=True)
        seconds=retry_details(self.response(429,{'Retry-After':future}))['retry_after_seconds']
        self.assertTrue(18<=seconds<=20)
        for value in ('bad','-1','NaN'):
            self.assertIsNone(retry_details(self.response(429,{'Retry-After':value}))['retry_after_seconds'])

    def test_recording_failure_stops_without_hiding_429(self):
        session=mock.Mock();session.get.return_value=self.response(429,{'Retry-After':'1'})
        with mock.patch('pathlib.Path.open',side_effect=OSError('disk')),mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            with self.assertRaises(ApiRequestError) as error:Client(session=session).get('get/C/M/HS',{},Path('/private/tmp/retry-diagnostic.json'))
        self.assertEqual(error.exception.status_code,429);self.assertEqual(session.get.call_count,1)
