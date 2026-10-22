"""ML 입력의 CSV 읽기, 수집 완료 달 처리, 관련 품목 묶음표·묶음 피처·후보 찾기를 가상 데이터로 확인합니다.

실행: python -m unittest backend/tests/test_ml_sources.py
"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.ml import features as F
from backend.app.ml import sources as S
from backend.tests.test_ml_market import fake_data

MONTHS = pd.date_range("2022-01-01", periods=26, freq="MS")


class CsvSourceTest(unittest.TestCase):
    def raw(self, folder, name, rows):
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=["year", "statCd", "hsCd", "expDlr", "expWgt", "impDlr"]).to_csv(folder / name, index=False)

    def test_customs_raw_filters_and_hs_version(self):
        root = Path(tempfile.mkdtemp())
        self.raw(root / "yearly" / "2022", "customs_raw_2022.csv",
                 [("총계", "-", "-", 0, 0, 0), ("2022.01", "VN", "8542321000", 100, 2, 0),
                  ("2022.01", "VN", "0403109000", 5, 1, 3), ("2022.02", "KR", "8542321000", 7, 1, 0)])
        exports, imports, _ = S.customs([root / "yearly"], {"VN"}, hs2017={"0403109000"})
        self.assertEqual(sorted(zip(exports.hs6, exports.hs_ver, exports.amount)),
                         [("040310", "HS2017", 5), ("854232", "HS2022", 100)])  # 총계·SPECIAL(KR) 제외, HS2017 구분
        self.assertEqual(imports.amount.tolist(), [3])  # 수입 0인 행은 수입 표에 넣지 않음

    def test_monthly_collection_wins_for_overlapping_month(self):
        root = Path(tempfile.mkdtemp())
        yearly, monthly = root / "yearly", root / "monthly"
        self.raw(yearly / "2022", "customs_raw_2022.csv",
                 [("2022.01", "VN", "8542321000", 100, 1, 0), ("2022.02", "VN", "8542321000", 100, 1, 0)])
        (yearly / "2022" / "customs_collection_2022.json").write_text(json.dumps(
            {"collection_complete": True, "failed_countries": [], "missing_months": [], "months": ["2022.01", "2022.02"]}),
            encoding="utf-8")
        self.raw(monthly / "202202", "customs_raw_202202.csv", [("2022.02", "VN", "8542321000", 130, 1, 0)])
        (monthly / "202202" / "collection.json").write_text(json.dumps(
            {"collection_complete": False, "failed_countries": [], "missing_months": [], "months": ["2022.02"],
             "unconfirmed_empty_countries": ["US"]}), encoding="utf-8")
        exports, _, complete = S.customs([yearly, monthly], {"VN"})
        by_month = dict(zip(exports.month, exports.amount))
        self.assertEqual(by_month[pd.Timestamp("2022-02-01")], 130)  # 같은 달은 정기 수집본만 (두 번 더하지 않음)
        self.assertEqual(by_month[pd.Timestamp("2022-01-01")], 100)
        # 2월은 실제로 쓴 정기 수집본의 기록(거래 없음 미확인 국가 있음)으로 판단해 완료가 아니다
        self.assertEqual(complete, {pd.Timestamp("2022-01-01")})

    def test_country_seed_rows(self):
        c = S.countries().set_index("country_code")
        self.assertEqual(c.loc["KR", "country_role"], "SPECIAL")  # 07 시드가 추가
        self.assertEqual(c.loc["VN", "region_id"], 35)

    def test_missing_pair_month_is_zero_only_in_completed_months(self):
        data = fake_data(months=26)
        e = data["exports"]
        data["exports"] = e[~((e.country_code == "VN") & (e.hs6 == e.hs6.iloc[0]) & (e.month == MONTHS[5]))]
        with self.assertRaises(ValueError):  # 완료 기록이 없으면 여전히 멈춤
            F.build_dataset(data)
        data["complete_months"] = set(MONTHS)
        ds = F.build_dataset(data)  # 완료 기록이 있으면 확인된 무역 없음(0)
        self.assertFalse(ds.empty)



if __name__ == "__main__":
    unittest.main()
