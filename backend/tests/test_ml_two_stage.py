"""2단계 구성(회귀 → 순위 학습)의 후보 선택 · 순서 · 앙상블을 가상 데이터로 확인한다 (실데이터 학습 없음).

실행: python -m unittest backend.tests.test_ml_two_stage
"""
import unittest

import numpy as np

from backend.app.ml import two_stage as T
from backend.app.ml.candidate_models import needed_columns
from backend.app.ml.experiments import FEATURE_SETS, prepare_scenarios
from backend.app.ml.feature_recovery import coverage
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
