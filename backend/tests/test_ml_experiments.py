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


class ScenarioOrderTest(unittest.TestCase):
    def test_build_dataset_is_already_key_sorted(self):
        # 정렬돼 있어야 prepare_scenarios가 큰 표를 복사하지 않는다
        keys = dataset()[["anchor", "country_code", "hs6"]].reset_index(drop=True)
        pd.testing.assert_frame_equal(keys, keys.sort_values(list(keys)).reset_index(drop=True))

    def test_row_order_does_not_change_prepared_frame(self):
        frame = dataset()
        shuffled = frame.sample(frac=1, random_state=1)
        before = shuffled.index.copy()
        prepared = E.prepare_scenarios({"a": frame, "b": shuffled}, 12)
        pd.testing.assert_frame_equal(prepared["a"], prepared["b"])
        self.assertTrue(prepared["b"].index.equals(pd.RangeIndex(len(frame))))
        self.assertTrue(shuffled.index.equals(before))  # 입력 표는 그대로


class NdcgTest(unittest.TestCase):
    def test_fast_ndcg_matches_public_function(self):
        import numpy as np
        from sklearn.metrics import ndcg_score
        from backend.app.ml.experiment_metrics import _ndcg
        rng = np.random.default_rng(0)
        for _ in range(20):
            relevance = rng.random((1, 15))
            score = rng.integers(0, 4, (1, 15)).astype(float)  # 동점 포함
            for k in (5, 10):
                self.assertAlmostEqual(_ndcg(relevance, score, k), ndcg_score(relevance, score, k=k))


def groupby_details(rows, score, ks, min_candidates):
    """배열 방식으로 바꾸기 전 groupby 구현. 새 구현과 값이 같은지 비교하는 기준."""
    import numpy as np
    from backend.app.ml.experiment_metrics import finite
    from sklearn.metrics import ndcg_score
    details = []
    for direction, group, item in [("country", "country_code", "hs6"), ("product", "hs6", "country_code")]:
        for value, g in rows.assign(score=score).groupby(group, sort=True):
            g = g.sort_values(item, kind="stable")
            actual = g.sort_values("y_growth", ascending=False, kind="stable")
            predicted = g.sort_values("score", ascending=False, kind="stable")
            relevance = g.y_growth.rank(pct=True).to_numpy()[None, :]
            correlation = finite(g.y_growth.corr(g.score, method="spearman"))
            for k in ks:
                if len(g) < max(min_candidates, k):
                    continue
                picked, top_k = set(predicted.head(k)[item]), set(actual.head(k)[item])
                top20 = set(actual.head(max(1, int(np.ceil(len(g) * .2))))[item])
                hits = len(picked & top20)
                p, r = hits / k, hits / len(top20)
                details.append({"direction": direction, "group": str(value), "k": k, "candidates": len(g),
                                "precision_at_k": len(picked & top_k) / k, "hit_at_k": float(bool(picked & top_k)),
                                "ndcg_at_k": float(ndcg_score(relevance, g.score.to_numpy()[None, :], k=k)),
                                "top20_precision_at_k": p, "top20_recall_at_k": r,
                                "top20_f1_at_k": 2*p*r/(p+r) if p+r else 0.0,
                                "grew_share_at_k": float((predicted.head(k).y_growth > 0).mean()),
                                "mean_actual_growth_at_k": float(predicted.head(k).y_growth.mean()),
                                "spearman": correlation})
    return pd.DataFrame(details)


class RankingMetricTest(unittest.TestCase):
    def test_array_version_matches_groupby_version(self):
        import numpy as np
        from backend.app.ml.experiment_metrics import evaluate_predictions
        rng = np.random.default_rng(0)
        countries = [f"C{i}" for i in range(6)]
        products = [f"{i:06d}" for i in rng.choice(999999, 40, replace=False)]
        rows = pd.DataFrame([(c, p) for c in countries for p in products], columns=["country_code", "hs6"])
        rows = rows.sample(frac=.7, random_state=0)  # 그룹 크기를 다르게 (일부는 K보다 작음)
        rows["y_growth"] = rng.integers(-3, 4, len(rows)) / 2  # 정답 동점
        score = rng.integers(0, 5, len(rows)).astype(float)  # 점수 동점
        score[rows.country_code.eq("C0").to_numpy()] = 1.0  # 점수 상수 그룹 → Spearman 미평가
        _, details = evaluate_predictions(rows, score, ks=(5, 10), min_candidates=5)
        pd.testing.assert_frame_equal(details, groupby_details(rows, score, (5, 10), 5), check_dtype=False)


class BestCandidateTest(unittest.TestCase):
    def test_skips_baselines_and_sensitivity(self):
        run = Path(tempfile.mkdtemp())
        manifest = {"status": "completed",
                    "config": {"validation_anchors": ["2025-01-01"], "seeds": [0], "primary_metric": "country_ndcg_at_5"},
                    "scenarios": {"base": {"provenance": {}}, "recovered": {"provenance": {"recovery": [{"policy": "x"}]}},
                                  "sens": {"provenance": {"recovery": [{"policy": "x; sensitivity_current_values"}]}}}}
        results = [{"scenario": s, "feature_set": "all", "model": m, "kind": m, "status": "ok",
                    "metrics": {"country_ndcg_at_5": v}}
                   for s, m, v in [("base", "baseline_growth", .9), ("sens", "hgb", .8), ("recovered", "hgb", .6),
                                   ("base", "ridge", .5)]]
        (run / "manifest.json").write_text(json.dumps(manifest), "utf-8")
        (run / "results.json").write_text(json.dumps(results), "utf-8")
        best = E.best_candidate(run)
        self.assertEqual((best.scenario, best.model), ("recovered", "hgb"))

    def test_cli_refuses_sampled_holdout(self):
        from unittest import mock
        with mock.patch("sys.argv", ["experiments", "--holdout", "--max-train-rows", "100"]), \
                mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            E.main()


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
