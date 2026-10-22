"""수출 유망 시장 모델의 피처 계산·라벨 규칙·학습→예측→조회를 가상 데이터로 확인합니다.

실행: python -m unittest backend/tests/test_ml_market.py
"""
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from backend.app.ml import features as F
from backend.app.ml import market_model as M
from backend.app.ml import preprocess as pp

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
    market = pd.DataFrame(market, columns=["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd"])
    return {"exports": exports,
            # 테스트용 가상 공개일. 실제 CSV에 이 날짜를 적용해서는 안 된다.
            "availability": pd.DataFrame([
                (dataset, source, y, f"{y + 1}-01-01")
                for dataset, source in (("market", "BACI"), ("wdi", "WDI"))
                for y in range(2021, 2027)
            ], columns=["dataset", "source", "year", "available_from"]),
            "countries": pd.DataFrame({"country_code": COUNTRIES, "region_id": [35, 21, 30, 155],
                                       "currency_code": [None, "USD", None, None]}),
            "fx": fx, "news": news, "wdi_yearly": pp.wdi_yearly(wdi),
            "market_yearly": pp.market_yearly(market.assign(source="BACI"))}


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


class PreprocessTest(unittest.TestCase):
    def test_market_yearly(self):
        raw = pd.DataFrame([
            (2022, "VN", "850760", 100.0, 10.0), (2023, "VN", "850760", 200.0, 30.0),
            (2025, "VN", "850760", 400.0, 40.0),  # 2024년이 없어 성장률을 만들지 않음
            (2023, "VN", "850760", 100.0, 10.0),  # HS 묶음 통일로 생긴 같은 키는 합침
            (2023, "US", "010110", 0.0, 0.0),  # 시장 없음
            (2023, "XX", "010110", 50.0, 5.0),  # 대상 국가 아님
        ], columns=["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd"]).assign(source="BACI")
        with unittest.mock.patch.object(pp, "CLIP_Q", (0.0, 1.0)):  # 행이 적어 자르기는 끔
            m = pp.market_yearly(raw, countries={"VN", "US"}).set_index("year")
        self.assertEqual(m.index.tolist(), [2022, 2023, 2025])
        self.assertEqual(m.loc[2023, "import_world_usd"], 300.0)
        self.assertAlmostEqual(m.loc[2023, "kor_share"], 40 / 300)
        self.assertAlmostEqual(m.loc[2023, "mkt_growth"], np.log1p(300) - np.log1p(100))
        self.assertAlmostEqual(m.loc[2023, "kor_share_change"], 40 / 300 - 0.1)
        self.assertTrue(np.isnan(m.loc[2025, "mkt_growth"]))
        self.assertTrue(np.isnan(m.loc[2022, "mkt_growth"]))

    def test_wdi_fills_late_indicators_from_previous_year(self):
        wdi = pd.DataFrame([(2023, "VN", 4e11, 5.0, 1e8, 4000.0), (2024, "VN", 4.5e11, 6.0, 1.01e8, 4400.0),
                            (2025, "VN", None, None, 1.02e8, None),  # GDP만 아직 발표 전
                            (2023, "TW", None, None, None, None), (2024, "TW", None, None, 2.3e7, None)],
                           columns=["year", "country_code", "gdp_usd", "gdp_growth_pct", "population",
                                    "gdp_per_capita_usd"])
        with unittest.mock.patch.object(pp, "CLIP_Q", (0.0, 1.0)):
            w = pp.wdi_yearly(wdi).set_index(["country_code", "year"])
        self.assertAlmostEqual(w.loc[("VN", 2025), "log_gdp"], np.log(4.5e11))  # 2024년 값
        self.assertAlmostEqual(w.loc[("VN", 2025), "log_population"], np.log(1.02e8))  # 발표된 값은 그대로
        self.assertTrue(w.loc[("VN", 2025), "filled"])
        self.assertFalse(w.loc[("VN", 2024), "filled"])
        self.assertTrue(np.isnan(w.loc[("TW", 2024), "log_gdp"]))  # 이을 과거 값이 없음
        self.assertNotIn(("TW", 2023), w.index)  # 값이 전혀 없던 해는 만들지 않음

    def test_growth_after_baci_compares_comtrade_with_comtrade(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "baci").mkdir()
        (tmp / "comtrade").mkdir()
        cols = "year,country_code,hs6,import_world_usd,import_from_kor_usd\n"
        (tmp / "baci" / "baci_imports_hs22.csv").write_text(cols + "2023,NA,010110,90,9\n2024,NA,010110,100,10\n")
        (tmp / "comtrade" / "comtrade_imports_2024.csv").write_text(cols + "2024,NA,010110,105,10\n")  # 연결 연도 (CIF)
        (tmp / "comtrade" / "comtrade_imports_2025.csv").write_text(cols + "2025,NA,010110,210,20\n")
        raw = pp.read_market(tmp)
        self.assertEqual(sorted(raw.source), ["BACI", "BACI", "COMTRADE", "COMTRADE"])
        with unittest.mock.patch.object(pp, "CLIP_Q", (0.0, 1.0)):
            m = pp.market_yearly(raw).set_index("year")
        self.assertEqual(m.index.tolist(), [2023, 2024, 2025])  # 2024년은 BACI 한 행만 (연결 연도 행은 빠짐)
        self.assertEqual(m.loc[2024, "source"], "BACI")
        self.assertAlmostEqual(m.loc[2024, "mkt_growth"], np.log1p(100) - np.log1p(90))  # BACI끼리
        self.assertAlmostEqual(m.loc[2025, "mkt_growth"], np.log1p(210) - np.log1p(105))  # Comtrade끼리
        self.assertEqual(m.loc[2025, "country_code"], "NA")  # 나미비아 NA 보존

    def test_growth_missing_without_same_source_previous_year(self):
        raw = pd.DataFrame([(2024, "VN", "010110", 100.0, 10.0, "BACI"), (2025, "VN", "010110", 210.0, 20.0, "COMTRADE")],
                           columns=["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd", "source"])
        m = pp.market_yearly(raw).set_index("year")
        self.assertTrue(np.isnan(m.loc[2025, "mkt_growth"]))  # 2024년 Comtrade가 없으면 BACI와 섞어 비교하지 않음

    def test_seed_countries_include_extra_seed(self):
        countries = pp.seed_countries()
        self.assertIn("KP", countries)  # 07_country_extra_seed.sql
        self.assertNotIn("KR", countries)  # SPECIAL


