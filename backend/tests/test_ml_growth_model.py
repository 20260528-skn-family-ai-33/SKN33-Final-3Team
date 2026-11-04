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


# 2025-08 Holdout(aug_dev/rerun_note.txt v1)에서 검증한 설정. G.HUBER가 바뀌면 이 테스트가 실패해야 한다.
VERIFIED_HUBER = dict(objective="huber", alpha=1.0, num_leaves=63, n_estimators=1000, learning_rate=.02,
                      min_child_samples=300, colsample_bytree=.7, subsample=.8, subsample_freq=1, reg_lambda=5)
VERIFIED_DROP = {"gdp_growth", "region_id", "grp2_c_growth_12m", "news_all_3m", "rel_input_imp_growth_3m",
                 "imp_hs4_world_growth", "news_3m", "news_change", "grp4_c_growth_3m", "hs4_unit_trend",
                 "rel_input_imp_growth_12m", "fx_change_12m", "imp_hs4_from_c_growth", "grp4_c_growth_12m",
                 "country_growth", "grp4_world_growth_12m"}


class GrowthModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = prepare_scenarios({"fake": dataset()[[*needed_columns(), "exp_12m"]]}, 12)["fake"]
        cls.rows = frame[frame.y_growth.notna()]
        cls.latest = frame[frame.anchor == frame.anchor.max()]
        cls.bundle = G.fit_members(cls.rows, n_jobs=1)

    def test_settings_match_the_verified_holdout(self):
        self.assertEqual(G.HUBER, VERIFIED_HUBER)
        self.assertEqual(set(G.DROP_FEATURES), VERIFIED_DROP)
        self.assertEqual(len(G.DROP_FEATURES), 16)
        self.assertEqual(G.BOUNDARY_PCT, 0.0)
        self.assertEqual((G.UP, G.DOWN), ("UP", "DOWN"))  # 코드값 (화면 문구는 쓰는 쪽에서)

    def test_input_without_dropped_columns_gives_the_same_features(self):
        reduced = self.rows.drop(columns=[c for c in G.DROP_FEATURES if c in self.rows])
        for external in G.MEMBERS.values():
            self.assertEqual(G.feature_columns(reduced, external), G.feature_columns(self.rows, external))
        G.fit(reduced, "first_release", n_jobs=1)  # 열이 없어도 학습된다

    def test_features_drop_the_selected_16_and_v1_has_no_external(self):
        for name, external in G.MEMBERS.items():
            columns = G.feature_columns(self.rows, external)
            self.assertFalse(set(columns) & set(G.DROP_FEATURES), name)
            self.assertTrue(set(columns) <= set(T._available(self.rows, T._candidates(external))), name)
        self.assertFalse(set(G.feature_columns(self.rows, "none")) & set(MARKET + WDI))

    def test_members_use_huber_settings(self):
        self.assertEqual(set(self.bundle["members"]), set(G.MEMBERS))
        for member in self.bundle["members"].values():
            params = member["model"].steps[-1][1].get_params()
            for key, value in VERIFIED_HUBER.items():
                self.assertEqual(params[key], value, key)
            self.assertEqual(member["model"].steps[-1][1].n_features_in_, len(member["features"]))

    def test_predict_returns_pct_and_direction_for_every_latest_pair(self):
        pred = G.predict(self.bundle, self.latest)
        self.assertEqual(pred[T.KEYS].to_dict("list"), self.latest[T.KEYS].reset_index(drop=True).to_dict("list"))
        self.assertTrue(np.isfinite(pred.growth_pct).all())
        np.testing.assert_array_equal(pred.direction == G.UP, pred.growth_pct >= G.BOUNDARY_PCT)
        logs = [m["model"].predict(T._x(self.latest, m["features"])) for m in self.bundle["members"].values()]
        np.testing.assert_allclose(pred.growth_pct, np.expm1(np.mean(logs, axis=0)) * 100)  # v1·v3 로그 평균

    def test_v3_uses_external_features_and_bundle_records_training(self):
        ext = set(self.bundle["features"]["ext"])
        self.assertTrue(ext & set(MARKET + WDI))  # v3가 v1과 같아지지 않는다
        self.assertGreater(len(ext), len(self.bundle["features"]["base"]))
        self.assertEqual(self.bundle["train_rows"], len(self.rows))
        self.assertEqual(self.bundle["train_start"], str(self.rows.anchor.min().date()))
        self.assertEqual(self.bundle["train_end"], str(self.rows.anchor.max().date()))
        self.assertIn("lightgbm", self.bundle["runtime"])
        self.assertGreater(self.bundle["fit_seconds"], 0)

    def test_warns_and_records_when_external_features_are_missing(self):
        rows = self.rows.copy()
        rows[MARKET + WDI] = np.nan  # load_frame(external=True)를 거치지 않은 행
        with self.assertLogs(G.log, "WARNING"):
            bundle = G.fit_members(rows, n_jobs=1)
        self.assertEqual(bundle["features"]["ext"], bundle["features"]["base"])
        self.assertEqual(bundle["external_dropped"]["ext"], [c for c in MARKET + WDI if c not in G.DROP_FEATURES])

    def test_model_a_regressor_keeps_default_settings(self):
        columns = G.feature_columns(self.rows)
        model = T._regressor(T._x(self.rows, columns), self.rows.y_growth, columns, 0, 1)
        self.assertNotEqual(model.steps[-1][1].get_params()["objective"], "huber")
        self.assertEqual(model.steps[-1][1].get_params()["n_estimators"], 300)

    def test_boundary_counts_zero_as_increase(self):
        self.assertEqual(G.direction([0.0, -0.001, 3.2]).tolist(), [G.UP, G.DOWN, G.UP])

    def test_fit_rejects_missing_labels(self):
        rows = self.rows.copy()
        rows.loc[rows.index[0], "y_growth"] = np.nan
        with self.assertRaises(ValueError):
            G.fit(rows, n_jobs=1)


if __name__ == "__main__":
    unittest.main()
