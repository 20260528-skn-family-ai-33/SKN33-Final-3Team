"""실제 Client 경로에 HTTP 모의 응답을 연결해 공통 장애와 범위 오류를 구분합니다."""
import json
import unittest
from unittest import mock
import requests
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.collectors.un_data.api import ApiRequestError,Client
from backend.app.scheduler.jobs import un_data_refresh as job

class ApiClientFailureTest(unittest.TestCase):
    def request(self,session):
        # 성공 응답은 저장하므로 테스트별 임시 폴더를 사용합니다.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp,mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            return Client(session=session,interval=0).get('preview/C/M/HS',{},Path(tmp)/'response.json')

    def test_http_status_and_retry_limit(self):
        for status,attempts in [(401,1),(403,1),(429,3),(400,1),(404,1),(408,3),(500,3),(503,3)]:
            with self.subTest(status=status):
                session=mock.Mock();session.get.return_value=mock.Mock(status_code=status)
                with self.assertRaises(ApiRequestError) as error:self.request(session)
                self.assertEqual((error.exception.status_code,error.exception.kind,error.exception.attempts),(status,'http',attempts))
                self.assertEqual(session.get.call_count,attempts)

    def test_network_error_has_no_status_or_sensitive_original_message(self):
        session=mock.Mock();session.get.side_effect=requests.Timeout('SECRET-KEY in request URL')
        with self.assertRaises(ApiRequestError) as error:self.request(session)
        self.assertEqual((error.exception.kind,error.exception.status_code,error.exception.attempts),('network',None,3))
        self.assertNotIn('SECRET-KEY',str(error.exception));self.assertEqual(session.get.call_count,3)

    def test_invalid_response_is_common_error(self):
        for body in ['<html>gateway down</html>','[]','{"data":[],"count":1}','{"data":[],"count":0,"error":"secret response"}']:
            with self.subTest(body=body):
                session=mock.Mock();session.get.return_value=mock.Mock(status_code=200,text=body)
                with self.assertRaises(ApiRequestError) as error:self.request(session)
                self.assertEqual(error.exception.kind,'response');self.assertEqual(error.exception.status_code,200)
                self.assertEqual(session.get.call_count,1)
                self.assertNotIn('secret response',str(error.exception))

    def test_transient_server_failure_recovers_within_request(self):
        session=mock.Mock();session.get.side_effect=[mock.Mock(status_code=503),mock.Mock(status_code=200,text='{"data":[],"count":0}')]
        self.assertEqual(self.request(session),[]);self.assertEqual(session.get.call_count,2)


class ApiFailureIsolationTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    run_job=fixture.MonthlyRefreshTest.run_job
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def client(self,fail_frequency='A',failure=503):
        source=fixture.FakeClient();session=mock.Mock();data_calls=[]
        def get(url,**kwargs):
            path=url.split('/v1/')[1]
            if not path.startswith('getDa'):
                data_calls.append(path)
                if '/'+fail_frequency+'/' in path:
                    if isinstance(failure,Exception):raise failure
                    return mock.Mock(status_code=failure)
            rows=source.get(path,kwargs['params'],None)
            return mock.Mock(status_code=200,text=json.dumps({'count':len(rows),'data':rows}))
        session.get.side_effect=get
        return Client(session=session,interval=0),data_calls

    def run_fast(self,client):
        with mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            return self.run_job(client)

    def journal(self):
        return json.loads((self.state/'pending_datasets.json').read_text(encoding='utf-8'))

    def test_server_outage_after_discovery_stops_before_next_scope(self):
        before=self.checkpoint();client,calls=self.client()
        with self.assertRaises(ApiRequestError):self.run_fast(client)
        self.assertEqual(len(calls),3);self.assertTrue(all('/A/' in p for p in calls))
        self.assertEqual(len(self.journal()['datasets']),2);self.assertEqual(before,self.checkpoint());self.assertEqual(self.snapshot(),[])
        manifest=json.loads(next(self.state.glob('runs/*/manifest.json')).read_text())
        self.assertEqual((manifest['http_status'],manifest['api_error_kind'],manifest['api_attempts'],manifest['pending']),(503,'http',3,2))

    def test_network_outage_after_discovery_stops_before_next_scope(self):
        client,calls=self.client(failure=requests.ConnectionError('offline'))
        with self.assertRaises(ApiRequestError) as error:self.run_fast(client)
        self.assertEqual(error.exception.kind,'network');self.assertEqual(len(calls),3)
        self.assertTrue(all('/A/' in p for p in calls));self.assertEqual(len(self.journal()['datasets']),2)

    def test_rate_limit_exhaustion_records_retry_and_retains_pending(self):
        before=self.checkpoint();client,calls=self.client(failure=429)
        with self.assertRaises(ApiRequestError):self.run_fast(client)
        self.assertEqual(len(calls),3)
        self.assertEqual(self.checkpoint(),before)
        self.assertEqual(len(self.journal()['datasets']),2)
        manifest=json.loads(next(self.state.glob('runs/*/manifest.json')).read_text())
        self.assertEqual(manifest['http_status'],429)
        self.assertEqual(manifest['api_attempts'],3)
        self.assertFalse(manifest['api_retry_info']['will_retry'])

    def test_auth_error_stops_once_and_retains_pending(self):
        client,calls=self.client(failure=401)
        with self.assertRaises(ApiRequestError):self.run_fast(client)
        self.assertEqual(len(calls),1);self.assertEqual(len(self.journal()['datasets']),2)

    def test_success_before_outage_is_skipped_after_recovery(self):
        before=self.checkpoint();client,calls=self.client(fail_frequency='M')
        with self.assertRaises(ApiRequestError):self.run_fast(client)
        self.assertEqual(set(self.journal()['completed_versions']),{'A_2023_40'})
        self.assertEqual(before,self.checkpoint())
        client,calls=self.client(fail_frequency='none')
        result=self.run_fast(client)
        self.assertEqual((result['succeeded'],result['skipped']),(1,1))
        self.assertTrue(all('/M/' in p for p in calls));self.assertEqual(self.journal()['datasets'],{})
        self.assertEqual(len(self.snapshot()),3)

    def test_data_error_continues_other_scope_even_with_http_words(self):
        original=job.process_dataset
        def fail_annual(*args):
            if args[3]['freqCode']=='A':raise ValueError('데이터 설명에 HTTP 429 포함')
            return original(*args)
        with mock.patch.object(job,'process_dataset',side_effect=fail_annual):
            with self.assertRaises(ValueError):self.run_job()
        self.assertEqual(set(self.journal()['datasets']),{'A_2023_40'})
        self.assertEqual({r[0] for r in self.snapshot()},{'M'})

    def test_type_not_message_controls_abort(self):
        with mock.patch.object(job,'process_dataset',side_effect=ApiRequestError('통신 장애',kind='network')) as process:
            with self.assertRaises(ApiRequestError):self.run_job()
        self.assertEqual(process.call_count,1);self.assertEqual(len(self.journal()['datasets']),2)

if __name__=='__main__':unittest.main()
