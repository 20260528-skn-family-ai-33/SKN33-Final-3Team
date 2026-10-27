"""월별 LightGBM 전체 재학습(run)을 가상 데이터·SQLite로 확인한다 (실데이터 학습·MySQL 연결 없음).

실행: python -m unittest backend.tests.test_ml_candidate_models
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import joblib
import numpy as np
from sqlalchemy import select

from backend.app.db.session import make_engine, session_scope
from backend.app.ml import candidate_models as C
from backend.app.models.ml_model_version import MlModelVersion
from backend.tests.test_ml_experiments import dataset


FILES = [{"path": "data/customs/a.csv", "bytes": 1, "sha256": "a"},
         {"path": "data/customs_load_outputs/monthly/202602/state.json", "bytes": 1, "sha256": "s"}]
DB = {"fx": {"rows": 10, "sha256": "f"}, "country_mapping_sha256": "c"}


def loaded(end="2026-02-01", files=FILES, db=DB):
    """load_training_data 대역. 가상 원본은 2022-01~2026-02."""
    return dataset(), {}, {"raw_period_end": end, "files": files, **db}


class MonthlyRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.engine = make_engine("sqlite://")
        MlModelVersion.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def run_month(self, month="2026-02-01", files=FILES, db=DB):
        with patch.object(C.artifacts, "input_manifest", return_value=files), \
                patch.object(C.artifacts, "db_inputs", return_value=db):
            return C.run(month, self.engine, out_dir=self.out, cache_dir=None, n_jobs=1)

    def first_then(self, files=FILES, db=DB):
        """기본 입력으로 한 번 학습한 뒤 바뀐 입력으로 다시 실행한 모델 이름."""
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()):
            self.run_month()
        with patch.object(C.artifacts, "load_training_data", return_value=loaded(files=files, db=db)):
            return self.run_month(files=files, db=db)["model_name"]

    def records(self):
        with session_scope(self.engine) as session:
            return [(r.model_name, r.status, r.train_rows, r.training_mode, r.artifact_path)
                    for r in session.scalars(select(MlModelVersion))]

    def test_saves_dated_model_and_records_it(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()):
            result = self.run_month()
        labeled = int(dataset().y_growth.notna().sum())
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(joblib.load(self.out / "lightgbm_202602.joblib")["train_rows"], labeled)
        self.assertEqual(self.records(), [("lightgbm_202602", "READY", labeled, "FULL_RETRAIN", "lightgbm_202602.joblib")])

    def test_rerun_only_resends_record(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()):
            self.run_month()
        with patch.object(C.artifacts, "load_training_data", side_effect=AssertionError("같은 달은 재학습하지 않아야 함")):
            self.assertEqual(self.run_month()["status"], "SUCCESS")
        self.assertEqual(len(self.records()), 1)

    def test_corrected_input_trains_new_revision(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()):
            self.run_month()
        corrected = [{**FILES[0], "sha256": "b"}]  # 같은 달 원본 정정
        with patch.object(C.artifacts, "load_training_data", return_value=loaded(files=corrected)):
            self.assertEqual(self.run_month(files=corrected)["model_name"], "lightgbm_202602_r2")
        with patch.object(C.artifacts, "load_training_data", side_effect=AssertionError("같은 입력은 재학습하지 않아야 함")):
            self.assertEqual(self.run_month(files=corrected)["model_name"], "lightgbm_202602_r2")
        self.assertEqual(sorted(r[0] for r in self.records()), ["lightgbm_202602", "lightgbm_202602_r2"])
        self.assertEqual(C.read_json(self.out / "lightgbm_202602_r2.registry.json")["parent_version_id"], "lightgbm_202602")

    def test_db_fx_correction_trains_new_revision(self):
        self.assertEqual(self.first_then(db={**DB, "fx": {"rows": 10, "sha256": "g"}}), "lightgbm_202602_r2")

    def test_run_state_or_db_outage_is_not_a_correction(self):
        state_changed = [FILES[0], {**FILES[1], "sha256": "WAITING_FOR_ML 뒤 ML 결과 기록"}]
        self.assertEqual(self.first_then(files=state_changed), "lightgbm_202602")
        outage = {"fx": {"rows": 0, "sha256": "empty"}, "country_mapping_sha256": "no currency"}
        self.assertEqual(self.first_then(db=outage), "lightgbm_202602")
        self.assertEqual(len(self.records()), 1)

    def test_db_outage_picks_latest_revision_with_same_files(self):
        corrected = {**DB, "fx": {"rows": 10, "sha256": "g"}}
        self.assertEqual(self.first_then(db=corrected), "lightgbm_202602_r2")
        outage = {"fx": {"rows": 0, "sha256": "empty"}, "country_mapping_sha256": "no currency"}
        with patch.object(C.artifacts, "load_training_data", side_effect=AssertionError("재학습하지 않아야 함")):
            self.assertEqual(self.run_month(db=outage)["model_name"], "lightgbm_202602_r2")  # 정정 전 판이 아님
        self.assertEqual(len(self.records()), 2)

    def saved_version(self, name, registered=True):
        """이전 달 버전의 로컬 기록 JSON. registered=False는 학습 뒤 DB 기록에 실패한 버전."""
        record = {"version_id": name, "model_key": "model_a", "model_name": name,
                  "created_at": "2026-02-01T00:00:00+09:00", "status": "READY", "artifact_path": f"{name}.joblib",
                  "artifact_sha256": "0" * 64, "parent_version_id": None, "training_mode": "FULL_RETRAIN",
                  "train_start": None, "train_end": None, "train_rows": 0, "data_info": {}, "evaluation": {},
                  "skip_reason": None}
        (self.out / f"{name}.registry.json").write_text(json.dumps(record), "utf-8")
        if registered:
            C.register_records([record], self.engine)

    def parent_of_next_month(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded("2026-03-01")):
            self.run_month("2026-03-01")
        return C.read_json(self.out / "lightgbm_202603.registry.json")["parent_version_id"]

    def test_parent_is_latest_revision_by_number(self):
        for name in ("lightgbm_202602", "lightgbm_202602_r2", "lightgbm_202602_r10"):
            self.saved_version(name)
        self.assertEqual(self.parent_of_next_month(), "lightgbm_202602_r10")

    def test_parent_skips_version_missing_from_db(self):
        self.saved_version("lightgbm_202602")
        self.saved_version("lightgbm_202602_r2", registered=False)
        self.assertEqual(self.parent_of_next_month(), "lightgbm_202602")

    def test_db_reads_use_job_engine(self):
        db = Mock(return_value=DB)
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()) as load, \
                patch.object(C.artifacts, "input_manifest", return_value=FILES), patch.object(C.artifacts, "db_inputs", db):
            C.run("2026-02-01", self.engine, out_dir=self.out, cache_dir=None, n_jobs=1)
        db.assert_called_once_with(self.engine)
        self.assertIs(load.call_args.kwargs["engine"], self.engine)

    def test_refuses_month_missing_from_data(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded("2026-01-01")):
            with self.assertRaises(ValueError):
                self.run_month()
        self.assertFalse((self.out / "lightgbm_202602.joblib").exists())
        self.assertEqual(self.records(), [])


class SnapshotCandidateTest(unittest.TestCase):
    def test_damaged_file_is_retrained_not_skipped(self):
        out = Path(tempfile.mkdtemp())
        (out / "ridge_v1.joblib").write_bytes(b"truncated")  # 저장 중 끊긴 파일
        argv = ["candidate_models", "--models", "ridge", "--out-dir", str(out), "--n-jobs", "1"]
        with patch("sys.argv", argv), patch.object(C, "_snapshot_frame", return_value=dataset()[C.needed_columns()]):
            C.main()
        bundle = joblib.load(out / "ridge_v1.joblib")
        self.assertTrue(np.isfinite(bundle["pipeline"].predict(dataset()[bundle["features"]].head())).all())
        self.assertEqual(list(out.glob("*.tmp")), [])

    def test_file_from_other_environment_is_not_overwritten(self):
        out = Path(tempfile.mkdtemp())
        (out / "ridge_v1.joblib").write_bytes(b"made with other sklearn")
        argv = ["candidate_models", "--models", "ridge", "--out-dir", str(out), "--n-jobs", "1"]
        with patch("sys.argv", argv), patch("sys.stderr"), \
                patch.object(C.joblib, "load", side_effect=ModuleNotFoundError("No module named 'sklearn._x'")), \
                patch.object(C, "_snapshot_frame", side_effect=AssertionError("학습하지 않아야 함")), \
                self.assertRaises(SystemExit):
            C.main()
        self.assertEqual((out / "ridge_v1.joblib").read_bytes(), b"made with other sklearn")


if __name__ == "__main__":
    unittest.main()
