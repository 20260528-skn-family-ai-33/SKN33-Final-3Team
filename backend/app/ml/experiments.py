"""명시적 호출로만 실행하는 모델 A 실험. 운영 학습/DB/스케줄러와 독립적이다."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import time
import traceback
from uuid import uuid4
from zoneinfo import ZoneInfo
import hashlib

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge, SGDRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from threadpoolctl import threadpool_limits

from backend.app.ml import features as F
from backend.app.ml.experiment_metrics import evaluate_predictions
from backend.app.ml.feature_recovery import coverage, file_info
from backend.app.ml.runtime import runtime_versions
from backend.app.ml.transforms import TrainingClipper

ROOT = Path(__file__).resolve().parents[3]
BASELINES = ("baseline_growth", "baseline_mean", "baseline_zero")
OPTIONAL = {"lightgbm": "lightgbm", "xgboost": "xgboost"}
KINDS = {*BASELINES, "hgb", "ridge", "elasticnet", "sgd", "random_forest", "extra_trees", *OPTIONAL}
FEATURE_SETS = {
    "all": list(F.FEATURES),
    "without_related": [c for c in F.FEATURES if c not in F.RELATED_FEATURES],
    "without_news": [c for c in F.FEATURES if not c.startswith("news_")],
    "core": list(F.FEATURES[:12]) + F.CATEGORICAL,
    "core_related": [c for c in F.FEATURES if c in F.FEATURES[:12] or c in F.RELATED_FEATURES or c in F.CATEGORICAL],
    "without_external_fx": [c for c in F.FEATURES if not c.startswith(("mkt_", "kor_share", "fx_"))
                            and c not in ("log_gdp", "gdp_growth", "log_population", "log_gdp_pc")],
}


@dataclass
class ModelSpec:
    name: str
    kind: str
    params: dict = field(default_factory=dict)


@dataclass
class ExperimentConfig:
    # 기본 Dev 두 시점으로 비교하고, 마지막 2025-09는 사용자가 따로 실행한다.
    validation_anchors: tuple = ("2025-02-01", "2025-05-01")
    holdout_anchor: str | None = "2025-09-01"
    phase: str = "dev"
    horizon: int = 12
    gap_months: int = 12
    ks: tuple = (5, 10)
    min_candidates: int = 10
    seeds: tuple = (0,)
    max_train_rows: int | None = None
    sampling_seed: int = 0
    n_jobs: int = 2
    target_clip: tuple | None = (.01, .99)
    drop_constant: bool = True
    direction_threshold: float = 0.0
    primary_metric: str = "country_ndcg_at_5"
    save_predictions: bool = True
    save_models: bool = False
    make_pdf: bool = False
    output_root: str = str(ROOT / "data/ml/experiments")


def model_catalog():
    return [ModelSpec("hgb", "hgb"), ModelSpec("ridge", "ridge"),
            ModelSpec("elasticnet", "elasticnet"), ModelSpec("sgd_huber", "sgd"),
            ModelSpec("random_forest", "random_forest"), ModelSpec("extra_trees", "extra_trees"),
            ModelSpec("lightgbm", "lightgbm"), ModelSpec("xgboost", "xgboost")]


def dependency_status(specs):
    return pd.DataFrame([{"model": s.name, "kind": s.kind,
                          "package": OPTIONAL.get(s.kind, "scikit-learn"),
                          "installed": s.kind not in OPTIONAL or importlib.util.find_spec(OPTIONAL[s.kind]) is not None}
                         for s in specs])


def _write(path, value):
    path = Path(path)
    # JSON NaN을 남기지 않고 미평가를 null로 저장한다.
    def clean(obj):
        if isinstance(obj, dict): return {str(k): clean(v) for k, v in obj.items()}
        if isinstance(obj, (tuple, list)): return [clean(v) for v in obj]
        if isinstance(obj, (float, np.floating)): return float(obj) if np.isfinite(obj) else None
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.bool_): return bool(obj)
        if isinstance(obj, np.ndarray): return clean(obj.tolist())
        if isinstance(obj, (Path, pd.Timestamp)): return str(obj)
        return obj if obj is None or isinstance(obj, (str, int, bool)) else repr(obj)
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def load_snapshot(path=ROOT / "data/ml/features_snapshot.pkl"):
    """신뢰하는 로컬 pickle만 사용. 기존 모델 로더의 상충하는 runtime 이력을 우회 수정하지 않는다.

    새 실험은 저장 모델을 로드하지 않고 현재 커널에서 새로 학습한다.
    pickle 호환성이 없으면 원인과 생성 환경을 확인하고 새 환경/원본 재생성을 선택한다.
    """
    path = Path(path)
    frame = pd.read_pickle(path)
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("피처 스냅샷이 DataFrame이 아닙니다")
    frame.attrs["snapshot"] = file_info(path)
    frame.attrs["loaded_runtime"] = runtime_versions()
    summary = path.parent / "features_snapshot_summary.json"
    if summary.exists():
        frame.attrs["snapshot_summary"] = json.loads(summary.read_text("utf-8"))
    return frame


def _anchor(value):
    timestamp = pd.Timestamp(value)
    if timestamp != timestamp.to_period("M").to_timestamp():
        raise ValueError(f"기준월은 월 초 날짜로 지정하세요: {value}")
    return timestamp


def _validate(config, specs, feature_sets, scenarios):
    if config.phase not in ("dev", "holdout") or config.horizon not in F.A_TARGETS:
        raise ValueError("phase=dev/holdout, horizon=6/12만 지원합니다")
    if config.gap_months < config.horizon:
        raise ValueError("학습 정답 창과 평가 정답 창을 분리하도록 gap >= horizon이 필요합니다")
    if not config.ks or any(not isinstance(k, int) or k < 1 for k in config.ks) or len(set(config.ks)) != len(config.ks):
        raise ValueError("ks는 중복 없는 양의 정수여야 합니다")
    if config.n_jobs < 1 or config.min_candidates < 1 or not config.seeds or len(set(config.seeds)) != len(config.seeds):
        raise ValueError("양의 n_jobs/min_candidates 및 중복 없는 seeds를 지정하세요")
    if config.max_train_rows is not None and config.max_train_rows < 2:
        raise ValueError("max_train_rows는 None 또는 2 이상이어야 합니다")
    if config.target_clip is not None and not (len(config.target_clip) == 2 and 0 <= config.target_clip[0] < config.target_clip[1] <= 1):
        raise ValueError("target_clip은 None 또는 0<=low<high<=1 분위수 쌍입니다")
    if not np.isfinite(config.direction_threshold):
        raise ValueError("방향 판정 임계값이 유한하지 않습니다")
    anchors = [_anchor(a) for a in config.validation_anchors]
    if not anchors or len(set(anchors)) != len(anchors):
        raise ValueError("평가 기준월을 중복 없이 지정하세요")
    if config.holdout_anchor:
        holdout = _anchor(config.holdout_anchor)
        if config.phase == "dev" and max(anchors) >= holdout:
            raise ValueError("Dev 평가월은 예약 Holdout보다 앞이어야 합니다")
        if config.phase == "holdout" and anchors != [holdout]:
            raise ValueError("Holdout 실행은 예약한 기준월 하나만 지정하세요")
    elif config.phase == "holdout":
        raise ValueError("holdout_anchor가 필요합니다")
    if config.phase == "holdout" and (len(specs) != 1 or len(feature_sets) != 1 or len(scenarios) != 1 or len(config.seeds) != 1):
        raise ValueError("Holdout에는 Dev에서 고정한 모델·피처·자료 시나리오·seed 하나만 넣으세요")
    if config.phase == "holdout" and any("sensitivity" in str(f.attrs.get("recovery", "")) for f in scenarios.values()):
        raise ValueError("추정 공개일 민감도 시나리오는 수정값을 미리 쓴 낙관적 비교라 Holdout·채택에 쓸 수 없습니다")
    for names in ([s.name for s in specs], list(feature_sets), list(scenarios)):
        if not names or len(set(names)) != len(names) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in names):
            raise ValueError("각 이름은 중복 없는 영문·숫자·밑줄·하이픈이어야 합니다")
    for spec in specs:
        if spec.kind not in KINDS or spec.kind in BASELINES or spec.name in BASELINES:
            raise ValueError(f"지원하지 않거나 예약된 모델: {spec}")
        if set(spec.params) & {"n_jobs", "random_state", "nthread"}:
            raise ValueError("병렬 수와 난수는 ExperimentConfig.n_jobs/seeds로 지정하세요")
        if spec.params.get("early_stopping", False) is not False or spec.params.get("early_stopping_rounds") is not None:
            raise ValueError("이 실험은 고정 반복 수로 비교합니다. 외부 평가행을 early stopping에 쓰지 마세요")
    for name, cols in feature_sets.items():
        if not cols or len(set(cols)) != len(cols) or set(cols) - set(F.FEATURES):
            raise ValueError(f"{name}: 입력은 중복 없는 F.FEATURES 부분집합이어야 합니다. 식별자/정답 금지")
    if not re.fullmatch(r"(country|product)_(ndcg|precision)_at_\d+", config.primary_metric):
        raise ValueError("primary_metric은 country/product의 ndcg/precision_at_K 중 선택하세요")
    if int(config.primary_metric.rsplit("_", 1)[1]) not in config.ks:
        raise ValueError("primary_metric의 K를 ks에도 포함하세요")


def prepare_scenarios(scenarios, horizon):
    target, baseline = F.A_TARGETS[horizon]
    normalized = {}
    reference = None
    for name, original in scenarios.items():
        required = {"anchor", "country_code", "hs6", target, baseline, *F.FEATURES}
        missing = required - set(original)
        if missing:
            raise ValueError(f"{name} 스냅샷 열 누락 {sorted(missing)}. 특히 6개월 실험은 원본부터 새 스냅샷이 필요합니다")
        # 얕은 사본에 바뀌는 열만 새로 넣는다. 173만 행 전체를 복사·정렬하면 8GB PC에서 메모리가 모자란다.
        frame = original.copy(deep=False)
        frame["anchor"] = pd.to_datetime(frame.anchor, errors="raise")
        keys = ["anchor", "country_code", "hs6"]
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            raise ValueError(f"{name}: 행 키 결측/중복")
        if not frame.anchor.eq(frame.anchor.dt.to_period("M").dt.to_timestamp()).all():
            raise ValueError("anchor가 월 초가 아닙니다")
        if not frame.hs6.astype(str).str.fullmatch(r"\d{6}").all():
            raise ValueError("hs6를 앞자리 0을 보존한 6자리 문자열로 제공하세요")
        frame["hs6"] = frame.hs6.astype(str)
        frame["y_growth"] = pd.to_numeric(frame[target], errors="raise")
        if np.isinf(frame.y_growth.to_numpy(dtype=float)).any():
            raise ValueError("정답에 무한대가 있습니다")
        # 학습 순서·표본 추출이 입력 순서에 좌우되지 않게 키 순서로 맞춘다.
        # 키 열만으로 순서를 구하고, 이미 정렬된 입력(스냅샷·복구 결과)이면 전체 사본을 만들지 않는다.
        order = frame[keys].reset_index(drop=True).sort_values(keys).index.to_numpy()
        if (order != np.arange(len(order))).any():
            frame = frame.iloc[order]
        frame.index = pd.RangeIndex(len(frame))
        # 복구 실험은 X만 변경. 동일 후보/정답/기준선을 보장한다.
        comparison = frame[[*keys, "y_growth", baseline]]
        if reference is not None and not reference.equals(comparison):
            raise ValueError("자료 시나리오의 행 키·정답·기준선이 다릅니다. 같은 평가 대상을 사용하세요")
        reference = comparison if reference is None else reference
        frame.attrs = dict(original.attrs)
        normalized[name] = frame
    return normalized


def _split_masks(frame, anchor, config):
    anchor = _anchor(anchor)
    labeled = frame.y_growth.notna()
    train = labeled & (frame.anchor <= anchor - pd.DateOffset(months=config.gap_months))
    test = labeled & (frame.anchor == anchor)
    if train.sum() < 2 or not test.any():
        raise ValueError(f"{anchor.date()}: 학습/평가 행 부족")
    return train, test


def split_rows(frame, anchor, config):
    # 정답 있는 행 전체를 중간 사본으로 만들지 않고 train·test만 바로 뽑는다.
    train_mask, test_mask = _split_masks(frame, anchor, config)
    train, test = frame[train_mask], frame[test_mask]
    if config.max_train_rows is not None and len(train) > config.max_train_rows:
        train = train.sample(config.max_train_rows, random_state=config.sampling_seed).sort_index()
    return train, test


def build_model(spec, columns, seed, n_jobs):
    """모든 인코더·대체·스케일·절단 경계는 해당 train에서만 fit."""
    categories = [c for c in F.CATEGORICAL if c in columns]
    numeric = [c for c in columns if c not in categories]
    # 부스팅 3종은 결측을 직접 다루므로 대체 없이 넘기고 범주는 정수 코드로 준다.
    # XGBoost에 원핫·밀집 행렬을 주면 학습 100만 행에서 수 GB가 되어 이 방식이 메모리에 맞다.
    native = spec.kind in ("hgb", "lightgbm", "xgboost")
    linear = spec.kind in ("ridge", "elasticnet", "sgd")
    if native:
        nums = "passthrough"
        cats = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan, encoded_missing_value=np.nan)
    else:
        steps = [("imputer", SimpleImputer(strategy="median", add_indicator=True))]
        if linear:
            steps.append(("scale", StandardScaler()))
        nums = Pipeline(steps)
        cats = Pipeline([("imputer", SimpleImputer(strategy="constant", fill_value=-1)),
                         ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=True))])
    transformers = []
    if numeric: transformers.append(("numeric", nums, numeric))
    if categories: transformers.append(("category", cats, categories))
    preprocess = ColumnTransformer(transformers, sparse_threshold=0 if native else 1.0)
    categorical_indices = list(range(len(numeric), len(columns)))
    if spec.kind == "hgb":
        defaults = dict(max_iter=300, learning_rate=.05, early_stopping=False)
        defaults.update(spec.params)
        defaults.update(random_state=seed, categorical_features=categorical_indices)
        estimator = HistGradientBoostingRegressor(**defaults)
    elif spec.kind == "ridge":
        estimator = Ridge(**{**dict(alpha=10.0, solver="lsqr"), **spec.params})
    elif spec.kind == "elasticnet":
        estimator = ElasticNet(**{**dict(alpha=.001, l1_ratio=.2, max_iter=3000, random_state=seed), **spec.params})
    elif spec.kind == "sgd":
        estimator = SGDRegressor(**{**dict(loss="huber", alpha=.0001, max_iter=2000, early_stopping=False,
                                          random_state=seed), **spec.params})
    elif spec.kind in ("random_forest", "extra_trees"):
        cls = RandomForestRegressor if spec.kind == "random_forest" else ExtraTreesRegressor
        estimator = cls(**{**dict(n_estimators=200, max_depth=14, min_samples_leaf=30, max_features=.8,
                                 n_jobs=n_jobs, random_state=seed), **spec.params})
    elif spec.kind == "lightgbm":
        from lightgbm import LGBMRegressor
        estimator = LGBMRegressor(**{**dict(n_estimators=300, learning_rate=.05, num_leaves=31,
                                           verbosity=-1, n_jobs=n_jobs, random_state=seed), **spec.params})
    elif spec.kind == "xgboost":
        from xgboost import XGBRegressor
        estimator = XGBRegressor(**{**dict(n_estimators=300, learning_rate=.05, max_depth=6,
                                          tree_method="hist", objective="reg:squarederror",
                                          n_jobs=n_jobs, random_state=seed), **spec.params})
    else:
        raise ValueError(spec.kind)
    return Pipeline([("clip", TrainingClipper()), ("preprocess", preprocess), ("model", estimator)]), categorical_indices


def _frame_hash(frame, columns):
    """열 단위로 해시해 큰 표의 부분 사본을 만들지 않는다."""
    digest = hashlib.sha256()
    for col in dict.fromkeys(columns):
        digest.update(col.encode("utf-8"))
        digest.update(pd.util.hash_pandas_object(frame[col], index=False).to_numpy(dtype=np.uint64).astype("<u8").tobytes())
    return digest.hexdigest()


def leaderboard(run_dir):
    results = json.loads((Path(run_dir) / "results.json").read_text("utf-8"))
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text("utf-8"))
    expected = len(manifest["config"]["validation_anchors"]) * len(manifest["config"]["seeds"])
    if not results:
        return pd.DataFrame()
    records = pd.json_normalize(results, sep=".")
    rows = []
    keys = ["scenario", "feature_set", "model"]
    for values, group in records.groupby(keys, sort=False):
        row = dict(zip(keys, values))
        success = group[group.status == "ok"]
        row.update(successful_runs=len(success), expected_runs=expected,
                   complete=bool(len(success) == expected and manifest["status"] != "interrupted"), kind=group.kind.iloc[0])
        for col in records.columns:
            if col.startswith("metrics.") or col in ("fit_seconds", "predict_seconds", "metric_seconds"):
                data = pd.to_numeric(success[col], errors="coerce").dropna()
                label = col.removeprefix("metrics.")
                row[label + "_mean"] = float(data.mean()) if len(data) else None
                row[label + "_std"] = float(data.std(ddof=0)) if len(data) else None
                row[label + "_valid_runs"] = len(data)
        rows.append(row)
    return pd.DataFrame(rows)


def holdout_configuration(dev_run_dir, scenario, feature_set, model, seed=0):
    """Dev에서 완료된 후보 하나의 설정을 그대로 복사한다. 학습은 실행하지 않는다."""
    run_dir = Path(dev_run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    config = ExperimentConfig(**manifest["config"])
    if config.phase != "dev" or not config.holdout_anchor or seed not in config.seeds:
        raise ValueError("Holdout을 예약한 Dev 결과와 사전에 사용한 seed가 필요합니다")
    board = leaderboard(run_dir)
    if board.empty:
        raise ValueError("Dev 결과가 없습니다")
    selected = board[(board.scenario == scenario) & (board.feature_set == feature_set) & (board.model == model)]
    valid = config.primary_metric + "_valid_runs"
    if selected.empty or not bool(selected.iloc[0].complete) or selected.iloc[0].get(valid, 0) != selected.iloc[0].expected_runs:
        raise ValueError("모든 Dev fold의 주 지표가 있는 완료 후보만 선택할 수 있습니다")
    spec = next((ModelSpec(**s) for s in manifest["models"] if s["name"] == model), None)
    if spec is None:
        raise ValueError("학습 모델을 선택하세요. 기준선은 Holdout에서도 자동 비교합니다")
    config.phase = "holdout"
    config.validation_anchors = (config.holdout_anchor,)
    config.seeds = (seed,)
    return config, [spec], {feature_set: manifest["feature_sets"][feature_set]}


def run_experiments(scenarios, specs, feature_sets=None, config=None):
    """노트북에서 직접 호출. 작업은 순차 실행하며 모델 내부 스레드도 n_jobs로 제한한다.

    결과는 매번 새 data/ml/experiments/<KST-ID>/에 저장한다. 운영 모델 승격 없음.
    예외/의존성 누락도 결과에 남겨 성공한 일부 fold만으로 순위를 만들지 않는다.
    """
    config = config or ExperimentConfig()
    feature_sets = feature_sets or {"all": FEATURE_SETS["all"]}
    _validate(config, specs, feature_sets, scenarios)
    frames = prepare_scenarios(scenarios, config.horizon)
    for frame in frames.values():
        for anchor in config.validation_anchors:
            _split_masks(frame, anchor, config)  # 행 부족이면 실행 폴더를 만들기 전에 멈춘다
    run_id = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
    run_dir = Path(config.output_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    target, baseline = F.A_TARGETS[config.horizon]
    manifest = {"run_id": run_id, "status": "running", "config": asdict(config),
                "models": [asdict(s) for s in specs], "feature_sets": feature_sets,
                "runtime": runtime_versions(), "scenarios": {},
                "sources": [file_info(Path(__file__)), file_info(ROOT / "backend/app/ml/experiment_metrics.py"),
                            file_info(ROOT / "backend/app/ml/feature_recovery.py"),
                            file_info(ROOT / "backend/app/ml/transforms.py")],
                "notes": ["과거 정답을 보고 여러 번 설정을 조정한 시점은 독립 Holdout이 아님",
                          "기존 HGB와 달리 내부 무작위 early stopping을 끄고 반복 수 고정",
                          "ML 입력은 현재 제공 스냅샷. 원천 공표 지연/뉴스 태깅 버전의 완전성은 별도 검증 필요"]}
    for name, frame in frames.items():
        manifest["scenarios"][name] = {"rows": len(frame), "provenance": frame.attrs,
                                       # 2: 키 순서로 정렬한 표의 열 단위 해시. 1(행 단위)로 만든 예전 값과는 비교하지 않는다
                                       "data_hash_version": 2,
                                       "data_sha256": _frame_hash(frame, ["anchor", "country_code", "hs6", "y_growth", baseline, *F.FEATURES])}
    _write(run_dir / "manifest.json", manifest)
    results = []
    _write(run_dir / "results.json", results)
    all_specs = [ModelSpec(k, k) for k in BASELINES] + list(specs)
    try:
        for scenario, frame in frames.items():
            for feature_set, candidates in feature_sets.items():
                for anchor in config.validation_anchors:
                    train, test = split_rows(frame, anchor, config)
                    x_train = train[candidates].replace([np.inf, -np.inf], np.nan)
                    x_test = test[candidates].replace([np.inf, -np.inf], np.nan)
                    stats = coverage(x_train, candidates)
                    columns = stats.loc[stats.status != "all_missing", "feature"].tolist()
                    if config.drop_constant:
                        columns = stats.loc[stats.status == "available", "feature"].tolist()
                    clip = train.y_growth.quantile(config.target_clip).tolist() if config.target_clip else None
                    y_fit = train.y_growth.clip(*clip) if clip else train.y_growth
                    anchor_name = _anchor(anchor).strftime("%Y%m")
                    split_id = f"{scenario}__{feature_set}__{anchor_name}"
                    stats.merge(coverage(x_test, candidates), on="feature", suffixes=("_train", "_test")).to_csv(
                        run_dir / f"{split_id}__coverage.csv", index=False, encoding="utf-8-sig")
                    train[["anchor", "country_code", "hs6"]].to_csv(run_dir / f"{split_id}__train_keys.csv.gz", index=False)
                    test[["anchor", "country_code", "hs6", "y_growth"]].to_csv(run_dir / f"{split_id}__test_rows.csv.gz", index=False)
                    for seed in config.seeds:
                        for spec in all_specs:
                            case_id = f"{split_id}__{spec.name}__s{seed}"
                            record = {"case_id": case_id, "scenario": scenario, "feature_set": feature_set,
                                      "model": spec.name, "kind": spec.kind, "params": spec.params,
                                      "anchor": str(_anchor(anchor).date()), "seed": seed, "status": "running",
                                      "phase": config.phase, "horizon": config.horizon,
                                      "train_rows": len(train), "test_rows": len(test),
                                      "train_start": str(train.anchor.min().date()), "train_end": str(train.anchor.max().date()),
                                      "features_used": columns, "target_clip": clip}
                            print(f"[{case_id}] train={len(train):,}, test={len(test):,}, features={len(columns)}", flush=True)
                            started = time.perf_counter()
                            try:
                                if spec.kind in OPTIONAL and importlib.util.find_spec(OPTIONAL[spec.kind]) is None:
                                    record.update(status="skipped", reason=f"선택 의존성 미설치: {OPTIONAL[spec.kind]}")
                                else:
                                    model = None
                                    with threadpool_limits(limits=config.n_jobs):
                                        if spec.kind == "baseline_growth":
                                            score = test[baseline].to_numpy(dtype=float)
                                        elif spec.kind == "baseline_zero":
                                            score = np.zeros(len(test))
                                        elif spec.kind == "baseline_mean":
                                            score = np.repeat(float(y_fit.mean()), len(test))
                                        else:
                                            if not columns:
                                                raise ValueError("이 학습 구간에서 유효한 입력 피처가 없습니다")
                                            model, cat_indices = build_model(spec, columns, seed, config.n_jobs)
                                            fit_kw = {"model__categorical_feature": cat_indices} if spec.kind == "lightgbm" else {}
                                            model.fit(x_train[columns], y_fit, **fit_kw)
                                            record["fit_seconds"] = time.perf_counter() - started
                                            tick = time.perf_counter()
                                            score = model.predict(x_test[columns])
                                            record["predict_seconds"] = time.perf_counter() - tick
                                            record["effective_params"] = model[-1].get_params()
                                        record.setdefault("fit_seconds", 0.0)
                                        record.setdefault("predict_seconds", time.perf_counter() - started)
                                        tick = time.perf_counter()
                                        metrics, groups = evaluate_predictions(test, score, config.ks, config.min_candidates,
                                                                               config.direction_threshold)
                                        record.update(status="ok", metrics=metrics)
                                        groups.to_csv(run_dir / f"{case_id}__groups.csv", index=False, encoding="utf-8-sig")
                                        segments = {}
                                        semiconductor = test.hs6.str[:4].isin(["8541", "8542"]).to_numpy()
                                        for segment, mask in [("semiconductor", semiconductor), ("other", ~semiconductor)]:
                                            if mask.any():
                                                segments[segment], _ = evaluate_predictions(test.loc[mask], np.asarray(score)[mask],
                                                                                           config.ks, config.min_candidates, config.direction_threshold)
                                        record["segments"] = segments
                                        record["metric_seconds"] = time.perf_counter() - tick
                                        if config.save_predictions:
                                            test[["anchor", "country_code", "hs6", "y_growth"]].assign(score=score).to_csv(
                                                run_dir / f"{case_id}__predictions.csv.gz", index=False)
                                        if config.save_models and model is not None:
                                            import joblib
                                            joblib.dump({"pipeline": model, "features": columns, "target_clip": clip,
                                                         "case": case_id, "config": asdict(config)}, run_dir / f"{case_id}.joblib")
                            except Exception as exc:
                                record.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
                                print(record["error"], flush=True)
                            finally:
                                record["elapsed_seconds"] = time.perf_counter() - started
                                results.append(record)
                                _write(run_dir / "results.json", results)
        manifest["status"] = "completed" if all(r["status"] == "ok" for r in results) else "completed_with_issues"
    except BaseException:
        manifest["status"] = "interrupted"
        raise
    finally:
        manifest["finished_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
        _write(run_dir / "manifest.json", manifest)
        from backend.app.ml.experiment_report import write_reports
        # 리포트만 재생성할 수 있다. 실패해도 metrics/manifest를 보존한다.
        try:
            write_reports(run_dir, make_pdf=config.make_pdf)
        except Exception as exc:
            _write(run_dir / "report_error.json", {"error": f"{type(exc).__name__}: {exc}"})
            print(f"리포트 생성 실패 (결과 JSON 보존): {exc}", flush=True)
    return run_dir


def best_candidate(run_dir):
    """Dev 주 지표 1위 학습 모델. 모든 fold를 마친 original 시나리오 후보만 (기준선·민감도 제외)."""
    board = leaderboard(run_dir)
    primary = json.loads((Path(run_dir) / "manifest.json").read_text("utf-8"))["config"]["primary_metric"]
    if board.empty or primary + "_mean" not in board:
        raise ValueError("Holdout 후보를 고를 Dev 결과가 없습니다")
    ok = (board.complete & ~board.model.isin(BASELINES) & (board.scenario == "original")
          & (board[primary + "_valid_runs"] == board.expected_runs))
    if not ok.any():
        raise ValueError("모든 Dev fold를 마친 학습 모델 후보가 없습니다")
    return board[ok].sort_values(primary + "_mean", ascending=False).iloc[0]


def main():
    """노트북과 같은 함수로 데이터 로드 → (민감도) → Dev 비교·리포트 → (Holdout)을 한 번에 실행한다."""
    import argparse
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/ml/features_snapshot_py311.pkl",
                        help="피처 스냅샷 (기본: 이 PC Python 3.11용)")
    parser.add_argument("--models", nargs="+", default=["hgb", "ridge", "extra_trees"],
                        choices=sorted(KINDS - set(BASELINES)), help="비교할 모델 (기준선 3종은 자동 포함)")
    parser.add_argument("--feature-sets", nargs="+", default=["all", "without_related"], choices=sorted(FEATURE_SETS))
    parser.add_argument("--sensitivity", action="store_true",
                        help="추정 공개일로 시장·WDI를 붙인 민감도 시나리오 추가 (Holdout 후보에서는 제외)")
    parser.add_argument("--horizon", type=int, default=F.HORIZON, choices=sorted(F.A_TARGETS))
    parser.add_argument("--max-train-rows", type=int, help="학습 행 상한 (시간 측정용, 평가 행은 그대로)")
    parser.add_argument("--n-jobs", type=int, default=2)
    parser.add_argument("--holdout", action="store_true", help="Dev 주 지표 1위 후보 하나로 Holdout까지 실행")
    parser.add_argument("--no-pdf", action="store_true", help="PDF 없이 HTML·CSV만")
    parser.add_argument("--save-models", action="store_true",
                        help="학습한 모델을 실행 폴더에 <조합 이름>.joblib으로 저장 (Holdout 모델 포함, 기준선 제외)")
    args = parser.parse_args()

    if args.horizon == 6:  # 정답 6개월: 간격 6개월, 정답 있는 마지막 기준월 2026-03을 Holdout으로
        anchors, holdout, gap = ("2025-08-01", "2025-11-01"), "2026-03-01", 6
    else:
        anchors, holdout, gap = ExperimentConfig.validation_anchors, ExperimentConfig.holdout_anchor, 12
    config = ExperimentConfig(validation_anchors=anchors, holdout_anchor=holdout, horizon=args.horizon, gap_months=gap,
                              max_train_rows=args.max_train_rows, n_jobs=args.n_jobs, make_pdf=not args.no_pdf,
                              save_models=args.save_models)
    print(f"스냅샷 읽는 중: {args.snapshot}", flush=True)
    frame = load_snapshot(args.snapshot)
    scenarios = {"original": frame}
    if args.sensitivity:
        from backend.app.ml.feature_recovery import recover_external
        estimate = ROOT / "backend/app/ml/reference/experiments/availability_release_estimate.csv"
        scenarios["sensitivity_release_estimate"], audit = recover_external(frame, availability=estimate)
        print(audit[["feature", "observed_before", "observed_after"]].to_string(index=False), flush=True)
    specs = [ModelSpec(kind, kind) for kind in args.models]
    dev = run_experiments(scenarios, specs, {name: FEATURE_SETS[name] for name in args.feature_sets}, config)
    print(f"\nDev 리포트: {dev / 'report.html'}" + ("" if args.no_pdf else f"\n           {dev / 'report.pdf'}"))
    if args.holdout:
        best = best_candidate(dev)
        print(f"\nHoldout 후보: {best.scenario} / {best.feature_set} / {best.model} "
              f"({config.primary_metric} Dev 평균 {best[config.primary_metric + '_mean']:.4f})", flush=True)
        hold_config, hold_specs, hold_features = holdout_configuration(dev, best.scenario, best.feature_set, best.model)
        hold = run_experiments({best.scenario: scenarios[best.scenario]}, hold_specs, hold_features, hold_config)
        print(f"Holdout 리포트: {hold / 'report.html'}")


if __name__ == "__main__":
    main()
