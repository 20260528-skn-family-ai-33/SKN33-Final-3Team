"""별도 SGD A·B 계열. bootstrap 한 번 이후 append-only 정답에 partial_fit을 적용한다.

실데이터 실행은 명시적인 bootstrap/update CLI로만 시작한다. 기존 HGB 산출물을 대체하지 않는다.
"""
import argparse
import copy
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from filelock import FileLock
from sklearn.linear_model import SGDClassifier, SGDRegressor
from sklearn.metrics import f1_score
from sklearn.preprocessing import OneHotEncoder

from backend.app.ml import artifacts as A, features as F, market_model as M
from backend.app.ml.model_registry import read_json, sha256
from backend.app.ml.runtime import ensure_artifact_runtime

OUT_DIR = M.OUT_DIR / "incremental"
KEYS = ["anchor", "country_code", "hs6"]


class RebootstrapRequired(ValueError):
    """이미 학습한 행의 수정·삭제 또는 코드 변경은 기존 계수에서 되돌릴 수 없다."""


class FrozenEncoder:
    """첫 시간 분할의 학습 행으로만 통계를 만들고 추가학습 때 좌표계를 고정한다."""
    def fit(self, rows):
        self.keep_ = list(F.FEATURES)  # 초기 전부 결측인 열도 이후 값을 받을 수 있도록 차원을 유지
        self.numeric = [c for c in F.FEATURES if c not in F.CATEGORICAL]
        x = rows[self.numeric].replace([np.inf, -np.inf], np.nan)
        self.median = x.median().fillna(0)
        self.mean = x.fillna(self.median).mean()
        self.scale = x.fillna(self.median).std(ddof=0).replace(0, 1).fillna(1)
        self.categories = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        self.categories.fit(rows[F.CATEGORICAL].fillna(-1))
        return self

    def transform(self, rows):
        x = rows[self.numeric].replace([np.inf, -np.inf], np.nan)
        missing = x.isna().to_numpy(dtype=float)
        numeric = ((x.fillna(self.median) - self.mean) / self.scale).clip(-8, 8).to_numpy()
        return np.column_stack([numeric, missing, self.categories.transform(rows[F.CATEGORICAL].fillna(-1))])


def ledger(rows, key):
    target = "y_growth" if key == "model_a" else "training_target_b"
    if rows[KEYS].isna().any().any() or rows.duplicated(KEYS).any():
        raise ValueError("추가학습 키에 결측 또는 중복이 있습니다")
    if not np.isfinite(rows[target].to_numpy(dtype=float)).all():
        raise ValueError("추가학습 정답은 모두 유한 값이어야 합니다")
    out = rows[KEYS].copy()
    out["digest"] = pd.util.hash_pandas_object(rows[[*KEYS, *F.FEATURES, target]], index=False).to_numpy()
    return out.reset_index(drop=True)


def unseen(rows, previous, key):
    current = ledger(rows, key)
    if previous is None:
        return rows, current
    old_index, new_index = pd.MultiIndex.from_frame(previous[KEYS]), pd.MultiIndex.from_frame(current[KEYS])
    pos = new_index.get_indexer(old_index)
    if (pos < 0).any() or not np.array_equal(current.digest.to_numpy()[pos], previous.digest.to_numpy()):
        raise RebootstrapRequired(f"{key}: 이미 학습한 행이 수정·삭제됐습니다. 새 출력 폴더에 bootstrap이 필요합니다.")
    return rows.loc[~new_index.isin(old_index)], current


