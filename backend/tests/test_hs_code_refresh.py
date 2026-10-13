"""HS 판 갱신 흐름·batch_run 기록·스케줄을 가짜 단계 함수와 메모리 SQLite로 확인합니다 (다운로드·LLM·MySQL 없이 실행).

실행 (저장소 루트에서): python -m unittest backend/tests/test_hs_code_refresh.py
"""
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db.session import make_engine, session_scope  # noqa: E402
from backend.app.models.base import Base  # noqa: E402
from backend.app.models.batch import BatchRun, ServiceOption  # noqa: E402
from backend.app.scheduler import main as sched  # noqa: E402
from backend.app.scheduler.jobs import hs_code_refresh as refresh  # noqa: E402

KST = refresh.KST


NEED = {"01", "0101", "010121", "0101211000"}  # xlsx로 만들 코드 (가짜)


def fake_steps(file_date="20280101", loaded=frozenset(), missing=0, load_rc=0, calls=None):
    calls = calls if calls is not None else []

    def download(run):
        calls.append("download")
        run.request_count += 4  # 포털 요청 4번
        return Path(f"hs_names_{file_date}.xlsx")

    def build(xlsx, hs_ver, output_dir):
        calls.append(("build", xlsx.name, hs_ver))
        return {"csv": Path("hs.csv"), "hs_ver": hs_ver, "rows": 10, "missing": missing,
                "received": 17072, "requests": 150}

    def load(csv_path, track):
        calls.append(("load", track))
        return load_rc

    return {"download": download, "build": build, "load": load, "loaded": lambda ver: set(loaded),
            "expected_of": lambda xlsx: set(NEED), "log_dir": LOG_DIR}


def close_logs():
    for h in refresh.log.handlers:
        h.close()
    refresh.log.handlers.clear()


TMP = tempfile.TemporaryDirectory()  # 테스트 로그는 실제 hscode_outputs/logs가 아닌 임시 폴더에
LOG_DIR = Path(TMP.name)


class RefreshFlowTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite://")  # batch_run 기록용 (실제 MySQL에 쓰지 않음)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        close_logs()
        self.engine.dispose()

    def run_with(self, today, **kw):
        calls = []
        status = refresh.run_refresh(date(*today), batch_engine=self.engine, **fake_steps(calls=calls, **kw))
        return status, calls

    def batch_rows(self):
        with session_scope(self.engine) as s:
            return [(r.job_name, r.target_table, str(r.period_start), str(r.period_end), r.status,
                     r.request_count, r.received_count, r.finished_at is not None)
                    for r in s.query(BatchRun).order_by(BatchRun.run_id)]

    def test_new_edition_is_built_and_loaded(self):
        status, calls = self.run_with((2028, 1, 1))
        self.assertEqual(status, "DONE")
        # 적재는 갱신 실행 기록에 포함되므로 load는 track=False로 부름
        self.assertEqual(calls, ["download", ("build", "hs_names_20280101.xlsx", "HS2028"), ("load", False)])
        self.assertEqual(self.batch_rows(),
                         [("HS_CODE_REFRESH", "hs_code", "2028-01-01", "2032-12-31", "SUCCESS", 154, 17072, True)])
        with session_scope(self.engine) as s:  # 출처가 없으면 SOURCE 행으로 등록
            src = s.get(ServiceOption, "KCS_HS_NAMES")
            self.assertEqual((src.option_type, src.enabled), ("SOURCE", True))

    def test_every_run_is_recorded(self):
        for kw in ({"file_date": "20260101"}, {"loaded": NEED}, {"missing": 3}):  # NOT_READY, ALREADY_LOADED, FAILED
            self.run_with((2028, 1, 1), **kw)
        self.assertEqual([(r[4], r[5], r[6]) for r in self.batch_rows()],
                         [("SUCCESS", 4, 0), ("SUCCESS", 4, 0), ("FAILED", 154, 17072)])

    def test_old_edition_means_not_ready(self):
        status, calls = self.run_with((2028, 1, 1), file_date="20260101")  # 포털에 아직 HS2022 판만 있음
        self.assertEqual((status, calls), ("NOT_READY", ["download"]))

    def test_already_loaded_skips_llm(self):
        status, calls = self.run_with((2028, 3, 1), loaded=NEED)
        self.assertEqual((status, calls), ("ALREADY_LOADED", ["download"]))

    def test_partial_load_is_rebuilt(self):
        status, calls = self.run_with((2028, 3, 1), loaded={"01"})  # 1행만 있으면 완료로 보지 않음
        self.assertEqual(status, "DONE")
        self.assertIn(("load", False), calls)

    def test_failures(self):
        self.assertEqual(self.run_with((2028, 1, 1), missing=3)[0], "FAILED")      # 요약 누락 → 적재 안 함
        self.assertNotIn("load", self.run_with((2028, 1, 1), missing=3)[1])
        self.assertEqual(self.run_with((2028, 1, 1), load_rc=1)[0], "FAILED")      # 적재 실패

        def broken(run):
            raise refresh.DownloadError("포털 오류")
        steps = {**fake_steps(), "download": broken}
        self.assertEqual(refresh.run_refresh(date(2028, 1, 1), batch_engine=self.engine, **steps), "FAILED")
        self.assertEqual({r[4] for r in self.batch_rows()}, {"FAILED"})

    def test_job_runs_even_if_batch_db_is_down(self):
        down = make_engine("mysql+pymysql://u:p@127.0.0.1:1/x")  # 기록용 DB 연결 실패 → 경고만 남기고 작업은 진행
        try:
            status = refresh.run_refresh(date(2028, 1, 1), batch_engine=down, **fake_steps())
        finally:
            down.dispose()
        self.assertEqual(status, "DONE")


