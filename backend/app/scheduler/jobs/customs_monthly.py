"""월별 수집 → 검증·적재·집계 → 계산 → 팀 ML 작업. 단계별 체크포인트로 재실행한다."""
import argparse
import csv
import hashlib
import importlib
import json
import logging
import os
import re
from argparse import Namespace
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from backend.app.db.session import ENV_PATH, make_engine, mysql_url
from backend.app.scheduler.collectors.customs import customs_collect as collector
from backend.app.scheduler.jobs.customs_load import run as load_csv
from backend.app.services.customs_load import safe_error
from backend.app.services.customs_metrics import MetricCalculator
from backend.app.services.customs_stage import ValidationError

DEFAULT_OUTPUT = ENV_PATH.parent / "data/customs_load_outputs/monthly"
KST = ZoneInfo("Asia/Seoul")
log = logging.getLogger(__name__)
CACHE_VERSION = 2


def monthly_job(scheduler, month=None, attempt=1, **options):
    """실패해도 원래 대상 월을 유지한다. 공용 스케줄러 파일에는 의존하지 않는다."""
    if month is None:
        month = (datetime.now(KST).replace(day=1) - timedelta(days=1)).strftime("%Y%m")
    try:
        result = run_month(month, **options)
    except Exception as exc:
        # 잠금·파일 접근처럼 단계 처리 바깥에서 생기는 실패도 같은 월로 재시도한다.
        result = {"month": month, "status": "FAILED", "current_stage": "setup", "error": safe_error(exc)}
    log.info("수출입 월별 작업 %s: %s (단계 %s)", month, result["status"], result.get("current_stage"))
    if result["status"] == "FAILED" and attempt < 30:
        scheduler.add_job(monthly_job, "date", run_date=datetime.now(KST) + timedelta(days=1),
            args=[scheduler, month, attempt + 1], kwargs=options,
            id=f"customs_monthly_retry_{month}", replace_existing=True)
    return result


def pending_months(output, latest, start_month=None):
    """운영 시작 월부터 전월까지 폴더가 없는 월도 복구한다."""
    pattern = r"[0-9]{4}(0[1-9]|1[0-2])"
    if start_month is not None and (not re.fullmatch(pattern, start_month)
            or not 2 <= int(start_month[:4]) <= 9997 or start_month > latest):
        raise ValidationError("운영 시작 월은 전월 이하의 YYYYMM이어야 함")
    output = Path(output)
    folders = {folder.name: folder for folder in output.iterdir()
               if folder.is_dir() and re.fullmatch(pattern, folder.name)
               and 2 <= int(folder.name[:4]) <= 9997 and folder.name <= latest} if output.exists() else {}
    first = start_month if start_month is not None else min([*folders, latest])
    pending = []
    month = first
    while month <= latest:
        try:
            state = json.loads((output / month / "state.json").read_text(encoding="utf-8"))
            retry = not isinstance(state, dict) or state.get("status") not in ("SUCCESS", "WAITING_FOR_ML")
            if isinstance(state, dict) and state.get("signature") and state.get("collection_policy_version") != CACHE_VERSION:
                retry = True  # 빈 응답을 완료로 보던 이전 정책의 체크포인트는 한 번 재검증한다.
        except (OSError, ValueError, UnicodeError):
            retry = True
        if retry:
            pending.append(month)
        year, number = int(month[:4]), int(month[4:])
        month = f"{year + (number == 12):04d}{number % 12 + 1:02d}"
    return pending


def startup_job(scheduler, attempt=1, start_month=None, **options):
    """시작 시와 매월 실행 시 누락·실패한 월을 오래된 순서대로 재개한다."""
    try:
        latest = (datetime.now(KST).replace(day=1) - timedelta(days=1)).strftime("%Y%m")
        settings = {**dotenv_values(options.get("env_path", ENV_PATH)), **os.environ}
        start_month = start_month or settings.get("CUSTOMS_START_MONTH") or None
        months = sorted(set(pending_months(options.get("output", DEFAULT_OUTPUT), latest, start_month)) | {latest})
    except Exception as exc:
        log.error("수출입 미완료 월 탐색 실패: %s", safe_error(exc))
        if attempt < 30:
            scheduler.add_job(startup_job, "date", run_date=datetime.now(KST) + timedelta(days=1),
                args=[scheduler, attempt + 1], kwargs={"start_month": start_month, **options},
                id="customs_startup_retry", replace_existing=True)
        return []
    return [monthly_job(scheduler, month, **options) for month in months]


