"""환율 적재를 가상 ECOS 응답과 SQLite DB 파일로 확인합니다 (실제 ECOS·MySQL을 부르지 않음).

실행: python -m unittest backend/tests/test_fx_daily_load.py
"""
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.batch import BatchRun
from backend.app.models.currency import Currency, FxDaily
from backend.app.scheduler.jobs import fx_daily_load as fx

D1, D2 = date(2026, 10, 1), date(2026, 10, 2)
RATES = {"0000001": [(D1, Decimal("1390.5")), (D2, Decimal("1391"))],
         "0000002": [(D1, Decimal("925.31")), (D2, Decimal("926"))]}


def fake_fetch(rates):
    def fetch(key, item_code, start, end, quote_unit=None):
        if isinstance(rates.get(item_code), Exception):
            raise rates[item_code]
        return [(d, v) for d, v in rates[item_code] if start <= d <= end], 1
    return fetch


class FxDailyLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.url = f"sqlite:///{self.dir / 'test.db'}"
        engine = make_engine(self.url)
        Base.metadata.create_all(engine)
        with session_scope(engine) as s:
            CRUD(Currency).upsert_many(s, [
                {"currency_code": "USD", "name_ko": "미국 달러", "ecos_item_code": "0000001", "quote_unit": 1},
                {"currency_code": "JPY", "name_ko": "일본 엔", "ecos_item_code": "0000002", "quote_unit": 100},
                {"currency_code": "XXX", "name_ko": "ECOS 미제공", "ecos_item_code": None, "quote_unit": 1}])
        engine.dispose()
        self.env = self.dir / ".env"
        self.env.write_text("ecos=TESTKEY\n", encoding="utf-8")
        mock.patch.object(fx, "make_engine", side_effect=lambda url=None: make_engine(url or self.url)).start()
        mock.patch("backend.app.db.batch_log.make_engine", side_effect=lambda: make_engine(self.url)).start()

    def tearDown(self):
        mock.patch.stopall()
        for h in fx.log.handlers:
            h.close()
        fx.log.handlers.clear()
        self.tmp.cleanup()

    def run_load(self, rates, start=D1, end=D2):
        with mock.patch.object(fx.ecos, "fetch_daily", side_effect=fake_fetch(rates)) as fetch:
            rc = fx.run(start, end, self.url, log_dir=self.dir / "logs", env_path=self.env)
        return rc, fetch

    def table(self):
        engine = make_engine(self.url)
        with session_scope(engine) as s:
            rows = {(r.base_date, r.currency_code): r.rate_krw for r in CRUD(FxDaily).list(s)}
            runs = [(r.job_name, r.target_table, r.status, r.request_count, r.received_count)
                    for r in CRUD(BatchRun).list(s)]
        engine.dispose()
        return rows, runs

    def test_loads_only_currencies_with_ecos_code_and_records_batch(self):
        rc, fetch = self.run_load(RATES)
        rows, runs = self.table()
        self.assertEqual(rc, 0)
        # XXX(코드 없음)는 제외하고, 통화마다 currency의 기준 수량을 넘겨 단위를 확인하게 함
        self.assertEqual({(c.args[1], c.kwargs["quote_unit"]) for c in fetch.call_args_list},
                         {("0000001", 1), ("0000002", 100)})
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[(D1, "JPY")], Decimal("925.31"))  # 100엔 기준 값 그대로
        self.assertEqual(runs, [("FX_DAILY_LOAD", "fx_daily", "SUCCESS", 2, 4)])

    def test_rerun_updates_without_duplicates(self):
        self.run_load(RATES)
        corrected = {**RATES, "0000001": [(D1, Decimal("1388")), (D2, Decimal("1391"))]}
        rc, _ = self.run_load(corrected)
        rows, runs = self.table()
        self.assertEqual((rc, len(rows)), (0, 4))
        self.assertEqual(rows[(D1, "USD")], Decimal("1388"))
        self.assertEqual([r[2] for r in runs], ["SUCCESS", "SUCCESS"])

    def test_failure_keeps_existing_rates_and_records_failed(self):
        self.run_load(RATES)
        broken = {"0000001": [(D1, Decimal("1"))], "0000002": fx.ecos.EcosError("ECOS 오류 INFO-100", "INFO-100")}
        rc, _ = self.run_load(broken)
        rows, runs = self.table()
        self.assertEqual(rc, 1)
        self.assertEqual(rows[(D1, "USD")], Decimal("1390.5"))  # 한 통화가 실패하면 다른 통화도 저장하지 않음
        self.assertEqual([r[2] for r in runs], ["SUCCESS", "FAILED"])

    def test_start_after_end_fails_before_touching_db_or_ecos(self):
        with mock.patch.object(fx, "make_engine") as engine:
            rc, fetch = self.run_load(RATES, D2, D1)
        rows, runs = self.table()
        self.assertEqual((rc, rows, runs), (1, {}, []))
        engine.assert_not_called()
        fetch.assert_not_called()

    def test_log_is_not_duplicated_by_root_logger(self):
        self.run_load(RATES)
        self.assertFalse(fx.log.propagate)  # 공용 스케줄러의 logging.basicConfig와 겹쳐 두 번 찍히지 않음

    def test_no_data_period_is_success(self):
        rc, _ = self.run_load(RATES, date(2026, 10, 3), date(2026, 10, 4))
        rows, runs = self.table()
        self.assertEqual((rc, len(rows), runs[0][2:]), (0, 0, ("SUCCESS", 2, 0)))

    def add_rates(self, rows):
        engine = make_engine(self.url)
        with session_scope(engine) as s:
            CRUD(FxDaily).upsert_many(s, [{"base_date": d, "currency_code": c, "rate_krw": Decimal(v)} for d, c, v in rows])
        engine.dispose()

    def starts(self, today, start_date=None):
        engine = make_engine(self.url)
        with session_scope(engine) as s:
            result = fx.recent_starts(s, fx.load_targets(s), today, start_date=start_date)
        engine.dispose()
        return result

    def test_recent_starts_per_currency(self):
        today = date(2026, 10, 19)
        # 비어 있으면 최근 2일, FX_START_DATE가 있으면 그날부터
        self.assertEqual(self.starts(today), {"JPY": date(2026, 10, 18), "USD": date(2026, 10, 18)})
        self.assertEqual(self.starts(today, date(2026, 1, 1)), {"JPY": date(2026, 1, 1), "USD": date(2026, 1, 1)})
        # 월요일 실행: 금요일까지 있으면 금요일부터 (주말 건너뛴 전일 비교)
        self.add_rates([(date(2026, 10, 16), "USD", "1348.5"), (date(2026, 10, 16), "JPY", "853.02")])
        self.assertEqual(self.starts(today), {"JPY": date(2026, 10, 16), "USD": date(2026, 10, 16)})
        # 최신이면 최근 2일만
        self.add_rates([(date(2026, 10, 19), "USD", "1342.4"), (date(2026, 10, 19), "JPY", "850.24")])
        self.assertEqual(self.starts(today), {"JPY": date(2026, 10, 18), "USD": date(2026, 10, 18)})

    def test_one_currency_gap_is_not_hidden_by_another_currency(self):
        # USD는 10/19까지, JPY는 10/1까지만 있음 → JPY는 10/1부터 다시 받아야 함 (전체 MAX로 계산하면 놓침)
        self.add_rates([(date(2026, 10, 19), "USD", "1342.4"), (date(2026, 10, 1), "JPY", "925.31")])
        self.assertEqual(self.starts(date(2026, 10, 19)), {"JPY": date(2026, 10, 1), "USD": date(2026, 10, 18)})

    def test_new_currency_without_rows_starts_from_start_date(self):
        self.add_rates([(date(2026, 10, 19), "USD", "1342.4")])  # JPY는 새로 추가된 통화처럼 0건
        self.assertEqual(self.starts(date(2026, 10, 19), date(2026, 1, 1)),
                         {"JPY": date(2026, 1, 1), "USD": date(2026, 10, 18)})

    def test_recent_run_fetches_each_currency_from_its_own_start(self):
        today = fx.datetime.now(fx.KST).date()
        self.add_rates([(today, "USD", "1342.4"), (date(2026, 10, 1), "JPY", "925.31")])
        with mock.patch.object(fx.ecos, "fetch_daily", return_value=([], 1)) as fetch:
            rc = fx.run(None, None, self.url, log_dir=self.dir / "logs", env_path=self.env)
        self.assertEqual(rc, 0)
        self.assertEqual({c.args[1]: (c.args[2], c.args[3]) for c in fetch.call_args_list},
                         {"0000001": (today - fx.timedelta(days=1), today), "0000002": (date(2026, 10, 1), today)})
        _, runs = self.table()
        self.assertEqual(runs[-1][2], "SUCCESS")

    def test_second_run_while_first_is_running_is_skipped(self):
        # 정기 작업과 시작 작업이 겹친 경우: 늦게 시작한 실행은 아무것도 하지 않음 (오래된 값이 최신 값을 덮지 않게)
        self.assertTrue(fx.RUN_LOCK.acquire(blocking=False))
        try:
            rc, fetch = self.run_load(RATES)
        finally:
            fx.RUN_LOCK.release()
        rows, runs = self.table()
        self.assertEqual((rc, rows, runs), (0, {}, []))
        fetch.assert_not_called()
        rc, _ = self.run_load(RATES)  # 잠금이 풀리면 정상 실행
        self.assertEqual((rc, len(self.table()[0])), (0, 4))

    def test_skipped_run_does_not_touch_running_job_logs(self):
        # 진행 중인 실행의 로그 핸들러를 건너뛰는 실행이 닫거나 바꾸면, 진행 중인 실행의 나머지 로그가 엉뚱한 파일로 감
        running_log = fx.setup_log(self.dir / "running")
        handlers = list(fx.log.handlers)
        self.assertTrue(fx.RUN_LOCK.acquire(blocking=False))
        try:
            rc, fetch = self.run_load(RATES)
            fx.log.info("진행 중인 실행의 다음 로그")
        finally:
            fx.RUN_LOCK.release()
        self.assertEqual(rc, 0)
        fetch.assert_not_called()
        self.assertEqual(fx.log.handlers, handlers)  # 같은 핸들러 객체 그대로
        self.assertFalse((self.dir / "logs").exists())  # 건너뛴 실행은 새 로그 파일을 만들지 않음
        for h in handlers:
            h.flush()
        text = running_log.read_text(encoding="utf-8")
        self.assertIn("이미 실행 중이라 이번 실행은 건너뜁니다", text)
        self.assertIn("진행 중인 실행의 다음 로그", text)  # 진행 중인 실행은 원래 파일에 계속 기록

    def test_failed_run_releases_process_lock(self):
        with mock.patch.object(fx, "make_engine", side_effect=RuntimeError("DB down")):
            rc = fx.run(D1, D2, self.url, log_dir=self.dir / "logs", env_path=self.env)
        self.assertEqual(rc, 1)
        self.assertFalse(fx.RUN_LOCK.locked())


class SingleRunCleanupTest(unittest.TestCase):
    def test_cleanup_failure_releases_process_lock(self):
        for failure in ("release", "close"):
            with self.subTest(failure=failure):
                engine = mock.MagicMock()
                engine.dialect.name = "mysql"
                conn = engine.connect.return_value
                conn.execute.return_value.scalar.return_value = 1
                if failure == "release":
                    conn.execute.side_effect = [conn.execute.return_value, RuntimeError("release failed")]
                else:
                    conn.close.side_effect = RuntimeError("close failed")
                with mock.patch.object(fx, "RUN_LOCK", threading.Lock()):
                    with self.assertRaisesRegex(RuntimeError, f"{failure} failed"):
                        with fx.single_run(engine) as acquired:
                            self.assertTrue(acquired)
                    conn.close.assert_called_once()
                    self.assertFalse(fx.RUN_LOCK.locked())
                    conn.execute.side_effect = None
                    conn.close.side_effect = None
                    with fx.single_run(engine) as acquired:
                        self.assertTrue(acquired)  # 정리 실패 후에도 다음 실행 가능
                    self.assertFalse(fx.RUN_LOCK.locked())


if __name__ == "__main__":
    unittest.main()
