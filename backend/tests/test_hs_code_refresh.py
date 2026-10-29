"""HS 판 갱신 흐름·batch_run 기록·스케줄을 가짜 단계 함수와 메모리 SQLite로 확인합니다 (다운로드·LLM·MySQL 없이 실행).

실행 (저장소 루트에서): python -m unittest backend/tests/test_hs_code_refresh.py
"""
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db.session import make_engine, session_scope  # noqa: E402
from backend.app.models.base import Base  # noqa: E402
from backend.app.models.batch import BatchRun, ServiceOption  # noqa: E402
from backend.app.scheduler import main as sched  # noqa: E402
from backend.app.scheduler.jobs import hs_code_refresh as refresh  # noqa: E402
from backend.tests.scheduler_jobs import BASE_JOBS  # noqa: E402

KST = refresh.KST


NEED = {"01", "0101", "010121", "0101211000"}  # xlsx로 만들 코드 (가짜)


def loaded_as(codes, edition="20280101"):
    return {c: refresh.hb.source_for(edition) for c in codes}


def fake_steps(file_date="20280101", loaded=None, missing=0, load_rc=0, corr_rc=0, calls=None):
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

    def correlate(hs_ver):
        calls.append(("correlate", hs_ver))
        return corr_rc

    return {"download": download, "build": build, "load": load, "loaded": lambda ver: dict(loaded or {}), "correlate": correlate,
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
        # 적재 후 HS2028 ↔ 직전 판 대응표 확인·적재
        self.assertEqual(calls, ["download", ("build", "hs_names_20280101.xlsx", "HS2028"), ("load", False),
                                 ("correlate", "HS2028")])
        self.assertEqual(self.batch_rows(),  # 기간 = 기준일이 속한 해
                         [("HS_CODE_REFRESH", "hs_code", "2028-01-01", "2028-12-31", "SUCCESS", 154, 17072, True)])
        with session_scope(self.engine) as s:  # 출처가 없으면 SOURCE 행으로 등록
            src = s.get(ServiceOption, "KCS_HS_NAMES")
            self.assertEqual((src.option_type, src.enabled), ("SOURCE", True))

    def test_every_run_is_recorded(self):
        for kw in ({"file_date": "20260101"}, {"loaded": loaded_as(NEED)}, {"missing": 3}):  # NOT_READY, ALREADY_LOADED, FAILED
            self.run_with((2028, 1, 1), **kw)
        self.assertEqual([(r[4], r[5], r[6]) for r in self.batch_rows()],
                         [("SUCCESS", 4, 0), ("SUCCESS", 4, 0), ("FAILED", 154, 17072)])

    def test_old_edition_means_not_ready(self):
        status, calls = self.run_with((2028, 1, 1), file_date="20260101")  # 포털에 아직 HS2022 판만 있음
        self.assertEqual((status, calls), ("NOT_READY", ["download"]))
        status, calls = self.run_with((2027, 1, 1), file_date="20260101")  # 같은 HS 판이라도 올해 판이 아니면 대기
        self.assertEqual((status, calls), ("NOT_READY", ["download"]))
        status, calls = self.run_with((2026, 12, 20), file_date="20270101")  # 내년 판이 미리 올라와도 적용일 전엔 대기
        self.assertEqual((status, calls), ("NOT_READY", ["download"]))

    def test_new_yearly_edition_is_loaded(self):
        # 2027년 판이 올라오면: DB가 2026년 판으로 다 있어도 새 판으로 다시 적재 (새 10단위 추가·이름 갱신)
        status, calls = self.run_with((2027, 1, 5), file_date="20270101", loaded=loaded_as(NEED, "20260101"))
        self.assertEqual(status, "DONE")
        self.assertIn(("build", "hs_names_20270101.xlsx", "HS2022"), calls)
        status, calls = self.run_with((2027, 2, 1), file_date="20270101", loaded=loaded_as(NEED, "20270101"))
        self.assertEqual((status, calls), ("ALREADY_LOADED", ["download", ("correlate", "HS2022")]))

    def test_already_loaded_skips_llm(self):
        status, calls = self.run_with((2028, 3, 1), loaded=loaded_as(NEED))
        # 대응표가 빠져 있으면 채우도록 ALREADY_LOADED에서도 확인 (이미 있으면 확인만)
        self.assertEqual((status, calls), ("ALREADY_LOADED", ["download", ("correlate", "HS2028")]))

    def test_correlation_failure_retries(self):
        # 대응표 적재가 실패하면(UNSD에 아직 없음 등) FAILED → 다음 날 재시도. 재시도에서는 hs_code는 ALREADY_LOADED
        status, calls = self.run_with((2028, 1, 1), corr_rc=1)
        self.assertEqual((status, calls[-2:]), ("FAILED", [("load", False), ("correlate", "HS2028")]))
        status, _ = self.run_with((2028, 1, 2), loaded=loaded_as(NEED), corr_rc=1)
        self.assertEqual(status, "FAILED")
        status, calls = self.run_with((2028, 1, 1), file_date="20260101")  # NOT_READY면 대응표는 보지 않음
        self.assertNotIn(("correlate", "HS2028"), calls)

    def test_previous_ver(self):
        self.assertEqual([refresh.previous_ver(v) for v in ("HS2017", "HS2022", "HS2028", "HS2033")],
                         ["HS2017", "HS2017", "HS2022", "HS2028"])

    def test_ensure_correlation(self):
        from unittest import mock
        from backend.app.db.crud import CRUD
        from backend.app.models.hs_code_correlation import HsCodeCorrelation
        src = [("150920", "150910", "n:1"), ("150930", "150910", "n:1")]

        def put(*pairs):
            with session_scope(self.engine) as s:
                CRUD(HsCodeCorrelation).upsert_many(s, [{"new_ver": "HS2022", "new_code": n, "old_ver": "HS2017",
                                                         "old_code": o, "relation": "n:1", "group_code": "150920",
                                                         "source_edition": "t"} for n, o in pairs])

        corr = refresh.hs_correlation_load
        with mock.patch.object(corr, "fetch_pairs", return_value=(src, False)),                 mock.patch.object(corr, "run", return_value=0) as load,                 mock.patch.object(self.engine, "dispose"):  # 메모리 SQLite를 테스트에서 계속 쓰도록
            self.assertEqual(refresh.ensure_correlation("HS2017", self.engine), 0)  # 직전 판 없음 → 건너뜀
            self.assertEqual(refresh.ensure_correlation("HS2022", self.engine), 0)  # 없음 → 적재
            put(("150920", "150910"))  # 일부만 있음 → 다시 적재
            self.assertEqual(refresh.ensure_correlation("HS2022", self.engine), 0)
            self.assertEqual(load.call_count, 2)
            load.assert_called_with("HS2022", "HS2017", track=False)
            put(("150930", "150910"))  # 원천과 같음 → 확인만
            self.assertEqual(refresh.ensure_correlation("HS2022", self.engine), 0)
            self.assertEqual(load.call_count, 2)
        with mock.patch.object(corr, "fetch_pairs", side_effect=FileNotFoundError("UNSD에 없음")):
            self.assertEqual(refresh.ensure_correlation("HS2028", self.engine), 1)  # 원천 확인 실패 → FAILED로 재시도

    def test_partial_load_is_rebuilt(self):
        status, calls = self.run_with((2028, 3, 1), loaded=loaded_as({"01"}))  # 1행만 있으면 완료로 보지 않음
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
        # 실제 포트에 접속하지 않고 동일 DB 연결 예외를 주입합니다.
        from sqlalchemy.exc import OperationalError
        with patch.object(self.engine, 'connect', side_effect=OperationalError('connect', {}, Exception('synthetic offline DB'))):
            status = refresh.run_refresh(date(2028, 1, 1), batch_engine=self.engine, **fake_steps())
        self.assertEqual(status, "DONE")


def xlsx_bytes(rows):
    import io
    import pandas as pd
    buf = io.BytesIO()
    pd.DataFrame(rows, columns=["코드", "품목명"]).to_excel(buf, index=False)
    return buf.getvalue()


GOOD_XLSX = xlsx_bytes([("01", "살아 있는 동물"), ("0101", "말"), ("0101211000", "농가 사육용")])


class FakePortal:
    """공공데이터포털 다운로드 순서(데이터셋 화면 → 파일 정보 → 다운로드 제한 → 파일)를 흉내 냅니다. 파일 응답을 차례로 돌려줍니다."""

    def __init__(self, *contents):
        self.contents, self.downloads = list(contents), 0

    def Session(self):
        portal = self

        class Res:
            def __init__(self, text="", data=None, content=b""):
                self.text, self.data, self.content, self.headers = text, data, content, {}

            def json(self):
                return self.data

            def raise_for_status(self):
                pass

        class Session:
            headers, hooks = {}, {"response": []}

            def get(self, url, **kw):
                if "fileData.do" in url:
                    return Res(text="<title>관세청_HS부호 단위별 품목명_20270101 | 공공데이터포털</title>"
                                    "fn_fileDataDown('15130660', 'uddi:x', '','1', '3')")
                portal.downloads += 1
                return Res(content=portal.contents.pop(0))

            def post(self, url, **kw):
                if "selectFileDataDownload" in url:
                    return Res(data={"status": True, "atchFileId": "a", "fileDetailSn": "1",
                                     "dataSetFileDetailInfo": {"dataNm": "hs"}})
                return Res(data={"needCaptcha": False})

        return Session()


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def download(self, portal):
        from unittest import mock
        with mock.patch.object(refresh, "requests", portal):
            return refresh.download_latest(self.dir)

    def test_broken_xlsx_is_not_cached(self):
        portal = FakePortal(b"PKbroken", GOOD_XLSX)  # PK로 시작하지만 엑셀이 아닌 응답
        with self.assertRaisesRegex(refresh.DownloadError, "읽지 못했습니다"):
            self.download(portal)
        self.assertEqual(list(self.dir.iterdir()), [])  # 손상 파일·임시 파일이 남지 않음
        path = self.download(portal)  # 다음 실행에서 다시 받아 정상 저장
        self.assertEqual((path.name, portal.downloads), ("hs_names_20270101.xlsx", 2))
        self.assertEqual(self.download(portal), path)  # 정상 캐시는 다시 받지 않음
        self.assertEqual(portal.downloads, 2)

    def test_unreadable_cache_is_downloaded_again(self):
        (self.dir / "hs_names_20270101.xlsx").write_bytes(b"PKbroken")  # 이전에 저장된 손상 파일
        portal = FakePortal(GOOD_XLSX)
        path = self.download(portal)
        self.assertEqual(portal.downloads, 1)
        refresh.check_xlsx(path)  # 교체된 파일은 읽힘

    def test_xlsx_without_ten_digit_codes_is_rejected(self):
        portal = FakePortal(xlsx_bytes([("01", "살아 있는 동물")]))  # 엑셀이지만 10단위 코드가 없음
        with self.assertRaisesRegex(refresh.DownloadError, "10단위"):
            self.download(portal)
        self.assertEqual(list(self.dir.iterdir()), [])


class SchedulerTest(unittest.TestCase):
    def test_trigger_every_year(self):
        now, fires = datetime(2026, 10, 13, tzinfo=KST), []
        prev = None
        for _ in range(3):
            prev = sched.HS_REFRESH_TRIGGER.get_next_fire_time(prev, now)
            fires.append(prev.strftime("%Y-%m-%d %H:%M %z"))
            now = prev.replace(second=1)
        self.assertEqual(fires, ["2027-01-01 00:15 +0900", "2028-01-01 00:15 +0900", "2029-01-01 00:15 +0900"])

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

    def test_check_runs_on_every_start(self):
        # 일부만 적재된 채 재시작해도 갱신 작업이 실행되어야 함 (완료 판단은 작업 안에서)
        with patch.dict("os.environ", {"SHORTNEWS_DAILY_ENABLED": "false"}):
            jobs = {j.id for j in sched.build_scheduler().get_jobs()}
        self.assertEqual(jobs, BASE_JOBS)

    def test_embed_runs_every_day(self):
        now = datetime(2026, 12, 31, 23, 0, tzinfo=KST)
        first = sched.HS_EMBED_TRIGGER.get_next_fire_time(None, now)
        second = sched.HS_EMBED_TRIGGER.get_next_fire_time(first, first.replace(second=1))
        self.assertEqual([t.strftime("%Y-%m-%d %H:%M") for t in (first, second)], ["2027-01-01 01:00", "2027-01-02 01:00"])


def tearDownModule():
    close_logs()
    TMP.cleanup()


if __name__ == "__main__":
    unittest.main()