class SchedulerTest(unittest.TestCase):
    def test_trigger_every_five_years_from_2028(self):
        now, fires = datetime(2026, 10, 13, tzinfo=KST), []
        prev = None
        for _ in range(3):
            prev = sched.HS_REFRESH_TRIGGER.get_next_fire_time(prev, now)
            fires.append(prev.strftime("%Y-%m-%d %H:%M %z"))
            now = prev.replace(second=1)
        self.assertEqual(fires, ["2028-01-01 00:15 +0900", "2033-01-01 00:15 +0900", "2038-01-01 00:15 +0900"])

    def test_retry_next_day_when_not_ready(self):
        class FakeScheduler:
            jobs = []

            def add_job(self, *args, **kw):
                self.jobs.append(kw)

        original = refresh.run_refresh
        try:
            for status, retried in (("NOT_READY", True), ("FAILED", True), ("DONE", False), ("ALREADY_LOADED", False)):
                fake = FakeScheduler()
                fake.jobs = []
                refresh.run_refresh = lambda s=status: s
                self.assertEqual(sched.hs_refresh_job(fake), status)
                self.assertEqual(bool(fake.jobs), retried, status)
                if retried:
                    run_date = fake.jobs[0]["run_date"]
                    self.assertEqual((run_date.hour, run_date.minute, fake.jobs[0]["args"][1]), (0, 15, 2))
            fake = FakeScheduler()
            fake.jobs = []
            refresh.run_refresh = lambda: "NOT_READY"
            sched.hs_refresh_job(fake, attempt=sched.RETRY_DAYS)  # 마지막 시도면 더 예약하지 않음
            self.assertEqual(fake.jobs, [])
        finally:
            refresh.run_refresh = original
            close_logs()

    def test_catch_up_every_start_after_2028(self):
        # 일부만 적재된 채 재시작해도 갱신 작업이 실행되어야 함 (완료 판단은 작업 안에서)
        self.assertFalse(sched.needs_catch_up(datetime(2027, 6, 1, tzinfo=KST)))
        self.assertTrue(sched.needs_catch_up(datetime(2028, 2, 1, tzinfo=KST)))
        self.assertTrue(sched.needs_catch_up(datetime(2033, 7, 1, tzinfo=KST)))


def tearDownModule():
    close_logs()
    TMP.cleanup()


if __name__ == "__main__":
    unittest.main()
