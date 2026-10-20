"""한국은행 ECOS 일별 환율을 받아 MySQL fx_daily 테이블에 적재합니다.

실행 (저장소 루트에서):
  python -m backend.app.scheduler.jobs.fx_daily_load --start 2026-10-01 --end 2026-10-07   # 기간 지정 (과거 적재·누락 복구)
  python -m backend.app.scheduler.jobs.fx_daily_load                                       # 최근 기간 갱신 (스케줄러와 같은 동작)
스케줄: backend/app/scheduler/main.py가 매일 실행하고, 스케줄러를 켤 때도 한 번 실행합니다.

- 수집 대상은 currency에서 ecos_item_code가 있는 통화입니다 (db/mysql/init/08_currency_seed.sql로 등록).
  통화를 늘리려면 seed에 한 줄을 추가해 실행하면 되고 이 코드는 바꾸지 않습니다.
- 기간을 주지 않으면 통화마다 따로 정합니다. 기본은 오늘(KST) 포함 최근 RECENT_DAYS일(2일)이고, 그 통화의 마지막 기준일이
  더 오래됐으면 그날부터 받으므로 주말·휴일·중단이나 한 통화만 비어 있던 날도 채워집니다.
  아직 한 건도 없는 통화(새로 추가한 통화 포함)는 .env의 FX_START_DATE(YYYY-MM-DD)부터 받습니다.
- 통화마다 ECOS 응답의 금액 단위('원')와 기준 수량(예: 100엔)이 currency.quote_unit과 같은지 확인하고, 다르면 저장하지 않습니다.
  rate_krw는 quote_unit만큼의 외화를 원으로 나타낸 값입니다 (JPY 850.24 = 100엔당 850.24원).
- 전일 대비 등락 금액·등락률은 저장하지 않습니다. 조회할 때 같은 통화의 직전 영업일 행과 비교해 계산합니다 (LAG).
- (기준일, 통화)가 이미 있으면 환율만 갱신합니다 (원천 정정 반영, 재실행해도 중복 없음).
  통화 하나라도 수집·검증에 실패하면 아무것도 저장하지 않습니다 (한 트랜잭션). 기존 환율은 지우거나 0으로 바꾸지 않습니다.
- 한 번에 하나만 실행합니다. 같은 프로세스(스케줄러의 정기·시작 작업)는 실행 잠금으로, 다른 프로세스(수동 실행·다른 서버)는
  MySQL GET_LOCK으로 막습니다. 이미 실행 중이면 이번 실행은 아무것도 하지 않고 건너뜁니다 (먼저 시작한 실행이 같은 기간을 받음).
- 실행할 때마다 batch_run에 한 건 기록합니다 (job FX_DAILY_LOAD, 대상 fx_daily, 기간 = 통화별 기간 전체를 덮는 범위).
- 진행 로그는 data/ecos_outputs/logs/fx_daily_load_<시각>.log에 남깁니다.
"""
import argparse
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from sqlalchemy import func, select, text

from backend.app.db.batch_log import Source, track_batch
from backend.app.db.crud import CRUD
from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.currency import Currency, FxDaily
from backend.app.scheduler.collectors.ecos import ecos_collect as ecos

KST = ZoneInfo("Asia/Seoul")
LOG_DIR = ENV_PATH.parent / "data/ecos_outputs/logs"
RECENT_DAYS = 2
JOB_NAME = "FX_DAILY_LOAD"
TARGET = FxDaily.__tablename__
SOURCE = Source("ECOS", "한국은행 ECOS 주요국 통화의 대원화환율",
                {"url": ecos.API_BASE, "stat_code": ecos.STAT_CODE, "cycle": ecos.CYCLE})
RUN_LOCK = threading.Lock()  # 같은 프로세스 안의 동시 실행(정기 작업 + 시작 작업) 방지
DB_LOCK = "fx_daily_load"     # MySQL 이름 잠금. DB 이름을 앞에 붙여 다른 DB와 겹치지 않게 함
log = logging.getLogger("fx_daily_load")


