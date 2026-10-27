"""월별 LightGBM 전체 재학습(run)을 가상 데이터·SQLite로 확인한다 (실데이터 학습·MySQL 연결 없음).

실행: python -m unittest backend.tests.test_ml_candidate_models
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
from sqlalchemy import select

from backend.app.db.session import make_engine, session_scope
from backend.app.ml import candidate_models as C
from backend.app.models.ml_model_version import MlModelVersion
from backend.tests.test_ml_experiments import dataset


def loaded(end="2026-02-01"):
    """load_training_data 대역. 가상 원본은 2022-01~2026-02."""
    return dataset(), {}, {"raw_period_end": end}


class MonthlyRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.engine = make_engine("sqlite://")
        MlModelVersion.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def run_month(self, month="2026-02-01"):
        return C.run(month, self.engine, out_dir=self.out, cache_dir=None, n_jobs=1)

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

    def test_refuses_month_missing_from_data(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded("2026-01-01")):
            with self.assertRaises(ValueError):
                self.run_month()
        self.assertFalse((self.out / "lightgbm_202602.joblib").exists())
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
