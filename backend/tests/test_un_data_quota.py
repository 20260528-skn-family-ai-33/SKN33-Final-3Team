import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from backend.app.scheduler.collectors.un_data.api import Client, ApiRequestError


class QuotaTest(unittest.TestCase):
    def request(self, message, headers, code=403):
        response=Mock(status_code=403,headers=headers,text=json.dumps({'statusCode':code,'message':message}))
        client=Client(session=Mock(get=Mock(return_value=response)),interval=0)
        with tempfile.TemporaryDirectory() as folder, patch('time.sleep'):
            with self.assertRaises(ApiRequestError) as raised:
                client.get('test',{},Path(folder)/'response.json')
        return raised.exception,client.request_count

    def test_explicit_quota_respects_long_wait_without_immediate_retry(self):
        error,count=self.request('Out of call volume quota. Quota will be replenished in 01:00:00.',{'Retry-After':'3600'})
        self.assertEqual((error.kind,error.status_code,count),('quota',403,1))
        self.assertEqual(error.retry_info['retry_after_seconds'],3600)

    def test_auth_and_ambiguous_403_never_retry(self):
        for message in ('Access denied due to invalid subscription key.','Forbidden','quota','Out of call volume quota. Quota will be replenished in 01:00:00.'):
            headers={} if message.startswith('Out') else {'Retry-After':'1'}
            error,count=self.request(message,headers)
            self.assertEqual((error.kind,count),('http',1))

    def test_short_quota_retry_is_bounded(self):
        error,count=self.request('Out of call volume quota. Quota will be replenished in 00:00:01.',{'Retry-After':'1'})
        self.assertEqual((error.kind,count),('quota',3))

    def test_oversized_retry_after_stops_without_retry(self):
        error,count=self.request('Out of call volume quota. Quota will be replenished in 01:00:00.',{'Retry-After':'9'*20})
        self.assertEqual((error.kind,count),('response',1))
