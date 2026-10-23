"""매일 03:00 KST에 전날 단신속보 전체 페이지를 수집하고 요약·분류 후 MySQL·Chroma에 적재합니다."""
import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.app.db.news_lock import database_identity

ROOT = Path(__file__).resolve().parents[4]
ENV_PATH = ROOT / ".env"
OUTPUT_DIR = ROOT / "data/shortnews/daily"
LOCK_DIR = ROOT / "data/shortnews/locks"
KST = ZoneInfo("Asia/Seoul")
JOB_ID = "shortnews_daily"
# DB 주소가 같아도 출처·본문 처리 계약이 달라지면 과거 완료 기록을 다시 검증합니다.
PIPELINE_REVISION = "KOTRA_SHORT_NEWS:full-body-db-candidates-mysql-chroma:v2"
log = logging.getLogger(__name__)


def register_jobs(scheduler):
    """정기·시작 복구 일정을 등록합니다. 등록 자체로 API·DB를 호출하지 않습니다."""
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(run, CronTrigger(hour=3, minute=0, second=0, timezone=KST),
                      id=JOB_ID, max_instances=1, coalesce=True,
                      misfire_grace_time=6 * 3600, replace_existing=True)
    scheduler.add_job(run, "date", run_date=datetime.now(KST) + timedelta(seconds=10),
                      id="shortnews_startup", max_instances=1, coalesce=True,
                      misfire_grace_time=6 * 3600, replace_existing=True)


def save_result(directory, result):
    atomic_json(directory / "job_result.json", result)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_progress(progress):
    if not isinstance(progress, dict) or not isinstance(progress.get("days"), dict):
        raise ValueError("단신속보 재개 상태 형식 오류")
    canonical_day(progress["start_date"])
    for day, status in progress["days"].items():
        canonical_day(day)
        if status not in ("RUNNING", "SUCCESS", "FAILED", "PARTIAL"):
            raise ValueError("단신속보 재개 상태 값 오류")


def canonical_day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("재개 날짜는 YYYY-MM-DD 형식이어야 합니다")
    return value


def read_state(root, yesterday, database_id):
    path = root / "state.json"
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("단신속보 재개 상태 형식 오류")
        if state.get("schema_version") in (2, 3):
            canonical_day(state["start_date"])
            if not isinstance(state.get("databases"), dict):
                raise ValueError("DB별 재개 상태 형식 오류")
            for key, progress in state["databases"].items():
                if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                    raise ValueError("DB 식별값 형식 오류")
                validate_progress(progress)
            if state["schema_version"] == 2 or state.get("pipeline_revision") != PIPELINE_REVISION:
                for progress in state["databases"].values():
                    progress["days"] = {day: "FAILED" for day in progress["days"]}
                state.update(schema_version=3, pipeline_revision=PIPELINE_REVISION)
            if database_id not in state["databases"]:
                known_days = {day for progress in state["databases"].values() for day in progress["days"]}
                state["databases"][database_id] = {
                    "start_date": state["start_date"], "days": {day: "FAILED" for day in known_days}}
            return state
        if state.get("schema_version") != 1:
            raise ValueError("지원하지 않는 재개 상태 버전")
        validate_progress(state)
        # v1 완료 기록은 어느 DB의 기록인지 알 수 없으므로 모두 다시 검증합니다.
        progress = {"start_date": state["start_date"], "days": {day: "FAILED" for day in state["days"]}}
        return {"schema_version": 3, "pipeline_revision": PIPELINE_REVISION,
                "start_date": progress["start_date"], "databases": {database_id: progress}}
    # DB 식별이 없는 이전 기록은 모두 재확인합니다. 읽을 수 없는 결과 파일도 복구를 막지 않습니다.
    days = {}
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        try:
            day = date.fromisoformat(folder.name)
        except ValueError:
            continue
        days[day.isoformat()] = "FAILED"
    start_date = min([yesterday.isoformat(), *days])
    return {"schema_version": 3, "pipeline_revision": PIPELINE_REVISION, "start_date": start_date,
            "databases": {database_id: {"start_date": start_date, "days": days}}}


