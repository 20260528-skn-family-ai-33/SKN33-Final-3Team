"""실험 후보 모델을 정답 있는 전체 기준월로 학습해 저장한다.

전처리·기본 설정·피처 선택·정답 절단은 experiments.py와 같고, 평가 분할 없이 정답 있는 모든 행으로 학습한다.
운영 HGB(market_model train)의 버전 폴더·predictions.csv·조회 결과는 바꾸지 않는다.

1) 스냅샷으로 한 번 저장: data/ml/candidates/<모델>_<버전>.joblib (이미 있으면 건너뜀)
    python -m backend.app.ml.candidate_models --models lightgbm random_forest --version v1 --n-jobs 4

2) 월별 수집 뒤 최종 모델(2단계 C K=100, v1 · v3 앙상블) 전체 재학습(run): 수집 원본으로 피처를 만들어 학습하고
   data/ml/monthly_models/model_a_two_stage_<YYYYMM>.joblib 저장 → DB ml_model_version에 기록한다.
   이어서 같은 달 피처 표로 증감 예상 모델(모델 B, growth_model)을 같은 절차로 학습해
   model_b_growth_<YYYYMM>.joblib 저장 → ml_model_version(model_key=model_b)에 기록한다. B가 실패해도 A 결과는 그대로다.
    자동: .env에 CUSTOMS_ML_ENTRYPOINT=backend.app.ml.candidate_models:run (customs_monthly의 ML 단계)
    수동: python -m backend.app.ml.candidate_models --monthly 2026-10-01
"""
import argparse
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import version as package_version
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from filelock import FileLock
from backend.app.storage.workspace import stored, checkpoint
from threadpoolctl import threadpool_limits

from backend.app.ml import artifacts
from backend.app.ml import features as F
from backend.app.ml.experiment_metrics import evaluate_predictions
from backend.app.ml.experiments import (BASELINES, FEATURE_SETS, KINDS, OPTIONAL, ROOT, SNAPSHOT_PY311, ExperimentConfig,
                                        ModelSpec, build_model, load_snapshot, prepare_scenarios)
from backend.app.ml.feature_recovery import coverage
from backend.app.ml.model_registry import read_json, register_records, registered_versions, sha256, version_key, write_json
from backend.app.ml.runtime import runtime_versions

OUT_DIR = ROOT / "data/ml/candidates"
MONTHLY_DIR = ROOT / "data/ml/monthly_models"
FEATURE_CACHE = ROOT / "data/ml/monthly_feature_cache"  # 월이 추가되면 영향받은 기준월만 다시 계산
KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger(__name__)


def needed_columns(feature_set="all"):
    target, baseline = F.A_TARGETS[F.HORIZON]
    return list(dict.fromkeys(["anchor", "country_code", "hs6", target, baseline, *FEATURE_SETS[feature_set]]))


def prepare_rows(frame, feature_set="all"):
    """정답 있는 학습 X·y·기준월, 마지막 정답 기준월 행, 최신 기준월 X.

    frame은 호출할 때 바로 만들어 넘긴다(호출 쪽이 들고 있지 않아야 8GB PC에서 큰 표 사본이 하나씩만 남는다).
    """
    candidates = FEATURE_SETS[feature_set]
    frame = prepare_scenarios({"original": frame}, F.HORIZON)["original"]  # experiments와 같은 키 순서
    labeled = frame.y_growth.notna().to_numpy()
    anchors = frame.anchor[labeled]
    last = labeled & (frame.anchor == anchors.max()).to_numpy()
    test_rows = frame.loc[last, ["anchor", "country_code", "hs6", "y_growth", F.A_TARGETS[F.HORIZON][1]]]
    latest = frame.loc[frame.anchor == frame.anchor.max(), ["anchor", "country_code", "hs6", *candidates]]
    y = frame.y_growth[labeled]
    x = frame.loc[labeled, candidates]
    source = frame.attrs.get("source")
    del frame
    if any(np.isinf(x[c].to_numpy(dtype=float)).any() for c in candidates):
        x = x.replace([np.inf, -np.inf], np.nan)
    stats = coverage(x, candidates)
    columns = stats.loc[stats.status == "available", "feature"].tolist()  # experiments의 drop_constant와 같은 기준
    if columns != candidates:
        x = x[columns]
    latest = latest[["anchor", "country_code", "hs6", *columns]].replace([np.inf, -np.inf], np.nan)
    info = {"train_rows": len(x), "train_start": str(anchors.min().date()), "train_end": str(anchors.max().date()),
            "feature_set": feature_set}
    return {"x": x, "y": y, "anchors": anchors, "test_rows": test_rows, "latest": latest, "columns": columns,
            "info": info, "source": source}


