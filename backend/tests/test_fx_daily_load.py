"""환율 적재를 가상 ECOS 응답과 SQLite DB 파일로 확인합니다 (실제 ECOS·MySQL을 부르지 않음).

실행: python -m unittest backend/tests/test_fx_daily_load.py
"""
import tempfile
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

    def test_no_data_period_is_success(self):
        rc, _ = self.run_load(RATES, date(2026, 10, 3), date(2026, 10, 4))
        rows, runs = self.table()
        self.assertEqual((rc, len(rows), runs[0][2:]), (0, 0, ("SUCCESS", 2, 0)))

    def test_recent_period(self):
        today = date(2026, 10, 19)
        engine = make_engine(self.url)
        with session_scope(engine) as s:
            self.assertEqual(fx.recent_period(s, today), (date(2026, 10, 18), today))  # 비어 있고 시작일 설정 없음: 최근 2일
            self.assertEqual(fx.recent_period(s, today, start_date=date(2025, 1, 1)), (date(2025, 1, 1), today))
            CRUD(FxDaily).upsert_many(s, [{"base_date": date(2026, 9, 1), "currency_code": "USD",
                                           "rate_krw": Decimal("1300")}])
        with session_scope(engine) as s:  # 마지막 적재일이 14일보다 오래됐으면 그날부터
            self.assertEqual(fx.recent_period(s, today, start_date=date(2025, 1, 1)), (date(2026, 9, 1), today))
            CRUD(FxDaily).upsert_many(s, [{"base_date": date(2026, 10, 16), "currency_code": "USD",
                                           "rate_krw": Decimal("1340")}])
        with session_scope(engine) as s:  # 월요일 실행, 마지막 적재가 금요일이면 금요일부터 (주말 건너뛴 전일 비교 가능)
            self.assertEqual(fx.recent_period(s, today), (date(2026, 10, 16), today))
            CRUD(FxDaily).upsert_many(s, [{"base_date": date(2026, 10, 19), "currency_code": "USD",
                                           "rate_krw": Decimal("1342.4")}])
        with session_scope(engine) as s:
            self.assertEqual(fx.recent_period(s, today), (date(2026, 10, 18), today))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
