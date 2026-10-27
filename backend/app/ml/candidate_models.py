"""실험 후보 모델을 정답 있는 전체 기준월로 학습해 저장한다.

전처리·기본 설정·피처 선택·정답 절단은 experiments.py와 같고, 평가 분할 없이 정답 있는 모든 행으로 학습한다.
운영 HGB(market_model train)의 버전 폴더·predictions.csv·조회 결과는 바꾸지 않는다.

1) 스냅샷으로 한 번 저장: data/ml/candidates/<모델>_<버전>.joblib (이미 있으면 건너뜀)
    python -m backend.app.ml.candidate_models --models lightgbm random_forest --version v1 --n-jobs 4

2) 월별 수집 뒤 LightGBM 전체 재학습(run): 수집 원본으로 피처를 만들어 학습하고
   data/ml/monthly_models/lightgbm_<YYYYMM>.joblib 저장 → DB ml_model_version에 기록한다.
    자동: .env에 CUSTOMS_ML_ENTRYPOINT=backend.app.ml.candidate_models:run (customs_monthly의 ML 단계)
    수동: python -m backend.app.ml.candidate_models --monthly 2026-10-01
"""
import argparse
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
from threadpoolctl import threadpool_limits

from backend.app.ml import artifacts
from backend.app.ml import features as F
from backend.app.ml.experiment_metrics import evaluate_predictions
from backend.app.ml.experiments import (BASELINES, FEATURE_SETS, KINDS, OPTIONAL, ROOT, ExperimentConfig, ModelSpec,
                                        build_model, load_snapshot, prepare_scenarios)
from backend.app.ml.feature_recovery import coverage
from backend.app.ml.model_registry import read_json, register_records, registered_versions, sha256, write_json
from backend.app.ml.runtime import runtime_versions

OUT_DIR = ROOT / "data/ml/candidates"
MONTHLY_DIR = ROOT / "data/ml/monthly_models"
FEATURE_CACHE = ROOT / "data/ml/monthly_feature_cache"  # 월이 추가되면 영향받은 기준월만 다시 계산
KST = ZoneInfo("Asia/Seoul")


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
    """수집 원본으로 전체 피처를 만든다. 이번 수집 월이 학습 자료에 없으면 멈춘다."""
    ds, data, source = artifacts.load_training_data(cache_dir=cache_dir, engine=engine)
    del data
    if pd.Timestamp(source["raw_period_end"]) < month:
        raise ValueError(f"수집 원본이 {source['raw_period_end']}까지만 있어 {month.date()} 수집분으로 학습할 수 없습니다")
    frame = ds[needed_columns()]
    frame.attrs = {"source": source}
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


def _same_inputs(snapshot, files, db):
    """저장된 학습 계보(input_snapshot)와 지금 입력이 같은 원본인지.

    state.json은 실행 단계·ML 결과가 실행마다 바뀌는 체크포인트라 뺀다. 데이터를 정하는 collection_dir는
    선택된 CSV의 경로·해시로 이미 비교된다. DB 환율·통화 매핑도 비교하되, 환율을 못 읽은 빈 표(DB 장애)는
    정정으로 보지 않는다. 그때 다시 학습하면 환율 피처 없는 모델이 새 버전으로 남는다.
    """
    def data(rows):
        return [f for f in rows if not f["path"].endswith("/state.json")]
    if data(snapshot["files"]) != data(files):
        return False
    if db["fx"]["rows"] == 0 and snapshot["fx"]["rows"] > 0:
        return True
    return snapshot["fx"] == db["fx"] and snapshot["country_mapping_sha256"] == db["country_mapping_sha256"]


def _version_key(path, kind):
    """<모델>_<YYYYMM>[_r<n>].registry.json → (YYYYMM, n). 이름 정렬로는 _r10이 _r2보다 앞선다."""
    match = re.fullmatch(rf"{re.escape(kind)}_(\d{{6}})(?:_r(\d+))?\.registry\.json", path.name)
    return (match[1], int(match[2] or 1)) if match else None


