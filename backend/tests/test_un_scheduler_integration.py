"""실제 APScheduler executor에서 UN 실패/동시 실행과 다른 작업 격리 검증."""
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest import mock
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from backend.app.scheduler import main as scheduler
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler.jobs import un_data_resume as resume


class SchedulerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=Path(self.temp.name)

    def initialized(self):
        (self.state/'checkpoint.json').write_text('{}',encoding='utf-8')

    def test_registered_jobs_and_times(self):
        before=datetime.now(scheduler.KST)
        with mock.patch.dict('os.environ', {'SHORTNEWS_DAILY_ENABLED': 'false'}):
            s=scheduler.build_scheduler(self.state,Path('custom-scope'),Path('custom-env'))
        jobs={j.id:j for j in s.get_jobs()}
        self.assertEqual(len(jobs),10)  # 단신속보는 명시적으로 활성화한 경우만 등록
        start=jobs['un_data_startup'].trigger.run_date
        self.assertTrue(before+timedelta(seconds=9)<=start<=datetime.now(scheduler.KST)+timedelta(seconds=11))
        j=jobs['un_data_monthly'];self.assertEqual(j.max_instances,1)
        self.assertEqual(j.args,(self.state,Path('custom-scope'),Path('custom-env')))
        now=datetime(2026,10,16,tzinfo=scheduler.KST)
        self.assertEqual(j.trigger.get_next_fire_time(None,now).isoformat(),'2026-11-01T03:00:00+09:00')
        self.assertEqual(jobs['hs_code_embed'].func,scheduler.embed.run)
        self.assertEqual(jobs['hs_code_refresh'].func,scheduler.hs_refresh_job)

    def test_no_checkpoint_skips_without_api_or_db(self):
        with mock.patch.object(job,'execute_once') as execute:
            self.assertEqual(scheduler.un_refresh_job(self.state),'NOT_INITIALIZED')
            execute.assert_not_called()
        self.assertFalse((self.state/'checkpoint.json').exists())

    def test_success_options_forwarded(self):
        self.initialized()
        with mock.patch.object(job,'execute_once') as execute:
            self.assertEqual(scheduler.un_refresh_job(self.state,'scope','env'),'SUCCESS')
            execute.assert_called_once_with('scope',self.state,'env')

    def test_failure_returns_and_releases_process_lock(self):
        self.initialized()
        with mock.patch.object(job,'execute_once',side_effect=ValueError('bad checkpoint')):
            self.assertEqual(scheduler.un_refresh_job(self.state),'FAILED')
        with mock.patch.object(job,'execute_once'):
            self.assertEqual(scheduler.un_refresh_job(self.state),'SUCCESS')

    def test_simultaneous_startup_and_monthly_only_one_runs(self):
        self.initialized();entered=threading.Event();release=threading.Event()
        def slow(*args):entered.set();release.wait(3)
        with mock.patch.object(job,'execute_once',side_effect=slow) as execute:
            thread=threading.Thread(target=scheduler.un_refresh_job,args=(self.state,));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(scheduler.un_refresh_job(self.state),'ALREADY_RUNNING')
                self.assertEqual(execute.call_count,1)
            finally:release.set();thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_real_scheduler_keeps_other_job_after_un_failure(self):
        self.initialized();done=threading.Event();events={}
        def fake_un_failure():
            return scheduler.un_refresh_job(self.state)
        def fake_other_job():
            return 'HS_OK'
        def fake_executor_failure():
            raise RuntimeError('synthetic executor failure')
        def listener(event):
            events[event.job_id]=event
            if {'un_data_startup','hs_code_embed','synthetic_executor_failure'}<=events.keys():done.set()
        with mock.patch.object(job,'execute_once',side_effect=RuntimeError('synthetic API failure')) as execute:
            # 공용 운영 스케줄은 등록하지 않습니다. 처리 wrapper 아래 I/O는 fake입니다.
            s=BackgroundScheduler(timezone=scheduler.KST)
            s.add_listener(listener,EVENT_JOB_EXECUTED|EVENT_JOB_ERROR)
            s.add_job(fake_un_failure,'date',id='un_data_startup')
            s.add_job(fake_other_job,'date',id='hs_code_embed')
            s.add_job(fake_executor_failure,'date',id='synthetic_executor_failure')
            s.start()
            scheduler_thread=s._thread;executor=s._executors['default']
            try:self.assertTrue(done.wait(4))
            finally:s.shutdown(wait=True)
            execute.assert_called_once()
        self.assertEqual(events['un_data_startup'].retval,'FAILED')
        self.assertEqual(events['hs_code_embed'].retval,'HS_OK')
        self.assertIsInstance(events['synthetic_executor_failure'].exception,RuntimeError)
        self.assertTrue(all(events[name].exception is None for name in ('un_data_startup','hs_code_embed')))
        self.assertFalse(s.running)
        self.assertFalse(scheduler_thread.is_alive())
        self.assertTrue(all(not thread.is_alive() for thread in executor._pool._threads))

    def test_old_schedule_rejected_before_db(self):
        with mock.patch('sys.argv',['un_data_refresh','--schedule']),mock.patch.object(job,'make_engine') as make:
            with self.assertRaises(SystemExit) as error:job.main()
            self.assertEqual(error.exception.code,2);make.assert_not_called()

    def test_execute_once_reads_env_and_closes_resources(self):
        env=self.state/'sample.env';env.write_text('UN_COMTRADE_API_KEY=test-un-key\n',encoding='utf-8')
        with mock.patch.dict(job.os.environ,{},clear=True),mock.patch.object(job,'make_engine') as make,mock.patch.object(job,'Client') as client,mock.patch.object(resume,'run_resumable',return_value={'status':'DB_VERIFIED'}) as run:
            job.execute_once('scope',self.state,env)
            client.assert_called_once_with('test-un-key')
            run.assert_called_once_with(make.return_value,client.return_value,'scope',self.state,resume_now=False)
            client.return_value.session.close.assert_called_once();make.return_value.dispose.assert_called_once()

    def test_execute_once_failure_closes_resources(self):
        with mock.patch.object(job,'make_engine') as make,mock.patch.object(job,'Client') as client,mock.patch.object(resume,'run_resumable',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):job.execute_once(state_dir=self.state)
            client.return_value.session.close.assert_called_once();make.return_value.dispose.assert_called_once()
