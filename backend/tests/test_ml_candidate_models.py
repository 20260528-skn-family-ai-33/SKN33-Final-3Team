"""월별 전체 재학습(run)을 가상 데이터·SQLite로 확인한다 (실데이터 학습·MySQL 연결 없음).

실행: python -m unittest backend.tests.test_ml_candidate_models
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import select

from backend.app.db.session import make_engine, session_scope
from backend.app.ml import candidate_models as C
from backend.app.ml.feature_recovery import MARKET, WDI
from backend.app.models.ml_model_version import MlModelVersion
from backend.tests.test_ml_experiments import dataset


FILES = [{"path": "data/customs/a.csv", "bytes": 1, "sha256": "a"},
         {"path": "data/customs_load_outputs/monthly/202602/state.json", "bytes": 1, "sha256": "s"}]
FX = pd.DataFrame({"month": ["2025-11-01", "2025-12-01"], "currency_code": "USD", "rate": [1400.0, 1410.0]})
DB = {"fx": C.artifacts.fx_info(FX), "country_mapping_sha256": "c"}


def loaded(end="2026-02-01", files=FILES, db=DB):
    """load_training_data 대역. 가상 원본은 2022-01~2026-02."""
    return dataset(), {"fx": FX}, {"raw_period_end": end, "files": files, **db}


class MonthlyRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        self.engine = make_engine("sqlite://")
        MlModelVersion.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def run_month(self, month="2026-02-01", files=FILES, db=DB, kind="lightgbm"):
        """리비전 · 기록 규칙은 모델과 무관해 빠른 단일 LightGBM으로 확인한다 (기본 kind는 아래 최종 모델 시험)."""
        with patch.object(C.artifacts, "input_manifest", return_value=files), \
                patch.object(C.artifacts, "db_inputs", return_value=db):
            return C.run(month, self.engine, kind=kind, out_dir=self.out, cache_dir=None, n_jobs=1)

    def test_default_trains_final_two_stage_model(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()):
            result = self.run_month(kind="two_stage")
        bundle = joblib.load(self.out / "model_a_two_stage_202602.joblib")
        self.assertEqual(set(bundle["members"]), {"base", "ext"})  # 최종 모델 = v1 · v3 앙상블
        self.assertFalse(set(MARKET + WDI) & set(bundle["features"]["base"]))  # v1은 외부 피처 없음
        self.assertTrue(set(MARKET + WDI) & set(bundle["features"]["ext"]))  # v3는 외부 피처 사용 (가상 데이터의 상수 열은 빠짐)
        latest = dataset().anchor.max()
        self.assertTrue((bundle["latest"].anchor == latest).all())
        self.assertTrue(np.isfinite(bundle["latest"][["growth", "order", "exp_12m"]].to_numpy()).all())
        self.assertIsNotNone(result["country_ndcg_at_5"])
        labeled = int(dataset().y_growth.notna().sum())
        self.assertEqual(self.records(), [("model_a_two_stage_202602", "READY", labeled, "FULL_RETRAIN", "model_a_two_stage_202602.joblib")])
        self.assertEqual(C.run.__kwdefaults__["kind"], "two_stage")  # customs_monthly는 kind 없이 부른다

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

    def test_parent_continues_across_model_family_change(self):
        self.saved_version("model_a_two_stage_202602")  # 앞 달은 다른 계열(최종 2단계 모델)로 기록
        self.assertEqual(self.parent_of_next_month(), "model_a_two_stage_202602")

    def test_parent_skips_version_missing_from_db(self):
        self.saved_version("lightgbm_202602")
        self.saved_version("lightgbm_202602_r2", registered=False)
        self.assertEqual(self.parent_of_next_month(), "lightgbm_202602")

    def test_db_reads_use_job_engine_and_first_run_skips_input_hashing(self):
        db, files = Mock(return_value=DB), Mock(return_value=FILES)
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()) as load, \
                patch.object(C.artifacts, "input_manifest", files), patch.object(C.artifacts, "db_inputs", db):
            C.run("2026-02-01", self.engine, kind="lightgbm", out_dir=self.out, cache_dir=None, n_jobs=1)
            self.assertIs(load.call_args.kwargs["engine"], self.engine)
            files.assert_not_called()  # 비교할 기록이 없는 첫 실행은 입력 파일을 다시 해시하지 않는다
            db.assert_not_called()
            C.run("2026-02-01", self.engine, kind="lightgbm", out_dir=self.out, cache_dir=None, n_jobs=1)  # 같은 달 재실행
        db.assert_called_once_with(self.engine, pd.Timestamp("2026-02-01"))  # 그 달까지의 환율만 비교
        files.assert_called_once()
        self.assertEqual(load.call_count, 1)

    def test_past_month_does_not_learn_later_data(self):
        # 누락 월 복구: 원본은 2026-02까지 있는데 2025-12 모델을 만든다
        with patch.object(C.artifacts, "load_training_data", return_value=loaded("2026-02-01")):
            self.run_month("2025-12-01")
        record = C.read_json(self.out / "lightgbm_202512.registry.json")
        self.assertEqual(record["train_end"], "2024-12-01")  # 정답의 미래 12개월이 2025-12 안에 끝나는 기준월까지
        self.assertEqual(record["data_info"]["input_snapshot"]["as_of_month"], "2025-12-01")

    def test_past_month_rerun_ignores_later_month_folders(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded("2026-02-01")):
            self.run_month("2025-12-01")
        later = [*FILES, {"path": "data/customs_load_outputs/monthly/202601/collection_a/customs_raw_202601.csv",
                          "bytes": 1, "sha256": "next"}]  # 그사이 다음 달 수집본이 들어옴
        with patch.object(C.artifacts, "load_training_data", side_effect=AssertionError("학습 내용이 같아 재학습하지 않아야 함")):
            self.assertEqual(self.run_month("2025-12-01", files=later)["model_name"], "lightgbm_202512")
        corrected = [*FILES, {"path": "data/customs_load_outputs/monthly/202512/collection_b/customs_raw_202512.csv",
                              "bytes": 1, "sha256": "fix"}]  # 그 달 자체의 재수집은 정정
        with patch.object(C.artifacts, "load_training_data", return_value=loaded("2026-02-01", files=corrected)):
            self.assertEqual(self.run_month("2025-12-01", files=corrected)["model_name"], "lightgbm_202512_r2")

    def test_model_code_change_trains_new_revision(self):
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()):
            self.run_month()
        changed = {"kind": "lightgbm", "sources": {"backend/app/ml/two_stage.py": "K를 바꾼 코드"}}
        with patch.object(C.artifacts, "load_training_data", return_value=loaded()), \
                patch.object(C, "_model_config", return_value=changed):
            self.assertEqual(self.run_month()["model_name"], "lightgbm_202602_r2")
        with patch.object(C.artifacts, "load_training_data", side_effect=AssertionError("같은 코드는 재학습하지 않아야 함")):
            self.assertEqual(self.run_month()["model_name"], "lightgbm_202602")  # 원래 코드면 처음 판을 재사용

    def test_daily_fx_after_the_month_is_not_a_correction(self):
        # fx_daily_load가 매일 쌓는 뒤 달 환율은 그 달 모델의 입력이 아니다. 재시도가 기록만 다시 보내야 한다
        later = pd.concat([FX, pd.DataFrame({"month": ["2026-03-01"], "currency_code": "USD", "rate": [1420.0]})])
        self.assertEqual(C.artifacts.fx_info(later, pd.Timestamp("2026-02-01")), DB["fx"])
        self.assertEqual(self.first_then(db={**DB, "fx": C.artifacts.fx_info(later, pd.Timestamp("2026-02-01"))}),
                         "lightgbm_202602")
        revised = FX.assign(rate=[1400.0, 1415.0])  # 그 달 이전 환율 정정은 새 리비전
        self.assertNotEqual(C.artifacts.fx_info(revised, pd.Timestamp("2026-02-01")), DB["fx"])

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
