"""월별 단계 재시작, ML 미연결 처리와 스케줄 대상 월을 외부 호출 없이 검증한다."""
import sys
import hashlib
import json
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from sqlalchemy import URL

from backend.app.scheduler.jobs import customs_monthly as monthly
from backend.app.services.customs_stage import ValidationError


class MonthlyPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stack = []
        for target, kwargs in (
                ("collector.resolve_countries", {"return_value": ["US"]}),
                ("mysql_url", {}), ("make_engine", {}),
                ("MetricCalculator", {}), ("load_csv", {"return_value": {"status": "LOADED"}}),
                ("collect_month", {"side_effect": self.collect})):
            patcher = patch.object(monthly, target, **kwargs) if "." not in target else patch(
                "backend.app.scheduler.jobs.customs_monthly." + target, **kwargs)
            self.stack.append(patcher)
            setattr(self, target.split(".")[-1], patcher.start())
        self.MetricCalculator.return_value.calculate.return_value = {"rows": {}}
        self.mysql_url.return_value = URL.create("mysql+pymysql", host="test", database="test")
        self.ml = types.ModuleType("backend.app.ml.test_pipeline")
        self.ml.run = MagicMock(return_value={"status": "SUCCESS", "model_run_id": 1})
        self.modules = patch.dict(sys.modules, {self.ml.__name__: self.ml})
        self.modules.start()

    def tearDown(self):
        self.modules.stop()
        for patcher in reversed(self.stack):
            patcher.stop()
        self.tmp.cleanup()

    def collect(self, month, directory, *_):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"customs_raw_{month}.csv"
        path.write_text("fixture", encoding="utf-8")
        return path

    def run_month(self, **kwargs):
        with patch.dict("os.environ", {"CUSTOMS_ML_ENTRYPOINT": ""}):
            return monthly.run_month("202509", output=self.root, env_path=self.root / ".env", **kwargs)

    def test_metrics_failure_restarts_at_metrics_without_collecting_or_loading_again(self):
        self.MetricCalculator.return_value.calculate.side_effect = [RuntimeError("실패"), {"rows": {}}]
        first = self.run_month(ml_entrypoint="backend.app.ml.test_pipeline:run")
        self.assertEqual(first["status"], "FAILED")
        self.assertEqual(first["current_stage"], "metrics")
        second = self.run_month(ml_entrypoint="backend.app.ml.test_pipeline:run")
        self.assertEqual(second["status"], "SUCCESS")
        self.collect_month.assert_called_once()
        self.load_csv.assert_called_once()
        self.ml.run.assert_called_once()

    def test_ml_is_waiting_until_real_entrypoint_is_configured_then_only_ml_runs(self):
        first = self.run_month()
        self.assertEqual(first["status"], "WAITING_FOR_ML")
        self.assertEqual(first["current_stage"], "ml")
        second = self.run_month(ml_entrypoint="backend.app.ml.test_pipeline:run")
        self.assertEqual(second["status"], "SUCCESS")
        self.collect_month.assert_called_once()
        self.load_csv.assert_called_once()
        self.MetricCalculator.return_value.calculate.assert_called_once()
        self.ml.run.assert_called_once()

    def test_failed_load_is_retried_before_metrics_or_ml(self):
        self.load_csv.side_effect = [{"status": "FAILED"}, {"status": "LOADED"}]
        self.assertEqual(self.run_month()["current_stage"], "load")
        self.MetricCalculator.assert_not_called()
        self.assertEqual(self.run_month()["status"], "WAITING_FOR_ML")
        self.collect_month.assert_called_once()
        self.assertEqual(self.load_csv.call_count, 2)

    def test_changed_checkpoint_csv_requires_explicit_refresh(self):
        self.run_month()
        next(self.root.glob("202509/collection_*/customs_raw_*.csv")).write_text("changed")
        self.assertEqual(self.run_month()["status"], "FAILED")
        self.assertEqual(self.run_month(refresh=True)["status"], "WAITING_FOR_ML")
        self.assertEqual(self.collect_month.call_count, 2)

    def test_same_month_lock_is_exclusive_and_released(self):
        path = self.root / "run.lock"
        with monthly.month_lock(path):
            with self.assertRaises(ValidationError):
                with monthly.month_lock(path):
                    self.fail("동시 실행 허용")
        with monthly.month_lock(path):
            pass

    def test_occupied_lock_returns_failed_and_registers_retry_without_overwriting_state(self):
        folder = self.root / "202509"
        folder.mkdir()
        original = '{"status":"RUNNING"}'
        (folder / "state.json").write_text(original, encoding="utf-8")
        scheduler = MagicMock()
        with monthly.month_lock(folder / "run.lock"):
            result = monthly.monthly_job(scheduler, "202509", output=self.root, env_path=self.root / ".env")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(scheduler.add_job.call_args.kwargs["args"][1], "202509")
        self.assertEqual((folder / "state.json").read_text(encoding="utf-8"), original)
        self.collect_month.assert_not_called()

    def test_corrupt_json_is_preserved_and_pipeline_recovers(self):
        folder = self.root / "202509"
        folder.mkdir()
        (folder / "state.json").write_text('{broken', encoding="utf-8")
        result = self.run_month()
        self.assertEqual(result["status"], "WAITING_FOR_ML")
        backup = next(folder.glob("state.corrupt_*.json"))
        self.assertEqual(backup.read_text(encoding="utf-8"), '{broken')
        self.assertEqual(json.loads((folder / "state.json").read_text(encoding="utf-8"))["completed"],
                         ["collect", "load", "metrics"])

    def test_invalid_checkpoint_structure_is_also_recovered(self):
        folder = self.root / "202509"
        folder.mkdir()
        (folder / "state.json").write_text('[]', encoding="utf-8")
        self.assertEqual(self.run_month()["status"], "WAITING_FOR_ML")
        self.assertEqual(len(list(folder.glob("state.corrupt_*.json"))), 1)

    def test_preparation_io_error_returns_failed_and_registers_retry(self):
        scheduler = MagicMock()
        with patch.object(monthly, "read_state", side_effect=PermissionError("테스트 읽기 실패")):
            result = monthly.monthly_job(scheduler, "202509", output=self.root, env_path=self.root / ".env")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(scheduler.add_job.call_args.kwargs["args"][1], "202509")
        self.collect_month.assert_not_called()

    def test_unverified_legacy_checkpoint_is_recollected_once(self):
        self.run_month()
        path = self.root / "202509/state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        target = self.mysql_url.return_value.render_as_string(hide_password=True)
        state["signature"] = hashlib.sha256(json.dumps(["202509", "auto", ["US"], target]).encode()).hexdigest()
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_month()["status"], "WAITING_FOR_ML")
        self.assertEqual(self.collect_month.call_count, 2)
        self.assertEqual(self.load_csv.call_count, 2)

    def test_empty_country_confirmation_is_scoped_to_month_and_survives_resume(self):
        self.resolve_countries.return_value = ["US", "VN"]
        first = self.run_month(confirmed_empty_countries=["VN"])
        self.assertEqual(first["confirmed_empty_countries"], ["VN"])
        second = self.run_month()
        self.assertEqual(second["status"], "WAITING_FOR_ML")
        self.collect_month.assert_called_once()
        self.assertEqual(self.collect_month.call_args.args[-1], ["VN"])
        other = monthly.run_month("202508", output=self.root, env_path=self.root / ".env")
        self.assertEqual(other["confirmed_empty_countries"], [])

    def test_unknown_empty_country_confirmation_fails_before_collection(self):
        self.assertEqual(self.run_month(confirmed_empty_countries=["VN"])["status"], "FAILED")
        self.collect_month.assert_not_called()


class CountryCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = self.root / "_cache"
        self.cache.mkdir()
        self.rows = [dict(year="2025.09", statCd="US", hsCd=code, statKor="테스트 품목",
                         expDlr="100", expWgt="1", impDlr="0", impWgt="0")
                     for code in ("0101210000", "0101211000")]

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self):
        with patch.object(monthly.collector, "load_key", return_value="TEST"):
            return monthly.collect_month("202509", self.root, self.root / ".env", ["US"])

    def test_partial_legacy_cache_without_completion_marker_is_refetched(self):
        monthly.collector.write_csv(self.cache / "US.csv", self.rows[:1])
        with patch.object(monthly.collector, "fetch", return_value=self.rows) as fetch:
            path = self.collect()
        fetch.assert_called_once()
        self.assertEqual(len(monthly.collector.read_csv(path)), 2)
        self.assertTrue(monthly.cache_ready(self.cache / "US.csv", "202509", "US"))

    def test_write_interrupted_after_first_row_never_publishes_complete_cache(self):
        write = monthly.collector.write_csv
        def interrupted(path, rows):
            if path.name.endswith(".csv.tmp"):
                write(path, rows[:1])
                raise OSError("쓰기 중 종료 모의")
            return write(path, rows)
        with patch.object(monthly.collector, "fetch", return_value=self.rows), \
                patch.object(monthly.collector, "write_csv", side_effect=interrupted):
            with self.assertRaises(ValidationError):
                self.collect()
        self.assertFalse((self.cache / "US.csv").exists())
        self.assertFalse((self.cache / "US.json").exists())
        self.assertFalse((self.cache / "US.csv.tmp").exists())
        with patch.object(monthly.collector, "fetch", return_value=self.rows) as fetch:
            self.collect()
        fetch.assert_called_once()
        self.assertTrue(monthly.cache_ready(self.cache / "US.csv", "202509", "US"))

    def test_completed_cache_is_reused_without_api_calls(self):
        monthly.save_cache(self.cache / "US.csv", self.rows, "202509", "US")
        with patch.object(monthly.collector, "fetch") as fetch:
            path = self.collect()
        fetch.assert_not_called()
        self.assertEqual(len(monthly.collector.read_csv(path)), 2)

    def test_truncated_or_wrong_period_marker_is_rejected(self):
        path = self.cache / "US.csv"
        for mode in ("truncated", "month", "country", "row_count", "broken_marker"):
            with self.subTest(mode=mode):
                monthly.save_cache(path, self.rows, "202509", "US")
                metadata_path = path.with_suffix(".json")
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if mode == "truncated":
                    monthly.collector.write_csv(path, self.rows[:1])
                elif mode == "broken_marker":
                    metadata_path.write_text('{broken', encoding="utf-8")
                else:
                    metadata[{"month":"month", "country":"country", "row_count":"row_count"}[mode]] = {
                        "month":"202508", "country":"VN", "row_count":1}[mode]
                    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                with patch.object(monthly.collector, "fetch", return_value=self.rows) as fetch:
                    self.collect()
                fetch.assert_called_once()

    def test_metadata_write_failure_does_not_make_cache_eligible(self):
        with patch.object(monthly.collector, "fetch", return_value=self.rows), \
                patch.object(monthly, "write_state", side_effect=OSError("표식 저장 실패")):
            with self.assertRaises(ValidationError):
                self.collect()
        self.assertFalse(monthly.cache_ready(self.cache / "US.csv", "202509", "US"))
        with patch.object(monthly.collector, "fetch", return_value=self.rows) as fetch:
            self.collect()
        fetch.assert_called_once()

    def test_missing_api_key_becomes_retryable_failure(self):
        with patch.object(monthly.collector, "load_key", side_effect=SystemExit("키 없음")):
            with self.assertRaises(ValidationError):
                monthly.collect_month("202509", self.root, self.root / ".env", ["US"])

    def test_unpublished_country_blocks_load_then_is_refetched_without_refetching_ready_country(self):
        vn_rows = [{**row, "statCd": "VN"} for row in self.rows]
        with patch.object(monthly.collector, "resolve_countries", return_value=["US", "VN"]), \
                patch.object(monthly.collector, "load_key", return_value="TEST"), \
                patch.object(monthly.collector, "fetch", side_effect=[self.rows, [], vn_rows]) as fetch, \
                patch.object(monthly, "mysql_url", return_value=URL.create("mysql+pymysql", host="test")), \
                patch.object(monthly, "make_engine"), patch.object(monthly, "MetricCalculator") as metrics, \
                patch.object(monthly, "load_csv", return_value={"status": "LOADED"}) as load, \
                patch.dict("os.environ", {"CUSTOMS_ML_ENTRYPOINT": ""}):
            metrics.return_value.calculate.return_value = {"rows": {}}
            options = dict(output=self.root, env_path=self.root / ".env")
            first = monthly.run_month("202509", **options)
            self.assertEqual(first["status"], "FAILED")
            self.assertNotIn("collect", first["completed"])
            load.assert_not_called()
            report_path = self.root / "202509" / first["collection_dir"] / "collection.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["unconfirmed_empty_countries"], ["VN"])
            second = monthly.run_month("202509", **options)
            self.assertEqual(second["status"], "WAITING_FOR_ML")
            self.assertEqual([call.args[1] for call in fetch.call_args_list], ["US", "VN", "VN"])
            load.assert_called_once()
            merged = monthly.collector.read_csv(load.call_args.args[0].csv)
            self.assertEqual({row["statCd"] for row in merged}, {"US", "VN"})

    def test_total_only_responses_are_not_cached_and_later_data_is_fetched(self):
        totals = [{**self.rows[0], "year": "총계", "hsCd": "-"}]
        with patch.object(monthly.collector, "fetch", side_effect=[totals, totals, self.rows]) as fetch:
            for _ in range(2):
                with self.assertRaises(ValidationError):
                    self.collect()
                self.assertFalse((self.cache / "US.json").exists())
            path = self.collect()
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(len(monthly.collector.read_csv(path)), 2)

    def test_total_only_cache_with_valid_hash_and_count_is_still_refetched(self):
        path = self.cache / "US.csv"
        totals = [{**self.rows[0], "year": "총계", "hsCd": "-"}]
        monthly.collector.write_csv(path, totals)
        monthly.write_state(path.with_suffix(".json"), {
            "version": monthly.CACHE_VERSION, "complete": True, "month": "202509", "country": "US",
            "row_count": 1, "sha256": monthly.file_digest(path)})
        self.assertFalse(monthly.cache_ready(path, "202509", "US"))
        with patch.object(monthly.collector, "fetch", return_value=self.rows) as fetch:
            self.collect()
        fetch.assert_called_once()
        with self.assertRaises(ValidationError):
            monthly.save_cache(path, totals, "202509", "US")

    def test_confirmed_no_trade_country_is_explicit_in_report_without_fake_rows(self):
        with patch.object(monthly.collector, "fetch", side_effect=[self.rows, []]), \
                patch.object(monthly.collector, "load_key", return_value="TEST"):
            path = monthly.collect_month("202509", self.root, self.root / ".env", ["US", "VN"], ["VN"])
        self.assertEqual(len(monthly.collector.read_csv(path)), 2)
        report = json.loads((self.root / "collection.json").read_text(encoding="utf-8"))
        self.assertEqual(report["confirmed_empty_countries"], ["VN"])
        self.assertEqual(report["unconfirmed_empty_countries"], [])
        self.assertTrue(report["collection_complete"])
        self.assertFalse((self.cache / "VN.json").exists())


