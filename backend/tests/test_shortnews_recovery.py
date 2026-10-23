"""DB 전환, 파일/체크포인트 실패와 중복 실행의 경계 검증. 실제 API·운영 DB는 사용하지 않습니다."""
import json
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from filelock import FileLock
from sqlalchemy.engine import make_url

from backend.app.scheduler.jobs import shortnews_daily as job
from backend.app.db import session
from backend.tests import test_shortnews_daily as daily, test_shortnews_load as fixture


class RecoveryBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.c = daily.ShortnewsDailyTest()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.c.fetch.side_effect = self.c.fetch_each_day

    def run_job(self, day="2026-10-23", db_url=None, **kwargs):
        return job.run(env_path=self.c.env, output_dir=self.c.output, db_url=db_url or self.c.db.url,
                       now=datetime.fromisoformat(day + "T03:00:00+09:00"), **kwargs)

    def other_database(self):
        other = fixture.ShortnewsLoadTest()
        other.setUp()
        other.doCleanups()  # 스냅샷 위치는 부모 fixture의 임시 경로를 사용합니다.
        self.addCleanup(other.tearDown)
        return other

    def saved_state(self):
        return json.loads((self.c.output / "state.json").read_text())

    def seed_failed(self):
        self.c.fetch.side_effect = RuntimeError("synthetic outage")
        try:
            self.assertEqual(self.run_job()["status"], "FAILED")
        finally:
            self.c.fetch.side_effect = self.c.fetch_each_day
        self.c.requests.clear()

    def test_database_switch_replays_history_and_switch_back_keeps_separate_progress(self):
        other = self.other_database()
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.c.requests.clear()
        result = self.run_job("2026-10-24", other.url)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261022", "20261023"])
        self.assertEqual({str(a["published_date"]) for a in other.rows("article")}, {"2026-10-22", "2026-10-23"})
        self.assertEqual(len(self.saved_state()["databases"]), 2)
        self.c.requests.clear()
        self.assertEqual(self.run_job("2026-10-24")["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261023"])
        self.assertEqual(len(self.c.db.rows("article")), 2)
        self.assertEqual(len(self.c.db.rows("article_tag")), 2)

    def test_identity_ignores_credentials_driver_and_default_port_but_not_database(self):
        first = "mysql+pymysql://alice:first-secret@EXAMPLE.test/trade"
        same = "mysql+mysqldb://bob:second-secret@example.test:3306/trade"
        self.assertEqual(job.database_identity(first), job.database_identity(same))
        self.assertNotEqual(job.database_identity(first), job.database_identity(same + "2"))
        self.assertNotEqual(job.database_identity(first), job.database_identity(same.replace(":3306", ":3307")))
        self.assertNotEqual(job.database_identity(first), job.database_identity(same.replace("example.test", "other.test")))
        self.assertNotEqual(job.database_identity(same + "?unix_socket=/tmp/a"),
                            job.database_identity(same + "?unix_socket=/tmp/b"))
        self.assertEqual(len(job.database_identity(first)), 64)

    def test_sqlite_identity_resolves_alias_paths(self):
        alias = "sqlite:///" + str(self.c.db.root / "unused" / ".." / "test.db")
        self.assertEqual(job.database_identity(alias), job.database_identity(self.c.db.url))

    def test_env_database_is_frozen_for_whole_recovery_run(self):
        other = self.other_database()
        self.c.output.mkdir()
        (self.c.output / "state.json").write_text(json.dumps({
            "schema_version": 1, "start_date": "2026-10-20", "days": {"2026-10-20": "SUCCESS"}}))
        with patch.object(session, "mysql_url", side_effect=[self.c.db.url, other.url]) as resolve:
            result = job.run(env_path=self.c.env, output_dir=self.c.output, now=daily.NOW)
        self.assertEqual(result["status"], "SUCCESS")
        resolve.assert_called_once_with(self.c.env)
        self.assertEqual(len(self.c.db.rows("article")), 3)
        self.assertEqual(other.rows("article"), [])

    def test_v1_checkpoint_is_revalidated_without_duplicate_rows(self):
        self.run_job()
        (self.c.output / "state.json").write_text(json.dumps({
            "schema_version": 1, "start_date": "2026-10-21", "days": {"2026-10-22": "SUCCESS"}}))
        self.c.requests.clear()
        self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261021", "20261022"])
        self.assertEqual(len(self.c.db.rows("article")), 2)
        self.assertEqual(len(self.c.db.rows("article_tag")), 2)
        self.assertEqual(self.saved_state()["schema_version"], 3)

    def test_invalid_database_settings_stop_before_api_and_state_changes(self):
        with patch.object(session, "mysql_url", side_effect=ValueError("password=secret")), \
                self.assertLogs(job.log, level="ERROR") as logs:
            result = job.run(env_path=self.c.env, output_dir=self.c.output, now=daily.NOW)
        self.assertEqual(result["status"], "FAILED")
        self.c.fetch.assert_not_called()
        self.assertFalse((self.c.output / "state.json").exists())
        self.assertNotIn("password=secret", str(logs.output) + json.dumps(result))

    def test_old_date_initial_report_failure_does_not_block_current_date(self):
        self.seed_failed()
        save = job.save_result
        def fail_old(directory, result):
            if result["target_date"] == "2026-10-22":
                raise PermissionError("old directory read-only")
            save(directory, result)
        with patch.object(job, "save_result", side_effect=fail_old):
            result = self.run_job("2026-10-24")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261023"])
        self.assertEqual(self.c.state()["days"], {"2026-10-22": "FAILED", "2026-10-23": "SUCCESS"})
        self.assertEqual(self.run_job("2026-10-24")["status"], "SUCCESS")
        self.assertEqual(len(self.c.db.rows("article")), 2)

    def test_old_date_directory_creation_failure_does_not_block_current_date(self):
        self.seed_failed()
        mkdir = Path.mkdir
        def fail_old(path, *args, **kwargs):
            if path.parent.name == "2026-10-22":
                raise PermissionError("synthetic mkdir failure")
            return mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", fail_old):
            result = self.run_job("2026-10-24")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261023"])
        self.assertEqual(self.c.state()["days"]["2026-10-23"], "SUCCESS")

    def test_final_report_failure_after_db_commit_retries_without_duplicates(self):
        self.seed_failed()
        save = job.save_result
        def fail_final(directory, result):
            if result["target_date"] == "2026-10-22" and "finished_at" in result:
                raise OSError("synthetic final report failure")
            save(directory, result)
        with patch.object(job, "save_result", side_effect=fail_final):
            result = self.run_job("2026-10-24")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["runs"][0]["mysql"], "SUCCESS")
        self.assertEqual(self.c.state()["days"], {"2026-10-22": "FAILED", "2026-10-23": "SUCCESS"})
        self.assertEqual(len(self.c.db.rows("article")), 2)
        self.assertEqual(self.run_job("2026-10-24")["status"], "SUCCESS")
        self.assertEqual(len(self.c.db.rows("article")), 2)
        self.assertEqual(len(self.c.db.rows("article_tag")), 2)

    def test_unexpected_day_exception_is_isolated(self):
        self.seed_failed()
        real = job.run_day
        def fail_day(target, *args):
            if target.isoformat() == "2026-10-22":
                raise RuntimeError("unexpected error")
            return real(target, *args)
        with patch.object(job, "run_day", side_effect=fail_day):
            self.assertEqual(self.run_job("2026-10-24")["status"], "FAILED")
        self.assertEqual(self.c.state()["days"], {"2026-10-22": "FAILED", "2026-10-23": "SUCCESS"})

    def test_checkpoint_failure_after_commit_stays_pending_when_later_day_saves(self):
        self.seed_failed()
        real = job.atomic_json
        key = job.database_identity(self.c.db.url)
        def fail_old_success(path, payload):
            if path.name == "state.json" and payload["databases"][key]["days"].get("2026-10-22") == "SUCCESS":
                raise OSError("synthetic checkpoint failure")
            return real(path, payload)
        with patch.object(job, "atomic_json", side_effect=fail_old_success):
            result = self.run_job("2026-10-24")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.c.state()["days"], {"2026-10-22": "RUNNING", "2026-10-23": "SUCCESS"})
        self.assertEqual(len(self.c.db.rows("article")), 2)
        self.assertEqual(self.run_job("2026-10-24")["status"], "SUCCESS")
        self.assertEqual(len(self.c.db.rows("article")), 2)

    def test_checkpoint_failure_before_one_day_skips_only_that_day(self):
        self.seed_failed()
        real = job.atomic_json
        key = job.database_identity(self.c.db.url)
        failed = False
        def fail_once(path, payload):
            nonlocal failed
            if (path.name == "state.json" and not failed and
                    payload["databases"][key]["days"].get("2026-10-22") == "RUNNING"):
                failed = True
                raise OSError("synthetic start checkpoint failure")
            return real(path, payload)
        with patch.object(job, "atomic_json", side_effect=fail_once):
            result = self.run_job("2026-10-24")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261023"])
        self.assertEqual(self.c.state()["days"], {"2026-10-22": "RUNNING", "2026-10-23": "SUCCESS"})

    def test_global_checkpoint_write_failure_stops_before_collection(self):
        with patch.object(job, "atomic_json", side_effect=OSError("full disk")):
            result = self.run_job()
        self.assertEqual(result["status"], "FAILED")
        self.c.fetch.assert_not_called()
        self.assertEqual(self.c.db.rows("article"), [])

    def test_same_database_different_output_is_locked(self):
        job.LOCK_DIR.mkdir(parents=True)
        lock = job.LOCK_DIR / (job.database_identity(self.c.db.url) + ".lock")
        with FileLock(str(lock), timeout=0):
            for root in (self.c.output, self.c.db.root / "another-output"):
                result = job.run(env_path=self.c.env, output_dir=root, db_url=self.c.db.url, now=daily.NOW)
                self.assertEqual(result["status"], "ALREADY_RUNNING")
        self.c.fetch.assert_not_called()
        self.assertEqual(self.run_job()["status"], "SUCCESS")

    def test_simultaneous_jobs_only_one_collects_and_commits(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def blocked_fetch(session, params):
            entered.set()
            if not release.wait(4):
                raise RuntimeError("test did not release collector")
            return self.c.fetch_each_day(session, params)
        self.c.fetch.side_effect = blocked_fetch
        worker = threading.Thread(target=lambda: outcomes.append(self.run_job()))
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            other = job.run(env_path=self.c.env, output_dir=self.c.db.root / "alternate",
                            db_url=self.c.db.url, now=daily.NOW)
            self.assertEqual(other["status"], "ALREADY_RUNNING")
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes[0]["status"], "SUCCESS")
        self.assertEqual(len(self.c.requests), 1)
        self.assertEqual(len(self.c.db.rows("article")), 1)
        self.assertEqual(len(self.c.db.rows("article_tag")), 1)

    def test_interrupt_leaves_retryable_state_and_releases_locks(self):
        with patch.object(job, "run_day", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job()
        self.assertEqual(self.c.state()["days"]["2026-10-22"], "RUNNING")
        self.assertEqual(self.run_job()["status"], "SUCCESS")

    def test_atomic_replace_failure_keeps_previous_valid_state(self):
        self.run_job()
        path = self.c.output / "state.json"
        before = path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("synthetic rename failure")):
            with self.assertRaises(OSError):
                job.atomic_json(path, {"incomplete": True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.run_job()["status"], "SUCCESS")

    def test_corrupt_v2_namespace_never_resets_or_calls_api(self):
        self.run_job()
        state = self.saved_state()
        state["databases"][job.database_identity(self.c.db.url)]["days"]["2026-10-22"] = "UNKNOWN"
        path = self.c.output / "state.json"
        path.write_text(json.dumps(state))
        before = path.read_bytes()
        self.c.fetch.reset_mock()
        self.assertEqual(self.run_job()["status"], "FAILED")
        self.c.fetch.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_unreadable_report_does_not_block_date_recovery(self):
        folder = self.c.output / "2026-10-20" / "old"
        folder.mkdir(parents=True)
        (folder / "job_result.json").write_text("unreadable old content")
        read = Path.read_text
        def fail_old(path, *args, **kwargs):
            if path.parent == folder:
                raise PermissionError("synthetic unreadable old report")
            return read(path, *args, **kwargs)
        with patch.object(Path, "read_text", fail_old):
            self.assertEqual(self.run_job()["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261020", "20261021", "20261022"])

    def test_new_database_replays_manual_dates_before_operating_start(self):
        other = self.other_database()
        self.run_job(target_date="2026-10-01")
        self.run_job()
        self.c.requests.clear()
        self.assertEqual(self.run_job(db_url=other.url)["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261001", "20261022"])
        self.assertEqual(len(other.rows("article")), 2)

    def test_latest_summary_failure_preserves_verified_day_checkpoint(self):
        real = job.atomic_json
        def fail_summary(path, payload):
            if path.name == "latest_run.json":
                raise OSError("synthetic summary failure")
            return real(path, payload)
        with patch.object(job, "atomic_json", side_effect=fail_summary):
            self.assertEqual(self.run_job()["status"], "FAILED")
        self.assertEqual(self.c.state()["days"]["2026-10-22"], "SUCCESS")
        self.c.requests.clear()
        self.assertEqual(self.run_job("2026-10-24")["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261023"])
        self.assertEqual(len(self.c.db.rows("article")), 2)


if __name__ == "__main__":
    unittest.main()