def _snapshot_frame(path):
    snapshot = load_snapshot(path)
    frame = snapshot[needed_columns()]
    frame.attrs = {"source": snapshot.attrs.get("snapshot")}
    return frame


def _month_frame(month, cache_dir, engine=None):
    """수집 원본으로 피처를 만들어 그 달 시점까지로 자른다. 이번 수집 월이 학습 자료에 없으면 멈춘다.

    누락 월 자동 복구는 지난달을 나중에 다시 돌리므로, 원본에 그 뒤의 달이 있어도 기준월은 month까지,
    정답은 미래 12개월이 month 안에 끝나는 기준월만 남긴다 (과거 월 이름으로 미래 자료를 학습하지 않게).
    """
    ds, data, source = artifacts.load_training_data(cache_dir=cache_dir, engine=engine)
    # 같은 달 재실행 비교(db_inputs(month=...))와 같은 범위: 그 달까지의 환율만 기록한다
    source = {**source, "fx": artifacts.fx_info(data["fx"], month)}
    del data
    if pd.Timestamp(source["raw_period_end"]) < month:
        raise ValueError(f"수집 원본이 {source['raw_period_end']}까지만 있어 {month.date()} 수집분으로 학습할 수 없습니다")
    target = F.A_TARGETS[F.HORIZON][0]
    # exp_12m: 2단계 모델의 최신 예측에 함께 저장하는 최근 12개월 수출액
    frame = ds.loc[ds.anchor <= month, [*needed_columns(), "exp_12m"]]
    frame.loc[frame.anchor > month - pd.DateOffset(months=F.HORIZON), target] = np.nan
    frame.attrs = {"source": {**source, "as_of_month": str(month.date())}}
    return frame


def train_full(x, y, kind, n_jobs, seed=0):
    config = ExperimentConfig(seeds=(seed,), n_jobs=n_jobs)
    clip = y.quantile(config.target_clip).tolist()
    model, cat_indices = build_model(ModelSpec(kind, kind), list(x.columns), seed, n_jobs)
    fit_kw = {"model__categorical_feature": cat_indices} if kind == "lightgbm" else {}
    started = time.perf_counter()
    with threadpool_limits(limits=n_jobs):
        model.fit(x, y.clip(*clip), **fit_kw)
    return {"pipeline": model, "features": list(x.columns), "target_clip": clip, "case": f"full__{kind}__s{seed}",
            "config": asdict(config), "fit_seconds": time.perf_counter() - started}


def holdout_check(rows, kind, n_jobs):
    """마지막 정답 기준월을 평가월로, 그보다 12개월 이상 앞선 기준월로 같은 설정을 학습해 작년 성장률 기준선과 비교."""
    anchors = rows["anchors"]
    test_anchor = anchors.max()
    train = (anchors <= test_anchor - pd.DateOffset(months=F.HORIZON)).to_numpy()
    test = (anchors == test_anchor).to_numpy()
    model = train_full(rows["x"][train], rows["y"][train], kind, n_jobs)["pipeline"]
    tested = rows["test_rows"]
    model_metrics, _ = evaluate_predictions(tested, model.predict(rows["x"][test]))
    base_metrics, _ = evaluate_predictions(tested, tested[F.A_TARGETS[F.HORIZON][1]].to_numpy(dtype=float))
    return _finite({"test_anchor": str(test_anchor.date()), "train_end": str(anchors[train].max().date()),
                    "train_rows": int(train.sum()), "test_rows": int(test.sum()),
                    "model": model_metrics, "baseline_growth_12m": base_metrics})