def register_jobs(scheduler, *, env_path=ENV_PATH, start_month=None, **options):
    """수출입 작업만 등록한다. 공용 스케줄러 담당자가 필요할 때 이 함수를 호출할 수 있다."""
    from apscheduler.triggers.cron import CronTrigger
    settings = {**dotenv_values(env_path), **os.environ}
    trigger = CronTrigger(day=int(settings.get("CUSTOMS_MONTHLY_DAY", 10)),
        hour=int(settings.get("CUSTOMS_MONTHLY_HOUR", 3)),
        minute=int(settings.get("CUSTOMS_MONTHLY_MINUTE", 0)), timezone=KST)
    kwargs = {"env_path": env_path, "start_month": start_month, **options}
    scheduler.add_job(startup_job, trigger, args=[scheduler], kwargs=kwargs, id="customs_monthly",
        coalesce=True, max_instances=1, misfire_grace_time=24 * 3600, replace_existing=True)
    scheduler.add_job(startup_job, "date", run_date=datetime.now(KST) + timedelta(seconds=20),
        args=[scheduler], kwargs=kwargs, id="customs_monthly_startup", replace_existing=True)


def build_scheduler(**options):
    from apscheduler.schedulers.blocking import BlockingScheduler
    scheduler = BlockingScheduler(timezone=KST)
    register_jobs(scheduler, **options)
    return scheduler


@contextmanager
def month_lock(path):
    """파일은 남겨도 OS 잠금은 프로세스 종료 시 해제된다. 같은 월 동시 실행을 막는다."""
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValidationError("같은 월의 수출입 파이프라인이 실행 중임") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def write_state(path, state):
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_state(path):
    """손상된 상태는 원문을 별도 보존하고 재수집부터 복구한다. DB 재적재는 업무 키로 중복 방지한다."""
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or (state.get("signature") and (
                not isinstance(state.get("completed"), list) or
                not isinstance(state.get("collection_dir"), str))):
            raise ValueError("체크포인트 구조 오류")
        return state
    except (ValueError, UnicodeError):
        backup = path.with_name(f"state.corrupt_{datetime.now(KST):%Y%m%d_%H%M%S_%f}.json")
        path.replace(backup)
        log.warning("손상된 체크포인트를 보존하고 다시 시작: %s", backup.name)
        return {}


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cache_ready(path, month, country):
    """파일 존재만으로 완료를 판정하지 않는다. 기간·국가·건수·해시가 모두 맞아야 한다."""
    try:
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        expected = {"version": CACHE_VERSION, "complete": True, "month": month, "country": country}
        if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in expected.items()):
            return False
        if type(metadata.get("row_count")) is not int or metadata["row_count"] <= 0:
            return False
        rows = collector.read_csv(path)
        return (file_digest(path) == metadata.get("sha256") and len(rows) == metadata["row_count"]
                and any(row.get("year") != "총계" for row in rows))
    except (OSError, ValueError, UnicodeError, csv.Error):
        return False


def save_cache(path, rows, month, country):
    """API 응답 전체를 임시 CSV에 쓴 뒤 교체하고, 마지막에 완료 표식을 게시한다."""
    if not any(row.get("year") != "총계" for row in rows):
        raise ValidationError("실적 행이 없는 응답은 완료 캐시로 저장할 수 없음")
    temporary = path.with_suffix(".csv.tmp")
    try:
        collector.write_csv(temporary, rows)
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        metadata = {"version": CACHE_VERSION, "complete": True, "month": month, "country": country,
                    "row_count": len(rows), "sha256": file_digest(temporary)}
        temporary.replace(path)
        write_state(path.with_suffix(".json"), metadata)
    finally:
        temporary.unlink(missing_ok=True)


