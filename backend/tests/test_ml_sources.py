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
from backend.app.ml import related_groups as R
from backend.app.ml import sources as S
from backend.tests.test_ml_market import fake_data

MONTHS = pd.date_range("2022-01-01", periods=26, freq="MS")
GROUPS = pd.DataFrame([("반도체", "8542", "product"), ("반도체", "8486", "input"), ("반도체", "8523", "related")],
                      columns=["group", "hs4", "role"]).assign(source="curated", note="")[R.COLUMNS]


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

    def test_monthly_checkpoint_selects_active_nested_collection(self):
        root = Path(tempfile.mkdtemp())
        month = root / "202202"
        active = month / "collection_20261001_030000_000001"
        self.raw(active, "customs_raw_202202.csv", [("2022.02", "VN", "8542321000", 130, 1, 0)])
        self.raw(month / "collection_20261022_030000_000001", "customs_raw_202202.csv",
                 [("2022.02", "VN", "8542321000", 999, 1, 0)])  # 이름이 더 최신이어도 체크포인트가 선택하지 않은 수집본
        self.raw(month, "customs_raw_202202.csv", [("2022.02", "VN", "8542321000", 100, 1, 0)])
        (month / "state.json").write_text(json.dumps(
            {"collection_dir": active.name, "completed": ["collect"]}), encoding="utf-8")
        (active / "collection.json").write_text(json.dumps(
            {"collection_complete": True, "months": ["2022.02"]}), encoding="utf-8")
        exports, _, complete = S.customs([root], {"VN"})
        self.assertEqual(exports.amount.tolist(), [130])
        self.assertEqual(complete, {pd.Timestamp("2022-02-01")})

    def test_unfinished_or_missing_selected_collection_does_not_fall_back(self):
        root = Path(tempfile.mkdtemp())
        month = root / "202202"
        self.raw(month, "customs_raw_202202.csv", [("2022.02", "VN", "8542321000", 100, 1, 0)])
        for state in ({"collection_dir": "collection_new", "completed": []},
                      {"collection_dir": "collection_new", "completed": ["collect"]},
                      {"collection_dir": "../other", "completed": ["collect"]}):
            with self.subTest(state=state):
                (month / "state.json").write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises((ValueError, FileNotFoundError)):
                    S.customs([root], {"VN"})

    def test_raw_money_missing_or_infinite_is_rejected_before_sum(self):
        root = Path(tempfile.mkdtemp())
        for column in (3, 5):
            for value in (np.nan, np.inf, -np.inf):
                with self.subTest(column=column, value=value):
                    row = ["2022.01", "VN", "8542321000", 100, 1, 0]
                    row[column] = value
                    self.raw(root / "2022", "customs_raw_2022.csv", [row])
                    with self.assertRaisesRegex(ValueError, "금액 결측 또는 비유한"):
                        S.customs([root], {"VN"})

    def test_missing_weight_is_not_a_zero_or_partial_total(self):
        root = Path(tempfile.mkdtemp())
        self.raw(root / "2022", "customs_raw_2022.csv",
                 [("2022.01", "VN", "8542321000", 100, np.nan, 0),
                  ("2022.01", "VN", "8542322000", 200, 2, 0)])
        exports, _, _ = S.customs([root], {"VN"})
        self.assertEqual(exports.amount.tolist(), [300])
        self.assertTrue(exports.weight.isna().all())

    def test_missing_availability_is_checked_before_customs_loading(self):
        from unittest import mock
        external = {"market_yearly": pd.DataFrame({"year": [2024]}),
                    "wdi_yearly": pd.DataFrame(), "availability": None}
        with mock.patch.object(S, "countries", return_value=S.countries()), \
             mock.patch.object(S, "hs_correlation", return_value=pd.DataFrame(columns=["new_code", "old_code", "group_code"])), \
             mock.patch.object(F.pp, "load_external", return_value=external), \
             mock.patch.object(S, "customs") as customs:
            with self.assertRaisesRegex(ValueError, "availability.csv"):
                F.load_data()
        customs.assert_not_called()

    def test_missing_pair_month_is_zero_only_in_completed_months(self):
        data = fake_data(months=26)
        e = data["exports"]
        data["exports"] = e[~((e.country_code == "VN") & (e.hs6 == e.hs6.iloc[0]) & (e.month == MONTHS[5]))]
        with self.assertRaises(ValueError):  # 완료 기록이 없으면 여전히 멈춤
            F.build_dataset(data)
        data["complete_months"] = set(MONTHS)
        ds = F.build_dataset(data)  # 완료 기록이 있으면 확인된 무역 없음(0)
        self.assertFalse(ds.empty)

    def test_completion_does_not_hide_observed_missing_or_infinite_amount(self):
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value):
                data = fake_data(months=26)
                data["exports"].loc[0, "amount"] = value
                data["complete_months"] = set(MONTHS)
                with self.assertRaisesRegex(ValueError, "미관측 또는 비유한"):
                    F.build_dataset(data)

    def test_completion_preserves_unknown_observed_weight(self):
        data = fake_data(months=26)
        e = data["exports"]
        chosen = (e.country_code == "VN") & (e.hs6 == e.hs6.iloc[0]) & (e.month == MONTHS[23])
        e.loc[chosen, "weight"] = np.nan
        data["complete_months"] = set(MONTHS)
        ds = F.build_dataset(data)
        row = ds[(ds.country_code == "VN") & (ds.hs6 == e.hs6.iloc[0]) & (ds.anchor == MONTHS[23])].iloc[0]
        self.assertTrue(np.isnan(row.unit_trend))


