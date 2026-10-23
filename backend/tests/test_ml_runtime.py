"""환경 불일치와 PDF 구조 검사/전체 검증 구분을 실제 학습 없이 검증한다."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from backend.app.ml import runtime as R, validation as V, report_workflow as W


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[2] / "tmp"
        self.parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="ml-runtime-test-", dir=self.parent)
        self.out = Path(self.temp.name).resolve()
        if not self.out.is_relative_to(self.parent.resolve()):
            raise ValueError("예상하지 않은 테스트 임시 경로")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.out / name).write_text(json.dumps(value), encoding="utf-8")

    def test_version_mismatch_is_shown_before_attempting_to_read_pickle(self):
        self.write("metadata.json", {"runtime": {"python": "3.12.14", "numpy": "2.5.4"}})
        with patch.object(R, "runtime_versions", return_value={"python": "3.11.9", "numpy": "1.25.2"}), \
             patch.object(V.pd, "read_pickle") as read:
            with self.assertRaisesRegex(R.ArtifactEnvironmentError, "numpy") as caught:
                V.read_feature_cache("features.pkl", self.out)
        read.assert_not_called()
        self.assertIn("저장 환경 2.5.4 / 현재 1.25.2", str(caught.exception))

    def test_python_patch_difference_is_accepted_but_dependency_versions_must_match(self):
        self.write("metadata.json", {"runtime": {"python": "3.12.14", "numpy": "2.5.4"}})
        result = R.ensure_artifact_runtime(self.out, current={"python": "3.12.10", "numpy": "2.5.4"})
        self.assertTrue(result["recorded"])

    def test_original_training_record_is_used_only_for_matching_model_run(self):
        meta = {"trained_at": "2026-10-22T12:30:50", "evaluation": {"model_a": {"ndcg_at_5": .7}}}
        run = {"status": "accepted", "started_at": "2026-10-22T12:28:01+09:00", "elapsed_seconds": 172.5,
               "evaluation": meta["evaluation"], "runtime": {"numpy": "2.5.4"}}
        self.write("metadata.json", meta)
        self.write("training_run.json", run)
        self.assertEqual(R.expected_runtime(self.out), run["runtime"])
        meta["trained_at"] = "2026-10-23T12:30:50"
        self.write("metadata.json", meta)
        self.assertEqual(R.expected_runtime(self.out), {})

    def test_new_version_can_read_runtime_from_model_specific_record(self):
        self.write("metadata.json", {"model_versions": [{"data_info": {"runtime": {"numpy": "2.5.4"}}}]})
        self.assertEqual(R.expected_runtime(self.out), {"numpy": "2.5.4"})

    def test_missing_numpy_module_reports_original_cause_without_import_aliases(self):
        with patch.object(V.pd, "read_pickle", side_effect=ModuleNotFoundError("No module named 'numpy._core'")):
            with self.assertRaisesRegex(R.ArtifactEnvironmentError, "numpy._core") as caught:
                V.read_feature_cache("features.pkl", self.out)
        self.assertIsInstance(caught.exception.__cause__, ModuleNotFoundError)

    def test_pdf_structure_success_cannot_hide_missing_artifact_checks(self):
        payload = {"unit_tests": {"passed": True}, "checks": [], "pdf_check": {"passed": True},
                   "errors": ["ModuleNotFoundError: No module named 'numpy._core'"]}
        with self.assertRaisesRegex(RuntimeError, "numpy._core") as caught:
            V.require_validation_success(payload)
        self.assertIn("산출물 검사가 실행되지 않았습니다", str(caught.exception))

    def test_success_requires_artifact_unit_test_and_pdf_checks(self):
        V.require_validation_success({"unit_tests": {"passed": True}, "errors": [],
            "checks": [{"name": "검사", "passed": True}], "pdf_check": {"passed": True}})

    def test_workflow_preflight_does_not_launch_tests_or_training_in_wrong_environment(self):
        with patch.object(R, "ensure_artifact_runtime", side_effect=R.ArtifactEnvironmentError("numpy 버전 불일치")), \
             patch.object(W, "execute_notebook") as execute, \
             patch.object(W, "train_for_report") as train, \
             patch.object(W, "failure_report") as report, redirect_stderr(io.StringIO()):
            result = W.main(["--data-dir", str(self.out), "--output-dir", str(self.out)])
        self.assertEqual(result, 1)
        execute.assert_not_called()
        train.assert_not_called()
        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