def setup_log(log_dir=LOG_DIR):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"fx_daily_load_{datetime.now(KST):%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    log.propagate = False  # 공용 스케줄러의 logging.basicConfig와 겹쳐 콘솔에 두 번 출력되지 않게
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


@contextmanager
def single_run(engine):
    """실행 잠금을 잡으면 True, 이미 다른 실행이 잡고 있으면 False를 돌려줍니다. 잠금은 블록이 끝나면 풉니다.
    MySQL 이름 잠금은 연결이 끊기면(프로세스 종료 포함) 서버가 자동으로 풉니다."""
    if not RUN_LOCK.acquire(blocking=False):
        yield False
        return
    conn = None
    try:
        if engine.dialect.name == "mysql":
            conn = engine.connect()
            if conn.execute(text("SELECT GET_LOCK(CONCAT(DATABASE(), '.', :name), 0)"), {"name": DB_LOCK}).scalar() != 1:
                yield False
                return
        yield True
    finally:
        try:
            if conn is not None:
                try:
                    conn.execute(text("SELECT RELEASE_LOCK(CONCAT(DATABASE(), '.', :name))"), {"name": DB_LOCK})
                finally:
                    conn.close()
        finally:
            RUN_LOCK.release()


def load_targets(session):
    """ECOS 항목 코드가 있는 통화만 [(통화 코드, 항목 코드, 기준 수량)]으로 돌려줍니다."""
    stmt = (select(Currency.currency_code, Currency.ecos_item_code, Currency.quote_unit)
            .where(Currency.ecos_item_code.is_not(None)).order_by(Currency.currency_code))
    return [tuple(row) for row in session.execute(stmt)]


def start_date_setting(env_path=ENV_PATH):
    value = ({**dotenv_values(env_path), **os.environ}.get("FX_START_DATE") or "").strip()
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def recent_starts(session, targets, today, days=RECENT_DAYS, start_date=None):
    """통화별 시작일 {통화 코드: 시작일}. 기본은 최근 days일이고, 그 통화의 마지막 적재일이 더 오래됐으면 그날부터,
    한 건도 없으면 start_date(FX_START_DATE)부터 받습니다. 통화마다 따로 봐서 한 통화의 최신일이 다른 통화의 공백을 가리지 않습니다."""
    window = today - timedelta(days=days - 1)
    latest = dict(session.execute(select(FxDaily.currency_code, func.max(FxDaily.base_date))
                                  .group_by(FxDaily.currency_code)).all())
    starts = {}
    for currency_code, *_ in targets:
        last = latest.get(currency_code)
        if last is not None:
            starts[currency_code] = min(window, last)
        elif start_date is not None:
            starts[currency_code] = min(window, start_date)
        else:
            starts[currency_code] = window
    return starts


def collect(key, targets, starts, end, batch=None):
    """모든 대상 통화를 각자의 시작일부터 받아 fx_daily 행 목록으로 만듭니다. 하나라도 실패하면 예외가 그대로 올라갑니다."""
    rows = []
    for currency_code, item_code, quote_unit in targets:
        start = starts[currency_code]
        fetched, calls = ecos.fetch_daily(key, item_code, start, end, quote_unit=quote_unit)
        if batch is not None:
            batch.request_count += calls
            batch.received_count += len(fetched)
        log.info(f"{currency_code}({item_code}, {quote_unit} {currency_code}당 {ecos.UNIT_NAME}) {start}~{end}: "
                 f"{len(fetched)}일 | API 호출 {calls}회")
        rows += [{"base_date": d, "currency_code": currency_code, "rate_krw": rate} for d, rate in fetched]
    return rows


