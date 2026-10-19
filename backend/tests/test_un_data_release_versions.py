"""성공 발표 버전의 영속화·재처리 방지·새 버전 갱신을 검증합니다."""
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler.jobs import un_data_load as loader

class ReleaseVersionsTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    run_job=fixture.MonthlyRefreshTest.run_job
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def state_data(self):
        return json.loads((self.state/'pending_datasets.json').read_text(encoding='utf-8'))

    def quota_run(self):
        client=fixture.FakeClient();get=client.get
        def fail_monthly(path,params,out):
            if not path.startswith('getDa') and '/M/' in path:raise job.ApiRequestError('UN API HTTP 429',kind='http',status_code=429)
            return get(path,params,out)
        with mock.patch.object(client,'get',side_effect=fail_monthly):
            with self.assertRaises(RuntimeError):self.run_job(client)

    def test_quota_retry_skips_successful_annual_and_finishes_monthly(self):
        before=self.checkpoint();self.quota_run()
        self.assertEqual(before,self.checkpoint())
        state=self.state_data();self.assertEqual(set(state['completed_versions']),{'A_2023_40'})
        self.assertEqual(set(state['datasets']),{'M_202401_8'})
        client=fixture.FakeClient();result=self.run_job(client)
        self.assertEqual((result['succeeded'],result['skipped']),(1,1))
        self.assertTrue(all('/M/' in path for path,_ in client.calls if not path.startswith('getDa')))
        self.assertEqual(self.state_data()['datasets'],{})
        self.assertEqual(len(self.snapshot()),3)
        self.assertEqual(json.loads(self.checkpoint())['last_successful_check_date'],'2024-02-02')

    def test_repeated_quota_never_refetches_successful_scope(self):
        self.quota_run();client=fixture.FakeClient();get=client.get;data_calls=[]
        def fail(path,params,out):
            if not path.startswith('getDa'):
                data_calls.append(path);raise job.ApiRequestError('UN API HTTP 429',kind='http',status_code=429)
            return get(path,params,out)
        with mock.patch.object(client,'get',side_effect=fail):
            with self.assertRaises(RuntimeError):self.run_job(client)
        self.assertEqual(len(data_calls),1);self.assertIn('/M/',data_calls[0])

    def test_same_release_skips_data_requests_after_success(self):
        datasets=[fixture.catalog(released='2024-02-02'),fixture.catalog('A',2023,40,released='2024-02-02')]
        self.run_job(fixture.FakeClient(datasets));before=self.snapshot();client=fixture.FakeClient(datasets)
        result=self.run_job(client)
        self.assertEqual((result['succeeded'],result['skipped']),(0,2))
        self.assertTrue(all(path.startswith('getDa') for path,_ in client.calls))
        self.assertEqual(before,self.snapshot())

    def test_new_time_updates_but_old_edition_is_excluded(self):
        self.run_job();dataset=fixture.catalog(released='2024-02-02')
        dataset['lastReleased']='2024-02-02T13:00:00'
        result=self.run_job(fixture.FakeClient([dataset],[fixture.raw(amount='200')]))
        self.assertEqual(result['succeeded'],1)
        dataset=dict(dataset,lastReleased='2024-02-02T14:00:00')
        self.assertEqual(self.run_job(fixture.FakeClient([dataset],[fixture.raw(amount='300')]))['succeeded'],1)
        dataset=dict(dataset,classificationCode='H2',lastReleased='2024-02-02T15:00:00')
        self.assertEqual(self.run_job(fixture.FakeClient([dataset],[fixture.raw(edition='H2')]))['succeeded'],0)
        self.assertEqual(next(r[4] for r in self.snapshot() if r[0]=='M'),'HS2022')

    def test_success_state_write_failure_replays_committed_scope(self):
        before=self.checkpoint();save=job.save
        def fail(path,obj):
            if Path(path).name=='pending_datasets.json' and obj.get('completed_versions'):raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=fail):
            with self.assertRaises(OSError):self.run_job()
        self.assertEqual(len(self.snapshot()),1)
        self.assertEqual(self.state_data()['completed_versions'],{})
        self.assertEqual(before,self.checkpoint())
        result=self.run_job(fixture.FakeClient(datasets=[]))
        self.assertEqual(result['succeeded'],2);self.assertEqual(len(self.snapshot()),3)

    def test_checkpoint_failure_retains_success_and_skips_reprocessing(self):
        save=job.save;before=self.checkpoint()
        def fail(path,obj):
            if Path(path).name=='checkpoint.json':raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=fail):
            with self.assertRaises(OSError):self.run_job()
        self.assertEqual(before,self.checkpoint())
        self.assertEqual(len(self.state_data()['completed_versions']),2)
        self.assertEqual(self.run_job()['skipped'],2)
        self.assertNotEqual(before,self.checkpoint())

    def test_failed_verification_never_marks_success(self):
        with mock.patch.object(loader,'verify',side_effect=ValueError('bad DB result')):
            with self.assertRaises(ValueError):self.run_job()
        self.assertEqual(self.state_data()['completed_versions'],{})
        self.assertEqual(len(self.state_data()['datasets']),2)
        self.assertEqual(self.snapshot(),[])

    def test_old_journal_without_completed_versions_is_supported(self):
        self.quota_run();state=self.state_data();del state['completed_versions']
        job.save(self.state/'pending_datasets.json',state)
        result=self.run_job()
        self.assertEqual(result['succeeded'],2)

    def test_foreign_or_invalid_success_state_rejected_before_api(self):
        self.quota_run();original=self.state_data()
        for change in ({'target_sha256':'other DB'},{'scope_sha256':'other scope'},{'completed_versions':{'A_2023_40':['H6','invalid']}}):
            job.save(self.state/'pending_datasets.json',{**original,**change})
            with mock.patch.object(job,'discover') as discover:
                with self.assertRaises(ValueError):self.run_job()
            discover.assert_not_called()

    def test_old_success_removed_before_new_version_can_commit(self):
        self.run_job();save=job.save
        def fail(path,obj):
            if Path(path).name=='manifest.json' and obj.get('status')=='DB_VERIFIED':raise OSError('record failed')
            return save(path,obj)
        dataset=fixture.catalog(released='2024-02-03')
        with mock.patch.object(job,'save',side_effect=fail):
            with self.assertRaises(OSError):self.run_job(fixture.FakeClient([dataset],[fixture.raw(amount='200')]),date(2024,2,4))
        self.assertNotIn('M_202401_8',self.state_data()['completed_versions'])
        self.assertIn('M_202401_8',self.state_data()['datasets'])
        self.assertEqual(self.run_job(fixture.FakeClient([], [fixture.raw(amount='200')]),date(2024,2,4))['succeeded'],1)

if __name__=='__main__':unittest.main()
