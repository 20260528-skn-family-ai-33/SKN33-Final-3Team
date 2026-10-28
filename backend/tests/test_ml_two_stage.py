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
        with self.assertRaisesRegex(SystemExit, "찾지 못했"):
            T.query(argparse.Namespace(model=[path], country="없는나라", hs6=None, top=5), error)

    def out_file(self, bundle):
        import tempfile
        from pathlib import Path
        import joblib
        path = Path(tempfile.mkdtemp()) / "model.joblib"
        joblib.dump(bundle, path)
        return path

    def test_cross_prediction_holds_out_whole_anchors(self):
        from unittest.mock import patch
        seen, real = [], T._regressor

        def record(x, y, columns, seed, n_jobs):
            seen.append(set(self.rows.anchor.loc[x.index]))
            return real(x, y, columns, seed, n_jobs)
        with patch.object(T, "_regressor", side_effect=record):
            T.fit(self.rows, self.columns, "C", k=5, n_jobs=1, folds=2)
        every = set(self.rows.anchor)
        folds = seen[1:]  # 첫 호출은 전체 행 1단계
        held_out = [every - fit_anchors for fit_anchors in folds]
        self.assertTrue(all(held_out))  # 교차 모델마다 기준월 하나 이상을 통째로 뺀다
        self.assertEqual(set().union(*held_out), every)  # 모든 기준월이 자기를 빼고 학습한 모델로 예측된다

    def test_combine_averages_and_refuses_different_targets(self):
        bundle = T.fit(self.rows, self.columns, "B", n_jobs=1, folds=2)
        pred = T.predict(bundle, self.latest)
        both = T.combine([pred, pred])
        np.testing.assert_allclose(both.growth, pred.growth)
        np.testing.assert_allclose(both.order, pred.order.rank(pct=True))
        with self.assertRaisesRegex(ValueError, "예측 대상"):
            T.combine([pred, pred.iloc[1:]])


if __name__ == "__main__":
    unittest.main()
