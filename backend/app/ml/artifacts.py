"""모델 A·B의 날짜 버전, 학습 데이터 계보, 최신 호환 산출물을 함께 저장한다."""
import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from filelock import FileLock

from backend.app.ml import features as F, sources as S
from backend.app.ml.model_registry import read_json, sha256, sync_version, write_json
from backend.app.ml.runtime import PACKAGES, runtime_versions

ROOT = Path(__file__).resolve().parents[3]
log = logging.getLogger(__name__)


def input_manifest():
    """실제로 선택할 수집 CSV·체크포인트·기준 파일의 경로와 내용을 기록한다. DB는 읽지 않는다."""
    paths = set()
    for folder in S.CUSTOMS_DIRS:
        for csv in S._customs_files(folder):
            paths.add(csv)
            paths.update(csv.parent.glob("customs_collection_*.json"))
            paths.update(p for p in [csv.parent / "collection.json", csv.parent.parent / "state.json"] if p.is_file())
    paths.update(S.SEED_DIR.glob("*country*seed*.sql"))
    paths.update([S.HSCODE_DIR / "HS2017/hs_code_hs2017.csv", S.HSCODE_DIR / "correlation/HS2022toHS2017.xlsx",
                  S.KEYWORD_CSV, ROOT / "backend/app/ml/reference/related_hs4.csv"])
    paths.update((S.COLLECTORS / "hscode/reference").glob("hs_names_*.xlsx"))
    raw_news = sorted(F.SHORTNEWS_DIR.glob("*/shortnews_raw.csv"))
    paths.update(raw_news[-1:])
    paths.update([F.SHORTNEWS_DIR / "short_news_analysis.jsonl", F.SHORTNEWS_DIR / "short_news_topics.jsonl"])
    external = ROOT / "data/external"
    paths.update([external / "availability.csv", external / "availability_snapshot.json"])
    paths.update(p for files in F.pp.external_files(external).values() for p in files)
    return [{"path": p.resolve().relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(paths) if p.is_file()]


def db_inputs(engine=None):
    """load_training_data가 DB에서 읽는 환율·통화 매핑의 지문. 같은 이름으로 학습 계보(info)에도 남는다."""
    fx, currencies = S.fx_from_db(engine)
    return {"fx": {"rows": len(fx), "sha256": _frame_digest(fx)},
            "country_mapping_sha256": _frame_digest(F.model_countries(S.countries(), currencies))}


def load_training_data(cache_dir=None, engine=None):
    """입력 파일이 처리 중 바뀌면 버전 계보에 다른 스냅샷을 기록하지 않도록 중단한다."""
    before = input_manifest()
    data = F.load_data(engine=engine)
    cache = None
    if cache_dir is None:
        ds = F.build_dataset(data)
    else:
        from backend.app.ml.feature_cache import build_cached
        ds, cache = build_cached(data, cache_dir)
    if before != input_manifest():
        raise ValueError("피처 생성 중 입력 파일이 변경됐습니다. 수집 완료 후 다시 실행하세요.")
    info = {"provenance": "collector_files", "files": before,
            "customs_complete_months": sorted(str(pd.Timestamp(m).date()) for m in data["complete_months"]),
            "raw_period_start": str(pd.to_datetime(data["exports"].month).min().date()),
            "raw_period_end": str(pd.to_datetime(data["exports"].month).max().date()),
            "fx": {"rows": len(data["fx"]), "sha256": _frame_digest(data["fx"])},
            "country_mapping_sha256": _frame_digest(data["countries"]),
            "pipeline_sources": {p: sha256(ROOT / p) for p in
                ("backend/app/ml/features.py", "backend/app/ml/sources.py", "backend/app/ml/preprocess.py",
                 "backend/app/ml/temporal.py", "backend/app/ml/related_groups.py",
                 "backend/app/ml/market_model.py", "backend/app/ml/transforms.py")}}
    if cache is not None:
        info["feature_cache"] = cache
    return ds, data, info


def _frame_digest(rows):
    # 행 순서는 무시하되 키·값·결측은 포함. pandas 버전도 메타정보에 함께 저장한다.
    values = pd.util.hash_pandas_object(rows, index=False).to_numpy(dtype=np.uint64)
    return hashlib.sha256(np.sort(values).astype("<u8").tobytes()).hexdigest()


def dataset_info(rows):
    columns = [c for c in ["anchor", "country_code", "hs6", *F.FEATURES, "y_growth", "training_target_b"] if c in rows]
    if rows.duplicated(["anchor", "country_code", "hs6"]).any():
        raise ValueError("학습 데이터의 국가·HS6·기준월 키가 중복됩니다")
    anchors = {str(pd.Timestamp(anchor).date()): {"rows": len(group), "sha256": _frame_digest(group[columns])}
               for anchor, group in rows.groupby("anchor", sort=True)}
    return {"rows": len(rows), "start": min(anchors) if anchors else None, "end": max(anchors) if anchors else None,
            "countries": int(rows.country_code.nunique()), "products": int(rows.hs6.nunique()),
            "anchors": anchors, "digest_columns": columns,
            "sha256": hashlib.sha256(json.dumps(anchors, sort_keys=True).encode()).hexdigest()}


def data_changes(current, previous):
    """신규 기준월의 행 수와 기존 기준월 내용 변경을 구분한다. 순증을 신규 행 수로 표현하지 않는다."""
    old = previous.get("dataset", {})
    anchors, old_anchors = current["dataset"]["anchors"], old.get("anchors", {})
    new = sorted(set(anchors) - set(old_anchors))
    files = {f["path"]: f["sha256"] for f in current["input_snapshot"].get("files", [])}
    old_files = {f["path"]: f["sha256"] for f in previous.get("input_snapshot", {}).get("files", [])}
    available = bool(previous)
    return {"comparison_available": available,
            "new_anchor_months": new if available else None,
            "new_anchor_rows": sum(anchors[a]["rows"] for a in new) if available else None,
            "changed_anchor_months": sorted(a for a in set(anchors) & set(old_anchors)
                                            if anchors[a] != old_anchors[a]) if available else None,
            "removed_anchor_months": sorted(set(old_anchors) - set(anchors)) if available else None,
            "row_count_difference": current["dataset"]["rows"] - old.get("rows", 0) if available else None,
            "added_files": sorted(set(files) - set(old_files)) if available else None,
            "changed_files": sorted(p for p in set(files) & set(old_files) if files[p] != old_files[p]) if available else None,
            "removed_files": sorted(set(old_files) - set(files)) if available else None,
            "note": "최초 버전·이전 입력 기록 없음은 신규로 단정하지 않음. 신규 기준월 행 수는 기존 월의 신규 쌍 수를 포함하지 않음."}


def _copy_atomic(source, destination):
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def save_version(models, predictions, metadata, training_sets, out_dir, data_info=None, eligible_b_rows=0,
                 registry_engine=None, register=True, extra_artifacts=None, model_details=None):
    """새 디렉터리를 완성한 뒤 최신 포인터를 바꾼다. DB 실패는 재전송 가능한 로컬 기록으로 보존한다."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = data_info or {"provenance": "features_only", "files": [],
                             "note": "호출자가 원본 정보를 제공하지 않아 피처 테이블만 기록함"}
    # 모델별 학습 집합을 각각 계산한다. B 미생성은 실제 학습 0행이다.
    datasets = {key: dataset_info(rows) for key, rows in training_sets.items()}
    with FileLock(str(out_dir / ".publish.lock")):
        previous = read_json(out_dir / "metadata.json", {})
        previous_models = {r["model_key"]: r for r in previous.get("model_versions", [])}
        created = datetime.now(ZoneInfo("Asia/Seoul"))
        version_id = created.strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:8]
        directory = out_dir / "versions" / version_id
        directory.mkdir(parents=True, exist_ok=False)
        records = []
        runtime = runtime_versions()
        for key in ("model_a", "model_b"):
            model = models[key]
            name = f"{key}_{version_id}"
            artifact = directory / f"{name}.joblib"
            if model is not None:
                joblib.dump(model, artifact)
            info = {"input_snapshot": snapshot, "dataset": datasets[key], "runtime": runtime,
                    "artifact_root": out_dir.as_posix(),
                    "eligible_rows": eligible_b_rows if key == "model_b" else datasets[key]["rows"],
                    "history_months": F.HISTORY, "target_horizon_months": F.HORIZON,
                    "features_used": model[0].keep_ if model is not None else [],
                    "type_thresholds": metadata.get("thresholds", {}) if key == "model_b" else None,
                    "target_clip": metadata.get("training_target_clip") if key == "model_a" else None}
            info["data_changes"] = data_changes(info, previous_models.get(key, {}).get("data_info", {}))
            info.update((model_details or {}).get(key, {}))
            evaluation = metadata["evaluation"]
            metrics = {k: evaluation[k] for k in (("model_a", "baseline_a", "model_a_product", "baseline_a_product")
                       if key == "model_a" else ("model_b", "baseline_b", "model_b_all")) if k in evaluation}
            records.append({"version_id": version_id, "model_key": key, "model_name": name,
                "created_at": created.isoformat(timespec="microseconds"), "status": "READY" if model is not None else "SKIPPED",
                "artifact_path": artifact.relative_to(out_dir).as_posix() if model is not None else None,
                "artifact_sha256": sha256(artifact) if model is not None else None,
                "parent_version_id": previous_models.get(key, {}).get("version_id"),
                "training_mode": info.get("training_mode", "FULL_RETRAIN"),
                "train_start": datasets[key]["start"], "train_end": datasets[key]["end"], "train_rows": datasets[key]["rows"],
                "data_info": info, "evaluation": metrics,
                "skip_reason": None if model is not None else "라벨·비교 평가 부족 또는 기준선 미달"})
        # 날짜 없는 파일은 호환용 최신 복사본. 이력과 조회의 기준은 버전 디렉터리와 metadata 포인터다.
        joblib.dump(models, directory / "models.joblib")
        extra_hashes = {}
        for filename, value in (extra_artifacts or {}).items():
            if Path(filename).name != filename or filename != "incremental_state.joblib":
                raise ValueError("허용되지 않은 학습 체크포인트 이름")
            joblib.dump(value, directory / filename)
            extra_hashes[filename] = sha256(directory / filename)
        predictions.to_csv(directory / "predictions.csv", index=False)
        meta = {**metadata, "version_id": version_id, "created_at": created.isoformat(timespec="microseconds"),
                "runtime": runtime, "extra_artifacts_sha256": extra_hashes,
                "created_timezone": "Asia/Seoul", "model_versions": records,
                "version_directory": directory.relative_to(out_dir).as_posix(),
                "registry_status_file": (directory / "registry_sync.json").relative_to(out_dir).as_posix()}
        # 첫 전환 때는 날짜 없는 기존 모델·예측·메타정보도 보존한다.
        legacy_pointer = None
        if previous and not previous.get("version_id") and not previous.get("version_directory"):
            legacy = out_dir / "versions" / ("legacy_" + version_id)
            legacy.mkdir()
            for name in ("models.joblib", "predictions.csv", "metadata.json"):
                if (out_dir / name).is_file():
                    shutil.copyfile(out_dir / name, legacy / name)
            meta["legacy_snapshot"] = legacy.relative_to(out_dir).as_posix()
            legacy_pointer = {**previous, "version_directory": meta["legacy_snapshot"]}
        elif previous and not previous.get("version_id"):
            meta["legacy_snapshot"] = previous["version_directory"]
        write_json(directory / "metadata.json", meta)
        (directory / "runtime-requirements.txt").write_text(
            "# Python " + runtime["python"] + "\n" + "\n".join(f"{p}=={runtime[p]}" for p in PACKAGES) + "\n",
            encoding="utf-8")
        write_json(directory / "registry.json", records)
        write_json(directory / "registry_sync.json", {"status": "PENDING"})
        if legacy_pointer is not None:
            # 첫 전환 중 최신 복사본 쓰기가 실패해도 기존 모델·예측은 아카이브를 계속 가리킨다.
            write_json(out_dir / "metadata.json", legacy_pointer)
        _copy_atomic(directory / "models.joblib", out_dir / "models.joblib")
        _copy_atomic(directory / "predictions.csv", out_dir / "predictions.csv")
        _copy_atomic(directory / "metadata.json", out_dir / "metadata.json")  # 최신 포인터를 마지막에 교체
    if register:
        result = sync_version(directory, registry_engine)
        log.info("모델 버전 %s | DB %s", version_id, result["status"])
    for record in records:
        log.info("%s | %s | 학습 %s행", record["model_name"], record["status"], record["train_rows"])
    return meta


def artifact_path(out_dir, name, metadata=None, version_id=None):
    """새 버전은 불변 디렉터리에서, 기존 버전은 날짜 없는 경로에서 읽는다."""
    out_dir = Path(out_dir).resolve()
    meta = metadata if metadata is not None else read_json(out_dir / "metadata.json", {})
    directory = str(Path("versions") / version_id) if version_id else meta.get("version_directory")
    path = (out_dir / directory / name).resolve() if directory else out_dir / name
    if not path.is_relative_to(out_dir):
        raise ValueError("모델 산출물 경로가 출력 폴더 밖입니다")
    if version_id and path.parent.parent != out_dir / "versions":
        raise ValueError("유효한 단일 버전 ID가 필요합니다")
    return path
