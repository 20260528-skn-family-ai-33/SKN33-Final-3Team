"""실행: python -m backend.app.scheduler.jobs.customs_load --year 2025

기본 CSV: 저장소 루트/data/raw/customs/customs_raw_<연도>.csv

HS 상위 사전을 준비한 뒤, 쓰기 없이 자동 보완 계획까지 검증할 때:
python -m backend.app.scheduler.jobs.customs_load --year 2022 --mode check \
    --hs-ver auto --unmapped-policy hold --invalid-policy hold
auto는 기존 HS10 또는 상위 HS2·HS4·HS6가 모두 있는 유일 판본을 사용한다.
실제 적재는 같은 옵션에서 --mode load로 명시적으로 선택한다.
"""
import argparse
import json
import sys
import tempfile
from time import perf_counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.db.session import ENV_PATH, make_engine, mysql_url
from backend.app.services.customs_load import Loader, job_logging, safe_error
from backend.app.services.customs_stage import Stage, ValidationError
from backend.app.services.customs_metrics import MetricCalculator
from backend.app.services.customs_reference import plan_hs_references

DEFAULT_OUTPUT = Path(__file__).resolve().parents[4] / "data/customs_load_outputs"
DEFAULT_CSV_DIR = ENV_PATH.parent / "data" / "raw" / "customs"


def run(args, *, return_report=False):
    started = perf_counter()
    work = args.output / datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M%S_%f")
    work.mkdir(parents=True, exist_ok=False)
    report = {"year": args.year, "file": args.csv.name, "mode": args.mode,
              "hs_ver": args.hs_ver, "status": "RUNNING", "database_loaded": False,
              "dictionary_changed": False,
              "unmapped_policy": getattr(args, "unmapped_policy", "fail"), "published_months": []}
    with job_logging(work / "customs_load.log") as logger, \
            (work / "events.jsonl").open("w", encoding="utf-8") as events:
        def log(event, **values):
            payload = {"at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
                       "event": event, **values}
            events.write(json.dumps(payload, ensure_ascii=False) + "\n")
            events.flush()
            logger.info("%s %s", event, json.dumps(values, ensure_ascii=False))
            if event == "month_success":
                report["database_loaded"] = True
                report["dictionary_changed"] |= bool(values.get("ensured_hs10"))
                report["published_months"].append({**values, "status": "SUCCESS"})
            elif event in ("commit_unknown", "metrics_commit_unknown"):
                report["commit_unknown"] = values
            elif event == "reference_validated":
                report["dictionary_verification"] = "PASSED"

        try:
            # 출력 폴더에 자동 정리할 작업 영역을 만든다. 결과와 오류 로그는 보존한다.
            with tempfile.TemporaryDirectory(prefix="stage_", dir=work) as temporary, \
                    Stage(Path(temporary) / "trade.sqlite3") as stage:
                invalid_output = work / "invalid_rows.csv" if getattr(args, "invalid_policy", "fail") == "hold" else None
                report.update(stage.ingest(args.csv, args.year, work / "errors.jsonl", args.batch_size,
                                          invalid_output=invalid_output))
                log("csv_validated", **{k: report[k] for k in (
                    "rows_total", "rows_valid", "rows_total_line", "invalid_rows", "duplicate_keys")})
                report["months"] = stage.summary()
                countries, codes = stage.references()
                report["reference_counts"] = {"countries": len(countries),
                                               "hs10": sum(len(c) == 10 for c in codes),
                                               "hs6": sum(len(c) == 6 for c in codes)}
                report["dictionary_verification"] = "NOT_RUN"
                report["missing_months"] = [f"{args.year:04d}-{m:02d}-01" for m in range(1, 13)
                                            if f"{args.year:04d}-{m:02d}-01" not in stage.months()]
                if not stage.validated:
                    raise ValidationError("무효·중복 행이 있거나 유효 행이 0건임; errors.jsonl 확인")
                if args.mode in ("check", "load"):
                    engine = make_engine(mysql_url(args.env))
                    try:
                        with engine.connect() as connection:
                            result = connection.exec_driver_sql("SELECT hs_code,hs_ver FROM hs_code")
                            versions, missing, additions = plan_hs_references(stage, result.fetchall(), args.hs_ver)
                            connection.rollback()
                            report["hs_reference_errors"] = missing
                            if missing and getattr(args, "unmapped_policy", "fail") == "fail":
                                report["dictionary_verification"] = "FAILED"
                                log("reference_missing", hs_code=missing, hs_ver=args.hs_ver)
                                raise ValidationError("HS10/동일 판본 HS6 누락 또는 복수 판본; 사전과 로그 확인")
                            if getattr(args, "unmapped_policy", "fail") == "hold":
                                report["input_months"] = report["months"]
                                report.update(stage.hold_unmapped_hs(args.csv, args.year,
                                    work / "held_rows.csv", missing))
                                log("hs_rows_held", rows=report["rows_held"], codes=report["held_hs10"])
                            stage.hs_versions = versions
                            stage.hs_additions = additions
                            report["hs10_supplement_plan"] = [
                                {"hs_code": c, "hs_ver": v, "name_ko": n, "source_edition": s,
                                 "basis": "same-version HS2/HS4/HS6"}
                                for c, v, n, s in additions.values()]
                            report["months"] = stage.summary()
                            report["rows_eligible"] = report["rows_valid"] - report.get("rows_held", 0)
                            report["hs10_codes_by_version"] = {
                                version: sum(v == version for v in versions.values())
                                for version in sorted(set(versions.values()))}
                            # check는 쓰지 않는다. load에서만 신규 HS10과 실적을 같은 월별 트랜잭션으로 반영한다.
                            loader = Loader(connection, log, logger=logger)
                            if args.mode == "load":
                                # 잠금 내부에서 스키마·사전을 최종 검증한다. 바깥에서 중복 점검하지 않는다.
                                loader.load(stage, args.hs_ver, args.source_code, args.batch_size)
                                if getattr(args, "calculate_metrics", True):
                                    report["metrics"] = MetricCalculator(connection, log, logger=logger).calculate(args.year, args.batch_size)
                                report["database_loaded"] = True
                                report["dictionary_verification"] = "PASSED"
                            else:
                                # check는 이미 읽은 사전 계획을 재사용한다. 보류된 코드는 검증 대상에서 제외한다.
                                loader.preflight(stage, args.hs_ver, args.source_code,
                                    hs_plan=(versions, {} if getattr(args, "unmapped_policy", "fail") == "hold" else missing,
                                             additions))
                    finally:
                        engine.dispose()
                report["status"] = {"load": "LOADED", "check": "CHECKED", "validate": "VALIDATED"}[args.mode]
                if report.get("rows_held") or report.get("invalid_rows_held"):
                    report["status"] += "_WITH_HOLDS"
                log("completed", status=report["status"])
        except Exception as exc:
            report["status"] = "COMMIT_UNKNOWN" if report.get("commit_unknown") else "FAILED"
            report["error"] = safe_error(exc)
            report["retryable"] = report["error"]["retryable"]
            log("failed", **report["error"])
        finally:
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if return_report:
        return report
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"결과 위치: {work}")
    return 0 if report["status"].removesuffix("_WITH_HOLDS") in ("VALIDATED", "CHECKED", "LOADED") else 1