def _finite(value):
    """DB JSON에는 NaN을 넣을 수 없어 미평가 값은 null로 둔다."""
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return None if isinstance(value, float) and not np.isfinite(value) else value


def _runtime(kind):
    runtime = runtime_versions()
    if kind in OPTIONAL:  # 다시 읽을 때 같은 패키지 버전이 필요하다
        runtime[OPTIONAL[kind]] = package_version(OPTIONAL[kind])
    return runtime


def _fx_unavailable(db):
    """환율 표가 비면 DB 장애로 본다 (fx_from_db는 실패해도 빈 표를 돌려준다)."""
    return db["fx"]["rows"] == 0


def _same_inputs(snapshot, files, db, month=None):
    """저장된 학습 계보(input_snapshot)와 지금 입력이 같은 원본인지.

    state.json은 실행 단계·ML 결과가 실행마다 바뀌는 체크포인트라 뺀다. 데이터를 정하는 collection_dir는
    선택된 CSV의 경로·해시로 이미 비교된다. DB 환율·통화 매핑도 비교하되, 환율을 못 읽은 빈 표(DB 장애)는
    정정으로 보지 않는다. 그때 다시 학습하면 환율 피처 없는 모델이 새 버전으로 남는다.
    month를 주면 그 달보다 뒤의 월별 수집 폴더(monthly/<YYYYMM>/)도 뺀다. 과거 월 모델은 그 달까지만 학습하므로
    나중에 들어온 달은 학습 내용을 바꾸지 않는다.
    """
    last = f"{month:%Y%m}" if month is not None else None

    def later(path):
        found = re.search(r"/monthly/(\d{6})/", path)
        return last is not None and found is not None and found[1] > last

    def data(rows):
        return [f for f in rows if not f["path"].endswith("/state.json") and not later(f["path"])]
    if data(snapshot["files"]) != data(files):
        return False
    if _fx_unavailable(db) and snapshot["fx"]["rows"] > 0:
        return True
    return snapshot["fx"] == db["fx"] and snapshot["country_mapping_sha256"] == db["country_mapping_sha256"]


# 학습 결과를 정하는 코드. 같은 달·같은 입력이라도 이 파일들(K·앙상블 구성·피처 계산 포함)이 바뀌면 새 리비전으로 학습한다.
MODEL_SOURCES = ("backend/app/ml/candidate_models.py", "backend/app/ml/two_stage.py", "backend/app/ml/experiments.py",
                 "backend/app/ml/feature_recovery.py", "backend/app/ml/features.py", "backend/app/ml/sources.py",
                 "backend/app/ml/preprocess.py", "backend/app/ml/temporal.py", "backend/app/ml/related_groups.py",
                 "backend/app/ml/transforms.py", "backend/app/ml/artifacts.py", "backend/app/ml/feature_cache.py")


def _model_config(kind):
    """모델 종류·학습 코드의 해시·패키지 버전. 재사용 판단에서 입력 원본과 함께 비교한다."""
    return {"kind": kind, "sources": {p: sha256(ROOT / p) for p in MODEL_SOURCES},
            "runtime": _runtime("lightgbm" if kind == "two_stage" else kind)}


MONTHLY_PREFIX = {"two_stage": "model_a_two_stage"}  # 월별 파일 이름 앞부분. 그 밖의 kind는 kind 그대로
GROWTH_PREFIX = "model_b_growth"  # 증감 예상(모델 B) 월별 파일. agents/market.py가 이 이름과 레지스트리로 찾는다
GROWTH_SOURCES = (*MODEL_SOURCES, "backend/app/ml/growth_model.py")


def _growth_config():
    """모델 B 재사용 판단용. 모델 A 학습 코드에 growth_model.py를 더한 해시와 패키지 버전."""
    return {"kind": "growth_huber", "sources": {p: sha256(ROOT / p) for p in GROWTH_SOURCES},
            "runtime": _runtime("lightgbm")}


