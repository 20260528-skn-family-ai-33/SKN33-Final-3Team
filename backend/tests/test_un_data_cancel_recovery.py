"""취소 의도 보존과 실제 프로세스 잠금 해제 후 ACTIVE 복구를 검증합니다."""
import json
import subprocess
import sys
import unittest
from unittest import mock
from backend.tests import test_un_data_resume as fixture
from backend.app.scheduler.jobs import un_data_resume as resume
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler import main as scheduler


class CancelRecoveryTest(unittest.TestCase):
    setUp=fixture.ResumeTest.setUp
    tearDown=fixture.ResumeTest.tearDown
    snapshot=fixture.ResumeTest.snapshot
    checkpoint=fixture.ResumeTest.checkpoint
    client=fixture.ResumeTest.client
    run_resume=fixture.ResumeTest.run_resume

    def test_cancel_requires_explicit_manual_resume(self):
        self.run_resume(budget=6)  # 연간 범위 성공 기록과 월별 미처리 상태를 보존합니다.
        with mock.patch.object(job,'run',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_resume(resume_now=True)
        state=resume.read_state(self.state)
        self.assertEqual(state['status'],'CANCELLED')
        self.assertIsNone(state['next_attempt_at'])
        client=self.client()
        self.assertEqual(self.run_resume(client)['status'],'CANCELLED')
        self.assertEqual(client.request_count,0)
        self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')
        result=self.run_resume(resume_now=True)
        self.assertEqual((result['status'],result['succeeded'],result['skipped']),('DB_VERIFIED',1,1))

    def test_cancel_after_checkpoint_does_not_disappear(self):
        original=job.run
        def finish_then_cancel(*args,**kwargs):
            original(*args,**kwargs)
            raise KeyboardInterrupt
        with mock.patch.object(job,'run',side_effect=finish_then_cancel):
            with self.assertRaises(KeyboardInterrupt):self.run_resume()
        self.assertEqual(self.run_resume()['status'],'CANCELLED')
        self.assertEqual(self.run_resume(resume_now=True)['status'],'DB_VERIFIED')

    def test_cancel_during_completion_save_is_recorded(self):
        original=job.save
        def save(path,value):
            if path.name=='continuation.json' and value.get('status')=='COMPLETE':
                raise KeyboardInterrupt
            return original(path,value)
        with mock.patch.object(job,'save',side_effect=save):
            with self.assertRaises(KeyboardInterrupt):self.run_resume()
        self.assertEqual(resume.read_state(self.state)['status'],'CANCELLED')

    def test_system_exit_is_not_user_cancel(self):
        with mock.patch.object(job,'run',side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):self.run_resume()
        self.assertEqual(resume.read_state(self.state)['status'],'ACTIVE')
        self.assertTrue(resume.active_is_idle(self.state))
        self.assertEqual(self.run_resume()['status'],'RECOVERY_REQUIRED')
        self.assertEqual(self.run_resume(resume_now=True)['status'],'DB_VERIFIED')

    def test_live_process_blocked_then_killed_process_requires_manual_resume(self):
        self.run_resume(budget=6)
        code="""import sys,time,json
from pathlib import Path
from filelock import FileLock
from backend.app.scheduler.jobs.un_data_refresh import save
folder=Path(sys.argv[1])
with FileLock(str(folder/'continuation.lock')):
 state=json.loads((folder/'continuation.json').read_text(encoding='utf-8'))
 state.update(status='ACTIVE',next_attempt_at=None)
 save(folder/'recovery_guard.json',{'reason':'execution_not_finalized'})
 save(folder/'continuation.json',state)
 print('locked',flush=True)
 time.sleep(60)
"""
        child=subprocess.Popen([sys.executable,'-c',code,str(self.state)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            self.assertFalse(resume.active_is_idle(self.state))
            with mock.patch.object(scheduler,'un_refresh_job') as refresh:
                self.assertEqual(scheduler.un_resume_job(self.state),'ALREADY_RUNNING')
                refresh.assert_not_called()
            child.kill();child.wait(timeout=5)
            self.assertTrue(resume.active_is_idle(self.state))
            with mock.patch.object(job,'execute_once') as execute:
                self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')
                execute.assert_not_called()
            self.assertEqual(self.run_resume(resume_now=True)['status'],'DB_VERIFIED')
            self.assertEqual(resume.read_state(self.state)['status'],'COMPLETE')
            self.assertEqual(len(self.snapshot()),3)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
            child.stdout.close()

    def test_scheduler_reports_cancelled_not_success(self):
        with mock.patch.object(job,'execute_once',return_value={'status':'CANCELLED'}):
            self.assertEqual(scheduler.un_refresh_job(self.state),'CANCELLED')

    def test_cancel_between_probe_and_resume_is_respected(self):
        self.run_resume(budget=1)
        state=resume.read_state(self.state);state.update(status='ACTIVE',next_attempt_at=None)
        job.save(self.state/'continuation.json',state)
        client=self.client()
        def cancel_then_run(*args,**kwargs):
            state.update(status='CANCELLED',reason='user_cancelled')
            job.save(self.state/'continuation.json',state)
            return self.run_resume(client)
        with mock.patch.object(job,'execute_once',side_effect=cancel_then_run):
            self.assertEqual(scheduler.un_resume_job(self.state),'CANCELLED')
        self.assertEqual(client.request_count,0)
        self.assertEqual(self.snapshot(),[])

    def test_second_runner_cannot_enter_after_idle_probe(self):
        from filelock import FileLock,Timeout
        self.run_resume(budget=1)
        state=resume.read_state(self.state);state.update(status='ACTIVE',next_attempt_at=None)
        job.save(self.state/'continuation.json',state)
        self.assertTrue(resume.active_is_idle(self.state))
        client=self.client();before=(self.state/'continuation.json').read_bytes()
        with FileLock(str(self.state/'continuation.lock'),timeout=0):
            with self.assertRaises(Timeout):self.run_resume(client)
        self.assertEqual(client.request_count,0)
        self.assertEqual((self.state/'continuation.json').read_bytes(),before)

    def test_real_sigint_records_cancelled_and_releases_lock(self):
        import signal
        from filelock import FileLock
        self.run_resume(budget=1)
        code="""import sys,time
from pathlib import Path
from backend.app.scheduler.jobs.un_data_resume import continuation_lock
try:
 with continuation_lock(Path(sys.argv[1])):
  print('ready',flush=True)
  time.sleep(60)
except KeyboardInterrupt:
 print('cancelled',flush=True)
"""
        child=subprocess.Popen([sys.executable,'-c',code,str(self.state)],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'ready')
            child.send_signal(signal.SIGINT)
            output,_=child.communicate(timeout=5)
            self.assertEqual(child.returncode,0)
            self.assertIn('cancelled',output)
            self.assertEqual(resume.read_state(self.state)['status'],'CANCELLED')
            with FileLock(str(self.state/'continuation.lock'),timeout=0):pass
            self.assertEqual(self.run_resume()['status'],'CANCELLED')
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
            child.stdout.close()


    def test_cancel_save_failure_blocks_restart_until_manual_resume(self):
        original=job.save
        def fail_cancel(path,value):
            if path.name=='continuation.json' and value.get('status')=='CANCELLED':
                raise OSError('disk full')
            return original(path,value)
        with mock.patch.object(job,'save',side_effect=fail_cancel),mock.patch.object(job,'run',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_resume()
        self.assertEqual(resume.read_state(self.state)['status'],'ACTIVE')
        self.assertTrue((self.state/resume.RECOVERY_GUARD).is_file())
        client=self.client()
        self.assertEqual(self.run_resume(client)['status'],'RECOVERY_REQUIRED')
        self.assertEqual(client.request_count,0)
        with mock.patch.object(scheduler,'un_refresh_job') as refresh:
            self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')
            refresh.assert_not_called()
        # 프로세스 메모리를 버린 새 인터프리터에서도 차단 표식이 유지됩니다.
        code="from backend.app.scheduler.main import un_resume_job;import sys;print(un_resume_job(sys.argv[1]))"
        child=subprocess.run([sys.executable,'-c',code,str(self.state)],capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0)
        self.assertEqual(child.stdout.strip(),'RECOVERY_REQUIRED')
        self.assertEqual(self.run_resume(resume_now=True)['status'],'DB_VERIFIED')
        self.assertFalse((self.state/resume.RECOVERY_GUARD).exists())

    def test_guard_storage_failure_prevents_execution(self):
        original=job.save
        def fail_guard(path,value):
            if path.name==resume.RECOVERY_GUARD:raise OSError('disk full')
            return original(path,value)
        with mock.patch.object(job,'save',side_effect=fail_guard),mock.patch.object(job,'run') as run:
            with self.assertRaises(OSError):self.run_resume()
            run.assert_not_called()
        self.assertEqual(self.snapshot(),[])

    def test_cancel_save_failure_after_commit_still_blocks(self):
        original_run=job.run;original_save=job.save
        def finish_then_cancel(*args,**kwargs):
            original_run(*args,**kwargs)
            raise KeyboardInterrupt
        def fail_cancel(path,value):
            if path.name=='continuation.json' and value.get('status')=='CANCELLED':raise OSError('disk full')
            return original_save(path,value)
        with mock.patch.object(job,'run',side_effect=finish_then_cancel),mock.patch.object(job,'save',side_effect=fail_cancel):
            with self.assertRaises(KeyboardInterrupt):self.run_resume()
        self.assertEqual(len(self.snapshot()),3)
        client=self.client()
        self.assertEqual(self.run_resume(client)['status'],'RECOVERY_REQUIRED')
        self.assertEqual(client.request_count,0)
        self.assertTrue((self.state/resume.RECOVERY_GUARD).exists())

    def check_finalization_interrupt(self,after_delete,save_failure,body_failure):
        from pathlib import Path
        original_unlink=Path.unlink;original_save=job.save
        primary=ValueError('original data failure')
        def unlink(path,*args,**kwargs):
            if path.name==resume.RECOVERY_GUARD:
                if after_delete:original_unlink(path,*args,**kwargs)
                raise KeyboardInterrupt('during guard removal')
            return original_unlink(path,*args,**kwargs)
        def save(path,value):
            if save_failure and path.name=='continuation.json' and value.get('status')=='CANCELLED':
                raise OSError('cancel record unavailable')
            return original_save(path,value)
        from contextlib import nullcontext
        with mock.patch.object(Path,'unlink',unlink),mock.patch.object(job,'save',side_effect=save):
            with mock.patch.object(job,'run',side_effect=primary) if body_failure else nullcontext():
                with self.assertRaises(ValueError if body_failure else KeyboardInterrupt) as caught:
                    self.run_resume(budget=1)
                if body_failure:self.assertIs(caught.exception,primary)
        self.assertTrue(resume.recovery_blocked(self.state))
        self.assertEqual((self.state/resume.RECOVERY_GUARD).exists(),not after_delete)
        self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')
        client=self.client();result=self.run_resume(client)
        self.assertIn(result['status'],('CANCELLED','RECOVERY_REQUIRED'))
        self.assertEqual(client.request_count,0)
        self.assertEqual(self.run_resume(resume_now=True)['status'],'DB_VERIFIED')
        self.assertFalse(resume.recovery_blocked(self.state))

    def test_cancel_before_delete(self):self.check_finalization_interrupt(False,False,False)
    def test_cancel_after_delete(self):self.check_finalization_interrupt(True,False,False)
    def test_cancel_before_delete_and_save_failure(self):self.check_finalization_interrupt(False,True,False)
    def test_cancel_after_delete_and_save_failure(self):self.check_finalization_interrupt(True,True,False)
    def test_body_failure_cancel_before_delete(self):self.check_finalization_interrupt(False,False,True)
    def test_body_failure_cancel_after_delete(self):self.check_finalization_interrupt(True,False,True)
    def test_body_failure_cancel_before_delete_and_save_failure(self):self.check_finalization_interrupt(False,True,True)
    def test_body_failure_cancel_after_delete_and_save_failure(self):self.check_finalization_interrupt(True,True,True)

    def test_release_record_failure_retains_fence_across_restart(self):
        original=job.save
        def save(path,value):
            if path.name.startswith(resume.RECOVERY_RELEASE+'.prepare.'):raise OSError('release record unavailable')
            return original(path,value)
        with mock.patch.object(job,'save',side_effect=save):
            with self.assertRaises(OSError):self.run_resume(budget=1)
        self.assertFalse((self.state/resume.RECOVERY_GUARD).exists())
        self.assertTrue(resume.recovery_blocked(self.state))
        code="from backend.app.scheduler.main import un_resume_job;import sys;print(un_resume_job(sys.argv[1]))"
        child=subprocess.run([sys.executable,'-c',code,str(self.state)],capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0)
        self.assertEqual(child.stdout.strip(),'RECOVERY_REQUIRED')

    def test_previous_release_cannot_unlock_new_attempt(self):
        self.run_resume(budget=1)
        self.assertFalse(resume.recovery_blocked(self.state))
        old=json.loads((self.state/resume.RECOVERY_RELEASE).read_text())
        with mock.patch.object(job,'run',side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):self.run_resume(resume_now=True)
        (self.state/resume.RECOVERY_GUARD).unlink()
        self.assertNotEqual(json.loads((self.state/resume.RECOVERY_FENCE).read_text()),old)
        self.assertTrue(resume.recovery_blocked(self.state))

    def check_publish_boundary(self,after_commit,cancel_save_failure,body_failure=False):
        original_replace=job.os.replace;original_save=job.save
        primary=ValueError('original failure before finalization')
        cancel_writes=[]
        def replace(source,destination):
            if str(destination).endswith('/'+resume.RECOVERY_RELEASE):
                if after_commit:original_replace(source,destination)
                raise KeyboardInterrupt('publish boundary')
            return original_replace(source,destination)
        def save(path,value):
            if path.name=='continuation.json' and value.get('status')=='CANCELLED':
                cancel_writes.append(True)
                if cancel_save_failure:raise OSError('cancel disk full')
            return original_save(path,value)
        from contextlib import nullcontext
        with mock.patch.object(job.os,'replace',side_effect=replace),mock.patch.object(job,'save',side_effect=save):
            with mock.patch.object(job,'run',side_effect=primary) if body_failure else nullcontext():
                with self.assertRaises(ValueError if body_failure else KeyboardInterrupt) as caught:
                    self.run_resume(budget=1)
                if body_failure:self.assertIs(caught.exception,primary)
        self.assertEqual(resume.recovery_blocked(self.state),not after_commit)
        if after_commit:
            self.assertEqual(cancel_writes,[])
            self.assertEqual(resume.read_state(self.state)['status'],'FAILED' if body_failure else 'WAITING')
        else:
            self.assertEqual(len(cancel_writes),1)
            self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')

    def test_publish_before_commit_cancel(self):self.check_publish_boundary(False,False)
    def test_publish_before_commit_cancel_and_disk_failure(self):self.check_publish_boundary(False,True)
    def test_publish_after_commit_interrupt_keeps_result(self):self.check_publish_boundary(True,False)
    def test_publish_after_commit_does_not_attempt_failing_cancel_write(self):self.check_publish_boundary(True,True)
    def test_body_error_preserved_before_publish_cancel(self):self.check_publish_boundary(False,True,True)
    def test_body_error_preserved_after_publish_interrupt(self):self.check_publish_boundary(True,True,True)

    def test_old_release_temp_cleanup_repro_is_before_commit_now(self):
        from pathlib import Path
        original_unlink=Path.unlink;original_save=job.save
        def unlink(path,*args,**kwargs):
            if path.name.startswith(resume.RECOVERY_RELEASE+'.') and path.suffix=='.tmp':
                raise KeyboardInterrupt('old release cleanup reproduction')
            return original_unlink(path,*args,**kwargs)
        def save(path,value):
            if path.name=='continuation.json' and value.get('status')=='CANCELLED':raise OSError('disk full')
            return original_save(path,value)
        with mock.patch.object(Path,'unlink',unlink),mock.patch.object(job,'save',side_effect=save):
            with self.assertRaises(KeyboardInterrupt):self.run_resume(budget=1)
        self.assertTrue(resume.recovery_blocked(self.state))
        self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')


    def check_unreadable_commit_receipt(self,after_commit,second_interrupt,body_failure):
        from pathlib import Path
        from contextlib import nullcontext
        original_replace=job.os.replace;original_read=Path.read_text
        primary=ValueError('original body failure')
        first_interrupt=KeyboardInterrupt('first interrupt')
        def replace(source,destination):
            if str(destination).endswith('/'+resume.RECOVERY_RELEASE):
                if after_commit:original_replace(source,destination)
                raise first_interrupt
            return original_replace(source,destination)
        def read(path,*args,**kwargs):
            if path.name==resume.RECOVERY_RELEASE:
                if second_interrupt:raise KeyboardInterrupt('second interrupt')
                raise OSError('receipt read unavailable')
            return original_read(path,*args,**kwargs)
        with mock.patch.object(job.os,'replace',side_effect=replace),mock.patch.object(Path,'read_text',read):
            with mock.patch.object(job,'run',side_effect=primary) if body_failure else nullcontext():
                with self.assertRaises(ValueError if body_failure else KeyboardInterrupt) as caught:
                    self.run_resume(budget=1)
                self.assertIs(caught.exception,primary if body_failure else first_interrupt)
        self.assertEqual(resume.recovery_blocked(self.state),not after_commit)
        self.assertEqual(resume.read_state(self.state)['status'],'FAILED' if body_failure else 'WAITING')

    def test_read_failure_before_commit_keeps_block(self):self.check_unreadable_commit_receipt(False,False,False)
    def test_read_failure_after_commit_preserves_result(self):self.check_unreadable_commit_receipt(True,False,False)
    def test_second_interrupt_before_commit_keeps_first(self):self.check_unreadable_commit_receipt(False,True,False)
    def test_second_interrupt_after_commit_keeps_first(self):self.check_unreadable_commit_receipt(True,True,False)
    def test_second_interrupt_before_commit_keeps_body_error(self):self.check_unreadable_commit_receipt(False,True,True)
    def test_second_interrupt_after_commit_keeps_body_error(self):self.check_unreadable_commit_receipt(True,True,True)
