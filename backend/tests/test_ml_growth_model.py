"""증감 예상 모델(개선 B)의 피처 · 설정 · 예측 형식을 가상 데이터로 확인한다 (실데이터 학습 없음).

실행: python -m unittest backend.tests.test_ml_growth_model
"""
import unittest

import numpy as np

from backend.app.ml import growth_model as G
from backend.app.ml import two_stage as T
from backend.app.ml.candidate_models import needed_columns
from backend.app.ml.experiments import prepare_scenarios
from backend.app.ml.feature_recovery import MARKET, WDI
from backend.tests.test_ml_experiments import dataset


class GrowthModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = prepare_scenarios({"fake": dataset()[[*needed_columns(), "exp_12m"]]}, 12)["fake"]
        cls.rows = frame[frame.y_growth.notna()]
        cls.latest = frame[frame.anchor == frame.anchor.max()]
        cls.bundle = G.fit_members(cls.rows, n_jobs=1)

    def test_features_drop_the_selected_16_and_v1_has_no_external(self):
        self.assertEqual(len(set(G.DROP_FEATURES)), 16)
        for name, external in G.MEMBERS.items():
            columns = G.feature_columns(self.rows, external)
            self.assertFalse(set(columns) & set(G.DROP_FEATURES), name)
            self.assertTrue(set(columns) <= set(T._available(self.rows, T._candidates(external))), name)
        self.assertFalse(set(G.feature_columns(self.rows, "none")) & set(MARKET + WDI))

    def test_members_use_huber_settings(self):
        self.assertEqual(set(self.bundle["members"]), set(G.MEMBERS))
        for member in self.bundle["members"].values():
            params = member["model"].steps[-1][1].get_params()
            for key, value in G.HUBER.items():
                self.assertEqual(params[key], value, key)
            self.assertEqual(member["model"].steps[-1][1].n_features_in_, len(member["features"]))

    def test_predict_returns_pct_and_direction_for_every_latest_pair(self):
        pred = G.predict(self.bundle, self.latest)
        self.assertEqual(pred[T.KEYS].to_dict("list"), self.latest[T.KEYS].reset_index(drop=True).to_dict("list"))
        self.assertTrue(np.isfinite(pred.growth_pct).all())
        np.testing.assert_array_equal(pred.direction == G.UP, pred.growth_pct >= G.BOUNDARY_PCT)
        logs = [m["model"].predict(T._x(self.latest, m["features"])) for m in self.bundle["members"].values()]
        np.testing.assert_allclose(pred.growth_pct, np.expm1(np.mean(logs, axis=0)) * 100)  # v1·v3 로그 평균

    def test_boundary_counts_zero_as_increase(self):
        self.assertEqual(G.direction([0.0, -0.001, 3.2]).tolist(), [G.UP, G.DOWN, G.UP])

    def test_fit_rejects_missing_labels(self):
        rows = self.rows.copy()
        rows.loc[rows.index[0], "y_growth"] = np.nan
        with self.assertRaises(ValueError):
            G.fit(rows, n_jobs=1)


if __name__ == "__main__":
    unittest.main()
