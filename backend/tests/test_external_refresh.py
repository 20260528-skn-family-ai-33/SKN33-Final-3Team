"""외부 데이터 월간 갱신의 단계별 batch_run 기록·실패 로그·BACI 판 확인을 가짜 수집 함수로 확인합니다.

실행: python -m unittest backend/tests/test_external_refresh.py
"""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.batch import BatchRun
from backend.app.scheduler import main as sched
from backend.app.scheduler.jobs import external_refresh as job

ec = job.ec


class ExternalRefreshTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite://")  # batch_run 기록용 (실제 MySQL에 쓰지 않음)
        Base.metadata.create_all(self.engine)
        tmp = Path(tempfile.mkdtemp())
        logs = tmp / "logs"
        for target, value in ((ec, ("OUT_DIR", tmp)), (job, ("LOG_DIR", logs)),
                              (job, ("FAILURE_LOG", logs / "download_failures.log"))):
            patcher = mock.patch.object(target, *value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp, self.failure_log = tmp, logs / "download_failures.log"
        self.addCleanup(lambda: [h.close() for h in job.log.handlers])

    def runs(self):
        with session_scope(self.engine) as s:
            return [(r.job_name, r.target_table, r.status, r.period_start, r.request_count, r.received_count)
                    for r in s.query(BatchRun).order_by(BatchRun.run_id)]

    def test_records_each_step_and_logs_failures_separately(self):
        def fake_download(version, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"zip")
            return True

        def fake_baci(zip_path, from_year):
            (zip_path.parent / "baci_imports_hs22.csv").write_text("year\n2022\n2025\n")
            return {"rows": 300}

        (self.tmp / "baci").mkdir()
        (self.tmp / "baci" / "BACI_HS22_V202601.zip").write_bytes(b"old")  # 지난 판
        comtrade_dir = self.tmp / "comtrade"
        for year in (2025, 2026):
            (comtrade_dir / "raw" / str(year)).mkdir(parents=True)
            (comtrade_dir / f"comtrade_imports_{year}.csv").write_text("year\n")

        with mock.patch.object(ec, "download_baci", side_effect=fake_download), \
                mock.patch.object(ec, "collect_baci", side_effect=fake_baci), \
                mock.patch.object(ec, "collect_wdi", side_effect=ConnectionError("WDI 응답 없음")), \
                mock.patch.object(ec, "collect_comtrade", return_value={"requests": 5, "rows": 9}) as comtrade, \
                mock.patch.object(job, "dotenv_values", return_value={ec.ENV_KEY: "test"}):
            statuses = job.run(date(2027, 2, 1), engine=self.engine)

        self.assertEqual(statuses, {"BACI": "DONE", "WDI": "FAILED", "COMTRADE_2026": "DONE"})
        comtrade.assert_called_once_with(2026)  # BACI가 2025년까지 있으니 2026년만
        self.assertEqual([p.name for p in (self.tmp / "baci").glob("*.zip")], ["BACI_HS22_V202701c.zip"])
        self.assertEqual(sorted(p.name for p in comtrade_dir.rglob("*")),  # BACI가 덮은 2025년은 지움
                         ["2026", "comtrade_imports_2026.csv", "raw"])
        self.assertEqual(self.runs(), [
            ("EXTERNAL_BACI", "external_csv", "SUCCESS", date(2022, 1, 1), 1, 300),
            ("EXTERNAL_WDI", "external_csv", "FAILED", date(2022, 1, 1), 0, 0),
            ("EXTERNAL_COMTRADE", "external_csv", "SUCCESS", date(2026, 1, 1), 5, 9)])
        failures = self.failure_log.read_text("utf-8").splitlines()
        self.assertEqual(len(failures), 1)
        self.assertIn("| WDI | 2022~2026 | ConnectionError: WDI 응답 없음", failures[0])

    def test_comtrade_limited_to_last_two_years_without_baci(self):
        wdi = self.tmp / "wdi"
        wdi.mkdir()
        for name in ("wdi_2022_2025.csv", "wdi_2022_2026.csv"):  # collect_wdi가 2026년판을 새로 썼다고 가정
            (wdi / name).write_text("year\n")
        with mock.patch.object(ec, "download_baci", return_value=False), \
                mock.patch.object(ec, "collect_wdi", return_value={}), \
                mock.patch.object(ec, "collect_comtrade", return_value={}) as comtrade, \
                mock.patch.object(job, "dotenv_values", return_value={ec.ENV_KEY: "test"}):
            job.run(date(2027, 2, 1), engine=self.engine)
        self.assertEqual([c.args[0] for c in comtrade.call_args_list], [2025, 2026])  # 2022~2024는 받지 않음
        self.assertEqual([p.name for p in wdi.iterdir()], ["wdi_2022_2026.csv"])  # 이전 기간 파일은 지움

    def test_baci_not_ready_then_already(self):
        with mock.patch.object(ec, "download_baci", return_value=False) as download:
            self.assertEqual(job.refresh_baci(date(2027, 1, 1)), {"status": "NOT_READY", "requests": 3})
        self.assertEqual([c.args[0] for c in download.call_args_list], ["V202701c", "V202701b", "V202701"])
        baci = self.tmp / "baci"
        baci.mkdir()
        (baci / "BACI_HS22_V202701b.zip").write_bytes(b"zip")  # 수정판만 있어도 찾는다
        (baci / "baci_imports_hs22.csv").write_text("year\n2025\n")  # zip보다 나중에 만든 집계본
        self.assertTrue(job.baci_done(date(2027, 3, 1)))
        with mock.patch.object(ec, "collect_wdi", return_value={}), \
                mock.patch.object(job, "dotenv_values", return_value={}):
            self.assertEqual(job.run(date(2027, 3, 1), engine=self.engine)["BACI"], "ALREADY")
        self.assertEqual([r[0] for r in self.runs()], ["EXTERNAL_WDI"])  # ALREADY는 batch_run에 남기지 않음

    def test_registered_monthly(self):
        jobs = {j.id: j for j in sched.build_scheduler().get_jobs()}
        self.assertEqual(str(jobs["external_refresh"].trigger), str(sched.EXTERNAL_TRIGGER))


if __name__ == "__main__":
    unittest.main()
