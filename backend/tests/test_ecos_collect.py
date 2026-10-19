"""ECOS 환율 수집 코드의 파싱·검증·페이지 이어받기·재시도·키 가림을 가상 응답으로 확인합니다 (실제 ECOS를 부르지 않음).

실행: python -m unittest backend/tests/test_ecos_collect.py
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests

from backend.app.scheduler.collectors.ecos import ecos_collect as ec

KEY = "SECRETKEY123"


def ok(rows, total=None):
    return {"StatisticSearch": {"list_total_count": len(rows) if total is None else total, "row": [
        {"STAT_CODE": "731Y001", "ITEM_CODE1": item, "TIME": time, "DATA_VALUE": value, "UNIT_NAME": "원"}
        for item, time, value in rows]}}


def result(code, message="메시지"):
    return {"RESULT": {"CODE": code, "MESSAGE": message}}


def response(payload, status=200):
    res = MagicMock(status_code=status)
    res.json.return_value = payload
    res.raise_for_status.side_effect = (requests.HTTPError(f"{status} for url: /api/StatisticSearch/{KEY}/json")
                                        if 400 <= status < 500 else None)
    return res


class ParseTest(unittest.TestCase):
    def test_values_become_decimal_with_source_date(self):
        total, rows = ec.parse_page(ok([("0000002", "20261002", "925.31"), ("0000002", "20261001", "1,001.5")]),
                                    "0000002")
        self.assertEqual(total, 2)
        self.assertEqual(rows, [(date(2026, 10, 2), Decimal("925.31")), (date(2026, 10, 1), Decimal("1001.5"))])

    def test_no_data_is_empty_not_error(self):
        self.assertEqual(ec.parse_page(result("INFO-200"), "0000001"), (0, []))

    def test_auth_error_is_not_retryable(self):
        with self.assertRaises(ec.EcosError) as caught:
            ec.parse_page(result("INFO-100", "인증키가 유효하지 않습니다."), "0000001")
        self.assertEqual(caught.exception.code, "INFO-100")
        self.assertFalse(caught.exception.retryable)

    def test_invalid_values_are_rejected(self):
        for value in ("0", "-1", "", "abc", "NaN"):
            with self.subTest(value=value), self.assertRaises(ec.EcosError):
                ec.parse_page(ok([("0000001", "20261001", value)]), "0000001")
        with self.assertRaises(ec.EcosError):
            ec.parse_page(ok([("0000001", "2026-10-01", "1390")]), "0000001")
        with self.assertRaises(ec.EcosError):  # 다른 통화가 섞이면 저장하면 안 됨
            ec.parse_page(ok([("0000003", "20261001", "1600")]), "0000001")


@patch.object(ec, "RETRY_WAIT", 0)
class FetchTest(unittest.TestCase):
    def test_pages_are_followed_until_total(self):
        pages = [ok([("0000001", "20261001", "1390")], total=2), ok([("0000001", "20261002", "1391")], total=2)]
        with patch.object(ec, "PAGE_SIZE", 1), patch.object(ec.requests, "get",
                                                            side_effect=[response(p) for p in pages]) as get:
            rows, calls = ec.fetch_daily(KEY, "0000001", date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(rows, [(date(2026, 10, 1), Decimal("1390")), (date(2026, 10, 2), Decimal("1391"))])
        self.assertEqual(calls, 2)
        self.assertIn("/1/1/731Y001/D/20261001/20261002/0000001", get.call_args_list[0].args[0])
        self.assertIn("/2/2/", get.call_args_list[1].args[0])

    def test_weekend_only_returns_empty(self):
        with patch.object(ec.requests, "get", return_value=response(result("INFO-200"))):
            self.assertEqual(ec.fetch_daily(KEY, "0000001", date(2026, 10, 3), date(2026, 10, 4)), ([], 1))

    def test_temporary_errors_are_retried(self):
        replies = [requests.Timeout("t"), response({}, 503), response(ok([("0000001", "20261001", "1390")]))]
        with patch.object(ec.requests, "get", side_effect=replies):
            rows, calls = ec.fetch_daily(KEY, "0000001", date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual((len(rows), calls), (1, 3))

    def test_retry_limit_then_error(self):
        with patch.object(ec.requests, "get", side_effect=requests.ConnectionError(f"url: /{KEY}/")) as get:
            with self.assertRaises(ec.EcosError) as caught:
                ec.fetch_daily(KEY, "0000001", date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(get.call_count, ec.RETRIES)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn(KEY, str(caught.exception))

    def test_client_error_is_not_retried_and_key_is_masked(self):
        with patch.object(ec.requests, "get", return_value=response({}, 404)) as get:
            with self.assertRaises(ec.EcosError) as caught:
                ec.fetch_daily(KEY, "0000001", date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(get.call_count, 1)
        self.assertNotIn(KEY, str(caught.exception))

    def test_out_of_range_or_duplicate_dates_are_rejected(self):
        for rows in ([("0000001", "20260930", "1390")],
                     [("0000001", "20261001", "1390"), ("0000001", "20261001", "1391")]):
            with self.subTest(rows=rows), patch.object(ec.requests, "get", return_value=response(ok(rows))):
                with self.assertRaises(ec.EcosError):
                    ec.fetch_daily(KEY, "0000001", date(2026, 10, 1), date(2026, 10, 2))


class KeyTest(unittest.TestCase):
    def test_key_name_is_case_insensitive(self):
        for name in ("ecos", "ECOS", "ECOS_API_KEY"):
            with self.subTest(name=name), patch.object(ec, "dotenv_values", return_value={name: " abc "}), \
                    patch.dict(ec.os.environ, {}, clear=True):
                self.assertEqual(ec.load_key(), "abc")

    def test_missing_key(self):
        with patch.object(ec, "dotenv_values", return_value={}), patch.dict(ec.os.environ, {}, clear=True):
            with self.assertRaises(ec.EcosError):
                ec.load_key()


if __name__ == "__main__":
    unittest.main()