def run(start=None, end=None, db_url=None, log_dir=LOG_DIR, env_path=ENV_PATH, track=True):
    """성공 0, 실패 1을 돌려줍니다. 다른 실행이 진행 중이라 건너뛰면 0입니다.
    start를 주지 않으면 통화별로 최근 기간을 정합니다. track=False면 batch_run 기록을 생략합니다."""
    # 로그 파일은 실행 잠금을 잡은 뒤에만 엽니다. 잠금 전에 setup_log()를 부르면 건너뛰는 실행이
    # 진행 중인 실행의 로그 핸들러를 닫고 바꿔 버려, 진행 중인 실행의 나머지 로그가 엉뚱한 파일로 갑니다.
    # 잠금 전 메시지는 기존 핸들러를 그대로 쓰고, 핸들러가 없으면 표준 오류로 나갑니다.
    log_path = None
    if start is not None and end is not None and start > end:
        log.error(f"환율 적재 실패: 시작일이 종료일보다 늦습니다 ({start} > {end})")
        return 1
    engine = None
    try:
        engine = make_engine(db_url)
        with single_run(engine) as acquired:
            if not acquired:
                log.warning("환율 적재가 이미 실행 중이라 이번 실행은 건너뜁니다 (진행 중인 실행의 로그 설정은 바꾸지 않음)")
                return 0
            log_path = setup_log(log_dir)
            with session_scope(engine) as s:
                targets = load_targets(s)
                if start is None:
                    end = datetime.now(KST).date()
                    starts = recent_starts(s, targets, end, start_date=start_date_setting(env_path))
            end = end or datetime.now(KST).date()
            if start is not None:
                if start > end:  # --start만 주고 오늘보다 늦은 날짜를 준 경우
                    log.error(f"환율 적재 실패: 시작일이 종료일보다 늦습니다 ({start} > {end}) | 로그: {log_path}")
                    return 1
                starts = {code: start for code, *_ in targets}
            first = min(starts.values(), default=end)
            log.info(f"환율 적재 시작 | {first} ~ {end} | 대상 {', '.join(c for c, *_ in targets) or '없음'}")
            if not track:
                return _run(engine, targets, starts, end, env_path, log_path, None)
            with track_batch(SOURCE, JOB_NAME, TARGET, first, end, engine=engine, logger=log) as batch:
                rc = _run(engine, targets, starts, end, env_path, log_path, batch)
                if rc != 0:
                    batch.fail()
            return rc
    except Exception:
        log.exception(f"환율 적재 실패: DB 연결 또는 대상 통화·기간 조회 중 오류 | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


def _run(engine, targets, starts, end, env_path, log_path, batch):
    try:
        if not targets:
            raise LookupError("currency에 ecos_item_code가 있는 통화가 없습니다. db/mysql/init/08_currency_seed.sql을 먼저 실행하세요.")
        rows = collect(ecos.load_key(env_path), targets, starts, end, batch)
        with session_scope(engine) as s:  # 모두 받은 뒤 한 트랜잭션으로 저장: 실패하면 롤백되어 기존 환율이 그대로 남음
            CRUD(FxDaily).upsert_many(s, rows)
    except (ecos.EcosError, LookupError) as e:
        log.error(f"환율 적재 실패: {e} | 로그: {log_path}")
        return 1
    except Exception:
        log.exception(f"환율 적재 실패: 예상하지 못한 오류 (DB 작업은 롤백) | 로그: {log_path}")
        return 1
    if not rows:
        log.warning(f"받은 환율 없음 (~{end}: 주말·휴일이거나 아직 공표 전) | 로그: {log_path}")
    log.info(f"적재 완료: fx_daily {len(rows):,}행 반영 (~{end}) | 로그: {log_path}")
    return 0


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(description="ECOS 일별 환율을 fx_daily 테이블에 적재")
    parser.add_argument("--start", type=parse_date, help="YYYY-MM-DD (주지 않으면 통화별로 최근 기간 갱신)")
    parser.add_argument("--end", type=parse_date, help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    args = parser.parse_args()
    if args.end and not args.start:
        parser.error("--end는 --start와 함께 지정합니다.")
    if args.start and args.end and args.start > args.end:
        parser.error(f"--start({args.start})가 --end({args.end})보다 늦습니다.")
    raise SystemExit(run(args.start, args.end, args.db_url))


if __name__ == "__main__":
    main()