class RelatedFeatureTest(unittest.TestCase):
    """같은 품목군 수출·한국 수입·상대국 HS4 수요·단신속보 피처."""

    def data(self, months=26):
        """VN에 같은 HS4(8507)인 두 품목과 다른 품목 하나. 850720만 2023년에 두 배가 된다."""
        month_list = pd.date_range("2022-01-01", periods=months, freq="MS")
        rows = []
        for i, m in enumerate(month_list):
            for hs6, amt in (("850710", 1e5), ("850720", 1e5 * (2 if i >= 12 else 1)), ("010110", 1e5)):
                rows.append((m, "VN", hs6, amt, 0.0, amt / 10))
        data = fake_data(months=months)
        data["exports"] = pd.DataFrame(rows, columns=["month", "country_code", "hs6", "amount", "anomaly_amount", "weight"])
        imports = [(m, "VN", "850710", 50.0 * (3 if i >= 12 else 1)) for i, m in enumerate(month_list)]
        imports += [(m, "CN", "850790", 10.0) for m in month_list]
        data["imports"] = pd.DataFrame(imports, columns=["month", "country_code", "hs6", "amount"])
        return data

    def row(self, ds, hs6):
        """첫 기준월 2023-12: 최근 12개월 = 2023년, 직전 12개월 = 2022년."""
        return ds[(ds.hs6 == hs6) & (ds.anchor == pd.Timestamp("2023-12-01"))].iloc[0]

    def test_same_group_exports_exclude_own_product(self):
        ds = F.build_dataset(self.data())
        a, b = self.row(ds, "850710"), self.row(ds, "850720")
        self.assertAlmostEqual(a.grp4_c_growth_12m, np.log1p(24e5) - np.log1p(12e5))  # 850720만 보고 두 배
        self.assertAlmostEqual(b.grp4_c_growth_12m, 0.0)  # 850710은 변화 없음
        self.assertAlmostEqual(self.row(ds, "010110").grp4_c_growth_12m, 0.0)  # 같은 HS4에 다른 품목 없음
        self.assertAlmostEqual(a.grp2_c_growth_12m, np.log1p(24e5) - np.log1p(12e5))  # 85장에서도 자신 제외

    def test_korea_imports_by_product_group_and_partner(self):
        ds = F.build_dataset(self.data())
        a = self.row(ds, "850710")
        three_x = np.log1p(50 * 3 * 12) - np.log1p(50 * 12)
        self.assertAlmostEqual(a.imp_hs6_world_growth, three_x)
        self.assertAlmostEqual(a.imp_hs4_world_growth, np.log1p(1800 + 120) - np.log1p(600 + 120))  # CN 850790 포함
        self.assertAlmostEqual(a.imp_hs4_from_c_growth, three_x)  # VN에서 들어온 8507만
        self.assertAlmostEqual(self.row(ds, "010110").imp_hs6_world_growth, 0.0)  # 수입한 적 없는 품목은 0

    def test_month_without_import_collection_is_missing_not_zero(self):
        data = self.data()
        data["imports"] = data["imports"][pd.to_datetime(data["imports"].month) != pd.Timestamp("2023-06-01")]
        ds = F.build_dataset(data)
        self.assertTrue(np.isnan(self.row(ds, "010110").imp_hs6_world_growth))  # 창 안에 미수집 달
        data["imports"] = data["imports"].iloc[0:0]
        self.assertTrue(F.build_dataset(data)[["imp_hs6_world_growth", "imp_exp_ratio_hs4"]].isna().all().all())

    def test_destination_hs4_growth_compares_same_source(self):
        raw = pd.DataFrame([(2024, "VN", "850710", 100.0, 1.0, "BACI"), (2024, "VN", "850720", 100.0, 1.0, "BACI"),
                            (2024, "VN", "850710", 110.0, 1.0, "COMTRADE"), (2024, "VN", "850720", 110.0, 1.0, "COMTRADE"),
                            (2025, "VN", "850710", 440.0, 1.0, "COMTRADE")],  # 850720은 2025년 신규 없음
                           columns=["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd", "source"])
        m = pp.market_yearly(raw).set_index(["year", "hs6"])
        self.assertAlmostEqual(m.loc[(2025, "850710"), "mkt_hs4_growth"], np.log1p(440) - np.log1p(220))  # Comtrade끼리
        self.assertTrue(np.isnan(m.loc[(2024, "850710"), "mkt_hs4_growth"]))  # 2023년 BACI 없음

    def test_previous_year_availability_looked_up_by_source(self):
        raw = pd.DataFrame([(2024, "VN", "850710", 100.0, 1.0, "BACI"), (2024, "VN", "850710", 105.0, 1.0, "COMTRADE"),
                            (2025, "VN", "850710", 210.0, 1.0, "COMTRADE")],
                           columns=["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd", "source"])
        avail = pd.DataFrame([("market", "BACI", 2024, "2026-01-22"), ("market", "COMTRADE", 2025, "2026-10-19")],
                             columns=["dataset", "source", "year", "available_from"])
        with self.assertRaises(ValueError):  # 2025년 성장률은 연결 연도 Comtrade 2024의 공개일도 필요
            F.dated_external(pp.market_yearly(raw), avail, "market")
        avail.loc[len(avail)] = ("market", "COMTRADE", 2024, "2026-10-21")
        dated = F.dated_external(pp.market_yearly(raw), avail, "market").set_index("year")
        self.assertEqual(dated.loc[2025, "available_from"], pd.Timestamp("2026-10-21"))

    def test_shortnews_files_counted_by_hs4_country_and_topic(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "run").mkdir()
        pd.DataFrame({"nttSn": ["1", "2", "3"], "nat": ["러시아연방", "베트남", "베트남"],
                      "othbcDt": ["2024-03-02", "2024-03-20", "2024-04-01"]}).to_csv(folder / "run" / "shortnews_raw.csv", index=False)
        pd.DataFrame({"external_id": ["1", "2", "3"], "hs_codes": [["850710", "850720"], ["850710"], []]}).to_json(
            folder / "short_news_analysis.jsonl", orient="records", lines=True, force_ascii=False)
        pd.DataFrame({"external_id": ["2", "3"], "picked": [["관세"], ["관세", "없는토픽"]]}).to_json(
            folder / "short_news_topics.jsonl", orient="records", lines=True, force_ascii=False)
        names = pd.DataFrame({"country_code": ["VN", "RU"], "name_ko": ["베트남", "러시아 연방"]})
        majors = pd.DataFrame({"topic": ["관세"], "major": ["무역·통상"]})
        news, topics, start = F.load_shortnews(folder, names, majors)
        counts = news.set_index(["month", "country_code", "hs4"])["count"]
        self.assertEqual(counts[(pd.Timestamp("2024-03-01"), "RU", "8507")], 1)  # 별칭으로 RU, 한 기사 = 1건
        self.assertEqual(counts[(pd.Timestamp("2024-03-01"), "*", "8507")], 2)
        t = topics.set_index(["month", "country_code", "major"])["count"]
        self.assertEqual(t[(pd.Timestamp("2024-04-01"), "VN", "무역·통상")], 1)
        self.assertEqual(t[(pd.Timestamp("2024-03-01"), "VN", "*")], 1)
        self.assertEqual(start, pd.Timestamp("2024-03-01"))
        ds = pd.DataFrame({"anchor": pd.to_datetime(["2024-04-01", "2024-05-01"]), "country_code": "VN", "hs6": "850710"})
        out = F._add_news(ds, news, topics, start)
        self.assertTrue(np.isnan(out.news_3m).all())  # 수집 시작 전 달이 창에 들어감
        out = F._add_news(ds, news, topics)
        self.assertEqual(out.news_trade_3m.tolist(), [2, 2])  # 베트남 기사 2건 모두 '관세' 토픽
        self.assertEqual(out.news_country_3m.tolist(), [2, 2])


