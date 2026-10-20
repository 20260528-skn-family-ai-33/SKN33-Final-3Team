"""수출 유망 시장 모델의 피처 계산·라벨 규칙·학습→예측→조회를 가상 데이터로 확인합니다.

실행: python -m unittest backend/tests/test_ml_market.py
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.ml import features as F
from backend.app.ml import market_model as M

COUNTRIES = ["VN", "US", "CN", "DE"]
PRODUCTS = [f"{h:02d}{i:04d}" for h, i in zip(range(1, 16), range(100, 115))]


def fake_data(months=50, seed=0):
    """국가 4 × 품목 15, 2022-01부터 months개월. 쌍마다 성장 추세가 달라 모델이 배울 신호가 있다."""
    rng = np.random.default_rng(seed)
    month_list = pd.date_range("2022-01-01", periods=months, freq="MS")
    rows, market = [], []
    for c in COUNTRIES:
        for p in PRODUCTS:
            base, trend = rng.uniform(2e4, 2e5), rng.normal(0, 0.03)
            for i, m in enumerate(month_list):
                amt = base * np.exp(trend * i) * rng.uniform(0.8, 1.2)
                rows.append((m, c, p, amt, amt * 0.1 if i % 13 == 0 else 0.0, amt / 5))
            for y in range(2021, 2027):
                world = 1e8 * np.exp(trend * 6 * (y - 2021)) * rng.uniform(0.9, 1.1)
                market.append((y, c, p, world, world * rng.uniform(0.01, 0.3)))
    exports = pd.DataFrame(rows, columns=["month", "country_code", "hs6", "amount", "anomaly_amount", "weight"])
    fx = pd.DataFrame([(m.strftime("%Y-%m-01"), "USD", 1300 + i) for i, m in enumerate(month_list)],
                      columns=["month", "currency_code", "rate"])
    news = pd.DataFrame([(month_list[-1].strftime("%Y-%m-01"), "VN", PRODUCTS[0], 3),
                         (month_list[-1].strftime("%Y-%m-01"), "*", PRODUCTS[0], 7)],
                        columns=["month", "country_code", "hs6", "count"])
    wdi = pd.DataFrame([(y, c, 1e12, 2.5, 5e7, 2e4) for y in range(2021, 2026) for c in COUNTRIES],
                       columns=["year", "country_code", "gdp_usd", "gdp_growth_pct", "population", "gdp_per_capita_usd"])
    return {"exports": exports,
            "countries": pd.DataFrame({"country_code": COUNTRIES, "region_id": [35, 21, 30, 155],
                                       "currency_code": [None, "USD", None, None]}),
            "fx": fx, "news": news, "wdi": wdi,
            "market": pd.DataFrame(market, columns=["year", "country_code", "hs6", "import_world_usd",
                                                    "import_from_kor_usd"])}


class FeatureTest(unittest.TestCase):
    def test_hs2017_codes_merge_into_hs2022_group(self):
        corr = pd.DataFrame({"new_code": ["850761", "850762"], "old_code": ["850760", "850760"],
                             "group_code": ["850761", "850761"]})
        df = pd.DataFrame({"hs_ver": ["HS2017", "HS2022", "HS2022", "HS2022"],
                           "hs6": ["850760", "850762", "850760", "010110"]})
        out = F.apply_hs_key(df, F.hs_key_map(corr))
        self.assertEqual(out.hs6.tolist(), ["850761", "850761", "850760", "010110"])  # HS2022 850760은 판이 달라 그대로

    def test_window_features_and_future_label(self):
        data = fake_data(months=36)
        ds = F.build_dataset(data)
        row = ds[(ds.country_code == "VN") & (ds.hs6 == PRODUCTS[0])
                 & (ds.anchor == pd.Timestamp("2023-12-01"))].iloc[0]
        e = data["exports"]
        amt = e[(e.country_code == "VN") & (e.hs6 == PRODUCTS[0])].set_index("month").amount
        s12, p12 = amt["2023-01":"2023-12"].sum(), amt["2022-01":"2022-12"].sum()
        self.assertAlmostEqual(row.growth_12m, np.log1p(s12) - np.log1p(p12))
        self.assertAlmostEqual(row.y_growth, np.log1p(amt["2024-01":"2024-12"].sum()) - np.log1p(s12))
        self.assertEqual(row.region_id, 35)
        self.assertTrue(np.isnan(ds[ds.anchor == ds.anchor.max()].y_growth).all())  # 최근 기준월은 예측 대상
        self.assertEqual(ds.anchor.min(), pd.Timestamp("2023-12-01"))  # 24개월 이력부터

    def test_small_pairs_dropped_and_short_history_rejected(self):
        with self.assertRaises(ValueError):
            F.build_dataset(fake_data(months=12))
        with self.assertRaises(ValueError):
            F.build_dataset(fake_data(), min_export=1e15)  # 기준을 넘는 쌍이 없음

    def test_market_type_rules(self):
        th = {"mkt_growth_hi": 0.1, "share_hi": 0.2, "cv_hi": 0.5}
        got = F.market_type(kor_growth=[0.3, 0.3, 0.0, -0.2, 0.1, 0.1, np.nan],
                            mkt_growth=[0.2, 0.2, 0.0, -0.1, 0.5, 0.0, 0.2],
                            share=[0.3, 0.1, 0.3, 0.1, 0.3, 0.1, 0.3],
                            cv=[0.1, 0.1, 0.1, 0.1, 0.9, 0.1, 0.1], th=th)
        np.testing.assert_array_equal(got[:6], [0, 1, 2, 3, 4, 2])
        self.assertTrue(np.isnan(got[6]))


class ModelTest(unittest.TestCase):
    def test_train_predict_and_query(self):
        out = Path(tempfile.mkdtemp())
        meta = M.train(F.build_dataset(fake_data()), out_dir=out)
        ev = meta["evaluation"]
        self.assertEqual(ev["model_a"]["countries"], len(COUNTRIES))
        self.assertIsNotNone(ev["baseline_a"]["precision_at_5"])
        self.assertIn("macro_f1", ev["model_b"])
        self.assertEqual(meta["predict_anchor"], "2026-02-01")

        top = M.top_products("VN", out_dir=out)
        self.assertEqual(len(top), 5)
        self.assertEqual(top.rank_in_country.tolist(), [1, 2, 3, 4, 5])
        self.assertTrue(top.score.is_monotonic_decreasing)
        countries = M.top_countries(PRODUCTS[0], out_dir=out)
        self.assertEqual(sorted(countries.country_code), sorted(COUNTRIES))  # 국가가 4개뿐이라 전부
        self.assertIn(M.market_type_of("VN", PRODUCTS[0], out_dir=out), F.TYPE_NAMES.values())
        self.assertIsNone(M.market_type_of("VN", "999999", out_dir=out))
        self.assertTrue((out / "models.joblib").exists())


if __name__ == "__main__":
    unittest.main()
