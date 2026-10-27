"""시간 교차검증의 누수 방지·이력 부족 처리와 실제 PDF 생성 검증."""
import importlib.util
import re
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
    def test_pdf_explains_unevaluated_b_and_late_external_data(self):
        from backend.app.ml.reporting import generate_report
        from pypdf import PdfReader
        context = {"metadata": {"features_used": [], "predict_anchor": "2026-09-01"},
                   "evaluation": {"model_b": "라벨 부족 (외부 시장 데이터가 없는 연도)"},
                   "snapshot": {"available_from": "2026-10-22"}, "predictions_summary": {"rows": 10}}
        with tempfile.TemporaryDirectory() as directory:
            path = generate_report(context, {}, Path(directory) / "report.pdf")
            text = re.sub(r"\s+", "", "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))
        for statement in ("모델 B는 평가하지 못했다: 라벨 부족 (외부 시장 데이터가 없는 연도)",
                          "외부 자료 사용 가능일 2026-10-22이 마지막 기준월 2026-09보다 늦어"):
            self.assertIn(re.sub(r"\s+", "", statement), text)

    def test_pdf_describes_actual_feature_usage_and_market_type_results(self):
        from backend.app.ml.reporting import generate_report
        from pypdf import PdfReader
        cases = [
            ({"features_used": ["fx_change_12m", "log_gdp", "mkt_growth", "kor_share"],
              "model_b": "classifier"}, {"rows": 10, "types_known": 10},
             ["환율: 모델 A 사용 피처 1/2개", "WDI: 모델 A 사용 피처 1/4개",
              "시장: 모델 A 사용 피처 2/5개", "시장 유형이 있는 예측은 10행이다.",
              "시장 유형 산출 방식: classifier", "모델 B 0.7500"]),
            ({"features_used": [], "model_b": "rule (기준선 미달)"}, {"rows": 10, "types_known": 3},
             ["환율: 모델 A 사용 피처 0/2개", "시장 유형이 있는 예측은 3행이다.",
              "시장 유형 산출 방식: rule (기준선 미달)"]),
            ({"features_used": []}, {"rows": 10, "types_known": 0},
             ["시장 유형이 있는 예측은 0행이다.", "시장 유형 산출 방식: 미확인"]),
            ({}, {}, ["환율: 모델 A 사용 여부 미확인", "시장 유형 예측 건수는 미확인이다."]),
        ]
        for metadata, predictions, expected in cases:
            with self.subTest(metadata=metadata, predictions=predictions), tempfile.TemporaryDirectory() as directory:
                context = {"metadata": metadata, "predictions_summary": predictions,
                           "evaluation": {"model_b": {"macro_f1": .75}},
                           # 원본에 값이 있어도 모델이 해당 피처를 선택하지 않을 수 있다.
                           "feature_summary": {"rows": 10, "feature_nonmissing": {"fx_change_12m": 10}}}
                path = generate_report(context, {}, Path(directory) / "report.pdf")
                text = re.sub(r"\s+", "", "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))
                for statement in expected:
                    self.assertIn(re.sub(r"\s+", "", statement), text)
                for stale in ("환율도 값이 없어", "현재 값 없어 결측", "시장·WDI가 모두 결측",
                              "B 점수와 유형을 미판정", "현재 모델 B는 과거 공개 시장 라벨을 확보할 때까지"):
                    self.assertNotIn(re.sub(r"\s+", "", stale), text)

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
