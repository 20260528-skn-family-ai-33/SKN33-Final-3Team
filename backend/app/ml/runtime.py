"""학습 산출물의 라이브러리 버전을 확인한다. 모델 로드·학습 없이 실행 가능."""
import argparse
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PACKAGES = ("numpy", "pandas", "scipy", "scikit-learn", "joblib")
ROOT = Path(__file__).resolve().parents[3]


class ArtifactEnvironmentError(RuntimeError):
    """저장 환경과 현재 실행 환경이 맞지 않음. 재학습 오류와 구분한다."""


def runtime_versions():
    versions = {"python": platform.python_version()}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _read(path):
    return json.loads(path.read_text("utf-8")) if path.is_file() else {}


def expected_runtime(data_dir, version_id=None):
    data_dir = Path(data_dir).resolve()
    directory = (data_dir / "versions" / version_id).resolve() if version_id else data_dir
    if not directory.is_relative_to(data_dir):
        raise ValueError("유효한 버전 경로가 필요합니다")
    meta = _read(directory / "metadata.json")
    if meta.get("runtime"):
        return meta["runtime"]
    for record in meta.get("model_versions", []):
        if record.get("data_info", {}).get("runtime"):
            return record["data_info"]["runtime"]
    # 최초 모델은 metadata에 runtime이 없어, 같은 실행임을 확인한 기록만 보완 근거로 사용한다.
    run = _read(data_dir / "training_run.json")
    if not meta.get("version_id") and run.get("status") == "accepted" and run.get("evaluation") == meta.get("evaluation"):
        try:
            zone = ZoneInfo("Asia/Seoul")
            start = datetime.fromisoformat(run["started_at"])
            trained = datetime.fromisoformat(meta["trained_at"])
            start = start if start.tzinfo else start.replace(tzinfo=zone)
            trained = trained if trained.tzinfo else trained.replace(tzinfo=zone)
            if start <= trained <= start + timedelta(seconds=float(run["elapsed_seconds"]) + 60):
                return run.get("runtime", {})
        except (KeyError, TypeError, ValueError):
            pass
    return {}


def environment_message(expected, current, detail=""):
    rows = [f"{name}: 저장 환경 {version} / 현재 {current.get(name, 'not installed')}"
            for name, version in expected.items() if name in ("python", *PACKAGES)]
    return ("저장된 피처·모델과 실행 환경이 호환되지 않습니다.\n" + detail + "\n" + "\n".join(rows)
            + f"\n현재 Python: {sys.executable}\n"
            "학습 당시 버전을 설치한 별도 환경을 사용하세요. 현재 제공 산출물의 고정 버전은 "
            "backend/requirements-ml-runtime-20261022.txt에 있습니다. 환경 변경 후 Jupyter 커널을 재시작하세요. "
            "이 문제 때문에 원본 전처리나 모델 학습을 다시 실행할 필요는 없습니다.")


def ensure_artifact_runtime(data_dir, version_id=None, current=None):
    expected = expected_runtime(data_dir, version_id)
    current = runtime_versions() if current is None else current
    differences = []
    for name, version in expected.items():
        if name not in ("python", *PACKAGES):
            continue
        actual = current.get(name, "not installed")
        mismatch = (str(version).split(".")[:2] != str(actual).split(".")[:2]) if name == "python" else version != actual
        if mismatch:
            differences.append(name)
    if differences:
        raise ArtifactEnvironmentError(environment_message(expected, current, "불일치: " + ", ".join(differences)))
    return {"expected": expected, "current": current, "recorded": bool(expected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/ml")
    args = parser.parse_args()
    try:
        result = ensure_artifact_runtime(args.data_dir)
    except ArtifactEnvironmentError as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