def pending_dates(state, yesterday):
    # 자정 직후 시작 복구가 성공했어도 03:00 정기 실행에서 전날을 다시 확인합니다.
    days = {yesterday} | {date.fromisoformat(day) for day, status in state["days"].items()
                          if status != "SUCCESS" and date.fromisoformat(day) <= yesterday}
    day = date.fromisoformat(state["start_date"])
    while day <= yesterday:
        if state["days"].get(day.isoformat()) != "SUCCESS":
            days.add(day)
        day += timedelta(days=1)
    return sorted(days)


def validate_dates(raw_path, target, directory):
    from backend.app.services import shortnews_load as records
    errors = []
    for number, row in enumerate(records.read_records(raw_path), 1):
        try:
            matches = records.published_date(row.get("othbcDt")) == target
        except (ValueError, TypeError):
            matches = False
        if not matches:
            errors.append({"row": number, "nttSn": row.get("nttSn"), "othbcDt": row.get("othbcDt")})
    if errors:
        atomic_json(directory / "date_errors.json", {"target_date": target.isoformat(), "errors": errors})
        raise ValueError("조회 날짜와 다른 날짜 또는 잘못된 날짜가 있습니다")


def run_day(target, root, started, env_path, db_url, database_id):
    directory = root / target.isoformat() / (started.astimezone(KST).strftime("%H%M%S_%f_") + uuid4().hex)
    result = {"target_date": target.isoformat(), "started_at": datetime.now(KST).isoformat(),
              "status": "RUNNING", "stage": "files", "collection": None,
              "mysql": "NOT_STARTED", "output_dir": str(directory), "database_id": database_id}
    try:
        directory.mkdir(parents=True, exist_ok=False)
        save_result(directory, result)
        result["stage"] = "collection"
        from backend.app.scheduler.collectors.shortnews import shortnews_collect as collector
        from backend.app.scheduler.jobs import shortnews_pipeline
        args = argparse.Namespace(env=Path(env_path), format="json", page_size=100,
                                  max_pages=0, title=None, date=target.strftime("%Y%m%d"), delay=0.3)
        raw_path = collector.collect(args, directory)
        report = json.loads((directory / "shortnews_collection.json").read_text(encoding="utf-8"))
        if not isinstance(report.get("collection_complete"), bool):
            raise ValueError("수집 완료 여부를 확인할 수 없습니다")
        result["collection"] = report
        result["stage"] = "date_validation"
        validate_dates(raw_path, target, directory)
        result["stage"] = "pipeline"
        save_result(directory, result)
        processed = shortnews_pipeline.run(raw_path, db_url=db_url, env_path=env_path,
                                          log_dir=directory / "logs", period=(target, target))
        result["pipeline"] = processed
        result["mysql"] = processed["mysql"]
        result["chroma"] = processed["chroma"]
        result["status"] = "FAILED" if processed["status"] != "SUCCESS" else "SUCCESS" if report["collection_complete"] else "PARTIAL"
        result["stage"] = "finished"
    except Exception as exc:
        result.update(status="FAILED", error_type=type(exc).__name__)
        if result["stage"] == "mysql":
            result["mysql"] = "FAILED"
        log.error("단신속보 일일 작업 실패: 날짜=%s 단계=%s 오류=%s", target, result["stage"], type(exc).__name__)
    finally:
        result["finished_at"] = datetime.now(KST).isoformat()
        try:
            save_result(directory, result)
        except Exception as exc:
            result.update(processing_status=result["status"], status="FAILED", report_error_type=type(exc).__name__)
            log.error("단신속보 날짜별 결과 저장 실패: 날짜=%s 오류=%s", target, type(exc).__name__)
    return result