def _find_revision(out_dir, base, config, month, engine, usable=lambda record: True):
    """같은 달 기록 중 지금 입력·학습 코드와 같은 것을 찾는다. 없으면 다음 빈 리비전(<base>_r<n>)을 돌려준다.

    반환: (name, artifact, record_path, record). record가 None이면 그 이름으로 새로 학습한다.
    usable(record)가 False인 기록(예: 학습 실패로 SKIPPED)은 재사용하지 않고 다음 리비전으로 넘어간다.
    기록 없이 파일만 있는 이름(직접 만든 파일, 기록 전에 멈춘 학습)은 덮어쓰지 않고 건너뛴다.
    """
    files = db = None
    matched = None
    for revision in range(1, 100):
        name = base if revision == 1 else f"{base}_r{revision}"
        artifact, record_path = out_dir / f"{name}.joblib", out_dir / f"{name}.registry.json"
        record = read_json(record_path)
        if record is None:
            if artifact.exists():
                continue
            break
        if files is None:  # 비교할 기록이 있을 때만 입력 파일 전체를 해시한다
            files, db = artifacts.input_manifest(), artifacts.db_inputs(engine, month)  # 학습·기록과 같은 DB
        if (usable(record) and record["data_info"].get("model_config") == config
                and _same_inputs(record["data_info"]["input_snapshot"], files, db, month)):
            matched = name, artifact, record_path, record
            # 환율을 못 읽으면 파일만 비교하므로 정정 전·후 리비전이 모두 맞는다. 그때는 끝까지 보고 가장 최신을 고른다.
            if not _fx_unavailable(db):
                break
    else:
        if matched is None:
            raise ValueError(f"{base}: 같은 달 재학습 기록이 너무 많습니다")
    return matched if matched is not None else (name, artifact, record_path, None)


def _parent(out_dir, prefix, model_key, name, engine):
    """DB에 기록된 직전 버전. 같은 (월, 리비전)이면 같은 계열을 우선한다."""
    current = version_key(f"{name}.registry.json")
    # 학습 실패(SKIPPED) 기록은 모델 파일이 없어 부모로 삼지 않는다.
    earlier = sorted(((key, p.name.startswith(f"{prefix}_")), record["version_id"])
                     for p in out_dir.glob("*.registry.json")
                     if (key := version_key(p)) is not None and key < current
                     and (model_key == "model_a") != p.name.startswith(f"{GROWTH_PREFIX}_")
                     and (record := read_json(p)).get("status") == "READY")
    in_db = registered_versions([version for _, version in earlier], model_key, engine)
    return next((version for _, version in reversed(earlier) if version in in_db), None)