def collect_month(month, directory, env_path, countries, confirmed_empty_countries=()):
    cache = directory / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    failed, ready, unresolved, confirmed = [], [], [], []
    allowed_empty = set(confirmed_empty_countries)
    if allowed_empty - set(countries):
        raise ValidationError("거래 없음 확인 국가는 이번 수집 대상 국가에 포함되어야 함")
    key = None
    for country in countries:
        path = cache / f"{country}.csv"
        if not cache_ready(path, month, country):
            if key is None:
                try:
                    key = collector.load_key(env_path)
                except SystemExit as exc:
                    raise ValidationError("관세청 API 인증키 설정 필요") from exc
            try:
                rows = collector.fetch(key, country, month, month)
                if not any(row.get("year") != "총계" for row in rows):
                    if country in allowed_empty:
                        confirmed.append(country)
                    else:
                        unresolved.append(country)
                        failed.append(country)
                        log.warning("%s %s 실적 행 없음: 거래 없음/미공개 미확인, 다음 실행에서 재조회", month, country)
                    continue
                save_cache(path, rows, month, country)
                if not cache_ready(path, month, country):
                    raise ValidationError("저장한 국가별 캐시의 완료 정보가 일치하지 않음")
            except (RuntimeError, OSError, ValueError, UnicodeError, csv.Error) as exc:
                failed.append(country)
                log.warning("%s %s 국가 캐시 수집 실패: %s", month, country, safe_error(exc))
                continue
        ready.append(country)
    csv_path = directory / f"customs_raw_{month}.csv"
    rows = collector.merge(ready, cache, csv_path)
    report = collector.summarize(rows, month, month, countries, failed)
    report.update(unconfirmed_empty_countries=unresolved, confirmed_empty_countries=confirmed)
    (directory / "collection.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if failed or not report["rows_data"] or report["missing_months"] or report["out_of_period_rows"]:
        raise ValidationError("대상 월 수집 미완료/미공개; 국가별 캐시를 유지하고 재실행 필요")
    return csv_path


def run_month(month, *, env_path=ENV_PATH, output=DEFAULT_OUTPUT, hs_ver="auto",
              ml_entrypoint=None, refresh=False, confirmed_empty_countries=None):
    try:
        return _run_month(month, env_path=env_path, output=output, hs_ver=hs_ver,
                          ml_entrypoint=ml_entrypoint, refresh=refresh,
                          confirmed_empty_countries=confirmed_empty_countries)
    except Exception as exc:
        # 잠금·접근 실패에서는 다른 실행의 체크포인트를 덮어쓰지 않는다.
        report = {"month": month, "status": "FAILED", "current_stage": "setup", "error": safe_error(exc)}
        log.error("수출입 월별 작업 준비 실패: %s", report["error"])
        return report


def _run_month(month, *, env_path, output, hs_ver, ml_entrypoint, refresh, confirmed_empty_countries):
    if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}(0[1-9]|1[0-2])", month) or not 2 <= int(month[:4]) <= 9997:
        raise ValidationError("대상 월은 YYYYMM, 연도는 2~9997이어야 함")
    env_path, output = Path(env_path), Path(output)
    settings = {**dotenv_values(env_path), **os.environ}
    ml_entrypoint = ml_entrypoint or settings.get("CUSTOMS_ML_ENTRYPOINT")
    folder = output / month
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    with month_lock(folder / "run.lock"):
        state = read_state(state_path)
        try:
            countries = collector.resolve_countries(env_path)
            if confirmed_empty_countries is None:
                confirmed_empty_countries = state.get("confirmed_empty_countries", [])
            if not isinstance(confirmed_empty_countries, (list, tuple)) or any(
                    not isinstance(country, str) for country in confirmed_empty_countries):
                raise ValidationError("거래 없음 확인 국가 목록 형식 오류")
            if set(confirmed_empty_countries) - set(countries):
                raise ValidationError("거래 없음 확인 국가는 이번 수집 대상 국가에 포함되어야 함")
            target = mysql_url(env_path).render_as_string(hide_password=True)
            signature = hashlib.sha256(json.dumps([month, hs_ver, countries, target, CACHE_VERSION,
                sorted(set(confirmed_empty_countries))]).encode()).hexdigest()
            if refresh or state.get("signature") != signature:
                state = {"month": month, "signature": signature, "completed": [],
                         "collection_policy_version": CACHE_VERSION,
                         "confirmed_empty_countries": sorted(set(confirmed_empty_countries)),
                         "collection_dir": f"collection_{datetime.now():%Y%m%d_%H%M%S_%f}"}
            raw_dir = folder / state["collection_dir"]
            raw_csv = raw_dir / f"customs_raw_{month}.csv"
            if "collect" in state["completed"] and (
                    not raw_csv.exists() or file_digest(raw_csv) != state.get("csv_sha256")):
                raise ValidationError("체크포인트의 CSV가 변경/삭제됨; --refresh로 원천 재수집 필요")
            # ML 모듈 설정이 바뀌면 이미 성공한 수집·적재·계산은 유지하고 ML만 다시 실행한다.
            if state.get("ml_entrypoint") != ml_entrypoint:
                state["completed"] = [s for s in state["completed"] if s != "ml"]
                state["ml_entrypoint"] = ml_entrypoint
            state.pop("error", None)
            for step in ("collect", "load", "metrics", "ml"):
                if step in state["completed"]:
                    continue
                state.update(status="RUNNING", current_stage=step)
                write_state(state_path, state)
                if step == "collect":
                    raw_csv = collect_month(month, raw_dir, env_path, countries, confirmed_empty_countries)
                    state["csv_sha256"] = file_digest(raw_csv)
                elif step == "load":
                    result = load_csv(Namespace(csv=raw_csv, year=int(month[:4]), mode="load",
                        hs_ver=hs_ver, source_code="CUSTOMS", batch_size=2000, env=env_path,
                        output=folder / "load", invalid_policy="fail", unmapped_policy="fail",
                        calculate_metrics=False), return_report=True)
                    state["load_report"] = result
                    if result["status"] != "LOADED":
                        raise ValidationError("수출입 검증/적재 실패; load 보고서의 원인을 해결한 뒤 재실행")
                elif step == "metrics":
                    engine = make_engine(mysql_url(env_path))
                    try:
                        with engine.connect() as connection:
                            state["metrics"] = MetricCalculator(connection).calculate(int(month[:4]))
                    finally:
                        engine.dispose()
                else:
                    if not ml_entrypoint:
                        state["status"] = "WAITING_FOR_ML"
                        write_state(state_path, state)
                        return state
                    module, separator, name = ml_entrypoint.partition(":")
                    if not separator or not module.startswith("backend.app.ml.") or not name.isidentifier():
                        raise ValidationError("CUSTOMS_ML_ENTRYPOINT는 backend.app.ml.<모듈>:<함수> 형식 필요")
                    operation = getattr(importlib.import_module(module), name)
                    engine = make_engine(mysql_url(env_path))
                    try:
                        result = operation(trade_month=f"{month[:4]}-{month[4:]}-01", engine=engine)
                        if not isinstance(result, dict) or result.get("status") != "SUCCESS":
                            raise ValidationError("ML 작업이 SUCCESS 결과를 반환하지 않음")
                        state["ml"] = result
                    finally:
                        engine.dispose()
                state["completed"].append(step)
                write_state(state_path, state)
            state.update(status="SUCCESS", current_stage=None)
        except Exception as exc:
            state.update(status="FAILED", error=safe_error(exc))
        write_state(state_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description="한 달 수출입 파이프라인 실행/실패 단계부터 재시작")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--month", help="대상 월 YYYYMM")
    mode.add_argument("--schedule", action="store_true", help="수출입 전용 월별 스케줄러 실행 (기본 매월 10일 03:00 KST)")
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hs-ver", default="auto")
    parser.add_argument("--ml-entrypoint")
    parser.add_argument("--refresh", action="store_true", help="새 캐시에서 재수집하고 이후 단계를 다시 실행")
    parser.add_argument("--start-month", help="자동 복구 시작 월 YYYYMM (또는 CUSTOMS_START_MONTH); --schedule 전용")
    parser.add_argument("--confirmed-empty-country", action="append",
        help="거래 없음이 별도로 확인된 국가 코드. 해당 --month에만 적용하며 여러 번 지정 가능")
    args = parser.parse_args()
    if args.schedule:
        if args.refresh or args.confirmed_empty_country:
            parser.error("--refresh, --confirmed-empty-country는 --month와 함께 사용합니다")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        scheduler = build_scheduler(env_path=args.env, output=args.output,
            hs_ver=args.hs_ver, ml_entrypoint=args.ml_entrypoint, start_month=args.start_month)
        for job in scheduler.get_jobs():
            print(f"등록: {job.id} | 다음 실행 {job.trigger.get_next_fire_time(None, datetime.now(KST))}")
        scheduler.start()
        return 0
    if args.start_month:
        parser.error("--start-month는 --schedule과 함께 사용합니다")
    report = run_month(args.month, env_path=args.env, output=args.output, hs_ver=args.hs_ver,
        ml_entrypoint=args.ml_entrypoint, refresh=args.refresh,
        confirmed_empty_countries=args.confirmed_empty_country)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
