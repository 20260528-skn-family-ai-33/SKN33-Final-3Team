"""범위 오류와 기록 저장 오류가 겹쳐도 원래 예외를 유지하며 실행을 중단합니다."""
import json
import unittest
from pathlib import Path
from unittest import mock
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.jobs import un_data_refresh as job

class ErrorRecordingTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    run_job=fixture.MonthlyRefreshTest.run_job
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def fail_record(self,target):
        original=ValueError('ORIGINAL_DATA_ERROR');storage=OSError('RECORD_DISK_FULL')
        before=self.checkpoint();save=job.save
        def fail(path,obj):
            path=Path(path)
            failed= isinstance(obj,dict) and obj.get('status')=='FAILED'
            failed_pending=path.name=='pending_datasets.json' and any(e['status']=='FAILED' for e in obj['datasets'].values())
            if (target=='scope' and failed) or (target=='pending' and failed_pending):raise storage
            if target=='all' and (failed or (isinstance(obj,dict) and obj.get('status')=='FAILED_OR_CHECKPOINT_PENDING')):raise storage
            return save(path,obj)
        with mock.patch.object(job,'process_dataset',side_effect=original) as process, mock.patch.object(job,'save',side_effect=fail),self.assertLogs('un_data_refresh',level='ERROR') as logs:
            with self.assertRaises(ValueError) as caught:self.run_job()
        self.assertIs(caught.exception,original);self.assertIs(caught.exception.__cause__,storage)
        self.assertEqual(process.call_count,1);self.assertEqual(before,self.checkpoint());self.assertEqual(self.snapshot(),[])
        self.assertIn('ORIGINAL_DATA_ERROR','\n'.join(logs.output));self.assertIn('RECORD_DISK_FULL','\n'.join(logs.output))
        if target!='all':
            manifest=json.loads(next(self.state.glob('runs/*/manifest.json')).read_text())
            self.assertEqual(manifest['error'],'ORIGINAL_DATA_ERROR')
            self.assertEqual(manifest['error_type'],'ValueError')
            self.assertEqual(manifest['recording_errors'][0]['error'],'RECORD_DISK_FULL')
        return original

    def test_scope_manifest_failure_keeps_original_and_stops(self):self.fail_record('scope')
    def test_pending_failure_keeps_original_and_stops(self):self.fail_record('pending')
    def test_all_error_files_fail_but_terminal_keeps_both_errors(self):self.fail_record('all')

    def test_summary_failure_keeps_first_data_error(self):
        for target in ('missing_amounts.json','manifest.json'):
            with self.subTest(target=target):
                original=ValueError('ORIGINAL_DATA_ERROR');save=job.save;before=self.checkpoint()
                def fail(path,obj):
                    path=Path(path)
                    is_root=path.parent.parent.name=='runs'
                    if is_root and path.name==target and (target=='missing_amounts.json' or obj.get('status')=='PARTIAL_FAILED'):
                        raise OSError('SUMMARY_DISK_FULL')
                    return save(path,obj)
                with mock.patch.object(job,'process_dataset',side_effect=original),mock.patch.object(job,'save',side_effect=fail),self.assertLogs('un_data_refresh',level='ERROR'):
                    with self.assertRaises(ValueError) as caught:self.run_job()
                self.assertIs(caught.exception,original);self.assertIsInstance(caught.exception.__cause__,OSError)
                self.assertEqual(before,self.checkpoint())

    def test_retry_after_recording_failure_recovers(self):
        self.fail_record('pending')
        result=self.run_job(fixture.FakeClient(datasets=[]))
        self.assertEqual(result['succeeded'],2);self.assertEqual(len(self.snapshot()),3)

    def test_real_validation_failure_remains_primary(self):
        save=job.save;before=self.checkpoint()
        def fail(path,obj):
            if isinstance(obj,dict) and obj.get('status')=='FAILED':raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=fail),self.assertLogs('un_data_refresh',level='ERROR'):
            with self.assertRaisesRegex(ValueError,'금액은 유한한') as caught:
                self.run_job(fixture.FakeClient([fixture.catalog()],[fixture.raw(amount='-1')]))
        self.assertIsInstance(caught.exception.__cause__,OSError)
        self.assertEqual(self.snapshot(),[]);self.assertEqual(before,self.checkpoint())

    def test_db_validation_rollback_survives_record_failure(self):
        from backend.app.scheduler.jobs import un_data_load as loader
        save=job.save;original=ValueError('DB_VERIFY_FAILED');before=self.checkpoint()
        def fail(path,obj):
            if isinstance(obj,dict) and obj.get('status')=='FAILED':raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(loader,'verify',side_effect=original),mock.patch.object(job,'save',side_effect=fail),self.assertLogs('un_data_refresh',level='ERROR'):
            with self.assertRaises(ValueError) as caught:self.run_job()
        self.assertIs(caught.exception,original);self.assertEqual(self.snapshot(),[])
        self.assertEqual(before,self.checkpoint())

    def test_recording_helper_success_preserves_payload(self):
        path=self.path/'error.json';payload={'error':'원래 오류'};manifest={}
        job.save_error_record(path,payload,ValueError('original'),manifest)
        self.assertEqual(json.loads(path.read_text()),payload);self.assertEqual(manifest,{})

if __name__=='__main__':unittest.main()
