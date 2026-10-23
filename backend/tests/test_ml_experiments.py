"""모델 A 실험 도구를 가상 데이터로 끝까지 돌려 본다 (실데이터 학습 없음).

실행: python -m unittest backend.tests.test_ml_experiments
"""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.app.ml import experiments as E
from backend.app.ml import features as F
from backend.app.ml import feature_recovery as R
from backend.app.ml.experiment_report import metric_matrix
from backend.tests.test_ml_market import fake_data

DS = None


def dataset():
    global DS
    if DS is None:
        DS = F.build_dataset(fake_data())  # 2022-01~2026-02, 정답 있는 기준월 2023-12~2025-02
    return DS


def config(out, **kw):
    base = dict(validation_anchors=("2025-01-01",), holdout_anchor="2025-02-01", ks=(5,), min_candidates=5,
                n_jobs=1, make_pdf=False, output_root=str(out))
    return E.ExperimentConfig(**{**base, **kw})


class ExperimentRunTest(unittest.TestCase):
    def test_dev_run_writes_all_reports(self):
        out = Path(tempfile.mkdtemp())
        specs = [E.ModelSpec("hgb_small", "hgb", {"max_iter": 20}), E.ModelSpec("ridge", "ridge")]
        run = E.run_experiments({"original": dataset()}, specs, {"all": E.FEATURE_SETS["all"]}, config(out))
        results = json.loads((run / "results.json").read_text("utf-8"))
        self.assertEqual({r["model"] for r in results}, {*E.BASELINES, "hgb_small", "ridge"})
        self.assertTrue(all(r["status"] == "ok" for r in results), [r.get("error") for r in results])
        # 학습은 평가 기준월보다 12개월 이상 앞선 기준월만
        self.assertTrue(all(r["train_end"] <= "2024-01-01" for r in results))
        for name in ("report.html", "report.md", "leaderboard.csv", "metric_matrix.csv", "fold_metrics.csv"):
            self.assertTrue((run / name).is_file(), name)
        matrix = pd.read_csv(run / "metric_matrix.csv")
        self.assertIn("NDCG@5", set(matrix["지표"]))
        self.assertIn("F1", set(matrix["지표"]))
        self.assertIn("지표별 비교", (run / "report.html").read_text("utf-8"))

    def test_sensitivity_scenario_is_refused_in_holdout(self):
        frame = dataset().copy()
        frame.attrs["recovery"] = [{"kind": "external", "policy": "...; sensitivity_current_values_at_estimated_release"}]
        holdout = config(Path(tempfile.mkdtemp()), phase="holdout", validation_anchors=("2025-02-01",))
        with self.assertRaisesRegex(ValueError, "민감도"):
            E.run_experiments({"sensitivity": frame}, [E.ModelSpec("ridge", "ridge")], {"all": E.FEATURE_SETS["all"]}, holdout)


class ReportTableTest(unittest.TestCase):
    def test_metric_matrix_picks_best_in_right_direction(self):
        board = pd.DataFrame({"scenario": ["s", "s"], "feature_set": ["all", "all"], "model": ["a", "b"],
                              "complete": [True, True], "comparable": [True, True],
                              "mae_mean": [1.0, 2.0], "country_ndcg_at_5_mean": [0.6, 0.7]})
        m = metric_matrix(board, (5,)).set_index("지표")
        self.assertEqual(m.loc["MAE", "최고 후보"], "s/all/a")  # 낮을수록 좋음
        self.assertEqual(m.loc["NDCG@5", "최고 후보"], "s/all/b")  # 높을수록 좋음

    def test_incomplete_candidate_is_never_best(self):
        board = pd.DataFrame({"scenario": ["s", "s"], "feature_set": ["all", "all"], "model": ["a", "b"],
                              "complete": [True, False], "comparable": [True, False], "country_ndcg_at_5_mean": [0.6, 0.9]})
        self.assertEqual(metric_matrix(board, (5,)).set_index("지표").loc["NDCG@5", "최고 후보"], "s/all/a")


class AvailabilityOverrideTest(unittest.TestCase):
    def write(self, rows):
        path = Path(tempfile.mkdtemp()) / "availability.csv"
        pd.DataFrame(rows, columns=["dataset", "source", "year", "available_from", "evidence"]).to_csv(path, index=False)
        return path

    def test_requires_evidence_and_year_end(self):
        with self.assertRaises(ValueError):
            R._availability_override(self.write([("wdi", "WDI", 2023, "2024-09-30", "")]))
        with self.assertRaises(ValueError):
            R._availability_override(self.write([("wdi", "WDI", 2023, "2023-06-30", "추정")]))
        ok = R._availability_override(self.write([("wdi", "WDI", 2023, "2024-09-30", "추정")]))
        self.assertEqual(len(ok), 1)

    def test_shipped_estimate_file_is_valid(self):
        path = Path(E.ROOT) / "backend/app/ml/reference/experiments/availability_release_estimate.csv"
        frame = R._availability_override(path)
        self.assertEqual(set(frame.dataset), {"market", "wdi"})


if __name__ == "__main__":
    unittest.main()