def main():
    parser = argparse.ArgumentParser(description="관세청 CSV 한 연도 검증 / 월별 MySQL 적재")
    parser.add_argument("--csv", type=Path,
                        help="CSV 경로 (기본: 저장소 루트/data/raw/customs/customs_raw_<year>.csv)")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--mode", choices=("validate", "check", "load"), default="validate",
                        help="validate: CSV만 검증, check: DB 사전 조회까지 검증(쓰기 없음), load: 실제 적재")
    parser.add_argument("--invalid-policy", choices=("fail", "hold"), default="fail",
                        help="hold: 형식 오류 행 원문 보류. 업무 키 중복·열 수 오류는 항상 중단")
    parser.add_argument("--unmapped-policy", choices=("fail", "hold"), default="fail",
                        help="기본 fail: 누락 시 전체 중단. hold: 미연결 HS 행 원문 보존 후 나머지 처리")
    parser.add_argument("--hs-ver", help="원천 판본(예: HS2022) 또는 auto(기존 HS10/상위 사전에서 유일한 판본); check/load 필수")
    parser.add_argument("--source-code", default="CUSTOMS")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.mode in ("check", "load") and not args.hs_ver:
        parser.error("check/load 모드는 --hs-ver를 명시해야 합니다 (연도에서 자동 추정하지 않음).")
    if args.unmapped_policy == "hold" and args.mode == "validate":
        parser.error("HS 보류는 DB 사전과 대조하는 check/load 모드에서만 사용합니다.")
    if args.batch_size <= 0 or not 1 <= args.year <= 9999:
        parser.error("batch-size는 양수, year는 1~9999이어야 합니다.")
    if args.csv is None:
        args.csv = DEFAULT_CSV_DIR / f"customs_raw_{args.year}.csv"
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
