"""학습(선택) → 검증 노트북 실행 → PDF 자동 생성. 저장소 루트에서 -m으로 실행한다."""
import argparse
import gc
import json
import logging
import os
import sys
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
NOTEBOOK = ROOT / "notebooks/ml_validation_report.ipynb"


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def train_for_report(data_dir, feature_cache, promotion_metric):
    from backend.app.ml import market_model as M
    from backend.app.ml.artifacts import load_training_data
    from backend.app.ml.validation import feature_summary, file_sha256
    attempt_path = data_dir / "report_training_attempt.json"
    attempt = {"started_at": now(), "status": "running"}
    write_json(attempt_path, attempt)
    try:
        ds, data, data_info = load_training_data()
        summary = feature_summary(ds, len(data["complete_months"]))
        summary["source_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): file_sha256(p)
            for p in (ROOT / "backend/app/ml/features.py", ROOT / "backend/app/ml/sources.py")}
        feature_cache.parent.mkdir(parents=True, exist_ok=True)
        ds.to_pickle(feature_cache)
        write_json(data_dir / "features_snapshot_summary.json", summary)
        del data
        gc.collect()
        metadata = M.train(ds, data_dir, promotion_metric=promotion_metric, data_info=data_info)
        attempt.update(status="accepted", trained_at=metadata["trained_at"], evaluation=metadata["evaluation"])
    except M.PromotionRejected as exc:
        attempt.update(status="rejected", error=str(exc), evaluation=exc.report)
    except Exception as exc:
        attempt.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        attempt["finished_at"] = now()
        write_json(attempt_path, attempt)
    return attempt


def execute_notebook(config, notebook_path=NOTEBOOK, timeout=1800):
    """사용 중인 Python으로 격리된 임시 커널을 띄운다. 전역 kernelspec은 변경하지 않는다."""
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager

    nb = nbformat.read(notebook_path, as_version=4)
    nb.cells.insert(1, nbformat.v4.new_code_cell("RUN_CONFIG = " + repr(config)))
    output_dir = Path(config["output_dir"])
    executed = output_dir / "ml_validation_report.executed.ipynb"
    # 커널의 자식 프로세스도 현재 환경과 저장소 import 경로를 사용한다.
    kernel_env = os.environ.copy()
    kernel_env["PYTHONPATH"] = str(ROOT) + os.pathsep + kernel_env.get("PYTHONPATH", "")
    kernel_env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="ml-report-kernel-", dir=output_dir) as directory:
        kernel_base = Path(directory).resolve()
        if not kernel_base.is_relative_to(output_dir.resolve()):
            raise ValueError("임시 커널 경로가 보고서 출력 폴더 밖입니다")
        name = "ml-report"
        spec = kernel_base / name
        spec.mkdir()
        write_json(spec / "kernel.json", {"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            "display_name": "ML report (current Python)", "language": "python"})
        manager = KernelManager(kernel_name=name, kernel_spec_manager=KernelSpecManager(
            kernel_dirs=[str(kernel_base)], ensure_native_kernel=False))
        client = NotebookClient(nb, km=manager, timeout=timeout, kernel_name=name,
            resources={"metadata": {"path": str(ROOT)}}, allow_errors=False)

        async def progress(cell, cell_index, **kwargs):
            if cell.cell_type == "code":
                print(f"[notebook {cell_index + 1}/{len(nb.cells)}] {cell.source.splitlines()[0][:90]}", flush=True)

        client.on_cell_start = progress
        try:
            client.execute(env=kernel_env, cleanup_kc=True)
        finally:
            nbformat.write(nb, executed)
    return executed


def failure_report(config, exc):
    """셀 실행 오류도 새 실행 ID로 기록한다. 지난 성공 PDF를 현재 결과로 보이지 않게 한다."""
    from backend.app.ml.validation import load_context, read_json
    from backend.app.ml.reporting import generate_report
    path = Path(config["output_dir"]) / "ml_report_validation.json"
    validation = read_json(path, {})
    if validation.get("run_id") != config["run_id"]:
        validation = {"run_id": config["run_id"], "generated_at": now(), "checks": [], "unit_tests": {}, "errors": []}
    validation.setdefault("errors", []).append(f"노트북 실행 실패: {type(exc).__name__}: {str(exc)[-3000:]}")
    try:
        context = load_context(Path(config["data_dir"]))
    except Exception as context_error:
        context = {"run_status": "failed", "error": str(context_error)}
    validation["trained_at"] = context.get("metadata", {}).get("trained_at")
    write_json(path, validation)
    generate_report(context, validation, Path(config["output_dir"]) / "ml_market_report.pdf")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", action="store_true", help="원본 수집 파일부터 피처를 만들고 학습 후 검증")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/ml")
    parser.add_argument("--feature-cache", type=Path, help="직접 생성한 신뢰할 수 있는 피처 pickle만 지정")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/pdf")
    parser.add_argument("--cv", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=1800, help="노트북 셀별 제한 초")
    args = parser.parse_args(argv)
    if args.folds < 2 or args.timeout < 1:
        parser.error("folds는 2 이상, timeout은 1 이상이어야 합니다")
    args.data_dir = args.data_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    feature_cache = (args.feature_cache or args.data_dir / "features_snapshot.pkl").resolve()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.train:
        result = train_for_report(args.data_dir, feature_cache, "country_ndcg_at_5")
        print(f"학습 상태: {result['status']}", flush=True)
        gc.collect()
    config = {"run_id": str(uuid.uuid4()), "data_dir": str(args.data_dir), "feature_cache": str(feature_cache),
              "output_dir": str(args.output_dir), "cv": args.cv, "folds": args.folds}
    try:
        if not args.train:
            from backend.app.ml.runtime import ensure_artifact_runtime
            ensure_artifact_runtime(args.data_dir)
        executed = execute_notebook(config, timeout=args.timeout)
    except Exception as exc:
        failure_report(config, exc)
        print(f"검증 실패. PDF와 실행 노트북을 확인하세요: {args.output_dir}\n{exc}", file=sys.stderr)
        return 1
    print(f"PDF: {args.output_dir / 'ml_market_report.pdf'}\n실행 노트북: {executed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
