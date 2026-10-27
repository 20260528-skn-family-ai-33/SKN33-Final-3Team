"""시간 누수·평가·저장 회귀 검사. 가상 공개일과 평가값은 실데이터 기준이 아니다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

from backend.app.ml import features as F, market_model as M, preprocess as pp
from backend.app.ml.temporal import dated_external
from backend.app.ml.transforms import TrainingClipper
from backend.tests.test_ml_market import fake_data, PRODUCTS
from backend.tests import test_external_data as external_tests

ec = external_tests.ec


class TemporalTest(unittest.TestCase):
    def test_missing_availability_is_not_guessed(self):
        data = fake_data()
        del data["availability"]
        with self.assertRaisesRegex(ValueError, "공개·버전"):
            F.build_dataset(data)

    def test_unpublished_values_not_attached(self):
        data = fake_data(months=36)
        data["availability"]["available_from"] = "2030-01-01"
        ds = F.build_dataset(data)
        self.assertTrue(ds[["mkt_growth", "mkt_log_import", "log_gdp"]].isna().all().all())
        self.assertTrue(M.available_b(ds, "2024-12-01").empty)

    def test_latest_available_year_used_and_not_calendar_minus_one(self):
        data = fake_data()
        dates = data["availability"]
        dates.loc[(dates.dataset == "market") & (dates.year >= 2023), "available_from"] = "2030-01-01"
        ds = F.build_dataset(data)
        row = ds[(ds.anchor == pd.Timestamp("2024-12-01")) & (ds.country_code == "VN") & (ds.hs6 == PRODUCTS[0])].iloc[0]
        source = data["market_yearly"]
        expected = source[(source.year == 2022) & (source.country_code == "VN") & (source.hs6 == PRODUCTS[0])].iloc[0]
        self.assertEqual(row.mkt_log_import, expected.mkt_log_import)

    def test_previous_revision_delays_derived_growth(self):
        data = fake_data()
        dates = data["availability"]
        dates.loc[(dates.dataset == "market") & (dates.year == 2022), "available_from"] = "2030-01-01"
        out = dated_external(data["market_yearly"], dates, "market")
        self.assertTrue((out[out.year == 2023].available_from == pd.Timestamp("2030-01-01")).all())

    def test_duplicate_availability_rejected(self):
        data = fake_data()
        duplicate = pd.concat([data["availability"], data["availability"].iloc[[0]]])
        with self.assertRaisesRegex(ValueError, "중복"):
            dated_external(data["market_yearly"], duplicate, "market")

    def test_r1_current_and_horizon_last_month_not_corrected(self):
        data = fake_data(months=38)
        before = F.build_dataset(data)
        data["exports"]["r1_adjustment"] = 0.0
        e = data["exports"]
        pair = (e.country_code == "VN") & (e.hs6 == PRODUCTS[0])
        e.loc[pair & e.month.isin(pd.to_datetime(["2023-12-01", "2024-12-01"])), "r1_adjustment"] = -1000.0
        after = F.build_dataset(data)
        key = (before.country_code == "VN") & (before.hs6 == PRODUCTS[0]) & (before.anchor == pd.Timestamp("2023-12-01"))
        cols = ["exp_12m", "growth_12m", "growth_3m", "y_growth", "future_cv", "cv_12m"]
        np.testing.assert_allclose(before.loc[key, cols], after.loc[key, cols])
        e.loc[pair & (e.month == pd.Timestamp("2023-11-01")), "r1_adjustment"] = -500.0
        historical = F.build_dataset(data)
        self.assertAlmostEqual(historical.loc[key, "exp_12m"].iloc[0], before.loc[key, "exp_12m"].iloc[0] - 500)

    def test_r1_only_flag_at_anchor_not_counted_as_known_anomaly(self):
        data = fake_data(months=36)
        e = data["exports"]
        mask = (e.month == pd.Timestamp("2023-12-01")) & (e.country_code == "VN") & (e.hs6 == PRODUCTS[0])
        e.loc[mask, "anomaly_amount"] = e.loc[mask, "amount"]
        e["r1_only_amount"] = 0.0
        before = F.build_dataset(data)
        e.loc[mask, "r1_only_amount"] = e.loc[mask, "amount"]
        after = F.build_dataset(data)
        key = (before.country_code == "VN") & (before.hs6 == PRODUCTS[0]) & (before.anchor == pd.Timestamp("2023-12-01"))
        expected = e.loc[mask, "amount"].iloc[0] / before.loc[key, "exp_12m"].iloc[0]
        self.assertAlmostEqual((before.loc[key, "anomaly_ratio"] - after.loc[key, "anomaly_ratio"]).iloc[0], expected)


class EvaluationTest(unittest.TestCase):
    def test_clip_bounds_persist_and_ignore_test_distribution(self):
        x = pd.DataFrame({"mkt_growth": [0., 1., 2., 3.], "gdp_growth": [1., 2., 3., 4.]})
        clipper = TrainingClipper(columns=("mkt_growth", "gdp_growth")).fit(x)
        bounds = dict(clipper.bounds_)
        out = clipper.transform(pd.DataFrame({"mkt_growth": [1e9], "gdp_growth": [-1e9]}))
        self.assertEqual(bounds, clipper.bounds_)
        self.assertEqual(out.mkt_growth.iloc[0], bounds["mkt_growth"][1])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transform.joblib"
            joblib.dump(clipper, path)
            pd.testing.assert_frame_equal(joblib.load(path).transform(x), clipper.transform(x))

    def test_preprocessing_keeps_raw_growth(self):
        raw = pd.DataFrame([(2022, "VN", "010110", 10., 1.), (2023, "VN", "010110", 10000., 2.)],
                           columns=["year", "country_code", "hs6", "import_world_usd", "import_from_kor_usd"])
        before = pp.market_yearly(raw.assign(source="BACI"))
        extra = raw.iloc[[1]].assign(year=2024, import_world_usd=1e20)
        after = pp.market_yearly(pd.concat([raw, extra]).assign(source="BACI"))
        self.assertEqual(before.iloc[-1].mkt_growth, after[after.year == 2023].iloc[0].mkt_growth)

    def test_rejected_models_leave_existing_outputs_intact(self):
        report = {"model_a": {"precision_at_5": .1}, "baseline_a": {"precision_at_5": .5}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            files = [path / name for name in ("models.joblib", "predictions.csv", "metadata.json")]
            for file in files:
                file.write_bytes(b"existing model")
            with patch.object(M, "evaluate", return_value=report):
                for metric in (None, "country_precision_at_5"):
                    with self.assertRaises(M.PromotionRejected):
                        M.train(pd.DataFrame(), path, promotion_metric=metric)
            self.assertTrue(all(file.read_bytes() == b"existing model" for file in files))

    def test_missing_score_not_promoted_and_equal_score_allowed(self):
        report = {"model_a": {"precision_at_5": None}, "baseline_a": {"precision_at_5": .5}}
        with self.assertRaises(M.PromotionRejected):
            M.require_promotion(report, "country_precision_at_5")
        report["model_a"]["precision_at_5"] = .5
        M.require_promotion(report, "country_precision_at_5")

    def test_model_b_compares_same_rows(self):
        ds = F.build_dataset(fake_data())
        anchor = ds[ds.y_growth.notna()].anchor.max()
        ds.loc[(ds.anchor == anchor) & (ds.country_code == "VN"), "mkt_growth"] = np.nan
        report = M.evaluate(ds)
        self.assertEqual(report["model_b"]["rows"], report["baseline_b"]["rows"])
        self.assertLess(report["model_b"]["rows"], report["model_b_all"]["rows"])
        self.assertEqual(report["model_a_product"]["products"], 0)  # 가상 후보는 국가 4개뿐

    def test_unknown_thresholds_do_not_produce_mature_market_label(self):
        th = {"mkt_growth_hi": np.nan, "share_hi": np.nan, "cv_hi": np.nan}
        self.assertTrue(np.isnan(F.market_type([0.], [0.], [.1], [.1], th)).all())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "predictions.csv").write_text("country_code,hs6,market_type_name\nVN,010110,\n")
            self.assertIsNone(M.market_type_of("VN", "010110", out))


class ObservationRegressionTest(unittest.TestCase):
    def test_missing_entire_month_cannot_create_false_future_labels(self):
        data = fake_data()
        data["exports"] = data["exports"][data["exports"].month != pd.Timestamp("2024-06-01")]
        with self.assertRaisesRegex(ValueError, "amount 60건.*2024-06"):
            F.build_dataset(data)

    def test_missing_pair_month_cannot_distort_history_or_group_totals(self):
        for month in ("2022-06-01", "2024-06-01"):
            with self.subTest(month=month):
                data = fake_data()
                e = data["exports"]
                gap = (e.country_code == "VN") & (e.hs6 == PRODUCTS[0]) & (e.month == pd.Timestamp(month))
                data["exports"] = e[~gap]
                with self.assertRaisesRegex(ValueError, "amount 1건"):
                    F.build_dataset(data)

    def test_null_amount_and_r1_adjustment_do_not_become_zero(self):
        for column in ("amount", "r1_adjustment"):
            with self.subTest(column=column):
                data = fake_data()
                if column == "r1_adjustment":
                    data["exports"][column] = 0.0
                data["exports"].loc[0, column] = np.nan
                with self.assertRaisesRegex(ValueError, column + " 1건"):
                    F.build_dataset(data)

    def test_observed_zero_is_valid_and_recent_future_stays_unknown(self):
        data = fake_data(months=36)
        e = data["exports"]
        pair = (e.country_code == "VN") & (e.hs6 == PRODUCTS[0])
        e.loc[pair & (e.month >= "2024-01-01"), ["amount", "weight", "anomaly_amount"]] = 0.0
        ds = F.build_dataset(data)
        row = ds[(ds.country_code == "VN") & (ds.hs6 == PRODUCTS[0]) & (ds.anchor == "2023-12-01")].iloc[0]
        self.assertAlmostEqual(row.y_growth, -np.log1p(row.exp_12m))
        self.assertTrue(ds.loc[ds.anchor == ds.anchor.max(), "y_growth"].isna().all())

    def test_optional_weight_null_stays_unknown(self):
        data = fake_data(months=36)
        e = data["exports"]
        e.loc[(e.country_code == "VN") & (e.hs6 == PRODUCTS[0]) & (e.month == "2023-12-01"), "weight"] = np.nan
        ds = F.build_dataset(data)
        row = ds[(ds.country_code == "VN") & (ds.hs6 == PRODUCTS[0]) & (ds.anchor == "2023-12-01")].iloc[0]
        self.assertTrue(np.isnan(row.unit_trend))
        self.assertTrue(np.isfinite(row.y_growth))

    def test_duplicate_month_is_rejected_instead_of_overwriting(self):
        data = fake_data()
        duplicate = data["exports"].iloc[[0]].assign(month=pd.Timestamp("2022-01-15"))
        data["exports"] = pd.concat([data["exports"], duplicate], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "중복"):
            F.build_dataset(data)


class NewsRegressionTest(unittest.TestCase):
    def inputs(self):
        ds = pd.DataFrame({"anchor": pd.date_range("2024-01-01", periods=7, freq="MS"),
                           "country_code": "VN", "hs6": PRODUCTS[0]})
        news = pd.DataFrame([(pd.Timestamp("2024-01-01"), "VN", PRODUCTS[0], 3),
                             (pd.Timestamp("2024-01-01"), "*", PRODUCTS[0], 5)],
                            columns=["month", "country_code", "hs6", "count"])
        return ds, news

    def test_recent_and_previous_windows_survive_last_article(self):
        ds, news = self.inputs()
        out = F._add_news(ds, news)
        self.assertEqual(out.news_3m.tolist(), [3, 3, 3, 0, 0, 0, 0])
        self.assertEqual(out.news_all_3m.tolist(), [5, 5, 5, 0, 0, 0, 0])
        np.testing.assert_allclose(out.news_change, [np.log(4)] * 3 + [-np.log(4)] * 3 + [0])

    def test_future_articles_do_not_change_past_features(self):
        ds, news = self.inputs()
        future = news.assign(month=pd.Timestamp("2025-01-01"), count=1000)
        pd.testing.assert_frame_equal(F._add_news(ds, news), F._add_news(ds, pd.concat([news, future])))
        # 기준월보다 미래 기사만 있는 경우도 과거는 0이다.
        self.assertTrue((F._add_news(ds, future)[["news_3m", "news_all_3m", "news_change"]] == 0).all().all())

    def test_single_anchor_includes_prior_news(self):
        ds, news = self.inputs()
        out = F._add_news(ds.iloc[[4]], news)
        self.assertAlmostEqual(out.news_change.iloc[0], -np.log(4))


class CollectorRegressionTest(unittest.TestCase):
    def test_empty_directory_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ec, "OUT_DIR", Path(tmp)):
            self.assertFalse(ec.check(False))

    def test_missing_trade_amount_is_not_zero(self):
        df = external_tests.CheckTest().frame(import_from_kor_usd=[np.nan, 2.])
        self.assertTrue(ec.check_baci(df))
        self.assertTrue(ec.check_baci(df.drop(columns="import_world_usd")))

    def test_raw_null_invalid_or_infinite_amount_rejected_before_sum(self):
        world = {"refYear": 2025, "reporterCode": 842, "partnerCode": 0, "cmdCode": "850760",
                 "primaryValue": 100., "classificationCode": "H6"}
        korea = dict(world, partnerCode=410, primaryValue=30.)
        for partner in (0, 410):
            for value in (None, np.nan, "", "invalid", np.inf, -np.inf):
                with self.subTest(partner=partner, value=value):
                    # 정상 금액과 같은 키여도 원본 결측을 집계로 숨기지 않는다.
                    invalid = dict(world, partnerCode=partner, primaryValue=value)
                    with self.assertRaisesRegex(ValueError, "primaryValue"):
                        ec.comtrade_imports([world, korea, invalid], {842: "US"})

    def test_explicit_zero_valid_and_out_of_scope_null_ignored(self):
        world = {"refYear": 2025, "reporterCode": 842, "partnerCode": 0, "cmdCode": "850760",
                 "primaryValue": 100., "classificationCode": "H6"}
        records = [world, dict(world, partnerCode=410, primaryValue=0.),
                   dict(world, classificationCode="H5", primaryValue=None)]
        out = ec.comtrade_imports(records, {842: "US"})
        self.assertEqual(out.import_from_kor_usd.iloc[0], 0.)
        self.assertEqual(out.import_world_usd.iloc[0], 100.)


class ExternalVersionTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "wdi").mkdir()
        self.wdi = self.dir / "wdi" / "wdi_2022_2025.csv"
        self.wdi.write_text("year,country_code\n2024,US\n", "utf-8")

    def snapshot(self):
        import json
        from backend.app.ml.model_registry import sha256
        files = [{"path": r"data\external\wdi\wdi_2022_2025.csv", "sha256": sha256(self.wdi)}]  # 실제 기록과 같은 형식
        (self.dir / "availability_snapshot.json").write_text(json.dumps({"files": files}), "utf-8")

    def test_recorded_version_passes(self):
        self.snapshot()
        pp.check_versions(self.dir)

    def test_changed_or_unrecorded_file_rejected(self):
        with self.assertRaisesRegex(ValueError, "availability_snapshot"):
            pp.check_versions(self.dir)  # 기록 파일 없음
        self.snapshot()
        self.wdi.write_text("year,country_code\n2024,US\n2025,US\n", "utf-8")  # 다시 받아 값이 바뀜
        with self.assertRaisesRegex(ValueError, "wdi/wdi_2022_2025.csv"):
            pp.check_versions(self.dir)


if __name__ == "__main__":
    unittest.main()