class ModelTest(unittest.TestCase):
    def test_related_features_are_selected_by_the_requested_direction_and_metric(self):
        def report():
            return {"model_a": {"ndcg_at_5": .7}, "model_a_product": {"ndcg_at_5": .9},
                    "features_used": F.FEATURES,
                    "without_related": {"model_a": {"ndcg_at_5": .8},
                        "model_a_product": {"ndcg_at_5": .6}, "features_used": ["growth_12m"]}}
        country = report()
        kept = M.select_a_features(country, "country_ndcg_at_5")
        self.assertEqual(country["model_a_variant"], "without_related")
        self.assertEqual(country["model_a"]["ndcg_at_5"], .8)
        self.assertEqual(country["with_related"]["model_a"]["ndcg_at_5"], .7)
        self.assertFalse(set(kept) & set(F.RELATED_FEATURES))
        product = report()
        self.assertEqual(M.select_a_features(product, "product_ndcg_at_5"), F.FEATURES)
        self.assertEqual(product["model_a_variant"], "with_related")

    def test_semiconductor_evaluation_reports_sparse_groups_without_inventing_scores(self):
        ds = F.build_dataset(fake_data())
        ds.loc[ds.hs6 == PRODUCTS[0], "hs6"] = "854232"
        report = M.evaluate(ds)
        chip, other = report["segments"]["semiconductor"], report["segments"]["other"]
        self.assertEqual(chip["rows"], len(COUNTRIES))
        self.assertEqual(chip["rows"] + other["rows"], report["test_rows"])
        self.assertEqual(chip["model_a"]["countries"], 0)
        self.assertIsNone(chip["model_a"]["ndcg_at_5"])
        self.assertEqual(other["model_a"]["countries"], len(COUNTRIES))
        self.assertIn("fx_change_12m", report["features_used"])
        self.assertFalse(set(report["without_related"]["features_used"]) & set(F.RELATED_FEATURES))

    def test_train_predict_and_query(self):
        out = Path(tempfile.mkdtemp())
        ds = F.build_dataset(fake_data())
        report = M.evaluate(ds)
        # 저장·조회 계약 검증을 위한 통과 보고서. 실제 성능 우수성을 주장하는 테스트가 아니다.
        report["model_a"]["ndcg_at_5"] = report["baseline_a"]["ndcg_at_5"]
        with unittest.mock.patch.object(M, "evaluate", return_value=report):
            meta = M.train(ds, out_dir=out, promotion_metric="country_ndcg_at_5")
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
        loaded = joblib.load(out / "models.joblib")
        latest = ds[ds.anchor == ds.anchor.max()]
        np.testing.assert_allclose(loaded["model_a"].predict(latest[F.FEATURES]), M.load_predictions(out).score)


if __name__ == "__main__":
    unittest.main()