class OnlineModel:
    def __init__(self, key, seed_rows, epochs=3, batch_size=4096):
        self.key, self.epochs, self.batch_size = key, epochs, batch_size
        self.encoder = FrozenEncoder().fit(seed_rows)
        self.estimator = (SGDRegressor(loss="huber", alpha=0.0001, learning_rate="invscaling", eta0=0.001,
                                       random_state=0, average=True) if key == "model_a" else
                          SGDClassifier(loss="log_loss", alpha=0.0001, learning_rate="constant", eta0=0.001,
                                        random_state=0, average=True))
        self.bounds = seed_rows.y_growth.quantile([.01, .99]).to_numpy() if key == "model_a" else None
        self.seen = None

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return self.encoder

    def update(self, rows):
        added, current = unseen(rows, self.seen, self.key)
        # 시간순, 같은 기준월 안에서는 고정 난수 순서. 이전 행을 반복 학습하지 않는다.
        for _, group in added.groupby("anchor", sort=True):
            x = self.encoder.transform(group)
            y = (group.y_growth.clip(*self.bounds).to_numpy() if self.key == "model_a"
                 else group.training_target_b.to_numpy(dtype=int))
            rng = np.random.default_rng(0)
            for _ in range(self.epochs):
                order = rng.permutation(len(group))
                for start in range(0, len(order), self.batch_size):
                    idx = order[start:start + self.batch_size]
                    kwargs = {"classes": np.array(list(F.TYPE_NAMES))} if self.key == "model_b" else {}
                    self.estimator.partial_fit(x[idx], y[idx], **kwargs)
        self.seen = current
        return len(added)

    def predict(self, rows):
        return self.estimator.predict(self.encoder.transform(rows))


def code_signature():
    return {name: sha256(Path(__file__).parent / name) for name in
            ("incremental.py", "features.py", "temporal.py", "transforms.py", "market_model.py")}


def b_rows(rows, cutoff, thresholds):
    known = M.available_b(rows, cutoff)
    x, y = M.labeled_b(known, thresholds)
    return x.assign(training_target_b=y)


def trained_rows(rows, model):
    if model is None or model.seen is None:
        return rows.iloc[:0]
    unseen(rows, model.seen, model.key)  # 보존할 모델도 수정된 정답과 혼합하지 않는다.
    index = pd.MultiIndex.from_frame(rows[KEYS])
    return rows.loc[index.isin(pd.MultiIndex.from_frame(model.seen[KEYS]))]


def evaluate_update(ds, state, epochs, batch_size):
    """운영 모델과 별도인 12개월 gap 체크포인트로 평가한다. 미래 정답 학습 누수를 방지한다."""
    labeled = ds[ds.y_growth.notna()]
    train, test = M.split(labeled)
    anchor = test.anchor.max()
    eval_a = state.get("eval_a")
    if eval_a is None:
        eval_a = OnlineModel("model_a", train, epochs, batch_size)
    eval_a.update(train)
    state["eval_a"] = eval_a
    score = eval_a.predict(test[F.FEATURES])
    report = {"horizon_months": F.HORIZON, "baseline_rule": "growth_12m",
        "evaluation_method": "separate_incremental_checkpoint_with_12_month_gap",
        "train_anchors": [str(train.anchor.min().date()), str(train.anchor.max().date())],
        "test_anchor": str(anchor.date()), "train_rows": len(train), "test_rows": len(test),
        "model_a": M.rank_metrics(test, score), "baseline_a": M.rank_metrics(test, test.growth_12m),
        "model_a_product": M.rank_metrics(test, score, "product"),
        "baseline_a_product": M.rank_metrics(test, test.growth_12m, "product"),
        "features_used": eval_a[0].keep_}
    # B의 유형 정의도 최초 유효 학습 시점 이후 고정한다. 새 정답으로 과거 정의를 바꾸지 않는다.
    thresholds = state.get("thresholds")
    if thresholds is None:
        proposed = F.type_thresholds(M.available_b(train, anchor))
        if all(np.isfinite(v) for v in proposed.values()):
            thresholds = state["thresholds"] = proposed
    if thresholds is None:
        report["model_b"] = "공개 시점·라벨 부족"
        return report
    tr = b_rows(train, anchor, thresholds)
    te = b_rows(test, ds.anchor.max(), thresholds)
    if tr.training_target_b.nunique() < 2 or te.empty:
        report["model_b"] = "학습 클래스 또는 평가 라벨 부족"
        return report
    eval_b = state.get("eval_b") or OnlineModel("model_b", tr, epochs, batch_size)
    eval_b.update(tr)
    state["eval_b"] = eval_b
    predicted, base = eval_b.predict(te[F.FEATURES]), F.past_type(te, thresholds)
    ok = np.isfinite(base)
    metric = lambda a, b: float(f1_score(a, b, labels=list(F.TYPE_NAMES), average="macro", zero_division=0))
    report["model_b_all"] = {"macro_f1": metric(te.training_target_b, predicted), "rows": len(te)}
    report["model_b"] = {"macro_f1": metric(te.training_target_b[ok], predicted[ok]) if ok.any() else None,
                         "rows": int(ok.sum())}
    report["baseline_b"] = {"macro_f1": metric(te.training_target_b[ok], base[ok]) if ok.any() else None,
                            "rows": int(ok.sum())}
    return report