class MonthlyScheduleTest(unittest.TestCase):
    def test_tenth_day_kst_and_previous_month_rollover(self):
        main = monthly
        with patch.dict("os.environ", {}, clear=True), patch.object(main, "dotenv_values", return_value={}):
            scheduler = main.build_scheduler()
        job = next(j for j in scheduler.get_jobs() if j.id == "customs_monthly")
        now = datetime(2026, 1, 1, tzinfo=main.KST)
        self.assertEqual(job.trigger.get_next_fire_time(None, now), datetime(2026, 1, 10, 3, tzinfo=main.KST))
        fake_datetime = MagicMock()
        fake_datetime.now.return_value = datetime(2026, 1, 10, 3, tzinfo=main.KST)
        with patch.object(main, "datetime", fake_datetime), patch.object(main, "run_month", return_value={"status": "SUCCESS"}) as run:
            main.monthly_job(scheduler)
        run.assert_called_once_with("202512")

    def test_retry_keeps_original_target_month_across_calendar_boundary(self):
        main = monthly
        scheduler = MagicMock()
        with patch.object(main, "run_month", return_value={"status": "FAILED"}):
            main.monthly_job(scheduler, "202512")
        self.assertEqual(scheduler.add_job.call_args.kwargs["args"][1:], ["202512", 2])

    def test_registration_keeps_existing_jobs(self):
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler(timezone=monthly.KST)
        scheduler.add_job(lambda: None, "interval", hours=1, id="existing_shared_job")
        with patch.dict("os.environ", {}, clear=True), patch.object(monthly, "dotenv_values", return_value={}):
            monthly.register_jobs(scheduler)
        self.assertEqual({j.id for j in scheduler.get_jobs()},
            {"existing_shared_job", "customs_monthly", "customs_monthly_startup"})
        self.assertIs(scheduler.get_job("customs_monthly_startup").func, monthly.startup_job)
        self.assertIs(scheduler.get_job("customs_monthly").func, monthly.startup_job)

    def test_restart_resumes_older_failed_month_before_latest_previous_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for month, status in (("202601", "FAILED"), ("202512", "RUNNING"),
                    ("202511", "SUCCESS"), ("202510", "WAITING_FOR_ML"), ("202604", "FAILED")):
                folder = root / month
                folder.mkdir()
                (folder / "state.json").write_text(json.dumps({"status": status}), encoding="utf-8")
            frozen = MagicMock()
            frozen.now.return_value = datetime(2026, 3, 1, tzinfo=monthly.KST)
            scheduler = MagicMock()
            with patch.object(monthly, "datetime", frozen), patch.object(monthly, "run_month",
                    side_effect=[{"status":"SUCCESS"}, {"status":"FAILED"}, {"status":"SUCCESS"}]) as run:
                monthly.startup_job(scheduler, output=root)
            self.assertEqual([call.args[0] for call in run.call_args_list], ["202512", "202601", "202602"])
            self.assertEqual(scheduler.add_job.call_args.kwargs["args"][1], "202601")

    def test_checkpoint_parse_failure_and_missing_state_remain_discoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for month, content in (("202512", '{broken'), ("202601", '[]')):
                folder = root / month
                folder.mkdir()
                (folder / "state.json").write_text(content, encoding="utf-8")
            (root / "202602").mkdir()
            (root / "not_a_month").mkdir()
            self.assertEqual(monthly.pending_months(root, "202602"), ["202512", "202601", "202602"])

    def test_uncaught_runner_exception_still_schedules_same_month_retry(self):
        scheduler = MagicMock()
        with patch.object(monthly, "run_month", side_effect=OSError("예상 밖 파일 오류")):
            result = monthly.monthly_job(scheduler, "202601")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(scheduler.add_job.call_args.kwargs["args"][1], "202601")

    def test_startup_discovery_error_is_retried_and_bounded(self):
        scheduler = MagicMock()
        with patch.object(monthly, "pending_months", side_effect=PermissionError("디렉터리 접근 실패")):
            monthly.startup_job(scheduler)
            self.assertEqual(scheduler.add_job.call_args.kwargs["id"], "customs_startup_retry")
            scheduler.reset_mock()
            monthly.startup_job(scheduler, attempt=30)
            scheduler.add_job.assert_not_called()

    def test_restart_after_long_shutdown_recovers_months_without_any_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "202512").mkdir()
            (root / "202512/state.json").write_text('{"status":"SUCCESS"}', encoding="utf-8")
            frozen = MagicMock()
            frozen.now.return_value = datetime(2026, 4, 10, tzinfo=monthly.KST)
            with patch.object(monthly, "datetime", frozen), patch.object(monthly, "run_month",
                    return_value={"status": "SUCCESS"}) as run, \
                    patch.dict("os.environ", {}, clear=True), patch.object(monthly, "dotenv_values", return_value={}):
                monthly.startup_job(MagicMock(), output=root)
            self.assertEqual([call.args[0] for call in run.call_args_list], ["202601", "202602", "202603"])

    def test_configured_start_recovers_even_when_no_month_was_ever_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "not_created"
            self.assertEqual(monthly.pending_months(root, "202602", "202511"),
                             ["202511", "202512", "202601", "202602"])
            self.assertEqual(monthly.pending_months(root, "202602"), ["202602"])
            with self.assertRaises(ValidationError):
                monthly.pending_months(root, "202602", "202600")
            with self.assertRaises(ValidationError):
                monthly.pending_months(root, "202602", "202603")

    def test_explicit_start_excludes_older_success_failed_and_legacy_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "202312"
            old.mkdir()
            for state in ({"status": "SUCCESS"}, {"status": "FAILED"},
                    {"status": "SUCCESS", "signature": "legacy", "collection_policy_version": 1}):
                with self.subTest(state=state):
                    (old / "state.json").write_text(json.dumps(state), encoding="utf-8")
                    self.assertEqual(monthly.pending_months(root, "202602", "202601"), ["202601", "202602"])

    def test_startup_explicit_start_overrides_environment_and_old_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "202312").mkdir()
            (root / "202312/state.json").write_text('{"status":"SUCCESS"}', encoding="utf-8")
            frozen = MagicMock()
            frozen.now.return_value = datetime(2026, 3, 10, tzinfo=monthly.KST)
            with patch.object(monthly, "datetime", frozen), patch.object(monthly, "run_month",
                    return_value={"status": "SUCCESS"}) as run, \
                    patch.dict("os.environ", {"CUSTOMS_START_MONTH": "202501"}), \
                    patch.object(monthly, "dotenv_values", return_value={}):
                monthly.startup_job(MagicMock(), output=root, start_month="202601")
            self.assertEqual([call.args[0] for call in run.call_args_list], ["202601", "202602"])

    def test_environment_start_month_is_used_without_forwarding_it_to_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen = MagicMock()
            frozen.now.return_value = datetime(2026, 3, 10, tzinfo=monthly.KST)
            with patch.object(monthly, "datetime", frozen), patch.object(monthly, "run_month",
                    return_value={"status": "SUCCESS"}) as run, \
                    patch.dict("os.environ", {"CUSTOMS_START_MONTH": "202512"}), \
                    patch.object(monthly, "dotenv_values", return_value={}):
                monthly.startup_job(MagicMock(), output=Path(tmp))
            self.assertEqual([call.args[0] for call in run.call_args_list], ["202512", "202601", "202602"])
            self.assertNotIn("start_month", run.call_args.kwargs)

    def test_prior_policy_completed_month_is_revalidated_but_current_policy_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for month, version in (("202512", 1), ("202601", monthly.CACHE_VERSION)):
                folder = root / month
                folder.mkdir()
                (folder / "state.json").write_text(json.dumps({"status": "WAITING_FOR_ML", "signature": "test",
                    "collection_policy_version": version}), encoding="utf-8")
            self.assertEqual(monthly.pending_months(root, "202602"), ["202512", "202602"])