def run(trade_month, engine=None, *, kind="lightgbm", out_dir=MONTHLY_DIR, cache_dir=FEATURE_CACHE, n_jobs=2):
    """customs_monthly ML 단계: 전체 재학습 → <모델>_<YYYYMM>.joblib → ml_model_version(model_a) 기록.

    같은 달을 입력 파일 그대로 다시 실행하면 재학습하지 않고 저장해 둔 기록만 DB에 다시 보낸다(같은 내용이면 그대로 둔다).
    DB 기록이 실패해도 모델 파일과 기록 JSON은 남아 다음 재시도에서 기록만 다시 보낸다.
    같은 달 원본(수집 파일·DB 환율)이 정정됐으면 <모델>_<YYYYMM>_r2, _r3 …으로 새로 학습한다 (DB 이력은 덮어쓰지 않는다).
    """
    month = pd.Timestamp(trade_month)
    if month != month.to_period("M").to_timestamp():
        raise ValueError("trade_month는 월 초(YYYY-MM-01)여야 합니다")
    base = f"{kind}_{month:%Y%m}"
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(out_dir / f".{base}.lock")):
        files = db = None
        matched = None
        for revision in range(1, 100):
            name = base if revision == 1 else f"{base}_r{revision}"
            artifact, record_path = out_dir / f"{name}.joblib", out_dir / f"{name}.registry.json"
            record = read_json(record_path)
            if record is None:
                break
            if files is None:  # 비교할 기록이 있을 때만 입력 파일 전체를 해시한다
                files, db = artifacts.input_manifest(), artifacts.db_inputs(engine)  # 학습·기록과 같은 DB
            if _same_inputs(record["data_info"]["input_snapshot"], files, db):
                matched = revision, name, artifact, record_path, record
                # 환율을 못 읽으면 파일만 비교하므로 정정 전·후 리비전이 모두 맞는다. 그때는 끝까지 보고 가장 최신을 고른다.
                if db["fx"]["rows"] > 0:
                    break
        else:
            if matched is None:
                raise ValueError(f"{base}: 같은 달 재학습 기록이 너무 많습니다")
        if matched is not None:
            revision, name, artifact, record_path, record = matched
        if record is None:
            # 부모는 DB에 기록된 직전 버전. 학습 전에 정해 DB에 못 닿으면 학습하기 전에 멈춘다.
            current = (f"{month:%Y%m}", revision)
            earlier = sorted((key, read_json(p)["version_id"]) for p in out_dir.glob(f"{kind}_*.registry.json")
                             if (key := _version_key(p, kind)) is not None and key < current)
            in_db = registered_versions([version for _, version in earlier], "model_a", engine)
            parent = next((version for _, version in reversed(earlier) if version in in_db), None)
            rows = prepare_rows(_month_frame(month, cache_dir, engine))
            evaluation = holdout_check(rows, kind, n_jobs)
            bundle = train_full(rows["x"], rows["y"], kind, n_jobs)
            created = datetime.now(KST)
            runtime = _runtime(kind)
            bundle.update(rows["info"], version=name, trade_month=str(month.date()), runtime=runtime,
                          trained_at=created.isoformat(timespec="seconds"))
            temporary = artifact.with_suffix(".joblib.tmp")
            joblib.dump(bundle, temporary)
            temporary.replace(artifact)
            record = {"version_id": name, "model_key": "model_a", "model_name": name,
                      "created_at": created.isoformat(timespec="microseconds"), "status": "READY",
                      "artifact_path": artifact.name, "artifact_sha256": sha256(artifact),
                      "parent_version_id": parent,
                      "training_mode": "FULL_RETRAIN", "train_start": rows["info"]["train_start"],
                      "train_end": rows["info"]["train_end"], "train_rows": rows["info"]["train_rows"],
                      "data_info": _finite({"model_family": f"{kind}_full", "trade_month": str(month.date()),
                                            "artifact_root": out_dir.as_posix(), "input_snapshot": rows["source"],
                                            "runtime": runtime, "features_used": bundle["features"],
                                            "target_clip": bundle["target_clip"], "fit_seconds": bundle["fit_seconds"]}),
                      "evaluation": evaluation, "skip_reason": None}
            write_json(record_path, record)
        elif not artifact.is_file() or sha256(artifact) != record["artifact_sha256"]:
            raise ValueError(f"{artifact}: 저장된 기록과 모델 파일이 다릅니다")
        register_records([record], engine)
    return {"status": "SUCCESS", "model_name": name, "artifact": artifact.as_posix(), "train_rows": record["train_rows"],
            "country_ndcg_at_5": record["evaluation"]["model"].get("country_ndcg_at_5"),
            "baseline_country_ndcg_at_5": record["evaluation"]["baseline_growth_12m"].get("country_ndcg_at_5")}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", choices=sorted(KINDS - set(BASELINES)))
    parser.add_argument("--version", default="v1")
    parser.add_argument("--n-jobs", type=int, default=2)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/ml/features_snapshot_py311.pkl")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--monthly", metavar="YYYY-MM-01", help="월별 LightGBM 전체 재학습과 DB 기록을 직접 실행")
    args = parser.parse_args()
    if args.monthly:
        print(run(args.monthly, n_jobs=args.n_jobs), flush=True)
        return
    if not args.models:
        parser.error("--models 또는 --monthly를 지정하세요")
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
