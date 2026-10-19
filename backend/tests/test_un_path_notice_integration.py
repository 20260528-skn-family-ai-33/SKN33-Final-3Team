"""경로 사전 검사·중복 알림·실제 재개 제어의 결합 회귀 (SQLite/모의 API)."""
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from backend.tests import test_un_data_resume as fixture
from backend.app.scheduler import main as scheduler
from backend.app.scheduler.jobs import un_data_resume as resume, un_data_refresh as job
from backend.app.scheduler.collectors.un_data import paths


class PathNoticeIntegrationTest(unittest.TestCase):
    tearDown=fixture.ResumeTest.tearDown
    client=fixture.ResumeTest.client
    run_resume=fixture.ResumeTest.run_resume
    snapshot=fixture.ResumeTest.snapshot

    def setUp(self):
        fixture.ResumeTest.setUp(self)
        patch=mock.patch.dict(scheduler.UN_STOP_NOTICES,clear=True)
        patch.start();self.addCleanup(patch.stop)

    def cancel(self):
        with mock.patch.object(job,'run',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_resume(resume_now=True)

    def test_windows_path_rejection_does_not_erase_cancel_or_authorize_resume(self):
        self.run_resume(budget=6);self.cancel()
        original=self.state
        deep=original.parent/('deep_'+'a'*120)
        shutil.copytree(original,deep)
        before={p.relative_to(deep):p.read_bytes() for p in deep.rglob('*.json')}
        rows=self.snapshot();client=self.client()
        self.state=deep
        try:
            with mock.patch.object(paths.sys,'platform','win32'):
                with self.assertRaisesRegex(ValueError,'짧은 영속 경로'):
                    self.run_resume(client,resume_now=True)
            self.assertEqual(client.request_count,0)
            self.assertEqual(rows,self.snapshot())
            self.assertEqual(before,{p.relative_to(deep):p.read_bytes() for p in deep.rglob('*.json')})
            self.assertTrue(resume.recovery_blocked(deep))
        finally:self.state=original
        self.assertEqual(self.run_resume()['status'],'CANCELLED')
        self.assertEqual(self.run_resume(resume_now=True)['status'],'DB_VERIFIED')

    def test_notice_polling_preserves_success_and_manual_resume_skips_it(self):
        self.run_resume(budget=6);self.cancel()
        before={p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*.json')}
        with mock.patch.object(scheduler.log,'warning') as warning:
            for _ in range(20):
                self.assertEqual(scheduler.un_resume_job(self.state),'RECOVERY_REQUIRED')
            self.assertEqual(warning.call_count,1)
        self.assertEqual(before,{p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*.json')})
        result=self.run_resume(resume_now=True)
        self.assertEqual((result['status'],result['succeeded'],result['skipped']),('DB_VERIFIED',1,1))
        with mock.patch.object(scheduler.log,'warning') as warning,mock.patch.object(scheduler.log,'info') as info:
            self.assertEqual(scheduler.report_un_status(self.state),'UNBLOCKED')
            warning.assert_not_called();self.assertEqual(info.call_count,1)

    def test_notice_checks_do_not_break_budget_cache_resume(self):
        now=datetime(2024,2,2,tzinfo=timezone.utc)
        first=self.run_resume(now=now,budget=2)
        self.assertEqual(first['status'],'WAITING')
        before={p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*.json')}
        with mock.patch.object(scheduler.log,'warning') as warning:
            for _ in range(20):
                self.assertEqual(scheduler.report_un_status(self.state),'UNBLOCKED')
            warning.assert_not_called()
        self.assertEqual(before,{p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*.json')})
        result=self.run_resume(now=now+timedelta(minutes=5))
        self.assertEqual(result['status'],'DB_VERIFIED')
        self.assertGreater(result['cache_hits'],0)
        self.assertEqual(len(self.snapshot()),3)