def process_date(target, root, started, env_path, db_url, database_id, state):
    """한 날짜의 파일·작업·체크포인트 실패를 격리합니다. 전역 상태 손실을 성공으로 취급하지 않습니다."""
    days = state["databases"][database_id]["days"]
    day = target.isoformat()
    outcome = {"target_date": day, "database_id": database_id, "status": "FAILED",
               "mysql": "NOT_STARTED", "stage": "checkpoint"}
    days[day] = "RUNNING"
    try:
        # 재시도 표식을 저장하지 못하면 이 날짜의 API·DB 작업을 시작하지 않습니다.
        atomic_json(root / "state.json", state)
    except Exception as exc:
        outcome["error_type"] = type(exc).__name__
        log.error("단신속보 시작 상태 저장 실패: 날짜=%s 오류=%s", day, type(exc).__name__)
        return outcome
    try:
        outcome = run_day(target, root, started, env_path, db_url, database_id)
    except Exception as exc:
        # run_day 경계 밖으로 나오는 일반 오류도 다음 날짜 실행을 막지 않습니다.
        outcome.update(stage="execution", error_type=type(exc).__name__)
        log.error("단신속보 날짜 처리 실패: 날짜=%s 오류=%s", day, type(exc).__name__)
    days[day] = outcome["status"]
    try:
        atomic_json(root / "state.json", state)
    except Exception as exc:
        # 이후 날짜의 상태 저장이 성공해도 이 날짜가 완료로 기록되지 않게 메모리도 되돌립니다.
        days[day] = "RUNNING"
        outcome.update(processing_status=outcome["status"], status="FAILED", checkpoint_error_type=type(exc).__name__)
        log.error("단신속보 종료 상태 저장 실패: 날짜=%s 오류=%s", day, type(exc).__name__)
    return outcome


def run(target_date=None, *, env_path=ENV_PATH, output_dir=OUTPUT_DIR, db_url=None, now=None):
    """KST 전날까지 미완료 날짜를 복구. 명시적 target_date는 그 날짜만 강제 재실행합니다."""
    from filelock import FileLock, Timeout
    started = now or datetime.now(KST)
    if started.tzinfo is None:
        raise ValueError("실행 시각에는 시간대가 필요합니다")
    yesterday = started.astimezone(KST).date() - timedelta(days=1)
    target = date.fromisoformat(str(target_date)) if target_date else yesterday
    root = Path(output_dir)
    result = {"target_date": target.isoformat(), "started_at": started.astimezone(KST).isoformat(),
              "status": "RUNNING", "stage": "collection", "collection": None, "mysql": "NOT_STARTED"}
    try:
        from backend.app.db.session import mysql_url
        from sqlalchemy.engine import make_url
        # 실행 중 .env가 변경되어도 상태 식별과 실제 적재가 같은 DB를 사용하도록 한 번만 확정합니다.
        resolved_url = make_url(db_url or mysql_url(env_path))
        database_id = database_identity(resolved_url)
        result["database_id"] = database_id
        root.mkdir(parents=True, exist_ok=True)
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        # 정기·수동 실행을 포함하여 수집부터 적재까지 잠급니다.
        # 같은 저장소 경로의 동일 DB는 출력 폴더를 달리 지정해도 동시에 실행하지 않습니다.
        with FileLock(str(root / "shortnews_daily.lock"), timeout=0), \
                FileLock(str(LOCK_DIR / (database_id + ".lock")), timeout=0):
            state = read_state(root, yesterday, database_id)
            progress = state["databases"][database_id]
            dates = [target] if target_date else pending_dates(progress, yesterday)
            atomic_json(root / "state.json", state)
            results = []
            for day in dates:
                results.append(process_date(day, root, started, env_path, resolved_url, database_id, state))
            if len(results) == 1:
                result = results[0]
            else:
                result.update(stage="finished", runs=results, mysql="SEE_RUNS" if results else "NOT_NEEDED",
                              status="FAILED" if any(r["status"] == "FAILED" for r in results) else
                              "PARTIAL" if any(r["status"] == "PARTIAL" for r in results) else "SUCCESS")
            atomic_json(root / "latest_run.json", result)
    except Timeout:
        result.update(status="ALREADY_RUNNING", stage="lock")
    except Exception as exc:
        result.update(status="FAILED", error_type=type(exc).__name__)
        log.error("단신속보 일일 작업 파일/잠금 오류: %s", type(exc).__name__)
    emit = log.info if result["status"] in ("SUCCESS", "ALREADY_RUNNING") else log.warning
    emit("단신속보 일일 작업: 날짜=%s 상태=%s 적재=%s 결과=%s",
         target, result["status"], result["mysql"], result.get("output_dir", root))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="해당 날짜만 강제 재실행 YYYY-MM-DD; 생략하면 전날까지 미완료 날짜 복구")
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--db-url", help="생략하면 --env 파일과 환경변수의 MYSQL_* 설정 사용")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    result = run(args.date, env_path=args.env, output_dir=args.output_dir, db_url=args.db_url)
    raise SystemExit(0 if result["status"] == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
