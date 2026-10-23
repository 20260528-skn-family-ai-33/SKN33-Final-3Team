"""버전별 로컬 기록을 DB에 동기화한다. 모델 학습·원본 데이터 DB 조회는 하지 않는다."""
import argparse
import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[3]
log = logging.getLogger(__name__)


def read_json(path, default=None):
    return json.loads(Path(path).read_text("utf-8")) if Path(path).exists() else default


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def register_records(records, engine=None):
    """두 모델을 한 트랜잭션으로 기록. 같은 내용 재전송은 허용하되 기존 이력은 덮어쓰지 않는다."""
    from backend.app.db.session import make_engine, session_scope
    from backend.app.models.ml_model_version import MlModelVersion
    own_engine = engine is None
    engine = engine if engine is not None else make_engine()
    try:
        with session_scope(engine) as session:
            for record in records:
                row = dict(record)
                row["created_at"] = datetime.fromisoformat(row["created_at"]).astimezone(
                    ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
                for field in ("train_start", "train_end"):
                    row[field] = date.fromisoformat(row[field]) if row[field] else None
                existing = session.get(MlModelVersion, (row["version_id"], row["model_key"]))
                if existing is None:
                    session.add(MlModelVersion(**row))
                elif any(getattr(existing, key) != value for key, value in row.items()):
                    raise ValueError(f"DB의 기존 버전 내용이 다릅니다: {row['version_id']}/{row['model_key']}")
    finally:
        if own_engine:
            engine.dispose()


def sync_version(version_dir, engine=None):
    """DB 장애 시 PENDING을 남긴다. 재시도는 학습 없이 이 함수/CLI만 실행한다."""
    version_dir = Path(version_dir).resolve()
    with FileLock(str(version_dir / ".registry.lock")):
        try:
            records = read_json(version_dir / "registry.json")
            if not records or {r["model_key"] for r in records} != {"model_a", "model_b"} or len(records) != 2:
                raise ValueError("모델 A·B 두 개의 기록이 필요합니다")
            for record in records:
                if record["version_id"] != version_dir.name:
                    raise ValueError("버전 폴더와 DB 기록의 ID가 다릅니다")
                if record["status"] == "READY":
                    artifact = (version_dir.parent.parent / record["artifact_path"]).resolve()
                    if not artifact.is_relative_to(version_dir) or sha256(artifact) != record["artifact_sha256"]:
                        raise ValueError("버전 모델 파일이 변경됐거나 경로가 잘못됐습니다")
            register_records(records, engine)
            result = {"status": "SYNCED", "synced_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat()}
        except Exception as exc:
            # 접속 예외 본문에는 URL·접속 정보가 포함될 수 있어 로컬 상태에는 예외 종류만 기록한다.
            result = {"status": "PENDING", "error_type": type(exc).__name__}
            log.warning("모델 버전 DB 기록 보류(%s). 모델 파일은 보존됨: %s", type(exc).__name__, version_dir)
        write_json(version_dir / "registry_sync.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sync"])
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/ml")
    args = parser.parse_args()
    versions = sorted(p for p in (args.data_dir / "versions").glob("*") if (p / "registry.json").is_file())
    if not versions:
        parser.exit(1, "동기화할 버전 기록이 없습니다.\n")
    failed = 0
    for directory in versions:
        if read_json(directory / "registry_sync.json", {}).get("status") == "SYNCED":
            continue
        result = sync_version(directory)
        print(directory.name, result["status"])
        failed += result["status"] != "SYNCED"
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
