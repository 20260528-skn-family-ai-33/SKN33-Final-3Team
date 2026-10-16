"""범위별 commit, 재시도 저널, 전체 점검일 전진 조건 (API 모의/SQLite)."""
import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler.jobs import un_data_load as loader


class FailureIsolationTest(unittest.TestCase):
    setUp = fixture.MonthlyRefreshTest.setUp
    tearDown = fixture.MonthlyRefreshTest.tearDown
    run_job = fixture.MonthlyRefreshTest.run_job
    snapshot = fixture.MonthlyRefreshTest.snapshot
    checkpoint = fixture.MonthlyRefreshTest.checkpoint

    def pending(self):
        return json.loads((self.state/'pending_datasets.json').read_text(encoding='utf-8'))

    def partial(self):
        client=fixture.FakeClient(rows=[fixture.raw(amount='250'),fixture.raw('A','2023',40,amount='-1')])
        with self.assertRaises(ValueError):self.run_job(client)

    def test_partial_commits_good_and_retries_missing_from_catalog(self):
        before=self.checkpoint();self.partial()
        self.assertEqual(before,self.checkpoint())
        self.assertEqual([r[0] for r in self.snapshot()],['M'])
        self.assertEqual(set(self.pending()['datasets']),{'A_2023_40'})
        self.assertEqual(self.pending()['datasets']['A_2023_40']['status'],'FAILED')
        manifests=[json.loads(p.read_text()) for p in self.state.glob('runs/*/manifest.json')]
        self.assertEqual(manifests[0]['status'],'PARTIAL_FAILED')
        self.assertEqual((manifests[0]['succeeded'],manifests[0]['failed']),(1,1))
        self.run_job(fixture.FakeClient([], [fixture.raw('A','2023',40)]))
        self.assertEqual({r[0] for r in self.snapshot()},{'M','A'})
        self.assertEqual(self.pending()['datasets'],{})
        self.assertEqual(json.loads(self.checkpoint())['last_successful_check_date'],'2024-02-02')

    def test_retry_and_new_dataset_and_latest_edition(self):
        self.partial()
        client=fixture.FakeClient([fixture.catalog('A',2023,40,'H2'),fixture.catalog(period=202402)],
            [fixture.raw('A','2023',40,edition='H2'),fixture.raw(period='202402')])
        self.run_job(client)
        self.assertEqual(len(self.snapshot()),3)
        self.assertEqual(next(r[4] for r in self.snapshot() if r[0]=='A'),'HS2002')
        self.assertEqual(self.pending()['datasets'],{})

    def test_db_verification_failure_rolls_back_only_one_scope(self):
        self.run_job();before=self.snapshot();checkpoint=self.checkpoint();verify=loader.verify
        def selective(session,rows,*args):
            if rows and rows[0][loader.HEAD[0]]=='A':raise ValueError('annual verify failed')
            return verify(session,rows,*args)
        client=fixture.FakeClient([fixture.catalog(released='2024-02-03'),fixture.catalog('A',2023,40,released='2024-02-03')],
            [fixture.raw(amount='300'),fixture.raw('A','2023',40,amount='400')])
        with mock.patch.object(loader,'verify',side_effect=selective):
            with self.assertRaisesRegex(ValueError,'annual verify'):self.run_job(client,date(2024,2,4))
        self.assertEqual([r for r in before if r[0]=='A'],[r for r in self.snapshot() if r[0]=='A'])
        self.assertEqual(next(r[5] for r in self.snapshot() if r[0]=='M'),300)
        self.assertEqual(checkpoint,self.checkpoint())

    def test_auth_quota_stops_all_scopes(self):
        checkpoint=self.checkpoint()
        with mock.patch.object(job,'process_dataset',side_effect=RuntimeError('HTTP 429')) as process:
            with self.assertRaises(RuntimeError):self.run_job()
        self.assertEqual(process.call_count,1)
        self.assertEqual(len(self.pending()['datasets']),2)
        self.assertEqual(checkpoint,self.checkpoint())

    def test_intent_write_failure_prevents_db_changes(self):
        save=job.save
        def fail(path,obj):
            if Path(path).name=='pending_datasets.json':raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=fail),mock.patch.object(job,'process_dataset') as process:
            with self.assertRaises(OSError):self.run_job()
        process.assert_not_called();self.assertEqual(self.snapshot(),[])

    def test_interrupt_after_commit_keeps_retry_and_replay_is_idempotent(self):
        save=job.save;checkpoint=self.checkpoint()
        def interrupt(path,obj):
            if Path(path).name=='manifest.json' and obj.get('status')=='DB_VERIFIED':raise KeyboardInterrupt()
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_job()
        self.assertEqual(len(self.snapshot()),1)
        self.assertEqual(len(self.pending()['datasets']),2)
        self.assertEqual(checkpoint,self.checkpoint())
        self.run_job(fixture.FakeClient(datasets=[]))
        self.assertEqual(len(self.snapshot()),3)
        self.assertEqual(self.pending()['datasets'],{})

    def test_journal_clear_failure_after_commit_replays(self):
        save=job.save
        def fail(path,obj):
            if Path(path).name=='pending_datasets.json' and len(obj['datasets'])==1:raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=fail):
            with self.assertRaises(OSError):self.run_job()
        self.assertEqual(len(self.snapshot()),1)
        self.assertEqual(len(self.pending()['datasets']),2)
        self.run_job(fixture.FakeClient(datasets=[]))
        self.assertEqual(len(self.snapshot()),3)

    def test_invalid_journal_rejected_before_processing(self):
        self.partial();pending=self.pending()
        for field,value in [('version',2),('target_sha256','wrong'),('published_from','2000-01-01')]:
            changed=dict(pending);changed[field]=value;job.save(self.state/'pending_datasets.json',changed)
            with mock.patch.object(job,'process_dataset') as process:
                with self.assertRaises(ValueError):self.run_job()
            process.assert_not_called()

    def test_discovery_failure_preserves_pending(self):
        self.partial();before=(self.state/'pending_datasets.json').read_bytes();checkpoint=self.checkpoint()
        with mock.patch.object(job,'discover',side_effect=RuntimeError('HTTP 503')):
            with self.assertRaises(RuntimeError):self.run_job()
        self.assertEqual(before,(self.state/'pending_datasets.json').read_bytes())
        self.assertEqual(checkpoint,self.checkpoint())

    def test_lock_loss_stops_without_advancing(self):
        from backend.app.scheduler.collectors.un_data.locking import Guard
        checkpoint=self.checkpoint()
        with mock.patch.object(Guard,'check') as check:
            def lose(*args):
                check.side_effect=RuntimeError('lock lost')
                raise ValueError('failed')
            with mock.patch.object(job,'process_dataset',side_effect=lose) as process:
                with self.assertRaisesRegex(RuntimeError,'lock lost'):self.run_job()
        self.assertEqual(process.call_count,1)
        self.assertEqual(checkpoint,self.checkpoint())

if __name__=='__main__':unittest.main()
