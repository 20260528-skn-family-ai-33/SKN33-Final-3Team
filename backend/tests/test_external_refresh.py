"""외부 데이터 월간 갱신의 단계별 batch_run 기록·실패 로그·BACI 판 확인을 가짜 수집 함수로 확인합니다.

실행: python -m unittest backend/tests/test_external_refresh.py
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd
import numpy as np

from backend.app.db.session import make_engine, session_scope
from backend.app.ml import features as F, preprocess as pp
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
        for year in (2024, 2025, 2026):
            (comtrade_dir / "raw" / str(year)).mkdir(parents=True)
            (comtrade_dir / f"comtrade_imports_{year}.csv").write_text("year\n")

        with mock.patch.object(ec, "download_baci", side_effect=fake_download), \
                mock.patch.object(ec, "collect_baci", side_effect=fake_baci), \
                mock.patch.object(ec, "collect_wdi", side_effect=ConnectionError("WDI 응답 없음")), \
                mock.patch.object(ec, "collect_comtrade", return_value={"requests": 5, "rows": 9}) as comtrade, \
                mock.patch.object(job, "dotenv_values", return_value={ec.ENV_KEY: "test"}):
            statuses = job.run(date(2027, 2, 1), engine=self.engine)

        self.assertEqual(statuses, {"BACI": "DONE", "WDI": "FAILED", "COMTRADE_2025": "DONE", "COMTRADE_2026": "DONE"})
        # BACI가 2025년까지 있으니 2025년(연결 연도)과 2026년
        self.assertEqual([c.args[0] for c in comtrade.call_args_list], [2025, 2026])
        self.assertEqual([p.name for p in (self.tmp / "baci").glob("*.zip")], ["BACI_HS22_V202701c.zip"])
        self.assertEqual(sorted(p.name for p in comtrade_dir.rglob("*")),  # 연결 연도 2025년은 남기고 2024년만 지움
                         ["2025", "2026", "comtrade_imports_2025.csv", "comtrade_imports_2026.csv", "raw"])
        self.assertEqual(self.runs(), [
            ("EXTERNAL_BACI", "external_csv", "SUCCESS", date(2022, 1, 1), 1, 300),
            ("EXTERNAL_WDI", "external_csv", "FAILED", date(2022, 1, 1), 0, 0),
            ("EXTERNAL_COMTRADE", "external_csv", "SUCCESS", date(2025, 1, 1), 5, 9),
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

    def write_availability(self, policy="first_release_dates"):
        (self.tmp / "availability.csv").write_text(
            "dataset,source,year,available_from,evidence\n"
            'market,BACI,2024,2026-01-22,"BACI V202601 릴리스 노트, 2026-01-22"\n', "utf-8")
        (self.tmp / "availability_snapshot.json").write_text(json.dumps(
            {"policy": policy, "files": [{"path": "data\\external\\baci\\baci_imports_hs22.csv", "sha256": "old"}]}),
            "utf-8")
        for folder, name, body in (("baci", "baci_imports_hs22.csv", "year,country_code\n2024,NA\n2025,VN\n"),
                                   ("comtrade", "comtrade_imports_2025.csv", "year,country_code\n2025,VN\n"),
                                   ("wdi", "wdi_2022_2026.csv", "year,country_code,gdp_usd\n2025,VN,1.0\n2026,VN,\n")):
            (self.tmp / folder).mkdir(exist_ok=True)
            (self.tmp / folder / name).write_text(body, "utf-8")

    def test_availability_follows_refreshed_files(self):
        self.write_availability()
        with self.assertRaises(ValueError):  # 갱신 전: 수집한 파일이 기록과 달라 ML이 멈춘다
            pp.check_versions(self.tmp)
        job.record_availability(date(2027, 2, 1))
        pp.check_versions(self.tmp)  # 갱신 후 통과
        dates = pd.read_csv(self.tmp / "availability.csv", dtype=str, keep_default_na=False)
        self.assertEqual(sorted(zip(dates.source, dates.year, dates.available_from)), [
            ("BACI", "2024", "2026-01-22"),  # 기존 최초 공개일은 그대로
            ("BACI", "2025", "2027-02-01"), ("COMTRADE", "2025", "2027-02-01"), ("WDI", "2025", "2027-02-01")])
        # WDI 2026은 값이 없어 날짜를 미리 붙이지 않는다
        self.assertEqual(dates.evidence[0], "BACI V202601 릴리스 노트, 2026-01-22")

    def test_empty_wdi_year_uses_published_prior_year_until_values_arrive(self):
        (self.tmp / "wdi").mkdir()
        path = self.tmp / "wdi/wdi_2025_2026.csv"
        raw = pd.DataFrame([
            (2025, "VN", 100.0, 3.0, 10.0, 10.0),
            (2026, "VN", None, None, None, None),
        ], columns=["year", "country_code", "gdp_usd", "gdp_growth_pct", "population", "gdp_per_capita_usd"])
        raw.to_csv(path, index=False)
        (self.tmp / "availability.csv").write_text(
            "dataset,source,year,available_from,evidence\nwdi,WDI,2025,2026-06-01,test\n", "utf-8")
        (self.tmp / "availability_snapshot.json").write_text(
            json.dumps({"policy": "first_release_dates", "files": []}), "utf-8")
        anchors = pd.DataFrame({"anchor": pd.to_datetime(["2026-05-01", "2026-06-01", "2027-01-01", "2027-02-01"]),
                                "country_code": ["VN"] * 4})

        job.record_availability(date(2027, 1, 1))
        data = pp.load_external(self.tmp)  # 해시 확인과 실제 WDI 전처리까지 수행
        self.assertEqual(data["wdi_yearly"].year.tolist(), [2025])
        self.assertEqual(data["availability"].year.tolist(), [2025])
        result = F._add_wdi(anchors, data["wdi_yearly"], data["availability"])
        self.assertTrue(pd.isna(result.log_gdp.iloc[0]))  # 이전 연도라도 공개 전이면 사용하지 않음
        np.testing.assert_allclose(result.log_gdp.iloc[1:], np.log(100.0))

        # 빈 연도의 실제 값이 나중에 도착하면 그때 사용 가능일을 기록한다.
        raw.loc[raw.year == 2026, ["gdp_usd", "gdp_growth_pct", "population", "gdp_per_capita_usd"]] = [200., 4., 20., 10.]
        raw.to_csv(path, index=False)
        job.record_availability(date(2027, 2, 1))
        data = pp.load_external(self.tmp)
        self.assertEqual(data["availability"].set_index("year").loc[2026, "available_from"], "2027-02-01")
        result = F._add_wdi(anchors, data["wdi_yearly"], data["availability"])
        np.testing.assert_allclose(result.log_gdp.iloc[2:], np.log([100., 200.]))

    def test_availability_left_alone_for_other_policy(self):
        self.write_availability(policy="snapshot_dates")
        job.record_availability(date(2027, 2, 1))
        with self.assertRaises(ValueError):  # 판마다 날짜를 정하는 정책은 사람이 확인할 때까지 멈춘다
            pp.check_versions(self.tmp)

    def test_registered_monthly(self):
        jobs = {j.id: j for j in sched.build_scheduler().get_jobs()}
        self.assertEqual(str(jobs["external_refresh"].trigger), str(sched.EXTERNAL_TRIGGER))


if __name__ == "__main__":
    unittest.main()
