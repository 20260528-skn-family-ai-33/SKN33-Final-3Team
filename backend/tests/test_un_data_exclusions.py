"""실패 승인·취소·새 발표 격리 통합 회귀 (SQLite/모의 API)."""
import json
from unittest import mock
import unittest
from backend.tests import test_un_data_failure_isolation as isolation
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.jobs.un_data_exclusions import manage
from backend.app.scheduler.jobs import un_data_refresh as job


class ExclusionTest(unittest.TestCase):
    setUp = fixture.MonthlyRefreshTest.setUp
    tearDown = fixture.MonthlyRefreshTest.tearDown
    run_job = fixture.MonthlyRefreshTest.run_job
    snapshot = fixture.MonthlyRefreshTest.snapshot
    checkpoint = fixture.MonthlyRefreshTest.checkpoint
    pending = isolation.FailureIsolationTest.pending
    partial = isolation.FailureIsolationTest.partial

    def approve(self, **kwargs):
        release=self.pending()['datasets']['A_2023_40']['dataset']['lastReleased']
        return manage(self.state, 'approve', key='A_2023_40', release=release,
                      approver=kwargs.get('approver','담당자'), reason=kwargs.get('reason','원천 오류 검토 완료'))

    def test_approval_advances_and_revocation_requeues_outside_catalog(self):
        self.partial(); approval=self.approve()
        result=self.run_job(fixture.FakeClient(datasets=[]))
        self.assertEqual(result['excluded_approved'],1)
        self.assertEqual(self.pending()['completed_versions'].get('A_2023_40'),None)
        manage(self.state,'revoke',key='A_2023_40',release=approval['version'][1],approver='담당자',reason='원천 수정 확인')
        self.assertIn('A_2023_40', self.pending()['datasets'])
        self.run_job(fixture.FakeClient([], [fixture.raw('A','2023',40)]))
        self.assertEqual(len(self.snapshot()),2)
        self.assertEqual(len(self.pending()['exclusion_audit']),2)

    def test_requires_failed_exact_release_and_nonblank_actor_reason(self):
        self.partial()
        for kwargs in ({'reason':' '},{'approver':''}):
            with self.assertRaises(ValueError):self.approve(**kwargs)
        with self.assertRaises(ValueError):
            manage(self.state,'approve',key='A_2023_40',release='2099-01-01',approver='담당자',reason='검토')
        self.assertEqual(manage(self.state)['approved'],{})

    def test_approval_cannot_exclude_new_release(self):
        self.partial();self.approve()
        client=fixture.FakeClient([fixture.catalog('A',2023,40,released='2024-02-02')], [fixture.raw('A','2023',40)])
        result=self.run_job(client)
        self.assertEqual(result['excluded_approved'],0)
        self.assertIn('A_2023_40', self.pending()['completed_versions'])

    def test_approval_save_failure_does_not_remove_pending(self):
        self.partial();before=(self.state/'pending_datasets.json').read_bytes()
        with mock.patch.object(job,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.approve()
        self.assertEqual(before,(self.state/'pending_datasets.json').read_bytes())

    def test_unapproved_failure_still_blocks_checkpoint(self):
        before=self.checkpoint()
        with mock.patch.object(job,'process_dataset',side_effect=ValueError('원천 오류')):
            with self.assertRaises(ValueError):self.run_job()
        self.approve()
        with mock.patch.object(job,'process_dataset',side_effect=ValueError('미승인 오류')):
            with self.assertRaises(ValueError):self.run_job(fixture.FakeClient(datasets=[]))
        self.assertEqual(before,self.checkpoint())
        self.assertEqual(set(self.pending()['datasets']),{'M_202401_8'})

    def test_approval_does_not_bypass_execution_lock(self):
        from filelock import FileLock, Timeout
        self.partial()
        with FileLock(str(self.state/'refresh.lock'),timeout=0):
            with self.assertRaises(Timeout):self.approve()
        self.assertEqual(manage(self.state)['approved'],{})

    # 제외는 이전 정상 데이터를 삭제하거나 실패한 새 버전을 성공으로 바꾸지 않습니다.
    def test_approval_preserves_previously_loaded_rows(self):
        self.run_job()
        before = self.snapshot()
        broken = fixture.FakeClient([fixture.catalog('A', 2023, 40, released='2024-02-02')],
                                    [fixture.raw('A', '2023', 40, amount='-1')])
        with self.assertRaises(ValueError):
            self.run_job(broken)
        self.approve()
        result = self.run_job(fixture.FakeClient([]))
        self.assertEqual(result['excluded_approved'], 1)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('A_2023_40', self.pending()['completed_versions'])

    # 과거 승인을 취소하더라도 이후 성공한 발표·금액으로부터 후퇴하지 않습니다.
    def test_revoke_after_newer_success_does_not_requeue_old_version(self):
        self.partial()
        approval = self.approve()
        newer = fixture.catalog('A', 2023, 40, released='2024-02-02')
        self.run_job(fixture.FakeClient([newer], [fixture.raw('A', '2023', 40, amount='300')]))
        before = self.snapshot()
        manage(self.state, 'revoke', key='A_2023_40', release=approval['version'][1],
               approver='담당자', reason='과거 승인 해제')
        self.assertNotIn('A_2023_40', self.pending()['datasets'])
        self.assertEqual(self.pending()['completed_versions']['A_2023_40'], job.release_version(newer))
        self.run_job(fixture.FakeClient([]))
        self.assertEqual(self.snapshot(), before)

    def test_revoke_preserves_newer_failed_version(self):
        self.partial()
        approval = self.approve()
        newer = fixture.catalog('A', 2023, 40, released='2024-02-02')
        with self.assertRaises(ValueError):
            self.run_job(fixture.FakeClient([newer], [fixture.raw('A', '2023', 40, amount='-1')]))
        manage(self.state, 'revoke', key='A_2023_40', release=approval['version'][1],
               approver='담당자', reason='과거 승인 해제')
        entry = self.pending()['datasets']['A_2023_40']
        self.assertEqual(entry['dataset'], newer)
        self.assertEqual(entry['status'], 'FAILED')

    def test_revoke_save_failure_keeps_approval_and_audit(self):
        self.partial()
        approval = self.approve()
        before = (self.state/'pending_datasets.json').read_bytes()
        with mock.patch.object(job, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                manage(self.state, 'revoke', key='A_2023_40', release=approval['version'][1],
                       approver='담당자', reason='원천 교정 확인')
        self.assertEqual((self.state/'pending_datasets.json').read_bytes(), before)

    def test_catalog_error_cannot_be_approved(self):
        before = self.checkpoint()
        broken = fixture.catalog('A', 2023, 40)
        broken['lastReleased'] = None
        with self.assertRaises(ValueError):
            self.run_job(fixture.FakeClient([broken]))
        self.assertIn('A_2023_40', manage(self.state)['catalog_errors'])
        with self.assertRaisesRegex(ValueError, '현재 실패한 데이터셋'):
            manage(self.state, 'approve', key='A_2023_40', release='2024-02-01',
                   approver='담당자', reason='확인')
        self.assertEqual(self.checkpoint(), before)


from backend.tests import test_un_data_resume as resume_fixture
from datetime import datetime, timezone, timedelta


class ExclusionResumeTest(unittest.TestCase):
    setUp = fixture.MonthlyRefreshTest.setUp
    tearDown = fixture.MonthlyRefreshTest.tearDown
    client = resume_fixture.ResumeTest.client
    run_resume = resume_fixture.ResumeTest.run_resume

    # 제외 승인과 실행 재개 승인은 별개입니다. 취소 표식은 승인 명령이 해제하지 않습니다.
    def test_approval_preserves_cancelled_state_and_blocks_automatic_resume(self):
        from backend.app.scheduler.jobs import un_data_resume as resume
        broken = fixture.FakeClient(rows=[fixture.raw(), fixture.raw('A', '2023', 40, amount='-1')])
        with self.assertRaises(ValueError):
            self.run_resume(self.client(broken))
        with mock.patch.object(job, 'run', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_resume()
        names = ('continuation.json', resume.RECOVERY_FENCE, resume.RECOVERY_GUARD)
        before = {name: (self.state/name).read_bytes() for name in names}
        release = manage(self.state)['failed']['A_2023_40']['dataset']['lastReleased']
        manage(self.state, 'approve', key='A_2023_40', release=release,
               approver='담당자', reason='원천 오류 확인')
        client = self.client()
        self.assertEqual(self.run_resume(client)['status'], 'CANCELLED')
        self.assertEqual(client.request_count, 0)
        self.assertEqual({name: (self.state/name).read_bytes() for name in names}, before)
        result = self.run_resume(resume_now=True)
        self.assertEqual(result['status'], 'DB_VERIFIED')
        self.assertEqual(result['excluded_approved'], 1)

    def test_revoke_refetches_failed_response_and_preserves_fences(self):
        broken=fixture.FakeClient(rows=[fixture.raw(),fixture.raw('A','2023',40,amount='-1')])
        with self.assertRaises(ValueError):self.run_resume(self.client(broken))
        listing=manage(self.state)
        release=listing['failed']['A_2023_40']['dataset']['lastReleased']
        manage(self.state,'approve',key='A_2023_40',release=release,approver='담당자',reason='검토 완료')
        manage(self.state,'revoke',key='A_2023_40',release=release,approver='담당자',reason='원천 교정 확인')
        source=fixture.FakeClient()
        result=self.run_resume(self.client(source))
        self.assertEqual(result['status'],'DB_VERIFIED')
        self.assertTrue(any('/A/' in path and not path.startswith('getDa') for path,_ in source.calls))

    def test_quota_resume_and_auth_403_separated(self):
        now=datetime(2024,2,2,tzinfo=timezone.utc)
        client=self.client()
        client.session.get.side_effect=lambda *a,**kw:mock.Mock(status_code=403,headers={'Retry-After':'10800'},
            text=json.dumps({'statusCode':403,'message':'Out of call volume quota. Quota will be replenished in 03:00:00.'}))
        result=self.run_resume(client,now=now)
        self.assertEqual(result['status'],'WAITING')
        self.assertEqual(datetime.fromisoformat(result['next_attempt_at'])-now,timedelta(hours=3))
        auth=self.client(status=403)
        result=self.run_resume(auth,now=now+timedelta(hours=3))
        self.assertEqual(result['status'],'FAILED')
        self.assertIsNone(result['next_attempt_at'])

    def test_new_release_survives_old_approved_batch_plan(self):
        broken=fixture.FakeClient(rows=[fixture.raw(),fixture.raw('A','2023',40,amount='-1')])
        with self.assertRaises(ValueError):self.run_resume(self.client(broken))
        release=manage(self.state)['failed']['A_2023_40']['dataset']['lastReleased']
        manage(self.state,'approve',key='A_2023_40',release=release,approver='담당자',reason='검토 완료')
        source=fixture.FakeClient([fixture.catalog('A',2023,40,released='2024-02-02')], [fixture.raw('A','2023',40)])
        result=self.run_resume(self.client(source))
        self.assertEqual(result['status'],'DB_VERIFIED')
        self.assertEqual(result['excluded_approved'],0)
        self.assertEqual(result['succeeded'],1)

    def test_extreme_wait_requires_manual_review_without_shortening(self):
        client=self.client()
        client.session.get.side_effect=lambda *a,**kw:mock.Mock(status_code=429,headers={'Retry-After':str(32*86400)})
        result=self.run_resume(client)
        self.assertEqual(result['status'],'RETRY_EXHAUSTED')
        self.assertIsNone(result['next_attempt_at'])
        self.assertEqual(client.request_count,1)
