"""시간 교차검증의 누수 방지·이력 부족 처리와 실제 PDF 생성 검증."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from backend.app.ml import features as F, validation as V


def small_dataset(months=22):
    anchors = pd.date_range("2023-12-01", periods=months, freq="MS")
    rows = [(anchor, "VN", f"{item:06d}", item / 10, item / 20)
            for anchor in anchors for item in range(1, 13)]
    ds = pd.DataFrame(rows, columns=["anchor", "country_code", "hs6", "y_growth", "growth_12m"])
    for name in F.FEATURES:
        if name not in ds:
            ds[name] = 0.0
    return ds


class TestTemporalReport(unittest.TestCase):
    def test_folds_purge_future_labels_and_keep_full_anchor(self):
        ds = small_dataset()
        # 누락 월이 있어도 행 위치가 아닌 실제 달력의 12개월을 분리한다.
        ds = ds[~ds.anchor.isin([pd.Timestamp("2024-02-01"), pd.Timestamp("2024-08-01")])]
        folds = V.temporal_folds(ds, min_train_rows=1)
        self.assertEqual(len(folds), 3)
        for fold in folds:
            train = ds[ds.anchor <= fold["train_cutoff"]]
            test = ds[ds.anchor == fold["validation_anchor"]]
            self.assertEqual(len(test), 12)
            self.assertGreaterEqual(train.anchor.nunique(), 3)
            self.assertLessEqual(train.anchor.max() + pd.DateOffset(months=12), test.anchor.min())
            self.assertFalse(set(train.anchor) & set(test.anchor))

    def test_insufficient_history_never_switches_to_random_kfold(self):
        ds = small_dataset(13)
        self.assertEqual(V.temporal_folds(ds, min_train_rows=1), [])
        result = V.temporal_cv(ds, {"features_used": ["growth_12m"]}, min_train_rows=1)
        self.assertEqual(result["status"], "insufficient_history")
        self.assertEqual(result["folds"], [])
        self.assertEqual(V.temporal_cv(small_dataset(5), {}, min_train_rows=1)["status"], "insufficient_history")

    def test_invalid_fold_count_fails_explicitly(self):
        with self.assertRaises(ValueError):
            V.temporal_folds(small_dataset(), n_splits=1)

    @patch.object(V.M, "fit_a")
    def test_small_anchor_population_triggers_refit_for_each_fold(self, fit):
        model = MagicMock()
        model.__getitem__.return_value = SimpleNamespace(keep_=["growth_12m"])
        model.predict.side_effect = lambda x: x.growth_12m.to_numpy()
        fit.return_value = model
        result = V.temporal_cv(small_dataset(), {"features_used": ["growth_12m"]}, min_train_rows=1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(fit.call_count, 3)
        self.assertEqual([len(c.args[0]) for c in fit.call_args_list], [36, 72, 120])
        self.assertAlmostEqual(result["aggregate"]["model_a"]["ndcg_at_5"]["mean"], 1.0)
        self.assertEqual(result["aggregate"]["model_a_product"]["ndcg_at_5"]["valid_folds"], 0)
        self.assertTrue(result["overlapping_validation_targets"])

    @patch.object(V.M, "fit_a")
    def test_auto_skips_sufficient_population(self, fit):
        result = V.temporal_cv(small_dataset(30), {"features_used": []}, min_train_rows=1)
        self.assertEqual(result["status"], "not_needed")
        fit.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("reportlab") and importlib.util.find_spec("pypdf"),
                     "PDF 검사 의존성: backend/requirements-ml-report.txt")
class TestPdfReport(unittest.TestCase):
    def test_pdf_contains_schema_methods_actual_metrics_and_failure_status(self):
        from backend.app.ml.reporting import generate_report
        from pypdf import PdfReader
        context = {"metadata": {"promotion_metric": "country_ndcg_at_5", "features_used": []},
                   "evaluation": {"model_a": {"ndcg_at_5": .7654}}, "run_status": "failed"}
        validation = {"generated_at": "2026-10-22T15:00:00+09:00", "unit_tests": {"passed": False},
                      "checks": [{"name": "검증 표본", "passed": False, "detail": "고유한 실패 원인 987"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = generate_report(context, validation, Path(directory) / "report.pdf")
            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertGreaterEqual(len(reader.pages), 8)
            for expected in ("전체 데이터 처리 흐름", "Input:", "Output", "K-fold", "실행 명령",
                             "0.7654", "실패·미완료", "고유한 실패 원인 987"):
                self.assertIn(expected, text)
            self.assertFalse(path.with_suffix(".part.pdf").exists())


if __name__ == "__main__":
    unittest.main()