@stored('models/monthly', lambda args: args['out_dir'])
def run(trade_month, engine=None, *, kind="two_stage", out_dir=MONTHLY_DIR, cache_dir=FEATURE_CACHE, n_jobs=4, growth=True):
    """customs_monthly ML 단계: 전체 재학습 → <모델>_<YYYYMM>.joblib → ml_model_version(model_a) 기록.

    kind="two_stage"(기본)는 최종 모델(2단계 v1 · v3 앙상블, two_stage.monthly), 그 밖의 kind는 단일 모델이다.

    같은 달을 입력 파일 그대로 다시 실행하면 재학습하지 않고 저장해 둔 기록만 DB에 다시 보낸다(같은 내용이면 그대로 둔다).
    DB 기록이 실패해도 모델 파일과 기록 JSON은 남아 다음 재시도에서 기록만 다시 보낸다.
    같은 달 원본(수집 파일·DB 환율)이 정정됐으면 <모델>_<YYYYMM>_r2, _r3 …으로 새로 학습한다 (DB 이력은 덮어쓰지 않는다).
    """
    month = pd.Timestamp(trade_month)
    if month != month.to_period("M").to_timestamp():
        raise ValueError("trade_month는 월 초(YYYY-MM-01)여야 합니다")
    prefix = MONTHLY_PREFIX.get(kind, kind)
    base = f"{prefix}_{month:%Y%m}"
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(out_dir / f".{base}.lock")):
        name, artifact, record_path, record = _find_revision(out_dir, base, _model_config(kind), month, engine)
        if record is None:
            # 부모는 DB에 기록된 직전 버전. 학습 전에 정해 DB에 못 닿으면 학습하기 전에 멈춘다.
            # 모델 계열(lightgbm → model_a_two_stage)이 바뀐 달에도 model_a 이력이 이어지도록 모든 월별 기록에서 찾는다.
            parent = _parent(out_dir, prefix, "model_a", name, engine)
            if kind == "two_stage":
                from backend.app.ml import two_stage  # two_stage가 이 모듈을 가져다 써 위에서 읽으면 순환 import
                bundle, evaluation, source = two_stage.monthly(_month_frame(month, cache_dir, engine), n_jobs)
            else:
                rows = prepare_rows(_month_frame(month, cache_dir, engine))
                evaluation = holdout_check(rows, kind, n_jobs)
                bundle = train_full(rows["x"], rows["y"], kind, n_jobs)
                bundle.update(rows["info"])
                source = rows["source"]
            created = datetime.now(KST)
            runtime = _runtime("lightgbm" if kind == "two_stage" else kind)
            bundle.update(version=name, trade_month=str(month.date()), runtime=runtime,
                          trained_at=created.isoformat(timespec="seconds"))
            temporary = artifact.with_suffix(".joblib.tmp")
            joblib.dump(bundle, temporary)
            temporary.replace(artifact)
            record = {"version_id": name, "model_key": "model_a", "model_name": name,
                      "created_at": created.isoformat(timespec="microseconds"), "status": "READY",
                      "artifact_path": artifact.name, "artifact_sha256": sha256(artifact),
                      "parent_version_id": parent,
                      "training_mode": "FULL_RETRAIN", "train_start": bundle["train_start"],
                      "train_end": bundle["train_end"], "train_rows": bundle["train_rows"],
                      "data_info": _finite({"model_family": f"{kind}_full", "trade_month": str(month.date()),
                                            "artifact_root": out_dir.as_posix(), "input_snapshot": source,
                                            "model_config": _model_config(kind),
                                            "runtime": runtime, "features_used": bundle["features"],
                                            "external_dropped": bundle.get("external_dropped"),
                                            "target_clip": bundle["target_clip"], "fit_seconds": bundle["fit_seconds"]}),
                      "evaluation": evaluation, "skip_reason": None}
            write_json(record_path, record)
        elif not artifact.is_file() or sha256(artifact) != record["artifact_sha256"]:
            raise ValueError(f"{artifact}: 저장된 기록과 모델 파일이 다릅니다")
        checkpoint(record_path)
        register_records([record], engine)
    result = {"status": "SUCCESS", "model_name": name, "artifact": artifact.as_posix(), "train_rows": record["train_rows"],
              "country_ndcg_at_5": record["evaluation"]["model"].get("country_ndcg_at_5"),
              "baseline_country_ndcg_at_5": record["evaluation"]["baseline_growth_12m"].get("country_ndcg_at_5")}
    if kind == "two_stage" and growth:
        # 피처 표는 모델 B 단계에서 다시 만든다. 모델 A 학습 동안 큰 표를 하나 더 들고 있지 않게 한다(8GB PC, 사용자 결정).
        result["growth"] = run_growth(month, engine, out_dir=out_dir, cache_dir=cache_dir, n_jobs=n_jobs)
    return result


GROWTH_RETRY_LIMIT = 3   # 같은 달 모델 B를 자동으로 다시 학습하는 최대 횟수(사용자 결정). CUSTOMS_ML_GROWTH_RETRIES로 바꾼다


