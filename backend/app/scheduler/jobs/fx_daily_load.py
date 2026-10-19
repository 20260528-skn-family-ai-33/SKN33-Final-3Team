"""한국은행 ECOS 일별 환율을 받아 MySQL fx_daily 테이블에 적재합니다.

실행 (저장소 루트에서):
  python -m backend.app.scheduler.jobs.fx_daily_load --start 2026-10-01 --end 2026-10-07   # 기간 지정 (과거 적재·누락 복구)
  python -m backend.app.scheduler.jobs.fx_daily_load                                       # 최근 기간 갱신 (스케줄러와 같은 동작)
스케줄: backend/app/scheduler/main.py가 매일 실행하고, 스케줄러를 켤 때도 한 번 실행합니다.

- 수집 대상은 currency에서 ecos_item_code가 있는 통화입니다 (db/mysql/init/08_currency_seed.sql로 등록).
  통화를 늘리려면 seed에 한 줄을 추가해 실행하면 되고 이 코드는 바꾸지 않습니다.
- 기간을 주지 않으면 오늘(KST) 포함 최근 RECENT_DAYS일을 다시 받습니다. 주말·휴일·짧은 중단으로 빠진 날이 겹쳐 받으며 채워집니다.
  fx_daily의 마지막 기준일이 그보다 오래됐으면 그날부터 받고, fx_daily가 비어 있으면 .env의 FX_START_DATE(YYYY-MM-DD)부터 받습니다.
- (기준일, 통화)가 이미 있으면 환율만 갱신합니다 (원천 정정 반영, 재실행해도 중복 없음).
  통화 하나라도 수집·검증에 실패하면 아무것도 저장하지 않습니다 (한 트랜잭션). 기존 환율은 지우거나 0으로 바꾸지 않습니다.
- 실행할 때마다 batch_run에 한 건 기록합니다 (job FX_DAILY_LOAD, 대상 fx_daily, 기간 = 조회 기간).
- 진행 로그는 data/ecos_outputs/logs/fx_daily_load_<시각>.log에 남깁니다.
"""
import argparse
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from sqlalchemy import func, select

from backend.app.db.batch_log import Source, track_batch
from backend.app.db.crud import CRUD
from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.currency import Currency, FxDaily
from backend.app.scheduler.collectors.ecos import ecos_collect as ecos

KST = ZoneInfo("Asia/Seoul")
LOG_DIR = ENV_PATH.parent / "data/ecos_outputs/logs"
RECENT_DAYS = 14
JOB_NAME = "FX_DAILY_LOAD"
TARGET = FxDaily.__tablename__
SOURCE = Source("ECOS", "한국은행 ECOS 주요국 통화의 대원화환율",
                {"url": ecos.API_BASE, "stat_code": ecos.STAT_CODE, "cycle": ecos.CYCLE})
log = logging.getLogger("fx_daily_load")


def setup_log(log_dir=LOG_DIR):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"fx_daily_load_{datetime.now(KST):%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def load_targets(session):
    """ECOS 항목 코드가 있는 통화만 [(통화 코드, 항목 코드)]로 돌려줍니다."""
    stmt = (select(Currency.currency_code, Currency.ecos_item_code)
            .where(Currency.ecos_item_code.is_not(None)).order_by(Currency.currency_code))
    return [tuple(row) for row in session.execute(stmt)]


def start_date_setting(env_path=ENV_PATH):
    value = ({**dotenv_values(env_path), **os.environ}.get("FX_START_DATE") or "").strip()
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def recent_period(session, today, days=RECENT_DAYS, start_date=None):
    """최근 days일. 마지막 적재일이 더 오래됐으면 그날부터, 비어 있으면 start_date부터 받습니다."""
    start = today - timedelta(days=days - 1)
    latest = session.scalar(select(func.max(FxDaily.base_date)))
    if latest is None and start_date is not None:
        start = min(start, start_date)
    elif latest is not None:
        start = min(start, latest)
    return start, today


def collect(key, targets, start, end, batch=None):
    """모든 대상 통화를 받아 fx_daily 행 목록으로 만듭니다. 하나라도 실패하면 예외가 그대로 올라갑니다."""
    rows = []
    for currency_code, item_code in targets:
        fetched, calls = ecos.fetch_daily(key, item_code, start, end)
        if batch is not None:
            batch.request_count += calls
            batch.received_count += len(fetched)
        log.info(f"{currency_code}({item_code}): {len(fetched)}일 | API 호출 {calls}회")
        rows += [{"base_date": d, "currency_code": currency_code, "rate_krw": rate} for d, rate in fetched]
    return rows


def run(start=None, end=None, db_url=None, log_dir=LOG_DIR, env_path=ENV_PATH, track=True):
    """성공 0, 실패 1을 돌려줍니다. start를 주지 않으면 최근 기간을 갱신합니다. track=False면 batch_run 기록을 생략합니다."""
    log_path = setup_log(log_dir)
    engine = None
    try:
        engine = make_engine(db_url)
        with session_scope(engine) as s:
            if start is None:
                start, end = recent_period(s, datetime.now(KST).date(), start_date=start_date_setting(env_path))
            targets = load_targets(s)
        end = end or datetime.now(KST).date()
        if start > end:
            raise ValueError(f"시작일이 종료일보다 늦습니다: {start} > {end}")
        log.info(f"환율 적재 시작 | {start} ~ {end} | 대상 {', '.join(c for c, _ in targets) or '없음'}")
        if not track:
            return _run(engine, targets, start, end, env_path, log_path, None)
        with track_batch(SOURCE, JOB_NAME, TARGET, start, end, engine=engine, logger=log) as batch:
            rc = _run(engine, targets, start, end, env_path, log_path, batch)
            if rc != 0:
                batch.fail()
        return rc
    except Exception:
        log.exception(f"환율 적재 실패: 대상 통화·기간을 정하지 못함 | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


def _run(engine, targets, start, end, env_path, log_path, batch):
    try:
        if not targets:
            raise LookupError("currency에 ecos_item_code가 있는 통화가 없습니다. db/mysql/init/08_currency_seed.sql을 먼저 실행하세요.")
        rows = collect(ecos.load_key(env_path), targets, start, end, batch)
        with session_scope(engine) as s:  # 모두 받은 뒤 한 트랜잭션으로 저장: 실패하면 롤백되어 기존 환율이 그대로 남음
            CRUD(FxDaily).upsert_many(s, rows)
    except (ecos.EcosError, LookupError) as e:
        log.error(f"환율 적재 실패: {e} | 로그: {log_path}")
        return 1
    except Exception:
        log.exception(f"환율 적재 실패: 예상하지 못한 오류 (DB 작업은 롤백) | 로그: {log_path}")
        return 1
    if not rows:
        log.warning(f"받은 환율 없음 ({start} ~ {end}: 주말·휴일이거나 아직 공표 전) | 로그: {log_path}")
    log.info(f"적재 완료: fx_daily {len(rows):,}행 반영 ({start} ~ {end}) | 로그: {log_path}")
    return 0


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(description="ECOS 일별 환율을 fx_daily 테이블에 적재")
    parser.add_argument("--start", type=parse_date, help="YYYY-MM-DD (주지 않으면 최근 기간 갱신)")
    parser.add_argument("--end", type=parse_date, help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    args = parser.parse_args()
    if args.end and not args.start:
        parser.error("--end는 --start와 함께 지정합니다.")
    raise SystemExit(run(args.start, args.end, args.db_url))


if __name__ == "__main__":
    main()
