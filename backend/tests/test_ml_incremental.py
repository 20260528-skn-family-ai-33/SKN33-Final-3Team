"""실제 SGD/HGB 학습 없이 캐시 동등성·새 행 선택·버전과 평가 격리를 확인한다."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from backend.app.ml import artifacts as A, feature_cache as C, features as F, incremental as I, market_model as M
from backend.app.ml.model_registry import read_json, sha256
from backend.tests.test_ml_market import fake_data


class EstimatorDouble:
    def __init__(self, **kwargs):
        self.count = 0
        self.classes_seen = []

    def partial_fit(self, x, y, **kwargs):
        self.count += len(x)
        if "classes" in kwargs:
            self.classes_seen.append(kwargs["classes"].tolist())
        return self

    def predict(self, x):
        return np.zeros(len(x)) if self.classes_seen else x[:, 1]


def table(months=39):
    rows = []
    for anchor in pd.date_range("2020-01-01", periods=months, freq="MS"):
        for item in range(12):
            rows.append({"anchor": anchor, "country_code": "VN", "hs6": f"{item + 1:06d}",
                         "y_growth": (item - 5) / 10, "future_cv": item / 10,
                         "future_mkt_growth": (item - 5) / 20, "future_share": item / 30,
                         "type_available_from": pd.Timestamp("2019-01-01")})
    ds = pd.DataFrame(rows)
    for i, c in enumerate(F.FEATURES):
        ds[c] = np.arange(len(ds)) % 12 + i
    ds["hs2"] = 1
    ds.loc[ds.anchor > ds.anchor.max() - pd.DateOffset(months=12), "y_growth"] = np.nan
    return ds


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / "tmp"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="incremental-test-", dir=parent)
        self.out = Path(self.temp.name).resolve()
        if not self.out.is_relative_to(parent.resolve()):
            raise ValueError("테스트 임시 경로가 작업 영역 밖입니다")

    def tearDown(self):
        self.temp.cleanup()


class CacheTest(WorkspaceTest):
    def data(self, months):
        # news 같은 외부 기준정보는 고정하여 월 추가만으로 인한 갱신을 검증한다.
        data = fake_data(50)
        end = pd.Timestamp("2022-01-01") + pd.DateOffset(months=months - 1)
        data["exports"] = data["exports"][data["exports"].month <= end]
        data["fx"] = data["fx"][pd.to_datetime(data["fx"].month) <= end]
        return data

    def assert_same(self, actual, expected):
        sort = lambda x: x.sort_values(I.KEYS).reset_index(drop=True)
        assert_frame_equal(sort(actual), sort(expected))

    def test_new_month_reuses_old_anchors_and_matches_full_build(self):
        C.build_cached(self.data(49), self.out)
        with patch.object(F, "build_dataset", wraps=F.build_dataset) as build:
            ds, info = C.build_cached(self.data(50), self.out)
        self.assertEqual(info["mode"], "PARTIAL")
        self.assertEqual(len(info["recomputed_anchors"]), 13)
        self.assertGreater(info["reused_rows"], 0)
        self.assertEqual(len(build.call_args.kwargs["anchors"]), 13)
        self.assert_same(ds, F.build_dataset(self.data(50)))

    def test_identical_inputs_skip_build_and_corrupt_cache_is_rejected(self):
        C.build_cached(self.data(49), self.out)
        with patch.object(F, "build_dataset", side_effect=AssertionError("불필요한 재계산")):
            _, info = C.build_cached(self.data(49), self.out)
        self.assertEqual(info["mode"], "REUSED")
        meta = read_json(self.out / "metadata.json")
        (self.out / meta["cache_file"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "해시"):
            C.build_cached(self.data(49), self.out)

    def test_historical_revision_and_external_change_match_full_rebuild(self):
        data = self.data(49)
        C.build_cached(data, self.out)
        data["exports"] = data["exports"].copy()
        data["exports"].loc[data["exports"].month == pd.Timestamp("2024-09-01"), "amount"] *= 1.2
        ds, info = C.build_cached(data, self.out)
        self.assertEqual(info["mode"], "PARTIAL")
        self.assert_same(ds, F.build_dataset(data))
        data["countries"] = data["countries"].assign(region_id=1)
        ds, info = C.build_cached(data, self.out)
        self.assertEqual(info["mode"], "FULL")
        self.assert_same(ds, F.build_dataset(data))


class IncrementalTest(WorkspaceTest):
    def setUp(self):
        super().setUp()
        for target in ("SGDRegressor", "SGDClassifier"):
            self.enterContext(patch.object(I, target, EstimatorDouble))
        # 평가 품질은 실제 학습 없이 판정할 수 없다. 저장 흐름 테스트에서는 고정 점수를 사용한다.
        self.enterContext(patch.object(M, "rank_metrics", return_value={"ndcg_at_5": .8, "precision_at_5": .5}))
        self.enterContext(patch.object(M, "use_classifier", return_value=True))

    def save(self, months=39, bootstrap=False, **kwargs):
        return I.train_incremental(table(months), self.out, bootstrap=bootstrap, register=False, **kwargs)

    def state(self, meta):
        return joblib.load(self.out / meta["version_directory"] / "incremental_state.joblib")

    def test_resume_updates_both_models_with_only_new_labels_and_saves_parent(self):
        first = self.save(bootstrap=True)
        old = self.state(first)
        old_path = self.out / first["model_versions"][0]["artifact_path"]
        digest = sha256(old_path)
        second = self.save(months=40)
        new = self.state(second)
        for key in ("model_a", "model_b"):
            self.assertEqual(new[key].estimator.count - old[key].estimator.count, 12 * 3)
            self.assertEqual(len(new[key].seen) - len(old[key].seen), 12)
            record = next(r for r in second["model_versions"] if r["model_key"] == key)
            self.assertEqual(record["training_mode"], "PARTIAL_FIT")
            self.assertEqual(record["data_info"]["updated_rows"], 12)
            self.assertEqual(record["parent_version_id"], first["version_id"])
            np.testing.assert_array_equal(new[key].encoder.mean, old[key].encoder.mean)
        self.assertEqual(sha256(old_path), digest)
        self.assertEqual(new["model_b"].estimator.classes_seen[-1], [0, 1, 2, 3, 4])
        self.assertEqual(M.load_models(self.out)["model_a"].estimator.count, new["model_a"].estimator.count)

    def test_repeated_update_is_idempotent_and_evaluation_remains_purged(self):
        first = self.save(bootstrap=True)
        second = self.save()
        self.assertEqual(second["update_status"], "NO_NEW_DATA")
        self.assertEqual(first["version_id"], second["version_id"])
        state = self.state(first)
        test_anchor = pd.Timestamp(first["evaluation"]["test_anchor"])
        for key in ("eval_a", "eval_b"):
            self.assertLessEqual(state[key].seen.anchor.max() + pd.DateOffset(months=12), test_anchor)
            self.assertLess(len(state[key].seen), len(state[key.replace("eval", "model")].seen))

    def test_revised_or_deleted_trained_rows_stop_without_publishing(self):
        first = self.save(bootstrap=True)
        before = (self.out / "metadata.json").read_bytes()
        for changed in (table(40).drop(index=0), table(40).assign(log_exp_12m=999)):
            with self.assertRaises(I.RebootstrapRequired):
                I.train_incremental(changed, self.out, register=False)
        self.assertEqual((self.out / "metadata.json").read_bytes(), before)
        self.assertEqual(len(list((self.out / "versions").iterdir())), 1)

    def test_rejection_preserves_version_and_checkpoint(self):
        first = self.save(bootstrap=True)
        before = (self.out / "metadata.json").read_bytes()
        with patch.object(M, "require_promotion", side_effect=M.PromotionRejected("기준선 미달", {})):
            with self.assertRaises(M.PromotionRejected):
                self.save(months=40)
        self.assertEqual((self.out / "metadata.json").read_bytes(), before)
        self.assertEqual(self.state(first)["model_a"].estimator.count, 27 * 12 * 3)

    def test_b_unavailable_is_skipped_then_created_when_labels_arrive(self):
        ds = table()
        ds["type_available_from"] = pd.NaT
        with patch.object(M, "use_classifier", return_value=False):
            first = I.train_incremental(ds, self.out, bootstrap=True, register=False)
        self.assertEqual(first["model_versions"][1]["status"], "SKIPPED")
        second = self.save(months=40)
        self.assertEqual(second["model_versions"][1]["training_mode"], "INITIAL_FIT")
        self.assertEqual(second["model_versions"][1]["status"], "READY")

    def test_b_failed_comparison_retains_existing_weights(self):
        first = self.save(bootstrap=True)
        old = self.state(first)["model_b"]
        with patch.object(M, "use_classifier", return_value=False):
            second = self.save(months=40)
        new = self.state(second)["model_b"]
        self.assertEqual(new.estimator.count, old.estimator.count)
        record = second["model_versions"][1]
        self.assertEqual(record["training_mode"], "UNCHANGED")
        self.assertEqual(record["data_info"]["updated_rows"], 0)
        self.assertFalse(record["data_info"]["evaluation_passed"])

    def test_bad_modes_changed_code_and_corrupt_checkpoint_fail_before_training(self):
        with self.assertRaisesRegex(ValueError, "bootstrap"):
            self.save()
        first = self.save(bootstrap=True)
        with self.assertRaisesRegex(ValueError, "빈 출력"):
            self.save(bootstrap=True)
        with patch.object(I, "code_signature", return_value={}):
            with self.assertRaises(I.RebootstrapRequired):
                self.save(months=40)
        path = self.out / first["version_directory"] / "incremental_state.joblib"
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "해시"):
            self.save(months=40)

    def test_new_key_in_old_anchor_is_allowed_but_duplicate_is_rejected(self):
        ds = table().dropna(subset=["y_growth"])
        old = I.ledger(ds, "model_a")
        new_row = ds.iloc[[0]].assign(country_code="US")
        added, _ = I.unseen(pd.concat([ds, new_row]), old, "model_a")
        self.assertEqual(len(added), 1)
        with self.assertRaisesRegex(ValueError, "중복"):
            I.unseen(pd.concat([ds, ds.iloc[[0]]]), old, "model_a")


if __name__ == "__main__":
    unittest.main()