@stored('models/monthly', lambda args: args['out_dir'])
def retry_pending(engine=None, *, out_dir=MONTHLY_DIR, cache_dir=FEATURE_CACHE, n_jobs=4, skip=(), limit=None):
    """스케줄러 복구 작업(customs_monthly.startup_job)이 부른다. 최신 READY 모델 A와 같은 달의 모델 B가 READY가 아니면
    (학습 실패 SKIPPED·기록 없음) 그 달 모델 B만 다시 학습한다. 그 달 수출입 작업은 SUCCESS라 월 복구 대상이 아니기 때문이다.

    skip: 이번 실행에서 방금 처리한 달(YYYYMM). 같은 실행 안에서 곧바로 다시 학습하지 않고 다음 실행에 맡긴다.
    limit: 같은 달 학습 실패(SKIPPED 기록)가 이만큼 쌓이면 더 하지 않는다(한 번에 약 20분·메모리 대부분). 그 뒤는 run_growth 수동.
    기록 없이 직접 만든 같은 달 파일은 run_growth가 덮어쓰지 않는다.
    반환: run_growth 결과, 다시 할 것이 없으면 None.
    """
    limit = GROWTH_RETRY_LIMIT if limit is None else int(limit)
    out_dir = Path(out_dir).resolve()
    ready = [(key, record) for path in out_dir.glob(f"{MONTHLY_PREFIX['two_stage']}_*.registry.json")
             if (key := version_key(path)) is not None and (record := read_json(path)).get("status") == "READY"]
    if not ready:
        return None
    month = pd.Timestamp(max(ready, key=lambda item: item[0])[1]["data_info"]["trade_month"])
    if f"{month:%Y%m}" in set(skip):
        return None
    # 리비전 순(_r10이 _r2보다 뒤). 아래 ready[-1:]가 최신 READY다
    growth = [read_json(p) for p in sorted(out_dir.glob(f"{GROWTH_PREFIX}_{month:%Y%m}*.registry.json"),
                                           key=lambda p: version_key(p) or ("", 0))
              if version_key(p) is not None and version_key(p)[0] == f"{month:%Y%m}"]
    ready = [record for record in growth if record.get("status") == "READY"]
    if ready:
        # 파일·기록 JSON은 READY인데 DB 기록이 실패했으면 학습 없이 기록만 다시 보낸다(run_growth의 같은 입력 재사용 경로)
        if all(record["version_id"] in registered_versions([record["version_id"]], "model_b", engine) for record in ready[-1:]):
            return None
        log.info("모델 B %s: DB 기록이 없어 다시 보냅니다(%s).", f"{month:%Y-%m}", ready[-1]["version_id"])
        return run_growth(month, engine, out_dir=out_dir, cache_dir=cache_dir, n_jobs=n_jobs)
    failures = sum(record.get("status") == "SKIPPED" for record in growth)
    if failures >= limit:
        log.warning("모델 B %s 자동 재학습 중단: 실패 %d회(상한 %d). run_growth로 수동 실행하세요.",
                    f"{month:%Y-%m}", failures, limit)
        return None
    log.info("모델 B %s 다시 학습(READY 기록 없음, 실패 %d회)", f"{month:%Y-%m}", failures)
    return run_growth(month, engine, out_dir=out_dir, cache_dir=cache_dir, n_jobs=n_jobs)