def train_incremental(ds, out_dir=OUT_DIR, *, bootstrap=False, data_info=None, epochs=3, batch_size=4096,
                      registry_engine=None, register=True):
    """전체 원본 재학습과 독립된 계열. 오류·채택 거부 시 기존 버전과 계수는 그대로 보존한다."""
    if epochs < 1 or batch_size < 1:
        raise ValueError("epochs와 batch_size는 양수여야 합니다")
    out_dir = Path(out_dir).resolve()
    if out_dir == M.OUT_DIR.resolve():
        raise ValueError("기존 전체학습 모델과 다른 출력 폴더를 사용하세요")
    out_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(out_dir / ".incremental.lock")):
        previous = read_json(out_dir / "metadata.json", {})
        if bootstrap and previous:
            raise ValueError("bootstrap은 빈 출력 폴더에만 가능합니다. 기존 모델을 보존하고 새 폴더를 지정하세요.")
        if not bootstrap and not previous:
            raise ValueError("먼저 bootstrap으로 추가학습용 모델을 한 번 생성하세요")
        if previous:
            if previous.get("model_family") != "sgd_incremental":
                raise ValueError("SGD 추가학습 계열이 아닌 모델입니다")
            ensure_artifact_runtime(out_dir)
            path = A.artifact_path(out_dir, "incremental_state.joblib", metadata=previous)
            if sha256(path) != previous.get("extra_artifacts_sha256", {}).get(path.name):
                raise ValueError("추가학습 체크포인트 해시 불일치")
            state = joblib.load(path)
            if state["code_signature"] != code_signature():
                raise RebootstrapRequired("학습 코드가 바뀌었습니다. 새 폴더에 bootstrap이 필요합니다.")
            epochs, batch_size = state["epochs"], state["batch_size"]
        else:
            state = {"code_signature": code_signature(), "epochs": epochs, "batch_size": batch_size}
        labeled = ds[ds.y_growth.notna()]
        if state.get("model_a") is not None:
            unseen(labeled, state["model_a"].seen, "model_a")
        report = evaluate_update(ds, state, epochs, batch_size)
        M.require_promotion(report, "country_ndcg_at_5")
        model_a = state.get("model_a") or copy.deepcopy(state["eval_a"])
        learned_a = model_a.update(labeled)
        if bootstrap:
            learned_a = len(labeled)
        th = state.get("thresholds") or {k: np.nan for k in ("mkt_growth_hi", "share_hi", "cv_hi")}
        eligible_b = b_rows(labeled, ds.anchor.max(), th)
        model_b = state.get("model_b")
        learned_b = 0
        if M.use_classifier(report):
            initial_b = model_b is None
            model_b = model_b or copy.deepcopy(state["eval_b"])
            learned_b = model_b.update(eligible_b)
            if initial_b:
                learned_b = len(eligible_b)
        sets = {"model_a": labeled, "model_b": trained_rows(eligible_b, model_b)}
        latest = ds[ds.anchor == ds.anchor.max()]
        fingerprint = A.dataset_info(ds)["sha256"]
        if previous and not learned_a and not learned_b and fingerprint == previous.get("feature_table_sha256"):
            return {**previous, "update_status": "NO_NEW_DATA"}
        pred = latest[["country_code", "hs6"]].assign(score=model_a.predict(latest[F.FEATURES]))
        if not np.isfinite(pred.score).all():
            raise ValueError("추가학습 모델의 예측에 비유한 값이 있습니다")
        pred["rank_in_country"] = pred.groupby("country_code").score.rank(ascending=False, method="first").astype(int)
        pred["rank_in_product"] = pred.groupby("hs6").score.rank(ascending=False, method="first").astype(int)
        pred["market_type"] = model_b.predict(latest[F.FEATURES]) if model_b is not None else F.past_type(latest, th)
        pred["market_type_name"] = pred.market_type.map(F.TYPE_NAMES)
        state.update(model_a=model_a, model_b=model_b)
        meta = {"model_family": "sgd_incremental", "model_a_variant": "sgd", "feature_table_sha256": fingerprint,
            "trained_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="microseconds"),
            "predict_anchor": str(latest.anchor.max().date()), "features": F.FEATURES,
            "features_used": model_a[0].keep_, "thresholds": {k: v if np.isfinite(v) else None for k, v in th.items()},
            "min_export_usd": F.MIN_EXPORT_USD, "training_target_clip": dict(zip(("low", "high"), model_a.bounds)),
            "model_b": "classifier" if model_b is not None else "rule (라벨·비교 평가 부족)",
            "promotion_metric": "country_ndcg_at_5", "evaluation": report}
        details = {}
        for key, count in (("model_a", learned_a), ("model_b", learned_b)):
            initial = bootstrap or (key == "model_b" and state.get("model_b") is not None
                                     and not any(r["model_key"] == key and r["status"] == "READY"
                                                 for r in previous.get("model_versions", [])))
            details[key] = {"model_family": "sgd_incremental", "updated_rows": count,
                "training_mode": ("INITIAL_FIT" if initial else "PARTIAL_FIT") if count else "UNCHANGED",
                "epochs_per_new_batch": epochs, "evaluation_passed": key == "model_a" or M.use_classifier(report)}
        return A.save_version({"model_a": model_a, "model_b": model_b}, pred, meta, sets, out_dir,
            data_info=data_info, eligible_b_rows=len(eligible_b), registry_engine=registry_engine, register=register,
            extra_artifacts={"incremental_state.joblib": state}, model_details=details)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "update"))
    parser.add_argument("--data-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=M.OUT_DIR / "monthly_feature_cache")
    parser.add_argument("--epochs", type=int, default=3, help="초기 설정. update는 저장된 값을 유지")
    args = parser.parse_args(argv)
    # 비싼 CSV 처리 전에도 잘못된 실행 모드·환경을 검출한다.
    previous = read_json(args.data_dir / "metadata.json", {})
    if args.command == "bootstrap" and previous:
        parser.error("bootstrap 대상에 모델이 있습니다. 새 --data-dir를 지정하세요")
    if args.command == "update":
        if not previous or previous.get("model_family") != "sgd_incremental":
            parser.error("SGD bootstrap 결과가 필요합니다")
        ensure_artifact_runtime(args.data_dir)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ds, data, info = A.load_training_data(cache_dir=args.cache_dir)
    del data
    result = train_incremental(ds, args.data_dir, bootstrap=args.command == "bootstrap", data_info=info, epochs=args.epochs)
    print(json.dumps({"version_id": result["version_id"], "update_status": result.get("update_status", "SAVED"),
                      "feature_cache": info["feature_cache"], "evaluation": result["evaluation"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    # -m 실행에서도 joblib 클래스 경로를 __main__이 아닌 정규 모듈명으로 저장한다.
    from backend.app.ml.incremental import main as run_main
    run_main()
