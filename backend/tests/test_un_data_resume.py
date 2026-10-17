"""묶음 수집·요청 예산·영속 재개를 실제 Client와 SQLite 트랜잭션으로 검증합니다."""
import json
import unittest
from datetime import datetime,timedelta,timezone,date
from unittest import mock
from pathlib import Path
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.collectors.un_data.api import Client
from backend.app.scheduler.jobs import un_data_resume as resume
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler import main as scheduler

class ResumeTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def client(self,source=None,status=None):
        source=source or fixture.FakeClient();session=mock.Mock()
        def get(url,**kw):
            if status:return mock.Mock(status_code=status,headers={'Retry-After':'1'})
            rows=source.get(url.split('/v1/')[1],kw['params'],None)
            return mock.Mock(status_code=200,headers={},text=json.dumps({'data':rows,'count':len(rows)}))
        session.get.side_effect=get
        return Client('TEST',session=session,interval=0)

    def run_resume(self,client=None,now=None,**kw):
        with mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            return resume.run_resumable(self.engine,client or self.client(),self.scope,self.state,
                today=date(2024,2,2),now=now or datetime(2024,2,2,tzinfo=timezone.utc),**kw)

    def test_budget_smaller_than_discovery_finishes_across_restarts(self):
        before=self.checkpoint();now=datetime(2024,2,2,tzinfo=timezone.utc);counts=[]
        for index in range(10):
            client=self.client();result=self.run_resume(client,now=now,budget=2)
            counts.append(client.request_count);self.assertLessEqual(client.request_count,2)
            if result['status']=='DB_VERIFIED':break
            self.assertEqual(result['reason'],'budget');self.assertEqual(self.checkpoint(),before)
            delayed=self.run_resume(self.client(),now=now+timedelta(seconds=299),budget=2)
            self.assertEqual(delayed['status'],'DEFERRED')
            now+=timedelta(minutes=5)
        else:self.fail('예산 재개가 앞으로 진행하지 못함')
        self.assertEqual(len(self.snapshot()),3)
        self.assertEqual(sum(counts),8)  # 목록 4 + 품목 4, 완료 요청 반복 없음

    def test_batch_reduces_calls_and_preserves_country_transactions(self):
        cfg=json.loads(self.scope.read_text());cfg['reporters']={'M':['8','40'],'A':[]}
        self.scope.write_text(json.dumps(cfg),encoding='utf-8')
        import hashlib
        checkpoint=json.loads(self.checkpoint());checkpoint['scope_sha256']=hashlib.sha256(self.scope.read_bytes()).hexdigest();job.save(self.state/'checkpoint.json',checkpoint)
        source=fixture.FakeClient([fixture.catalog(),fixture.catalog(reporter=40)],
                                  [fixture.raw(),fixture.raw(reporter=40)])
        client=self.client(source);result=self.run_resume(client)
        trade=[p for path,p in source.calls if not path.startswith('getDa')]
        self.assertEqual(len(trade),2)
        self.assertTrue(all(p['reporterCode']=='40,8' for p in trade))
        self.assertEqual(result['succeeded'],2);self.assertEqual(len(self.snapshot()),2)

    def test_api_resumes_three_times_then_stops(self):
        now=datetime(2024,2,2,tzinfo=timezone.utc);before=self.checkpoint()
        for attempt in range(4):
            result=self.run_resume(self.client(status=503),now=now)
            self.assertEqual(result['api_resumes'],attempt)
            self.assertEqual(result['status'],'RETRY_EXHAUSTED' if attempt==3 else 'WAITING')
            now+=timedelta(hours=2)
        client=self.client();self.assertEqual(self.run_resume(client,now=now)['status'],'RETRY_EXHAUSTED')
        self.assertEqual(client.request_count,0);self.assertEqual(self.checkpoint(),before)
        self.assertEqual(self.run_resume(client,now=now,resume_now=True)['status'],'DB_VERIFIED')

    def test_auth_error_is_not_automatically_retried(self):
        client=self.client(status=401);result=self.run_resume(client)
        self.assertEqual(result['status'],'FAILED');self.assertIsNone(result['next_attempt_at'])
        self.assertEqual(client.request_count,1)

    def test_retry_attempts_count_toward_budget(self):
        client=self.client(status=503);result=self.run_resume(client,budget=2)
        self.assertEqual(result['reason'],'api');self.assertEqual(client.request_count,2)

    def test_scope_change_rejects_existing_continuation(self):
        self.run_resume(budget=1)
        self.scope.write_text(self.scope.read_text()+' ')
        with self.assertRaisesRegex(ValueError,'다른 DB/범위'):self.run_resume()

    def test_changed_date_keeps_original_window_until_complete(self):
        now=datetime(2024,2,2,tzinfo=timezone.utc)
        self.run_resume(budget=1,now=now)
        source=fixture.FakeClient();client=self.client(source)
        with mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            resume.run_resumable(self.engine,client,self.scope,self.state,today=date(2024,2,3),now=now+timedelta(days=1))
        self.assertTrue(all(p['publishedDateTo']=='2024-02-02' for path,p in source.calls if path.startswith('getDa')))

    def test_scheduler_waits_without_calling_job(self):
        self.run_resume(budget=1,now=datetime.now(timezone.utc))
        with mock.patch.object(scheduler,'un_refresh_job') as run:
            self.assertEqual(scheduler.un_resume_job(self.state),'DEFERRED');run.assert_not_called()

    def test_scheduler_dispatches_due_retry(self):
        self.run_resume(budget=1,now=datetime.now(timezone.utc)-timedelta(minutes=10))
        with mock.patch.object(scheduler,'un_refresh_job',return_value='SUCCESS') as run:
            self.assertEqual(scheduler.un_resume_job(self.state),'SUCCESS');run.assert_called_once()

    def test_budget_releases_lock_and_does_not_write_partial_scope(self):
        self.run_resume(budget=5)
        # 목록 4회 + 첫 품목 1회까지만 수집. 해당 국가의 트랜잭션은 아직 시작하지 않습니다.
        self.assertEqual(self.snapshot(),[])
        from filelock import FileLock
        with FileLock(str(self.state/'refresh.lock'),timeout=0):pass
        with FileLock(str(self.state/'continuation.lock'),timeout=0):pass

    def test_successful_scope_is_not_refetched_after_budget_pause(self):
        first=self.client();result=self.run_resume(first,budget=6)
        self.assertEqual(result['reason'],'budget')
        self.assertEqual({row[0] for row in self.snapshot()},{'A'})
        source=fixture.FakeClient();second=self.client(source)
        result=self.run_resume(second,now=datetime(2024,2,2,tzinfo=timezone.utc)+timedelta(minutes=5))
        self.assertEqual(result['status'],'DB_VERIFIED')
        self.assertEqual(result['skipped'],1)
        self.assertTrue(all('/M/' in path for path,p in source.calls))

    def test_invalid_country_does_not_block_valid_country_in_batch(self):
        cfg=json.loads(self.scope.read_text());cfg['reporters']={'M':['8','40'],'A':[]}
        self.scope.write_text(json.dumps(cfg),encoding='utf-8')
        import hashlib
        checkpoint=json.loads(self.checkpoint());checkpoint['scope_sha256']=hashlib.sha256(self.scope.read_bytes()).hexdigest();job.save(self.state/'checkpoint.json',checkpoint)
        source=fixture.FakeClient([fixture.catalog(),fixture.catalog(reporter=40)],
                                  [fixture.raw(),fixture.raw(reporter=40,amount='-1')])
        with self.assertRaises(ValueError):self.run_resume(self.client(source))
        self.assertEqual({row[2] for row in self.snapshot()},{'AL'})
        self.assertEqual(resume.read_state(self.state)['status'],'FAILED')
        source=fixture.FakeClient([fixture.catalog(),fixture.catalog(reporter=40)],
                                  [fixture.raw(),fixture.raw(reporter=40)])
        result=self.run_resume(self.client(source))
        self.assertEqual(result['succeeded'],1);self.assertEqual(result['skipped'],1)
        self.assertEqual({row[2] for row in self.snapshot()},{'AL','AT'})

    def test_server_long_retry_after_is_not_shortened(self):
        client=self.client();client.session.get.return_value=None
        client.session.get.side_effect=lambda *a,**kw:mock.Mock(status_code=429,headers={'Retry-After':'10800'})
        now=datetime(2024,2,2,tzinfo=timezone.utc);result=self.run_resume(client,now=now)
        self.assertEqual(datetime.fromisoformat(result['next_attempt_at'])-now,timedelta(hours=3))
        self.assertEqual(client.request_count,1)

    def test_future_window_is_scheduled_after_old_cycle_completes(self):
        self.run_resume(budget=1)
        now=datetime(2024,2,3,tzinfo=timezone.utc)
        with mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            result=resume.run_resumable(self.engine,self.client(),self.scope,self.state,today=now.date(),now=now)
        self.assertEqual(result['status'],'DB_VERIFIED')
        state=resume.read_state(self.state)
        self.assertEqual((state['published_from'],state['published_to'],state['reason']),('2024-02-02','2024-02-03','catch_up'))

    def test_due_scheduler_executes_in_real_executor(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        import threading
        self.run_resume(budget=1,now=datetime.now(timezone.utc)-timedelta(minutes=10))
        done=threading.Event();results=[]
        s=BackgroundScheduler()
        def invoke():
            results.append(scheduler.un_resume_job(self.state));done.set()
        with mock.patch.object(scheduler,'un_refresh_job',return_value='SUCCESS'):
            s.add_job(invoke,'date',run_date=datetime.now(timezone.utc));s.start()
            try:self.assertTrue(done.wait(3))
            finally:s.shutdown()
        self.assertEqual(results,['SUCCESS'])

    def test_production_budget_counts_exactly_300_http_attempts(self):
        from backend.app.scheduler.collectors.un_data.continuation import RequestBudgetExceeded
        client=self.client();client.request_limit=resume.CALL_BUDGET
        with mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            for i in range(300):
                client.get('getDa/C/A/HS',{'period':'2023'},self.path/'response.json')
            with self.assertRaises(RequestBudgetExceeded):
                client.get('getDa/C/A/HS',{'period':'2023'},self.path/'response.json')
        self.assertEqual(client.request_count,300)
        self.assertEqual(client.session.get.call_count,300)

    def test_api_failure_after_success_resumes_only_unfinished_scope(self):
        first=self.client();normal=first.session.get.side_effect
        def fail_monthly(url,**kwargs):
            if '/get/C/M/' in url:return mock.Mock(status_code=503,headers={})
            return normal(url,**kwargs)
        first.session.get.side_effect=fail_monthly
        result=self.run_resume(first)
        self.assertEqual(result['reason'],'api')
        self.assertEqual({row[0] for row in self.snapshot()},{'A'})
        source=fixture.FakeClient();second=self.client(source)
        result=self.run_resume(second,now=datetime(2024,2,2,2,tzinfo=timezone.utc))
        self.assertEqual((result['succeeded'],result['skipped']),(1,1))
        self.assertEqual(len(source.calls),2)
        self.assertTrue(all('/M/' in path and not path.startswith('getDa') for path,p in source.calls))

    def test_diagnostic_storage_error_is_not_retried_as_429(self):
        from backend.app.scheduler.collectors.un_data.api import ApiRequestError
        client=self.client()
        with mock.patch.object(client,'get',side_effect=ApiRequestError('disk',kind='storage',status_code=429)):
            result=self.run_resume(client)
        self.assertEqual(result['status'],'FAILED')
        self.assertIsNone(result['next_attempt_at'])

    def test_duplicate_country_in_batch_preserves_other_country(self):
        cfg=json.loads(self.scope.read_text());cfg['reporters']={'M':['8','40'],'A':[]}
        self.scope.write_text(json.dumps(cfg),encoding='utf-8')
        import hashlib
        checkpoint=json.loads(self.checkpoint());checkpoint['scope_sha256']=hashlib.sha256(self.scope.read_bytes()).hexdigest()
        job.save(self.state/'checkpoint.json',checkpoint)
        rows=[fixture.raw(reporter=40),fixture.raw(reporter=40,partner=410),
              fixture.raw(),fixture.raw(partner=410),fixture.raw()]
        with self.assertRaises(ValueError):
            self.run_resume(self.client(fixture.FakeClient([fixture.catalog(),fixture.catalog(reporter=40)],rows)))
        self.assertEqual({row[2] for row in self.snapshot()},{'AT'})
        result=self.run_resume(self.client(fixture.FakeClient(
            [fixture.catalog(),fixture.catalog(reporter=40)],[fixture.raw(),fixture.raw(reporter=40)])))
        self.assertEqual((result['succeeded'],result['skipped']),(1,1))

    def test_budget_boundary_preserves_api_error_and_retry_limit(self):
        import requests
        original_state=self.state;checkpoint=json.loads(self.checkpoint())
        for status in (408,429,503,'network'):
            with self.subTest(status=status):
                self.state=original_state/str(status)
                job.save(self.state/'checkpoint.json',checkpoint)
                now=datetime(2024,2,2,tzinfo=timezone.utc)
                for attempt in range(4):
                    client=self.client(status=503 if status=='network' else status)
                    if status=='network':client.session.get.side_effect=requests.ConnectionError('test')
                    result=self.run_resume(client,now=now,budget=2)
                    self.assertEqual(result['reason'],'api')
                    self.assertEqual(result['http_status'],None if status=='network' else status)
                    self.assertEqual(client.request_count,2)
                    self.assertEqual(result['api_resumes'],attempt)
                    if attempt<3:
                        self.assertEqual(datetime.fromisoformat(result['next_attempt_at'])-now,timedelta(hours=2))
                    else:self.assertEqual(result['status'],'RETRY_EXHAUSTED')
                    now+=timedelta(hours=2)

    def test_300_call_boundary_keeps_503_without_extra_request(self):
        from backend.app.scheduler.collectors.un_data.api import ApiRequestError
        client=self.client(status=503);client.request_count=298;client.request_limit=300
        with mock.patch('backend.app.scheduler.collectors.un_data.api.time.sleep'):
            with self.assertRaises(ApiRequestError) as caught:
                client.get('getDa/C/M/HS',{},self.path/'response.json')
        self.assertEqual((caught.exception.kind,caught.exception.status_code,caught.exception.attempts),('http',503,2))
        self.assertEqual(client.request_count,300)
        self.assertEqual(client.session.get.call_count,2)