@stored('models/monthly', lambda args: args['out_dir'])
def run_growth(trade_month, engine=None, *, out_dir=MONTHLY_DIR, cache_dir=FEATURE_CACHE, n_jobs=4):
    """증감 예상 모델(모델 B) 월별 학습. 모델 A(run)와 같은 절차다: 같은 달 피처 표 → 같은 입력이면 재학습 없이 기록만
    다시 보냄 → 정정되면 _r2 … → <model_b_growth>_<YYYYMM>.joblib(최신 기준월 예측 latest 포함) → 레지스트리 JSON → DB.

    피처 표는 여기서 다시 만든다(모델 A 학습 동안 표를 하나 더 들고 있지 않게, 사용자 결정).
    실패해도 예외를 올리지 않는다(모델 A 결과를 지킨다). 학습 자체가 실패하면 그 달에 SKIPPED 기록(사유 포함)을 남기고
    다음 실행은 새 리비전으로 다시 학습한다. 피처 표·DB 같은 자료 단계 실패는 일시 장애일 수 있어 기록을 남기지 않고
    FAILED만 돌려준다. 어느 쪽이든 스케줄러 복구 작업이 retry_pending으로 다음 실행 때 다시 시도한다.
    """
    month = pd.Timestamp(trade_month)
    base = f"{GROWTH_PREFIX}_{month:%Y%m}"
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        with FileLock(str(out_dir / f".{base}.lock")):
            config = _growth_config()
            name, artifact, record_path, record = _find_revision(
                out_dir, base, config, month, engine, usable=lambda r: r.get("status") == "READY")
            manual = out_dir / f"{base}.joblib"
            if record is None and manual.is_file() and not (out_dir / f"{base}.registry.json").exists():
                # 기록 없이 직접 만든 같은 달 파일(growth_model.build·save)이 있다. 에이전트가 이 파일을 쓰므로 다시
                # 학습하지 않는다(약 20분·메모리 대부분). 월별 학습으로 바꾸려면 이 파일을 치운다.
                log.info("모델 B %s: 기록 없이 직접 만든 파일이 있어 학습하지 않습니다(%s).", f"{month:%Y-%m}", manual.name)
                return {"status": "SKIPPED", "model_name": name, "reason": "기록 없이 직접 만든 파일이 있어 덮어쓰지 않았습니다.",
                        "seconds": round(time.perf_counter() - started, 1)}
            if record is None:
                parent = _parent(out_dir, GROWTH_PREFIX, "model_b", name, engine)
                frame = _month_frame(month, cache_dir, engine)  # 실패하면 아래 FAILED(기록 없음)
                created = datetime.now(KST)
                try:
                    from backend.app.ml import growth_model  # growth_model이 이 모듈을 가져다 써 위에서 읽으면 순환 import
                    bundle, evaluation, source = growth_model.monthly(frame, n_jobs)
                except Exception as exc:  # 모델 A는 이미 저장됐다. 사유를 남기고 다음 실행에 다시 학습한다
                    record = {"version_id": name, "model_key": "model_b", "model_name": name,
                              "created_at": created.isoformat(timespec="microseconds"), "status": "SKIPPED",
                              "artifact_path": None, "artifact_sha256": None, "parent_version_id": parent,
                              "training_mode": "FULL_RETRAIN", "train_start": None, "train_end": None, "train_rows": 0,
                              "data_info": {"model_family": "growth_huber_full", "trade_month": str(month.date()),
                                            "artifact_root": out_dir.as_posix(), "model_config": config,
                                            "error_type": type(exc).__name__},
                              "evaluation": {}, "skip_reason": f"학습 실패: {type(exc).__name__}"[:200]}
                    write_json(record_path, record)
                    checkpoint(record_path)
                    register_records([record], engine)
                    return {"status": "SKIPPED", "model_name": name, "reason": record["skip_reason"],
                            "seconds": round(time.perf_counter() - started, 1)}
                runtime = _runtime("lightgbm")
                bundle.update(version=name, trade_month=str(month.date()), runtime=runtime,
                              trained_at=created.isoformat(timespec="seconds"))
                temporary = artifact.with_suffix(".joblib.tmp")
                joblib.dump(bundle, temporary)
                temporary.replace(artifact)
                record = {"version_id": name, "model_key": "model_b", "model_name": name,
                          "created_at": created.isoformat(timespec="microseconds"), "status": "READY",
                          "artifact_path": artifact.name, "artifact_sha256": sha256(artifact),
                          "parent_version_id": parent, "training_mode": "FULL_RETRAIN",
                          "train_start": bundle["train_start"], "train_end": bundle["train_end"],
                          "train_rows": bundle["train_rows"],
                          "data_info": _finite({"model_family": "growth_huber_full", "trade_month": str(month.date()),
                                                "anchor": bundle["anchor"], "artifact_root": out_dir.as_posix(),
                                                "input_snapshot": source, "model_config": config, "runtime": runtime,
                                                "features_used": bundle["features"],
                                                "external_dropped": bundle.get("external_dropped"),
                                                "fit_seconds": bundle["fit_seconds"],
                                                "holdout_seconds": bundle.get("holdout_seconds")}),
                          "evaluation": evaluation, "skip_reason": None}
                write_json(record_path, record)
            elif not artifact.is_file() or sha256(artifact) != record["artifact_sha256"]:
                raise ValueError(f"{artifact}: 저장된 기록과 모델 파일이 다릅니다")
            checkpoint(record_path)
            register_records([record], engine)
    except Exception as exc:
        log.warning("증감 예상 모델 월별 단계 실패(%s). 모델 A 결과는 유지합니다.", type(exc).__name__)
        return {"status": "FAILED", "model_name": base, "reason": type(exc).__name__,
                "seconds": round(time.perf_counter() - started, 1)}
    return {"status": "SUCCESS", "model_name": name, "artifact": artifact.as_posix(), "train_rows": record["train_rows"],
            "direction_accuracy": record["evaluation"].get("direction_accuracy"),
            "baseline_all_down": record["evaluation"].get("baseline_all_down"),
            "seconds": round(time.perf_counter() - started, 1)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", choices=sorted(KINDS - set(BASELINES)))
    parser.add_argument("--version", default="v1")
    parser.add_argument("--n-jobs", type=int, default=2)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT_PY311)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--monthly", metavar="YYYY-MM-01", help="월별 최종 모델(2단계 v1 · v3 앙상블) 전체 재학습과 DB 기록을 직접 실행")
    args = parser.parse_args()
    if args.monthly:
        print(run(args.monthly, n_jobs=args.n_jobs), flush=True)
        return
    if not args.models:
        parser.error("--models 또는 --monthly를 지정하세요")
    train_candidates(args, parser)


