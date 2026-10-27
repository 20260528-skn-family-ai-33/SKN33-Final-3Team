"""합성 API·임시 SQLite 검증. executor 시험만 합성 작업의 BackgroundScheduler를 시작합니다."""
import json
import os
import unittest
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from filelock import FileLock

from backend.app.scheduler.collectors.shortnews import shortnews_collect as collector
from backend.app.scheduler.jobs import shortnews_daily as job, shortnews_load as loader
from backend.app.scheduler.jobs import shortnews_pipeline
from backend.tests import test_shortnews_load as fixture

NOW = datetime.fromisoformat("2026-10-23T03:00:00+09:00")


class ShortnewsDailyTest(unittest.TestCase):
    def setUp(self):
        self.db = fixture.ShortnewsLoadTest()
        self.db.setUp()
        self.addCleanup(self.db.doCleanups)
        self.addCleanup(self.db.tearDown)
        self.output = self.db.root / "daily"
        lock_patch = patch.object(job, "LOCK_DIR", self.db.root / "locks")
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        self.env = self.db.root / "test.env"
        self.env.write_text("shortnews=synthetic-key\n", encoding="utf-8")
        self.requests = []
        self.pages = [([fixture.raw(day="2026-10-22")], 1)]

        def fetch(session, params):
            self.requests.append(dict(params))
            return self.pages[params["pageNo"] - 1]

        self.fetch = patch.object(collector, "fetch_page", side_effect=fetch).start()
        self.sleep = patch.object(collector.time, "sleep").start()
        # 날짜·복구 경계는 실제 SQLite 적재로 검사합니다. LLM/Chroma 전체 연결은
        # test_shortnews_pipeline에서 별도로 실제 Chroma와 함께 검증합니다.
        def mysql_only(raw_path, *, env_path=None, **options):
            rc = loader.run(raw_path, **options)
            return {"status": "FAILED" if rc else "SUCCESS", "mysql": "FAILED" if rc else "SUCCESS",
                    "chroma": "NOT_STARTED" if rc else "SUCCESS"}
        self.pipeline_patch = patch.object(shortnews_pipeline, "run", side_effect=mysql_only)
        self.pipeline_mock = self.pipeline_patch.start()
        self.addCleanup(patch.stopall)

    def run_job(self, **kwargs):
        return job.run(env_path=self.env, output_dir=self.output, db_url=self.db.url, now=NOW, **kwargs)

    def saved(self, result):
        return json.loads((Path(result["output_dir"]) / "job_result.json").read_text(encoding="utf-8"))

    def test_mysql_success_vector_partial_remains_retryable(self):
        self.pipeline_mock.side_effect=None
        self.pipeline_mock.return_value={'status':'PARTIAL','mysql':'SUCCESS','chroma':'PARTIAL'}
        out=self.run_job()
        self.assertEqual((out['status'],out['mysql']),('PARTIAL','SUCCESS'))
        self.assertEqual(self.state()['days']['2026-10-22'],'PARTIAL')
        self.pipeline_mock.return_value={'status':'SUCCESS','mysql':'SUCCESS','chroma':'SUCCESS'}
        fixed=self.run_job()
        self.assertEqual(fixed['status'],'SUCCESS')
        self.assertEqual(self.pipeline_mock.call_count,2)

    def test_schedule_is_active_at_three_kst_and_registration_does_not_run(self):
        scheduler = BlockingScheduler(timezone=job.KST)
        with patch.object(job, "run") as run, patch.object(scheduler, "start") as start:
            job.register_jobs(scheduler)
            scheduled = scheduler.get_job(job.JOB_ID)
            self.assertIs(scheduled.func, run)
            self.assertTrue(scheduled.coalesce)
            self.assertEqual(scheduled.max_instances, 1)
            self.assertEqual(scheduled.misfire_grace_time, 21600)
            self.assertEqual(scheduled.trigger.get_next_fire_time(None, NOW), NOW)
            self.assertNotEqual(getattr(scheduled, "next_run_time", "pending"), None)
            run.assert_not_called()
            start.assert_not_called()
        self.fetch.assert_not_called()

    def test_startup_recovery_is_registered_without_executing_or_changing_other_jobs(self):
        from backend.app.scheduler import main
        with patch.object(job, "register_jobs"):
            before = {j.id: j for j in main.build_scheduler().get_jobs()}
        started = datetime.now(job.KST)
        with patch.dict(os.environ, {'SHORTNEWS_DAILY_ENABLED': 'true'}):
            after = {j.id: j for j in main.build_scheduler().get_jobs()}
        self.assertEqual(set(after) - set(before), {"shortnews_daily", "shortnews_startup"})
        for key, original in before.items():
            updated = after[key]
            self.assertIs(original.func, updated.func)
            self.assertEqual(original.kwargs, updated.kwargs)
            for attr in ("max_instances", "coalesce", "misfire_grace_time", "next_run_time"):
                self.assertEqual(getattr(original, attr, "pending"), getattr(updated, attr, "pending"))
            if original.trigger.__class__.__name__ != "DateTrigger":
                self.assertEqual(str(original.trigger), str(updated.trigger))
            else:
                self.assertLess(abs((original.trigger.run_date - updated.trigger.run_date).total_seconds()), 2)
        startup = after["shortnews_startup"]
        self.assertIs(startup.func, job.run)
        self.assertTrue(started + timedelta(seconds=9) <= startup.trigger.run_date <= datetime.now(job.KST) + timedelta(seconds=11))
        self.fetch.assert_not_called()

    def test_collection_failure_does_not_stop_other_executor_jobs(self):
        self.fetch.side_effect = RuntimeError("synthetic failure")
        scheduler = BackgroundScheduler(timezone=job.KST)
        done, outcomes = threading.Event(), {}
        def fake_shortnews_failure():
            return self.run_job()  # 수집 API는 setUp의 fake이며 실패 후 적재는 호출하지 않습니다.
        def fake_other_job():
            return "OTHER_JOB_OK"
        def fake_executor_failure():
            raise RuntimeError("synthetic executor failure")
        def listener(event):
            outcomes[event.job_id] = event
            if len(outcomes) == 3:
                done.set()
        scheduler.add_listener(listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        scheduler.add_job(fake_shortnews_failure, "date", id="shortnews")
        scheduler.add_job(fake_other_job, "date", id="other")
        scheduler.add_job(fake_executor_failure, "date", id="synthetic_executor_failure")
        scheduler.start()
        scheduler_thread = scheduler._thread
        executor = scheduler._executors["default"]
        try:
            self.assertTrue(done.wait(4))
        finally:
            scheduler.shutdown(wait=True)
        self.assertEqual(outcomes["shortnews"].retval["status"], "FAILED")
        self.assertEqual(outcomes["other"].retval, "OTHER_JOB_OK")
        self.assertIsInstance(outcomes["synthetic_executor_failure"].exception, RuntimeError)
        self.assertIsNone(outcomes["shortnews"].exception)
        self.assertIsNone(outcomes["other"].exception)
        self.pipeline_mock.assert_not_called()
        self.assertFalse(scheduler.running)
        self.assertFalse(scheduler_thread.is_alive())
        self.assertTrue(all(not thread.is_alive() for thread in executor._pool._threads))

    def test_common_scheduler_preserves_existing_jobs(self):
        from backend.app.scheduler import main
        with patch.object(job, "run") as run, patch.dict(os.environ, {"SHORTNEWS_DAILY_ENABLED": "true"}):
            scheduler = main.build_scheduler()
            jobs = {scheduled.id: scheduled for scheduled in scheduler.get_jobs()}
            self.assertEqual(set(jobs), {"hs_code_refresh", "hs_code_refresh_retry", "hs_code_embed",
                                        "un_data_monthly", "un_data_startup", "un_data_resume",
                                        "customs_monthly", "customs_monthly_startup", "fx_daily_load",
                                        "fx_daily_startup", "shortnews_daily", "shortnews_startup"})
            self.assertIs(jobs[job.JOB_ID].func, run)
            run.assert_not_called()

    def test_all_pages_of_yesterday_are_loaded_and_rerun_is_idempotent(self):
        self.pages = [([fixture.raw("001", "2026-10-22")], 2), ([fixture.raw("002", "2026-10-22")], 2)]
        first = self.run_job()
        self.assertEqual(first["status"], "SUCCESS")
        self.assertEqual([p["pageNo"] for p in self.requests], [1, 2])
        self.assertTrue(all(p["search2"] == "20261022" and p["search1"] == "" for p in self.requests))
        self.assertEqual(self.saved(first), first)
        before = self.db.rows("article")
        self.assertEqual(len(before), 2)
        self.assertTrue(all(a["source_code"] == "KOTRA_SHORT_NEWS" for a in before))
        second = self.run_job(target_date="2026-10-22")
        self.assertEqual(second["status"], "SUCCESS")
        self.assertNotEqual(first["output_dir"], second["output_dir"])
        self.assertEqual(self.db.rows("article"), before)

    def test_team_fields_survive_collection_and_same_source_upsert(self):
        self.pages = [([fixture.raw("001", "2026-10-22", body="<수출전망> 문의 a@example.com",
                                    nttCn="<p>대체 본문</p>", smmarCn="선택하지 않을 개요")], 1)]
        first = self.run_job()
        self.assertEqual(first["status"], "SUCCESS")
        raw = json.loads((Path(first["output_dir"]) / "shortnews_raw.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(raw["smmarCn"], "선택하지 않을 개요")
        article = self.db.rows("article")[0]
        self.assertEqual(article["source_code"], "KOTRA_SHORT_NEWS")
        self.assertEqual(article["body_masked"], "<수출전망> 문의 [이메일]")
        self.pages = [([fixture.raw("001", "2026-10-22", nttCn="<p>새 본문</p><p>02-123-4567</p>")], 1)]
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        rows = self.db.rows("article")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["article_id"], article["article_id"])
        self.assertEqual(rows[0]["body_masked"], "새 본문\n[전화번호]")
        self.assertEqual(len(self.db.rows("article_tag")), 1)

    def test_zero_articles_records_requested_day_in_batch(self):
        self.pages = [([], 0)]
        result = self.run_job()
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.db.rows("article"), [])
        batch = self.db.rows("batch_run")[0]
        self.assertEqual((batch["period_start"], batch["period_end"]), (date(2026, 10, 22),) * 2)
        self.assertEqual(batch["received_count"], 0)

    def test_incomplete_collection_loads_saved_articles_but_reports_partial(self):
        self.pages = [([fixture.raw("001", "2026-10-22")], 2)] * 2
        result = self.run_job()
        self.assertEqual((result["status"], result["mysql"]), ("PARTIAL", "SUCCESS"))
        self.assertFalse(result["collection"]["collection_complete"])
        self.assertEqual(len(self.db.rows("article")), 1)

    def test_collection_failure_does_not_load_old_files_or_expose_error_text(self):
        self.fetch.side_effect = RuntimeError("serviceKey=secret-test-value")
        with patch.object(loader, "run") as load, self.assertLogs(job.log, level="ERROR") as logs:
            result = self.run_job()
        self.assertEqual((result["status"], result["mysql"]), ("FAILED", "NOT_STARTED"))
        load.assert_not_called()
        self.assertNotIn("secret-test-value", json.dumps(self.saved(result)) + str(logs.output))

    def test_load_failure_is_not_success(self):
        with patch.object(loader, "run", return_value=1):
            result = self.run_job()
        self.assertEqual((result["status"], result["mysql"]), ("FAILED", "FAILED"))
        self.assertEqual(self.saved(result)["status"], "FAILED")

    def test_chroma_failure_keeps_day_retryable_after_mysql_commit(self):
        self.pipeline_mock.side_effect = None
        self.pipeline_mock.return_value = {"status": "FAILED", "mysql": "SUCCESS", "chroma": "NOT_STARTED"}
        result = self.run_job()
        self.assertEqual((result['status'], result['mysql']), ('FAILED', 'SUCCESS'))
        state = json.loads((self.output / 'state.json').read_text(encoding="utf-8"))
        self.assertEqual(state['databases'][result['database_id']]['days']['2026-10-22'], 'FAILED')
        self.pipeline_mock.return_value = {"status": "SUCCESS", "mysql": "SUCCESS", "chroma": "SUCCESS"}
        result = self.run_job()
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual(self.pipeline_mock.call_args.kwargs['env_path'], self.env)
        self.assertEqual(self.saved(result)["status"], "SUCCESS")

    def test_missing_collection_report_never_calls_loader(self):
        with patch.object(collector, "collect", return_value=Path("old.csv")), patch.object(loader, "run") as load:
            result = self.run_job()
        self.assertEqual(result["status"], "FAILED")
        load.assert_not_called()

    def test_concurrent_run_is_skipped_and_lock_is_released(self):
        self.output.mkdir()
        with FileLock(str(self.output / "shortnews_daily.lock"), timeout=0):
            self.assertEqual(self.run_job()["status"], "ALREADY_RUNNING")
        self.fetch.assert_not_called()
        self.assertEqual(self.run_job()["status"], "SUCCESS")

    def test_target_day_uses_kst_across_year_and_leap_boundaries(self):
        self.pages = [([], 0)]
        for instant, expected in (("2026-12-31T18:00:00+00:00", "20261231"),
                                  ("2024-02-29T18:00:00+00:00", "20240229")):
            with self.subTest(instant=instant), patch.object(loader, "run", return_value=0):
                result = job.run(env_path=self.env, output_dir=self.output / expected, db_url=self.db.url,
                                 now=datetime.fromisoformat(instant))
                self.assertEqual(result["status"], "SUCCESS")
                self.assertEqual(self.requests[-1]["search2"], expected)

    def test_manual_retry_uses_explicit_date(self):
        self.pages = [([], 0)]
        self.assertEqual(self.run_job(target_date="2026-10-01")["target_date"], "2026-10-01")
        self.assertEqual(self.requests[-1]["search2"], "20261001")

    def test_custom_env_is_used_for_collection_and_mysql(self):
        from backend.app.db import session
        with patch.object(session, "mysql_url", return_value=self.db.url) as url, \
                patch.object(collector, "read_key", return_value="synthetic-key") as key:
            result = job.run(env_path=self.env, output_dir=self.output, now=NOW)
        self.assertEqual(result["status"], "SUCCESS")
        key.assert_called_once_with(self.env)
        url.assert_called_once_with(self.env)

    def state(self):
        state = json.loads((self.output / "state.json").read_text(encoding="utf-8"))
        return state["databases"][job.database_identity(self.db.url)]

    def fetch_each_day(self, session, params):
        self.requests.append(dict(params))
        return [fixture.raw(params["search2"], params["search2"])], 1

    def test_wrong_date_blocks_entire_day_preserves_raw_and_retries(self):
        self.pages = [([fixture.raw("1", "2026-10-22"), fixture.raw("2", "2026-10-21")], 2)]
        result = self.run_job()
        self.assertEqual((result["status"], result["mysql"]), ("FAILED", "NOT_STARTED"))
        self.assertEqual(self.db.rows("article"), [])
        errors = json.loads((Path(result["output_dir"]) / "date_errors.json").read_text(encoding="utf-8"))
        self.assertEqual(errors["errors"][0]["nttSn"], "2")
        self.assertEqual(len(json.loads((Path(result["output_dir"]) / "shortnews_raw.json").read_text(encoding="utf-8"))), 2)
        self.assertEqual(self.state()["days"]["2026-10-22"], "FAILED")
        self.pages = [([fixture.raw("1", "2026-10-22"), fixture.raw("2", "2026-10-22")], 2)]
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual(len(self.db.rows("article")), 2)

    def test_invalid_and_missing_response_dates_never_load(self):
        for value in ("", None, "not-a-date"):
            with self.subTest(value=value):
                self.pages = [([fixture.raw(othbcDt=value)], 1)]
                self.assertEqual(self.run_job()["status"], "FAILED")
                self.assertEqual(self.db.rows("article"), [])

    def test_failed_and_downtime_days_are_all_recovered_without_today(self):
        self.fetch.side_effect = RuntimeError("outage")
        self.assertEqual(self.run_job()["status"], "FAILED")
        self.fetch.side_effect = self.fetch_each_day
        result = job.run(env_path=self.env, output_dir=self.output, db_url=self.db.url,
                         now=datetime.fromisoformat("2026-10-26T03:00:00+09:00"))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.requests], ["20261022", "20261023", "20261024", "20261025"])
        self.assertEqual({str(r["published_date"]) for r in self.db.rows("article")},
                         {"2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"})
        self.assertTrue(all(s == "SUCCESS" for s in self.state()["days"].values()))

    def test_partial_day_is_retried_and_does_not_block_next_day(self):
        self.pages = [([fixture.raw("1", "2026-10-22")], 2)] * 2
        self.assertEqual(self.run_job()["status"], "PARTIAL")
        self.fetch.side_effect = self.fetch_each_day
        self.requests.clear()
        result = job.run(env_path=self.env, output_dir=self.output, db_url=self.db.url,
                         now=datetime.fromisoformat("2026-10-24T03:00:00+09:00"))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.requests], ["20261022", "20261023"])

    def test_old_failure_does_not_block_current_target(self):
        self.fetch.side_effect = RuntimeError("outage")
        self.run_job()
        def fetch(session, params):
            if params["search2"] == "20261022":
                raise RuntimeError("still unavailable")
            return self.fetch_each_day(session, params)
        self.fetch.side_effect = fetch
        result = job.run(env_path=self.env, output_dir=self.output, db_url=self.db.url,
                         now=datetime.fromisoformat("2026-10-24T03:00:00+09:00"))
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.state()["days"], {"2026-10-22": "FAILED", "2026-10-23": "SUCCESS"})
        self.assertEqual(len(self.db.rows("article")), 1)

    def test_database_failure_is_pending_then_recovered(self):
        with patch.object(loader, "run", return_value=1):
            self.assertEqual(self.run_job()["status"], "FAILED")
        self.assertEqual(self.state()["days"]["2026-10-22"], "FAILED")
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual(len(self.db.rows("article")), 1)

    def test_cycling_pages_stop_with_partial_state_and_saved_articles(self):
        self.pages = [([fixture.raw("1", "2026-10-22")], 3),
                      ([fixture.raw("2", "2026-10-22")], 3),
                      ([fixture.raw("1", "2026-10-22", nttSj="내용 변경")], 3)]
        result = self.run_job()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(self.db.rows("article")), 2)
        self.assertEqual(self.state()["days"]["2026-10-22"], "PARTIAL")

    def test_overlapping_pages_with_new_ids_continue(self):
        a, b, c = [fixture.raw(key, "2026-10-22") for key in ("1", "2", "3")]
        self.pages = [([a, b], 3), ([b, c], 3)]
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual(len(self.db.rows("article")), 3)

    def test_new_page_combination_without_new_ids_does_not_truncate_later_page(self):
        a, b, c, d = [fixture.raw(key, "2026-10-22") for key in ("1", "2", "3", "4")]
        self.pages = [([a, b], 4), ([b, c], 4), ([a, c], 4), ([d], 4)]
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len(self.db.rows("article")), 4)

    def test_successful_old_dates_are_skipped_but_yesterday_is_refreshed(self):
        self.run_job()
        self.requests.clear()
        self.fetch.side_effect = self.fetch_each_day
        result = job.run(env_path=self.env, output_dir=self.output, db_url=self.db.url,
                         now=datetime.fromisoformat("2026-10-24T00:01:00+09:00"))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.requests], ["20261023"])
        result = job.run(env_path=self.env, output_dir=self.output, db_url=self.db.url,
                         now=datetime.fromisoformat("2026-10-24T03:00:00+09:00"))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.requests], ["20261023", "20261023"])

    def test_interrupted_state_and_commit_before_checkpoint_are_replayed(self):
        real_save = job.atomic_json
        def fail_after_commit(path, value):
            if path.name == "state.json" and value["databases"][job.database_identity(self.db.url)]["days"].get("2026-10-22") == "SUCCESS":
                raise OSError("synthetic checkpoint failure")
            return real_save(path, value)
        with patch.object(job, "atomic_json", side_effect=fail_after_commit):
            self.assertEqual(self.run_job()["status"], "FAILED")
        self.assertEqual(self.state()["days"]["2026-10-22"], "RUNNING")
        self.assertEqual(len(self.db.rows("article")), 1)
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual(len(self.db.rows("article")), 1)

    def test_legacy_run_history_recovers_missing_dates(self):
        old = self.output / "2026-10-20" / "old"
        old.mkdir(parents=True)
        (old / "job_result.json").write_text(json.dumps({"target_date": "2026-10-20", "status": "SUCCESS"}), encoding="utf-8")
        self.fetch.side_effect = self.fetch_each_day
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.requests], ["20261020", "20261021", "20261022"])

    def test_corrupt_state_is_not_silently_reset(self):
        self.output.mkdir()
        (self.output / "state.json").write_text("broken", encoding="utf-8")
        self.assertEqual(self.run_job()["status"], "FAILED")
        self.fetch.assert_not_called()
        self.assertEqual((self.output / "state.json").read_text(encoding="utf-8"), "broken")


if __name__ == "__main__":
    unittest.main()
