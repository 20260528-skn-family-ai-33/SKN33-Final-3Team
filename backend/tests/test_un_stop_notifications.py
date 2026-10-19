"""경고 중복 억제는 알림만 바꾸며 취소·수동 재개 조건에는 영향을 주지 않습니다."""
import tempfile
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock
from filelock import FileLock, Timeout
from backend.app.scheduler import main as scheduler
from backend.app.scheduler.jobs import un_data_refresh as job, un_data_resume as resume


class StopNotificationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        patcher=mock.patch.dict(scheduler.UN_STOP_NOTICES,clear=True)
        patcher.start();self.addCleanup(patcher.stop)
        self.stop()

    def stop(self, token='attempt-1', status='ACTIVE', folder=None):
        folder=folder or self.folder
        job.save(folder/'checkpoint.json',{})
        job.save(folder/resume.RECOVERY_FENCE,{'attempt':token})
        job.save(folder/resume.RECOVERY_GUARD,{'reason':'execution_not_finalized'})
        job.save(folder/'continuation.json',{'status':status})

    def release(self):
        (self.folder/resume.RECOVERY_GUARD).unlink()
        job.save(self.folder/resume.RECOVERY_RELEASE,{'attempt':'attempt-1'})
        job.save(self.folder/'continuation.json',{'status':'COMPLETE'})

    def test_1440_polls_warn_once_and_never_resume_or_change_state(self):
        before={p.name:p.read_bytes() for p in self.folder.glob('*.json')}
        with mock.patch.object(scheduler.log,'warning') as warning, mock.patch.object(job,'execute_once') as execute:
            for _ in range(1440):
                self.assertEqual(scheduler.un_resume_job(self.folder),'RECOVERY_REQUIRED')
            self.assertEqual(warning.call_count,1);execute.assert_not_called()
            message=warning.call_args.args[0] % warning.call_args.args[1:]
            self.assertIn(str(self.folder),message)
            self.assertIn('--resume-now',message)
            self.assertIn('취소 기록 저장 실패 가능',message)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.folder.glob('*.json')})

    def test_startup_monthly_and_poll_share_notice(self):
        self.stop(status='CANCELLED')
        with mock.patch.object(scheduler.log,'warning') as warning, \
             mock.patch.object(job,'execute_once',return_value={'status':'CANCELLED'}):
            scheduler.un_resume_job(self.folder)
            self.assertEqual(scheduler.un_refresh_job(self.folder),'CANCELLED')
            scheduler.un_refresh_job(self.folder);scheduler.un_resume_job(self.folder)
            self.assertEqual(warning.call_count,1)

    def test_changed_cause_or_attempt_warns_again(self):
        with mock.patch.object(scheduler.log,'warning') as warning:
            scheduler.un_resume_job(self.folder)
            self.stop(status='CANCELLED');scheduler.un_resume_job(self.folder)
            self.stop(token='attempt-2',status='CANCELLED');scheduler.un_resume_job(self.folder)
            scheduler.un_resume_job(self.folder)
            self.assertEqual(warning.call_count,3)

    def test_separate_folders_have_separate_notices(self):
        other=self.folder/'other';self.stop(folder=other)
        with mock.patch.object(scheduler.log,'warning') as warning:
            for folder in (self.folder,other,self.folder,other):scheduler.un_resume_job(folder)
            self.assertEqual(warning.call_count,2)

    def test_recovery_reports_once_and_next_stop_warns(self):
        with mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(scheduler.log,'info') as info:
            scheduler.un_resume_job(self.folder)
            self.release()
            self.assertEqual(scheduler.un_resume_job(self.folder),'NO_RETRY')
            scheduler.un_resume_job(self.folder)
            self.assertEqual(info.call_count,1)
            self.assertIn('갱신 성공 여부',info.call_args.args[0])
            self.stop();scheduler.un_resume_job(self.folder)
            self.assertEqual(warning.call_count,2)

    def test_running_process_never_reports_stopped(self):
        with FileLock(str(self.folder/'continuation.lock')), \
             mock.patch.object(scheduler.log,'warning') as warning:
            self.assertEqual(scheduler.un_resume_job(self.folder),'ALREADY_RUNNING')
            warning.assert_not_called()

    def test_restart_warns_again_without_releasing_block(self):
        with mock.patch.object(scheduler.log,'warning') as warning:
            scheduler.un_resume_job(self.folder)
            scheduler.UN_STOP_NOTICES.clear()  # 새 프로세스의 빈 알림 메모리와 동일합니다.
            scheduler.un_resume_job(self.folder)
            self.assertEqual(warning.call_count,2)
            self.assertTrue(resume.recovery_blocked(self.folder))

    def test_corrupt_cancel_record_still_warns_once_and_blocks(self):
        (self.folder/'continuation.json').write_text('{',encoding='utf-8')
        with mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(job,'execute_once') as execute:
            for _ in range(3):self.assertEqual(scheduler.un_resume_job(self.folder),'RECOVERY_REQUIRED')
            self.assertEqual(warning.call_count,1);execute.assert_not_called()

    def test_concurrent_notices_are_deduplicated(self):
        with mock.patch.object(scheduler.log,'warning') as warning:
            threads=[threading.Thread(target=scheduler.report_un_status,args=(self.folder,)) for _ in range(10)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=3)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(warning.call_count,1)

    def test_notification_read_failure_does_not_change_success(self):
        with mock.patch.object(job,'execute_once',return_value={'status':'DB_VERIFIED'}), \
             mock.patch.object(resume,'read_state',side_effect=OSError('read failed')):
            self.assertEqual(scheduler.un_refresh_job(self.folder),'SUCCESS')

    def test_unconfirmed_or_cancelled_state_never_reports_recovered(self):
        scheduler.report_un_status(self.folder)
        with mock.patch.object(scheduler.log,'info') as info:
            scheduler.report_un_status(self.folder)
            self.release()
            job.save(self.folder/'continuation.json',{'status':'CANCELLED'})
            scheduler.report_un_status(self.folder)
            (self.folder/'continuation.json').unlink()
            scheduler.report_un_status(self.folder)
            info.assert_not_called()
            self.assertIn(self.folder.resolve(),scheduler.UN_STOP_NOTICES)

    def test_legacy_cancelled_without_guard_is_not_resumed(self):
        (self.folder/resume.RECOVERY_GUARD).unlink()
        (self.folder/resume.RECOVERY_FENCE).unlink()
        job.save(self.folder/'continuation.json',{'status':'CANCELLED'})
        with mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(job,'execute_once') as execute:
            for _ in range(3):self.assertEqual(scheduler.un_resume_job(self.folder),'NO_RETRY')
            self.assertEqual(warning.call_count,1);execute.assert_not_called()

    def test_manual_resume_completed_before_notice_has_no_stale_stop_warning(self):
        scheduler.report_un_status(self.folder)
        def completed(*args):
            self.release()
            return {'status':'CANCELLED'}  # 점검의 과거 결과와 알림 시점의 최신 상태가 다릅니다.
        with mock.patch.object(job,'execute_once',side_effect=completed), \
             mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(scheduler.log,'info') as info:
            self.assertEqual(scheduler.un_refresh_job(self.folder),'CANCELLED')
            self.assertFalse(resume.recovery_blocked(self.folder))
            warning.assert_not_called();self.assertEqual(info.call_count,1)

    def test_manual_resume_running_suppresses_both_stop_and_recovery_notice(self):
        scheduler.report_un_status(self.folder)
        before=dict(scheduler.UN_STOP_NOTICES)
        with FileLock(str(self.folder/'continuation.lock')), \
             mock.patch.object(job,'execute_once',return_value={'status':'CANCELLED'}), \
             mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(scheduler.log,'info') as info:
            scheduler.un_refresh_job(self.folder)
            self.assertEqual(scheduler.report_un_status(self.folder),'ALREADY_RUNNING')
            warning.assert_not_called();info.assert_not_called()
        self.assertEqual(scheduler.UN_STOP_NOTICES,before)

    def test_new_cancel_before_recovery_notice_uses_latest_disk_state(self):
        scheduler.report_un_status(self.folder)
        def new_cancel(*args):
            self.stop(token='attempt-2',status='CANCELLED')
            return {'status':'DB_VERIFIED'}
        with mock.patch.object(job,'execute_once',side_effect=new_cancel), \
             mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(scheduler.log,'info') as info:
            scheduler.un_refresh_job(self.folder)
            self.assertEqual(warning.call_count,1)
            self.assertEqual(warning.call_args.args[1],'CANCELLED')
            info.assert_not_called()
        self.assertTrue(resume.recovery_blocked(self.folder))

    def test_execution_lock_is_held_through_stop_and_recovery_emission(self):
        def assert_locked(*args,**kwargs):
            with self.assertRaises(Timeout):
                with FileLock(str(self.folder/'continuation.lock'),timeout=0):pass
        with mock.patch.object(scheduler.log,'warning',side_effect=assert_locked) as warning:
            scheduler.report_un_status(self.folder);warning.assert_called_once()
        self.release()
        with mock.patch.object(scheduler.log,'info',side_effect=assert_locked) as info:
            scheduler.report_un_status(self.folder);info.assert_called_once()

    def test_real_child_lock_defers_notice_until_process_exits(self):
        code="""import sys,time
from filelock import FileLock
with FileLock(sys.argv[1]):
 print('locked',flush=True)
 time.sleep(30)
"""
        child=subprocess.Popen([sys.executable,'-c',code,str(self.folder/'continuation.lock')],
                               stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            with mock.patch.object(scheduler.log,'warning') as warning:
                self.assertEqual(scheduler.report_un_status(self.folder),'ALREADY_RUNNING')
                self.assertEqual(scheduler.un_resume_job(self.folder),'ALREADY_RUNNING')
                warning.assert_not_called()
                child.terminate();child.wait(timeout=5)
                self.assertEqual(scheduler.un_resume_job(self.folder),'RECOVERY_REQUIRED')
                self.assertEqual(warning.call_count,1)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
            child.stdout.close()
