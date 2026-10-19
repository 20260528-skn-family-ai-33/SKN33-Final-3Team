"""외부 데이터 수집의 WDI 변환·BACI 집계·CSV 검사를 가상 데이터로 확인합니다.

실행: python -m unittest backend/tests/test_external_data.py
"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "app/scheduler/collectors/external/external_collect.py"
spec = importlib.util.spec_from_file_location("external_collect", SRC)
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)


def wdi_value(code, country, year, value):
    return {"indicator": {"id": code}, "country": {"id": country}, "date": str(year), "value": value}


class WdiTest(unittest.TestCase):
    def test_merges_indicators_and_drops_aggregates(self):
        countries = [{"iso2Code": "VN", "region": {"value": "East Asia & Pacific"}},
                     {"iso2Code": "NA", "region": {"value": "Sub-Saharan Africa"}},  # 나미비아
                     {"iso2Code": "1W", "region": {"value": "Aggregates"}}]
        values = [wdi_value("NY.GDP.MKTP.CD", "VN", 2023, 4.3e11),
                  wdi_value("SP.POP.TOTL", "VN", 2023, 1.0e8),
                  wdi_value("SP.POP.TOTL", "NA", 2023, 2.6e6),
                  wdi_value("SP.POP.TOTL", "1W", 2023, 8.0e9)]
        df = ec.wdi_frame(countries, values)
        self.assertEqual(sorted(df.country_code), ["NA", "VN"])
        vn = df[df.country_code == "VN"].iloc[0]
        self.assertEqual((vn.gdp_usd, vn.population), (4.3e11, 1.0e8))
        self.assertTrue(pd.isna(vn.gdp_growth_pct))


class BaciTest(unittest.TestCase):
    codes = pd.DataFrame({"country_code": [410, 704, 156, 490, 516, 697],
                          "country_iso2": ["KR", "VN", "CN", "", "", ""],
                          "country_iso3": ["KOR", "VNM", "CHN", "S19", "NAM", "R20"]})

    def test_world_and_korea_imports(self):
        trade = pd.DataFrame({"t": [2023] * 4, "i": [410, 156, 410, 156], "j": [704, 704, 156, 410],
                              "k": ["10110", "10110", "850760", "850760"], "v": [2.0, 3.0, 5.0, 7.0]})
        out = ec.baci_imports(trade, self.codes).set_index(["country_code", "hs6"])
        self.assertEqual(out.loc[("VN", "010110")].tolist()[1:], [5000.0, 2000.0])  # 앞자리 0 복원, 천 달러→달러
        self.assertEqual(out.loc[("CN", "850760")].import_from_kor_usd, 5000.0)
        self.assertEqual(out.loc[("KR", "850760")].import_from_kor_usd, 0.0)

    def test_blank_iso2_fixed_or_dropped(self):
        trade = pd.DataFrame({"t": [2023] * 3, "i": [410] * 3, "j": [490, 516, 697],
                              "k": ["850760"] * 3, "v": [1.0] * 3})
        self.assertEqual(ec.baci_imports(trade, self.codes).country_code.tolist(), ["TW", "NA"])


class ComtradeTest(unittest.TestCase):
    def test_world_and_korea_imports(self):
        def rec(reporter, partner, cmd, value, cl="H6"):
            return {"refYear": 2025, "reporterCode": reporter, "partnerCode": partner, "cmdCode": cmd,
                    "primaryValue": value, "classificationCode": cl}
        records = [rec(842, 0, "850760", 100.0), rec(842, 410, "850760", 30.0),
                   rec(842, 0, "010110", 5.0),  # 한국발 없음 → 0
                   rec(842, 156, "850760", 50.0),  # 다른 상대국은 무시
                   rec(842, 0, "999999", 1.0, cl="H5"),  # HS2022가 아닌 신고는 제외
                   rec(97, 0, "850760", 900.0)]  # EU 묶음 등 ISO2 없는 신고국 제외
        out = ec.comtrade_imports(records, {842: "US"}).set_index("hs6")
        self.assertEqual(sorted(out.index), ["010110", "850760"])
        self.assertEqual(out.loc["850760", ["import_world_usd", "import_from_kor_usd"]].tolist(), [100.0, 30.0])
        self.assertEqual(out.loc["010110", "import_from_kor_usd"], 0.0)

    def test_empty_or_filtered_out_records(self):
        h5 = {"refYear": 2025, "reporterCode": 842, "partnerCode": 0, "cmdCode": "850760",
              "primaryValue": 1.0, "classificationCode": "H5"}
        for records in ([], [h5]):
            out = ec.comtrade_imports(records, {842: "US"})
            self.assertTrue(out.empty)
            self.assertEqual(list(out.columns)[:3], ["year", "country_code", "hs6"])


class ComtradeCollectTest(unittest.TestCase):
    def test_resumes_from_cached_reporters(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        reporters = mock.Mock(**{"json.return_value": {"results": [
            {"reporterCode": 842, "reporterCodeIsoAlpha2": "US"},
            {"reporterCode": 704, "reporterCodeIsoAlpha2": "VN"}]}})
        rec = {"refYear": 2025, "partnerCode": 0, "cmdCode": "850760", "primaryValue": 1.0,
               "classificationCode": "H6"}
        calls = []

        def fake_get(path, key, **params):
            if "getDA" in path:
                return [{"reporterCode": 842}, {"reporterCode": 704}]
            calls.append(params["reporterCode"])
            if params["reporterCode"] == 842 and calls.count(842) == 1:
                raise ConnectionError("한도 초과")
            return [{**rec, "reporterCode": params["reporterCode"]}]

        with mock.patch.object(ec, "OUT_DIR", tmp), mock.patch.object(ec, "comtrade_get", side_effect=fake_get), \
                mock.patch.object(ec, "dotenv_values", return_value={ec.ENV_KEY: "k"}), \
                mock.patch.object(ec.requests, "get", return_value=reporters), \
                mock.patch.object(ec.time, "sleep"):
            with self.assertRaises(ConnectionError):
                ec.collect_comtrade(2025)  # VN(704)은 받아 두고 US(842)에서 끊김
            stats = ec.collect_comtrade(2025)
        self.assertEqual(calls, [704, 842, 842])  # 두 번째 실행은 못 받은 US만 다시 호출
        self.assertEqual(stats, {"requests": 3, "rows": 2})

    def test_retries_connection_error(self):
        ok = mock.Mock(status_code=200, **{"json.return_value": {"data": [1]}})
        with mock.patch.object(ec.requests, "get", side_effect=[ec.requests.ConnectionError(), ok]), \
                mock.patch.object(ec.time, "sleep"):
            self.assertEqual(ec.comtrade_get("x", "k"), [1])


class CheckTest(unittest.TestCase):
    def frame(self, **overrides):
        df = pd.DataFrame({"year": [2022, 2023], "country_code": ["VN", "VN"], "hs6": ["010110", "010110"],
                           "import_world_usd": [10.0, 20.0], "import_from_kor_usd": [1.0, 2.0]})
        return df.assign(**overrides)

    def run_checks(self, df):
        keys = ["year", "country_code", "hs6"]
        return (ec.check_frame(df, {"VN"}, keys, ["import_world_usd"], 2022) + ec.check_baci(df))

    def test_clean_frame_passes(self):
        self.assertEqual(self.run_checks(self.frame()), [])

    def test_catches_each_problem(self):
        self.assertTrue(self.run_checks(self.frame(year=[2023, 2023])))  # 시작 연도·키 중복
        self.assertTrue(self.run_checks(self.frame(country_code=["CN", "CN"])))  # 매핑률
        self.assertTrue(self.run_checks(self.frame(import_world_usd=[-1.0, 20.0])))  # 음수
        self.assertTrue(self.run_checks(self.frame(hs6=["10110", "010110"])))  # 6자리 아님
        self.assertTrue(self.run_checks(self.frame(import_from_kor_usd=[11.0, 2.0])))  # 점유율 > 1

    def test_seed_has_iso2_countries(self):
        countries = ec.seed_countries()
        self.assertIn("VN", countries)
        self.assertTrue(all(len(c) == 2 for c in countries))


if __name__ == "__main__":
    unittest.main()