class NumericSafetyTest(unittest.TestCase):
    def test_rounding_below_zero_is_clipped(self):
        np.testing.assert_array_equal(F._minus(np.array([1e11, 5.0]), np.array([1e11 + 3, 2.0])), [0.0, 3.0])

    def test_infinite_feature_values_become_missing(self):
        data = fake_data(months=26)
        e = data["exports"]
        last6 = (e.country_code == "VN") & (e.hs6 == e.hs6.iloc[0]) & (e.month >= MONTHS[18])
        data["exports"] = e.assign(amount=np.where(last6, 0.0, e.amount))  # 금액 0·중량 있음 → 단가 로그 -무한대
        ds = F.build_dataset(data)
        values = ds[[c for c in F.FEATURES if c in ds]].select_dtypes("number").to_numpy()
        self.assertFalse(np.isinf(values).any())

    def test_fx_db_failure_leaves_fx_empty(self):
        from unittest import mock
        with mock.patch("backend.app.db.session.make_engine", side_effect=ValueError("접속 정보 없음")):
            fx, currencies = S.fx_from_db()
        self.assertTrue(fx.empty and currencies.empty)


class RelatedGroupTest(unittest.TestCase):
    def data(self):
        rows, imps = [], []
        for i, m in enumerate(MONTHS):
            for hs6 in ("854232", "852351", "848620", "010110"):
                amt = 1e6 * (3 if hs6 == "852351" and i >= 12 else 1)
                rows.append((m, "VN", hs6, amt, 0.0, 1e4))
            imps.append((m, "JP", "848620", 1e6 * (2 if i >= 12 else 1)))
        data = fake_data(months=26)
        data["exports"] = pd.DataFrame(rows, columns=["month", "country_code", "hs6", "amount", "anomaly_amount", "weight"])
        data["imports"] = pd.DataFrame(imps, columns=["month", "country_code", "hs6", "amount"])
        data["related"] = GROUPS
        return data

    def test_cross_chapter_items_reach_product_rows(self):
        ds = F.build_dataset(self.data())
        at = ds[ds.anchor == pd.Timestamp("2023-12-01")].set_index("hs6")
        chip = at.loc["854232"]
        # 8523(저장장치, 85장)과 8486(장비, 84장)이 반도체 행에 반영된다
        self.assertAlmostEqual(chip.rel_exp_c_growth_12m, np.log1p(36e6) - np.log1p(12e6))  # 8523 3배, 8542 자신 제외
        self.assertAlmostEqual(chip.rel_input_imp_growth_12m, np.log1p(24e6) - np.log1p(12e6))  # 장비 수입 2배
        self.assertAlmostEqual(chip.grp4_c_growth_12m, 0.0)  # 같은 HS4에는 다른 품목이 없어 HS4 피처로는 안 보임
        self.assertTrue(np.isnan(at.loc["010110"].rel_exp_c_growth_12m))  # 묶음 product가 아닌 품목은 결측
        self.assertTrue(np.isnan(at.loc["848620"].rel_exp_c_growth_12m))  # input 역할만 있는 품목도 결측

    def test_curated_table_is_valid(self):
        self.assertEqual(R.validate(R.load_groups()), [])

    def test_validate_catches_problems(self):
        bad = pd.concat([GROUPS, GROUPS.assign(group="디스플레이").iloc[:1],
                         pd.DataFrame([("반도체", "85x", "foo", "curated", "")], columns=R.COLUMNS)], ignore_index=True)
        problems = " ".join(R.validate(bad, hs4_names={"8542": "", "8486": "", "8523": ""}))
        for word in ("4자리 숫자가 아닌", "알 수 없는 역할", "여러 묶음의 product", "사전에 없는"):
            self.assertIn(word, problems)

    def test_suggest_finds_leading_import_and_apply_is_strict(self):
        months = pd.date_range("2022-01-01", periods=45, freq="MS")
        rng = np.random.default_rng(1)
        wave = np.sin(np.arange(51) / 4) + 0.02 * rng.normal(size=51)
        rows = []
        for i, m in enumerate(months):
            rows.append((m, "export", "8542", 1e9 * np.exp(0.5 * wave[i])))  # 제품
            rows.append((m, "import", "9999", 1e9 * np.exp(0.5 * wave[i + 6])))  # 6개월 선행 수입
            rows.append((m, "import", "1111", 1e9 * np.exp(0.5 * rng.normal())))  # 무관
            rows.append((m, "export", "7777", 1e11))  # 한국 전체 수출·수입의 대부분 (경기 기준)
            rows.append((m, "import", "6666", 1e11))
        series = pd.DataFrame(rows, columns=["month", "flow", "hs4", "amount"])
        cand = R.suggest(series, GROUPS, "2025-09-01").set_index("hs4")
        self.assertEqual(cand.loc["9999", "role"], "input")
        self.assertEqual(cand.loc["9999", "lag"], 6)
        self.assertGreater(cand.loc["9999", "corr"], 0.8)
        merged = R.apply_candidates(GROUPS, cand.reset_index(), "2025-09")
        added = merged.iloc[len(GROUPS):]
        self.assertEqual(added.hs4.tolist(), ["9999"])  # 무관 품목은 기준 미달
        self.assertEqual(added.source.tolist(), ["data"])

    def test_suggest_ignores_months_after_until(self):
        series = pd.DataFrame([(m, "export", "8542", 1.0) for m in pd.date_range("2022-01-01", periods=30, freq="MS")],
                              columns=["month", "flow", "hs4", "amount"])
        self.assertTrue(R.suggest(series, GROUPS, "2022-06-01").empty)


class FxFromDbTest(unittest.TestCase):
    def test_own_engine_is_disposed_even_when_query_fails(self):
        from unittest import mock
        engine = mock.Mock()
        engine.connect.side_effect = OSError("DB 중단")
        with mock.patch("backend.app.db.session.make_engine", return_value=engine):
            fx, currencies = S.fx_from_db()
        self.assertTrue(fx.empty and currencies.empty)
        engine.dispose.assert_called_once()

    def test_given_engine_is_left_to_caller(self):
        from unittest import mock
        engine = mock.Mock()
        engine.connect.side_effect = OSError("DB 중단")
        S.fx_from_db(engine)
        engine.dispose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
