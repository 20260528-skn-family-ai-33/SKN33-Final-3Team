"""2단계 구성(회귀 → 순위 학습)의 후보 선택 · 순서 · 앙상블을 가상 데이터로 확인한다 (실데이터 학습 없음).

실행: python -m unittest backend.tests.test_ml_two_stage
"""
import unittest

import numpy as np

from backend.app.ml import two_stage as T
from backend.app.ml.candidate_models import needed_columns
from backend.app.ml.experiments import FEATURE_SETS, prepare_scenarios
from backend.app.ml.feature_recovery import MARKET, WDI, coverage
from backend.tests.test_ml_experiments import dataset


class TwoStageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = prepare_scenarios({"fake": dataset()[[*needed_columns(), "exp_12m"]]}, 12)["fake"]
        cls.rows = frame[frame.y_growth.notna()]
        cls.latest = frame[frame.anchor == frame.anchor.max()]
        candidates = list(FEATURE_SETS["all"])
        stats = coverage(cls.rows[candidates], candidates)
        cls.columns = stats.loc[stats.status == "available", "feature"].tolist()

    def test_filtering_puts_top_k_of_stage1_above_the_rest(self):
        bundle = T.fit(self.rows, self.columns, "C", k=5, n_jobs=1, folds=2)
        pred = T.predict(bundle, self.latest)
        order, growth = pred.order.to_numpy(), pred.growth.to_numpy()
        keep = T._top_k(self.latest, growth, 5)
        for country in self.latest.country_code.unique():
            mine = (self.latest.country_code == country).to_numpy()
            if (mine & ~keep).any():  # 후보는 2단계 점수로 후보 밖보다 항상 위
                self.assertGreater(order[mine & keep].min(), order[mine & ~keep].max())
        np.testing.assert_array_equal(order[~keep], growth[~keep])  # 후보 밖은 1단계 순서
        self.assertLess(bundle["stage2_rows"], len(self.rows))

    def test_stacking_orders_every_candidate_by_stage2(self):
        bundle = T.fit(self.rows, self.columns, "B", n_jobs=1, folds=2)
        pred = T.predict(bundle, self.latest)
        x = T._x(self.latest, bundle["features"]).assign(stage1_pred=pred.growth.to_numpy())
        np.testing.assert_allclose(pred.order - T.OFFSET, bundle["stage2"].predict(x[bundle["stage2_features"]]))
        self.assertEqual(bundle["stage2_features"][-1], "stage1_pred")

    def test_monthly_records_and_warns_when_external_features_are_missing(self):
        frame = dataset()[[*needed_columns(), "exp_12m"]].copy()
        frame[MARKET + WDI] = np.nan  # 외부 자료가 아직 하나도 들어오지 않은 서버
        with self.assertLogs(T.log, "WARNING"):
            bundle, _, _ = T.monthly(frame, n_jobs=1)
        self.assertEqual(bundle["external_dropped"], {"ext": MARKET + WDI})
        self.assertEqual(bundle["features"]["ext"], bundle["features"]["base"])

    def test_monthly_bundles_both_members_for_the_latest_anchor(self):
        bundle, evaluation, _ = T.monthly(dataset()[[*needed_columns(), "exp_12m"]], n_jobs=1, k=5)
        self.assertEqual(set(bundle["members"]), set(T.MEMBERS))
        latest = bundle["latest"]
        self.assertEqual(latest[T.KEYS].reset_index(drop=True).to_dict("list"),
                         self.latest[T.KEYS].reset_index(drop=True).to_dict("list"))  # 최신 기준월 전체 쌍
        self.assertTrue(np.isfinite(latest[["growth", "order"]].to_numpy()).all())
        np.testing.assert_array_equal(latest.growth_12m, self.latest.growth_12m)  # 현재 동향용 지난 12개월 실적
        for key in ("model", "model_stage1", "baseline_growth_12m"):
            self.assertIsNotNone(evaluation[key]["country_ndcg_at_5"], key)

    def test_query_maps_grouped_hs_code_and_reports_bad_country(self):
        import argparse
        import contextlib
        import io
        from unittest.mock import patch
        latest = self.latest[T.KEYS].assign(growth=np.linspace(-1, 1, len(self.latest)), order=0.0,
                                             exp_12m=self.latest.exp_12m.to_numpy())
        code, hs6 = latest.country_code.iloc[0], latest.hs6.iloc[0]
        path = self.out_file({"latest": latest})

        def error(message):
            raise SystemExit(message)
        with patch("backend.app.ml.candidate_query.comparison_hs6", return_value=hs6) as mapped, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            T.query(argparse.Namespace(model=[path], country=code, hs6="999999", top=5), error)
        mapped.assert_called_once_with("999999")  # 입력 코드를 비교용 대표 코드로 바꿔 찾는다
        self.assertIn("순위(2단계)", out.getvalue())
        self.assertIn("현재 동향 미확인 (실적 증감률 산정 불가) | 전망", out.getvalue())  # 예전 파일은 지난 실적이 없다
        with self.assertRaisesRegex(SystemExit, "찾지 못했"):
            T.query(argparse.Namespace(model=[path], country="없는나라", hs6=None, top=5), error)

    def test_market_view_splits_past_trend_from_ranked_outlook(self):
        import pandas as pd
        current = 100_000 * (1 + np.array([-.5, -.11, -.09, .09, .11, .5, np.nan, 0, 3]))
        pred = pd.DataFrame({"anchor": ["2026-09"] * 6 + ["2026-10"] * 3,
                             "growth": [-3, -2, -1, 0, 1, 2, 5, 6, 7],
                             "exp_12m": current,
                             "growth_12m": np.log1p(current) - np.log1p(100_000)})
        view = T.market_view(pred)
        self.assertEqual(view.trend.tolist(), ["침체", "침체", "정체", "정체", "성장", "성장", "미확인", "정체", "성장"])  # ±10% 안은 정체
        self.assertEqual(view.outlook.tolist(), ["저조", "저조", "보통", "보통", "유망", "유망", "저조", "보통", "유망"])  # 기준월마다 3등분
        self.assertEqual(T.market_view(pred.drop(columns="growth_12m")).trend.unique().tolist(), ["미확인"])
        self.assertEqual(T.market_view(pred.assign(growth=np.nan)).outlook.unique().tolist(), ["미확인"])  # 예측 없으면 등급 없음

    def test_market_view_undoes_log_smoothing_and_rejects_zero_previous_exports(self):
        import pandas as pd
        current = np.array([100_000., 100_000., 150_000., 0.])
        previous = np.array([0., 1., 100_000., 100_000.])
        pred = pd.DataFrame({"anchor": ["2026-09"] * 4, "growth": [0.] * 4,
                             "exp_12m": current, "growth_12m": np.log1p(current) - np.log1p(previous)})
        view = T.market_view(pred)
        self.assertTrue(np.isnan(view.past_pct.iloc[0]))
        self.assertEqual(view.trend.iloc[0], "미확인")
        np.testing.assert_allclose(view.past_pct.iloc[1:], [9_999_900., 50., -100.])
        self.assertTrue(T.market_view(pred.drop(columns="exp_12m")).past_pct.isna().all())

    def test_market_bundle_ranks_once_and_filters_like_query(self):
        from backend.app.ml.candidate_query import pair_rank
        n = len(self.latest)
        latest = self.latest[T.KEYS].assign(growth=np.linspace(-1, 1, n), order=np.arange(n) % 7, product=-np.arange(n) % 5,
                                             exp_12m=self.latest.exp_12m.to_numpy())
        bundle = T.load_market_bundle([self.out_file({"latest": latest})], "스냅샷은 읽지 않음.pkl")
        self.assertEqual(bundle["anchor"], str(self.latest.anchor.iloc[0].date()))
        code, hs6 = latest.country_code.iloc[3], latest.hs6.iloc[3]
        pair = T.predict_markets(bundle, country_code=code, hs6=hs6)
        self.assertEqual(list(pair.columns), T.MARKET_COLUMNS)
        expected = pair_rank(latest, code, hs6, country_by="order", product_by="product")  # query가 쓰던 순위 규칙
        self.assertEqual(pair.loc[0, ["country_rank", "country_size", "product_rank", "product_size"]].tolist(),
                         [expected[k] for k in ("country_rank", "country_size", "product_rank", "product_size")])
        top = T.predict_markets(bundle, country_code=code, top=3)
        self.assertEqual(top.hs6.tolist(), latest[latest.country_code == code].sort_values(["order", "hs6"], ascending=[False, True]).hs6[:3].tolist())
        self.assertTrue(top.country_rank.is_monotonic_increasing)
        countries = T.predict_markets(bundle, hs6=hs6, top=50)
        self.assertEqual(set(countries.country_code), set(latest[latest.hs6 == hs6].country_code))
        self.assertTrue(countries.product_rank.is_monotonic_increasing)
        self.assertTrue(T.predict_markets(bundle, country_code="없는나라").empty)  # 예측 대상이 아니면 빈 표
        with self.assertRaisesRegex(ValueError, "국가 또는 HS6"):
            T.predict_markets(bundle)

    def out_file(self, bundle):
        import tempfile
        from pathlib import Path
        import joblib
        path = Path(tempfile.mkdtemp()) / "model.joblib"
        joblib.dump(bundle, path)
        return path

    def test_cross_prediction_holds_out_whole_pairs(self):
        from unittest.mock import patch
        seen, real = [], T._regressor
        pairs = self.rows.country_code + "_" + self.rows.hs6

        def record(x, y, columns, seed, n_jobs):
            seen.append(set(pairs.loc[x.index]))
            return real(x, y, columns, seed, n_jobs)
        with patch.object(T, "_regressor", side_effect=record):
            T.fit(self.rows, self.columns, "C", k=5, n_jobs=1, folds=2)
        every = set(pairs)
        folds = seen[1:]  # 첫 호출은 전체 행 1단계
        held_out = [every - fit_pairs for fit_pairs in folds]
        self.assertTrue(all(held_out))  # 교차 모델마다 (국가, HS6) 쌍 일부를 모든 기준월에서 통째로 뺀다
        self.assertEqual(set().union(*held_out), every)  # 모든 쌍이 자기 쌍을 보지 않은 모델로 예측된다

    def test_report_writes_markdown_next_to_registry(self):
        import contextlib
        import io
        import json
        import tempfile
        from pathlib import Path
        record = {"model_name": "model_a_two_stage_202609", "created_at": "2026-10-10T03:20:00.000000+09:00",
                  "status": "READY", "training_mode": "FULL_RETRAIN", "parent_version_id": None,
                  "train_start": "2023-12-01", "train_end": "2025-09-01", "train_rows": 1122200,
                  "artifact_path": "model_a_two_stage_202609.joblib", "artifact_sha256": "a" * 64,
                  "data_info": {"trade_month": "2026-09-01", "features_used": {"base": ["x"] * 33, "ext": ["x"] * 42},
                                "external_dropped": {"ext": ["log_gdp"]}, "fit_seconds": 430.0, "runtime": {"lightgbm": "4.7.0"}},
                  "evaluation": {"test_anchor": "2025-09-01", "train_end": "2024-09-01", "train_rows": 511413, "test_rows": 51406,
                                 "model": {"country_ndcg_at_5": 0.30}, "model_stage1": {"product_ndcg_at_5": 0.68, "rmse": 2.3},
                                 "baseline_growth_12m": {"country_ndcg_at_5": 0.35, "product_ndcg_at_5": 0.6}}}
        path = Path(tempfile.mkdtemp()) / "model_a_two_stage_202609.registry.json"
        path.write_text(json.dumps(record), "utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            out = T.report(path)
        self.assertEqual(out.name, "model_a_two_stage_202609.md")
        text = out.read_text("utf-8")
        self.assertIn("| 국가 NDCG@5 (채택) | 0.3000 | 0.3500 | -0.0500 |", text)
        self.assertIn("base 33개, ext 42개", text)
        self.assertIn("ext: log_gdp", text)
        self.assertIn("기준선 이하", text)  # 채택 지표가 기준선보다 낮으면 경고

    def test_kfold_trains_only_12_months_before_each_validation_month_and_writes_report(self):
        import contextlib
        import io
        import tempfile
        import pandas as pd
        result = T.kfold(dataset()[[*needed_columns(), "exp_12m"]], n_splits=2, seeds=(0, 1), n_jobs=1, k=5,
                          min_train_anchors=1, min_train_rows=1)  # 가상 데이터는 정답 있는 기준월이 15개뿐
        self.assertEqual(len(result["folds"]), 2)
        for fold in result["folds"]:
            gap = pd.Timestamp(fold["validation_anchor"]) - pd.DateOffset(months=12)
            self.assertLessEqual(pd.Timestamp(fold["train_end"]), gap)  # 검증 기준월 정답 기간과 겹치지 않는 학습
            self.assertEqual([s["seed"] for s in fold["seeds"]], [0, 1])
            self.assertIsNotNone(fold["model"]["country_ndcg_at_5"])
            self.assertIsNotNone(fold["baseline"]["country_ndcg_at_5"])
        self.assertEqual(result["summary"]["country_ndcg_at_5"]["valid_folds"], 2)
        out = tempfile.mkdtemp()
        with contextlib.redirect_stdout(io.StringIO()):
            md, js = T.kfold_report(result, out)
            again, _ = T.kfold_report(result, out)  # 바로 이어 돌린 실행
        self.assertIn("| 국가 NDCG@5 (채택) |", md.read_text("utf-8"))
        self.assertTrue(js.is_file())
        self.assertNotEqual(md, again)  # 같은 분에 끝나도 앞 리포트를 덮어쓰지 않는다
        self.assertTrue(md.is_file())

    def test_combine_averages_and_refuses_different_targets(self):
        bundle = T.fit(self.rows, self.columns, "B", n_jobs=1, folds=2)
        pred = T.predict(bundle, self.latest)
        both = T.combine([pred, pred])
        np.testing.assert_allclose(both.growth, pred.growth)
        np.testing.assert_allclose(both.order, pred.order.rank(pct=True))
        np.testing.assert_allclose(both["product"], pred["product"].rank(pct=True))

    def test_product_direction_ranker_scores_every_pair_and_old_files_fall_back_to_stage1(self):
        bundle = T.fit(self.rows, self.columns, "C", k=5, n_jobs=1, folds=2)
        pred = T.predict(bundle, self.latest)
        x = T._x(self.latest, bundle["features"]).assign(stage1_pred=pred.growth.to_numpy())
        np.testing.assert_allclose(pred["product"], bundle["stage2_product"].predict(x[bundle["stage2_features"]]))
        old = {key: value for key, value in bundle.items() if key != "stage2_product"}  # 품목 방향 모델 이전 파일
        np.testing.assert_array_equal(T.predict(old, self.latest)["product"], pred.growth)
        with self.assertRaisesRegex(ValueError, "예측 대상"):
            T.combine([pred, pred.iloc[1:]])


if __name__ == "__main__":
    unittest.main()