@stored('models/candidates', lambda bound: bound['args'].out_dir)
def train_candidates(args, parser):
    targets = {kind: args.out_dir / f"{kind}_{args.version}.joblib" for kind in args.models}
    for kind, path in list(targets.items()):
        if path.exists():
            try:
                joblib.load(path)
            except (ImportError, AttributeError) as exc:
                # 다른 패키지 버전으로 만든 정상 파일일 수 있다. 손상으로 보고 덮어쓰지 않는다
                parser.error(f"{kind}: {path}를 이 환경에서 읽을 수 없습니다 ({type(exc).__name__}: {exc}). "
                             "패키지 버전을 맞추거나, 손상된 파일이면 지운 뒤 다시 실행하세요")
            except Exception as exc:  # 예전 실행이 저장 중 끊긴 파일. 건너뛰지 않고 다시 학습한다
                print(f"{kind}: 기존 파일을 읽을 수 없어 다시 학습 → {path} ({type(exc).__name__})", flush=True)
                continue
            print(f"{kind}: 이미 있음, 건너뜀 → {path}", flush=True)
            del targets[kind]
    if not targets:
        return
    rows = prepare_rows(_snapshot_frame(args.snapshot))
    x, y, latest, columns, info = rows["x"], rows["y"], rows["latest"], rows["columns"], rows["info"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for kind, path in targets.items():
        print(f"{kind}: 학습 시작 ({info['train_start']}~{info['train_end']} {info['train_rows']:,}행, "
              f"피처 {len(columns)}개, n_jobs={args.n_jobs})", flush=True)
        bundle = train_full(x, y, kind, args.n_jobs)
        bundle.update(info, snapshot=rows["source"], version=args.version, runtime=_runtime(kind),
                      trained_at=datetime.now(KST).isoformat(timespec="seconds"))
        score = bundle["pipeline"].predict(latest[columns])
        # 임시 파일에 저장해 다시 읽은 예측이 같을 때만 이름을 바꾼다. 중간에 끊기거나 검사에 실패한 파일이
        # 완료본 이름으로 남으면 다음 실행이 "이미 있음"으로 건너뛴다.
        # 숲 모델은 여러 스레드가 나무 예측을 더하는 순서가 매번 달라 같은 모델도 1e-15 수준으로 다르므로
        # 완전 일치 대신 허용 오차로 비교한다.
        temporary = path.with_suffix(".joblib.tmp")
        joblib.dump(bundle, temporary)
        reloaded = joblib.load(temporary)["pipeline"].predict(latest[columns])
        if not np.allclose(score, reloaded, rtol=0, atol=1e-9):
            raise ValueError(f"{temporary}: 다시 읽은 모델의 예측이 다릅니다")
        temporary.replace(path)
        print(f"{kind}: 저장 {path} ({path.stat().st_size / 1e6:.1f}MB, 학습 {bundle['fit_seconds']:.0f}초, "
              f"{latest.anchor.max().date()} 예측 {len(score):,}행 재확인 일치)", flush=True)
        del bundle


if __name__ == "__main__":
    main()
