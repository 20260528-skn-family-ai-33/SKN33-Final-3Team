"""실제 모델 학습·MySQL 연결 없이 A/B 버전 파일과 DB 이력을 검증한다."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import func, select

from backend.app.db.session import make_engine, session_scope
from backend.app.models.ml_model_version import MlModelVersion
from backend.app.ml import artifacts as A, market_model as M, model_registry as R


class ModelDouble:
    """학습된 객체의 대역. 실제 학습 처리는 없다."""
    def __init__(self, value=.5):
        self.value = value

    def __getitem__(self, index):
        return SimpleNamespace(keep_=["growth_12m"])

    def predict(self, rows):
        return np.full(len(rows), self.value)


def rows():
    data = pd.DataFrame({"anchor": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-02-01"]),
                         "country_code": ["VN", "US", "VN"], "hs6": ["010110", "010110", "020110"],
                         "y_growth": [.1, .2, .3]})
    for col in M.F.FEATURES:
        data[col] = 0.
    return data


def metadata():
    return {"trained_at": "2026-10-22T14:00:00+09:00", "predict_anchor": "2024-02-01",
            "features_used": ["growth_12m"], "evaluation": {
                "model_a": {"ndcg_at_5": .8}, "baseline_a": {"ndcg_at_5": .7},
                "model_b": {"macro_f1": .7}, "baseline_b": {"macro_f1": .6}}}


class RegistryTest(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / "tmp"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="model-registry-test-", dir=parent)
        self.out = Path(self.temp.name)
        if not self.out.resolve().is_relative_to(parent.resolve()):
            raise ValueError("예상하지 않은 테스트 임시 경로")
        self.engine = make_engine("sqlite://")
        MlModelVersion.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def save(self, b=True, dataset=None, info=None, **kwargs):
        data = rows() if dataset is None else dataset
        pred = data[data.anchor == data.anchor.max()][["country_code", "hs6"]].assign(score=.5)
        return A.save_version({"model_a": ModelDouble(), "model_b": ModelDouble(1) if b else None}, pred, metadata(),
            {"model_a": data, "model_b": data.iloc[:2] if b else data.iloc[:0]}, self.out,
            data_info=info, eligible_b_rows=2, registry_engine=self.engine, **kwargs)

    def count(self):
        with session_scope(self.engine) as session:
            return session.scalar(select(func.count()).select_from(MlModelVersion))

    def test_both_models_have_timestamp_names_distinct_training_sets_and_db_rows(self):
        meta = self.save()
        self.assertEqual(self.count(), 2)
        self.assertRegex(meta["version_id"], r"^\d{8}_\d{6}_\d{6}_[0-9a-f]{8}$")
        for record, expected_rows in zip(meta["model_versions"], (3, 2)):
            self.assertEqual(record["status"], "READY")
            self.assertEqual(record["train_rows"], expected_rows)
            self.assertIn(record["model_key"] + "_" + meta["version_id"], record["artifact_path"])
            self.assertIsInstance(joblib.load(self.out / record["artifact_path"]), ModelDouble)
            with session_scope(self.engine) as session:
                db = session.get(MlModelVersion, (meta["version_id"], record["model_key"]))
                self.assertEqual(db.created_at, datetime.fromisoformat(record["created_at"]).replace(tzinfo=None))
                self.assertEqual(db.data_info["dataset"]["rows"], expected_rows)
        state = R.read_json(self.out / meta["registry_status_file"])
        self.assertEqual(state["status"], "SYNCED")
        self.assertIsInstance(M.load_models(self.out)["model_a"], ModelDouble)

    def test_untrained_b_has_skip_record_and_no_fake_joblib(self):
        meta = self.save(b=False)
        b = meta["model_versions"][1]
        self.assertEqual((b["status"], b["train_rows"], b["artifact_path"]), ("SKIPPED", 0, None))
        self.assertEqual(b["data_info"]["eligible_rows"], 2)
        self.assertEqual(list((self.out / meta["version_directory"]).glob("model_b_*.joblib")), [])
        self.assertIsNone(M.load_models(self.out)["model_b"])
        self.assertEqual(self.count(), 2)

    def test_timestamp_collision_preserves_each_version_and_can_load_old_predictions(self):
        fixed = datetime(2026, 10, 22, 14, 15, 16, 123456, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch.object(A, "datetime") as clock:
            clock.now.return_value = fixed
            first = self.save()
            path = self.out / first["model_versions"][0]["artifact_path"]
            digest = R.sha256(path)
            second = self.save(dataset=rows().assign(hs6="030110"))
        self.assertNotEqual(first["version_id"], second["version_id"])
        self.assertEqual(R.sha256(path), digest)
        self.assertEqual(self.count(), 4)
        self.assertEqual(second["model_versions"][0]["parent_version_id"], first["version_id"])
        self.assertEqual(M.load_predictions(self.out, first["version_id"]).hs6.iloc[0], "020110")
        self.assertEqual(M.load_predictions(self.out).hs6.iloc[0], "030110")

    def test_delta_distinguishes_new_months_changed_values_and_changed_source_files(self):
        info = {"files": [{"path": "a.csv", "sha256": "old"}]}
        self.save(info=info)
        data = pd.concat([rows(), rows().iloc[[2]].assign(anchor=pd.Timestamp("2024-03-01"))], ignore_index=True)
        data.loc[0, "growth_12m"] = 99.
        meta = self.save(dataset=data, info={"files": [{"path": "a.csv", "sha256": "new"},
                                                    {"path": "b.csv", "sha256": "new"}]})
        change = meta["model_versions"][0]["data_info"]["data_changes"]
        self.assertEqual(change["new_anchor_months"], ["2024-03-01"])
        self.assertEqual(change["new_anchor_rows"], 1)
        self.assertEqual(change["changed_anchor_months"], ["2024-01-01"])
        self.assertEqual(change["changed_files"], ["a.csv"])
        self.assertEqual(change["added_files"], ["b.csv"])
        self.assertEqual(meta["model_versions"][0]["training_mode"], "FULL_RETRAIN")

    def test_row_order_does_not_change_snapshot_and_first_version_has_unknown_delta(self):
        self.assertEqual(A.dataset_info(rows()), A.dataset_info(rows().iloc[::-1]))
        record = self.save()["model_versions"][0]
        self.assertFalse(record["data_info"]["data_changes"]["comparison_available"])
        self.assertIsNone(record["data_info"]["data_changes"]["new_anchor_rows"])

    def test_incremental_modes_and_checkpoint_are_preserved_in_db_and_version(self):
        meta = self.save(model_details={"model_a": {"training_mode": "PARTIAL_FIT", "updated_rows": 1},
                                      "model_b": {"training_mode": "UNCHANGED", "updated_rows": 0}},
                         extra_artifacts={"incremental_state.joblib": {"seen": ["2024-01-01"]}})
        checkpoint = self.out / meta["version_directory"] / "incremental_state.joblib"
        self.assertEqual(meta["extra_artifacts_sha256"][checkpoint.name], R.sha256(checkpoint))
        self.assertEqual(joblib.load(checkpoint)["seen"], ["2024-01-01"])
        with session_scope(self.engine) as session:
            for key, mode, count in (("model_a", "PARTIAL_FIT", 1), ("model_b", "UNCHANGED", 0)):
                row = session.get(MlModelVersion, (meta["version_id"], key))
                self.assertEqual(row.training_mode, mode)
                self.assertEqual(row.data_info["updated_rows"], count)

    def test_db_outage_queues_both_models_and_retry_is_idempotent_without_training(self):
        with patch.object(R, "register_records", side_effect=ConnectionError("private connection")):
            meta = self.save()
        directory = self.out / meta["version_directory"]
        state = R.read_json(directory / "registry_sync.json")
        self.assertEqual(state, {"status": "PENDING", "error_type": "ConnectionError"})
        self.assertEqual(self.count(), 0)
        self.assertEqual(R.sync_version(directory, self.engine)["status"], "SYNCED")
        self.assertEqual(R.sync_version(directory, self.engine)["status"], "SYNCED")
        self.assertEqual(self.count(), 2)

    def test_existing_db_history_cannot_be_overwritten_by_changed_payload(self):
        meta = self.save()
        directory = self.out / meta["version_directory"]
        records = R.read_json(directory / "registry.json")
        records[1]["model_name"] = "different"
        R.write_json(directory / "registry.json", records)
        self.assertEqual(R.sync_version(directory, self.engine)["status"], "PENDING")
        with session_scope(self.engine) as session:
            db = session.get(MlModelVersion, (meta["version_id"], "model_b"))
            self.assertEqual(db.model_name, meta["model_versions"][1]["model_name"])

    def test_two_model_registration_rolls_back_together(self):
        meta = self.save(register=False)
        directory = self.out / meta["version_directory"]
        records = R.read_json(directory / "registry.json")
        records[1]["train_rows"] = -1
        R.write_json(directory / "registry.json", records)
        self.assertEqual(R.sync_version(directory, self.engine)["status"], "PENDING")
        self.assertEqual(self.count(), 0)

    def test_corrupt_version_model_is_not_registered(self):
        meta = self.save(register=False)
        (self.out / meta["model_versions"][0]["artifact_path"]).write_bytes(b"corrupt")
        self.assertEqual(R.sync_version(self.out / meta["version_directory"], self.engine)["status"], "PENDING")
        self.assertEqual(self.count(), 0)

    def test_legacy_bundle_is_archived_before_latest_alias_is_replaced(self):
        (self.out / "models.joblib").write_bytes(b"legacy")
        (self.out / "metadata.json").write_text(json.dumps(metadata()), encoding="utf-8")
        meta = self.save()
        self.assertEqual((self.out / meta["legacy_snapshot"] / "models.joblib").read_bytes(), b"legacy")

    def test_partial_export_does_not_replace_published_version(self):
        first = self.save()
        previous = (self.out / "metadata.json").read_bytes()
        with patch.object(A.joblib, "dump", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual((self.out / "metadata.json").read_bytes(), previous)
        self.assertIsInstance(M.load_models(self.out, first["version_id"])["model_a"], ModelDouble)

    def test_first_upgrade_copy_failure_keeps_legacy_model_and_prediction_together(self):
        joblib.dump({"model_a": ModelDouble(.1), "model_b": None}, self.out / "models.joblib")
        rows().iloc[:1][["country_code", "hs6"]].assign(score=.1).to_csv(self.out / "predictions.csv", index=False)
        R.write_json(self.out / "metadata.json", metadata())
        original = A._copy_atomic

        def fail_copy(source, destination):
            if destination.name == "predictions.csv":
                raise OSError("copy failed")
            original(source, destination)

        with patch.object(A, "_copy_atomic", side_effect=fail_copy):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(M.load_models(self.out)["model_a"].value, .1)
        self.assertEqual(M.load_predictions(self.out).score.iloc[0], .1)
        self.save()  # 이후 재실행도 원래 legacy를 다시 잘못 복사하지 않는다.

    def test_changed_source_during_feature_build_is_rejected_without_training(self):
        with patch.object(A, "input_manifest", side_effect=[[{"sha256": "old"}], [{"sha256": "new"}]]), \
             patch.object(A.F, "load_data", return_value={}), \
             patch.object(A.F, "build_dataset", return_value=rows()):
            with self.assertRaisesRegex(ValueError, "입력 파일이 변경"):
                A.load_training_data()

    def test_duplicate_training_keys_do_not_create_a_false_data_snapshot(self):
        with self.assertRaisesRegex(ValueError, "키가 중복"):
            self.save(dataset=pd.concat([rows(), rows().iloc[:1]]))
        self.assertEqual(self.count(), 0)
        self.assertFalse((self.out / "metadata.json").exists())

    def test_model_train_wires_both_model_records_without_real_fit(self):
        data = pd.concat([rows(), rows().iloc[[2]].assign(anchor=pd.Timestamp("2025-02-01"), y_growth=np.nan)])
        a, b = ModelDouble(), ModelDouble(1)
        classifier = MagicMock()
        classifier.fit.return_value = b
        labeled = data[data.y_growth.notna()]
        with patch.object(M, "evaluate", return_value=metadata()["evaluation"]), \
             patch.object(M, "fit_a", return_value=a) as fit, \
             patch.object(M, "available_b", return_value=labeled), \
             patch.object(M.F, "type_thresholds", return_value={"cv_hi": .5}), \
             patch.object(M, "labeled_b", return_value=(labeled, np.array([0, 1, 1]))), \
             patch.object(M, "classifier", return_value=classifier):
            meta = M.train(data, self.out, "country_ndcg_at_5", registry_engine=self.engine)
        fit.assert_called_once()
        self.assertEqual([r["status"] for r in meta["model_versions"]], ["READY", "READY"])
        self.assertEqual(self.count(), 2)


if __name__ == "__main__":
    unittest.main()
