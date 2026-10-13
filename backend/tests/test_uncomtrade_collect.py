"""UN Comtrade 수집 코드의 품목 읽기·상대국 선정·호출 묶기를 가상 데이터로 확인합니다 (API 호출 없음).

실행: python -m unittest backend/tests/test_uncomtrade_collect.py
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "app/scheduler/collectors/uncomtrade/uncomtrade_collect.py"
spec = importlib.util.spec_from_file_location("uncomtrade_collect", SRC)
uc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uc)

MD = """# 목록
## 1. 스피드 메이트
#### 주요 수출 품목 (자체 PB 브랜드)
| 품목 분류 | 4단위 | 6단위 | HSK 10단위 | 품명 |
| :--- | :---: | :---: | :---: | :--- |
| **차량용 배터리** | 8507 | 8507.10 | 8507.10-0000 | 납축전지 |
#### 주요 수입 품목 (글로벌 브랜드 직소싱)
| 품목명 | 한글 품명 설명 | HS 4단위 | HS 6단위 | HSK 10단위 |
| :--- | :--- | :---: | :---: | :---: |
| **와인** | 포도주 | 2204 | 2204.21 | 2204.21-1000 |
"""


class ItemsTest(unittest.TestCase):
    def test_parse_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.md"
            path.write_text(MD, encoding="utf-8")
            items = uc.parse_items(path)
        self.assertEqual(items["품목"].tolist(), ["차량용 배터리", "와인"])
        self.assertEqual(items["구분"].tolist(), ["수출", "수입"])
        self.assertEqual(items["hs6"].tolist(), ["850710", "220421"])
        self.assertEqual(items["hs10"].tolist(), ["8507100000", "2204211000"])

    def test_real_items_file(self):
        items = uc.parse_items(uc.DEFAULT_ITEMS)
        self.assertEqual(len(items), 37)
        self.assertTrue(items["hs6"].str.fullmatch(r"\d{6}").all())
        self.assertEqual(items[items["구분"] == "수출"].hs6.nunique(), 15)
        self.assertEqual(items[items["구분"] == "수입"].hs6.nunique(), 21)


class SelectTest(unittest.TestCase):
    def test_select_countries_excludes_total_rows(self):
        items = pd.DataFrame({"구분": ["수출", "수입"], "hs6": ["850710", "220421"]})
        rows = [
            ["총계", "-", "-", "-", "999999", "999999"],
            ["2025.01", "US", "미국", "8507100000", "300", "0"],
            ["2025.01", "JP", "일본", "8507100000", "100", "0"],
            ["2025.02", "US", "미국", "8507100000", "100", "0"],
            ["2025.01", "FR", "프랑스", "2204211000", "0", "500"],
            ["2025.01", "NA", "나미비아", "2204211000", "0", "50"],
        ]
        df = pd.DataFrame(rows, columns=["year", "statCd", "statCdCntnKor1", "hsCd", "expDlr", "impDlr"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "customs.csv"
            df.to_csv(path, index=False, encoding="utf-8-sig")
            targets, months = uc.select_countries(path, items, top_n=1, scope="item")
            every, _ = uc.select_countries(path, items, top_n=0, scope="customs")
        self.assertEqual(months, ["2025.01", "2025.02"])
        self.assertEqual(targets[["구분", "iso2", "kr_usd"]].values.tolist(), [["수출", "US", 400], ["수입", "FR", 500]])
        self.assertEqual(targets.iloc[0]["kr_share"], 0.8)
        # 관세청 거래국 전부(미국·일본·프랑스·나미비아)를 모든 품목에 적용, 거래 없는 나라는 0
        exp = every[every["구분"] == "수출"]
        self.assertEqual(exp["iso2"].tolist(), ["US", "JP", "FR", "NA"])
        self.assertEqual(exp["kr_usd"].tolist(), [400, 100, 0, 0])


class PlanTest(unittest.TestCase):
    def test_reporters_current_code_only(self):
        ref = {"results": [
            {"reporterCode": 699, "text": "India", "reporterCodeIsoAlpha2": "IN", "isGroup": False},
            {"reporterCode": 356, "text": "India (...1974)", "reporterCodeIsoAlpha2": "IN", "isGroup": False,
             "entryExpiredDate": "1974-12-31T00:00:00"},
            {"reporterCode": 490, "text": "Other Asia, nes", "reporterCodeIsoAlpha2": None, "isGroup": True},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rep.json"
            path.write_text(json.dumps(ref), encoding="utf-8")
            table = uc.load_reporters(path)
        self.assertEqual(table, {"IN": ("699", "India")})

    def test_plan_calls_respects_row_limit(self):
        rows = []
        for i in range(30):  # 품목 30개 × 각 10개국
            rows += [{"구분": "수출", "hs6": f"{100000 + i}", "un_code": str(100 + i * 3 + k)} for k in range(10)]
        targets = pd.DataFrame(rows)
        calls = uc.plan_calls(targets, ["202501"])
        covered = []
        for c in calls:
            n_rep, n_cmd = len(c["reporterCode"].split(",")), len(c["cmdCode"].split(","))
            self.assertLessEqual(n_rep * n_cmd * 2, uc.MAX_ROWS)
            self.assertEqual(c["flow"], "M")
            covered += c["cmdCode"].split(",")
        self.assertEqual(sorted(covered), sorted(targets["hs6"].unique()))

    def test_plan_calls_skips_unavailable_reporters(self):
        targets = pd.DataFrame([{"구분": "수입", "hs6": "220421", "un_code": c} for c in ["251", "842", "156"]])
        calls = uc.plan_calls(targets, ["202501", "202502"], {"202501": {"251", "842"}, "202502": set()})
        self.assertEqual(len(calls), 1)  # 2월은 데이터를 올린 나라가 없어 호출하지 않음
        self.assertEqual(calls[0]["reporterCode"], "251,842")  # 중국(156) 미보고 → 제외
        self.assertEqual(calls[0]["flow"], "X")

    def test_plan_calls_splits_large_reporter_list(self):
        targets = pd.DataFrame([{"구분": "수출", "hs6": "850710", "un_code": str(c)} for c in range(1, 301)])
        calls = uc.plan_calls(targets, ["202501"])
        self.assertEqual([len(c["reporterCode"].split(",")) for c in calls], [250, 50])

    def test_quota_wait(self):
        from types import SimpleNamespace as R
        msg = '{"statusCode": 403, "message": "Out of call volume quota. Quota will be replenished in 00:29:30."}'
        self.assertEqual(uc.quota_wait(R(status_code=403, text=msg)), 29 * 60 + 30 + 30)
        self.assertIsNone(uc.quota_wait(R(status_code=403, text="Forbidden")))
        self.assertIsNone(uc.quota_wait(R(status_code=200, text=msg)))

    def test_plan_key_calls_groups_periods(self):
        rows = [{"구분": "수출", "hs6": f"{100000 + i}", "un_code": str(c)} for i in range(20) for c in range(1, 121)]
        months = uc.month_range("202401", "202512")
        available = {m: {str(c) for c in range(1, 101)} for m in months}  # 100개국만 데이터 있음
        calls = uc.plan_key_calls(pd.DataFrame(rows), months, available)
        self.assertEqual({c["period"].count(",") + 1 for c in calls}, {12})  # 1년씩 묶음
        for c in calls:
            n = len(c["reporterCode"].split(",")) * len(c["cmdCode"].split(",")) * 12 * 2
            self.assertLessEqual(n, uc.KEY_MAX_ROWS)
            self.assertEqual(len(c["reporterCode"].split(",")), 100)
        self.assertEqual(sorted(x for c in calls[:len(calls) // 2] for x in c["cmdCode"].split(",")),
                         [f"{100000 + i}" for i in range(20)])

    def test_load_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text('customs=abc' + chr(10) + 'UN_COMTRADE="k123"' + chr(10), encoding="utf-8")
            self.assertEqual(uc.load_key(env), "k123")
            self.assertEqual(uc.load_key(Path(tmp) / "none.env"), "")

    def test_month_range(self):
        self.assertEqual(uc.month_range("202411", "202502"), ["202411", "202412", "202501", "202502"])


if __name__ == "__main__":
    unittest.main()
