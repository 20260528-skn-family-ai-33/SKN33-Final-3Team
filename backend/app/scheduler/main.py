"""배치 스케줄러 (APScheduler). 계속 켜 두는 프로세스로 실행합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.main

등록된 잡
- shortnews_daily (SHORTNEWS_DAILY_ENABLED=true일 때만): 매일 03:00(KST) 전날 단신속보 전체 페이지 수집 후 MySQL 적재.
  시작 10초 후에도 복구 점검하며, 실패·미완료·중단 기간은 날짜별 상태를 읽어 재처리합니다.
  수집·적재 결과는 data/shortnews/daily에 기록합니다.
- un_data_monthly: 매월 10일 03:00(KST) UN 변경분 갱신, 시작 10초 후에도 1회 점검.
  초기 CSV 적재가 필요하며, 체크포인트가 없으면 안내 후 UN 작업만 건너뜁니다.
- hs_code_refresh: 매년 1월 1일 00:15(KST)에 HS 코드 연간 갱신(다운로드 → CSV → 적재).
  관세청 10단위는 매년, 6단위 HS 판은 몇 년마다(HS2028 …) 바뀌며 둘 다 이 실행에서 처리합니다.
  hs_code 적재 뒤(또는 이미 적재돼 있으면) 올해 판 ↔ 직전 판 대응표(hs_code_correlation)가 없을 때 받아 적재합니다.
  올해 판 파일이 아직 안 올라왔거나(NOT_READY) 실패하면(FAILED) 다음 날 00:15에 다시 시도합니다 (최대 RETRY_DAYS일).
  스케줄러가 꺼져 있어 놓쳤거나 일부만 적재된 경우를 위해 켤 때마다 바로 한 번 실행합니다.
  완료 여부는 갱신 작업 안에서 판단하므로(이미 올해 판으로 모두 있으면 ALREADY_LOADED, LLM 호출 없음) 여기서 따로 보지 않습니다.
- hs_code_embed: 매일 01:00(KST)에 hs_code 테이블에 있지만 Chroma hs_code 컬렉션에 없는 코드만 임베딩해 추가.
  연간 갱신(00:15)이나 실적 적재가 추가한 코드가 그날 밤 vector DB에 들어갑니다. 새 코드가 없으면 OpenAI를 부르지 않습니다.
- customs_monthly: 매월 10일 03:00(KST)에 전월 수출입 수집·검증·적재·계산을 실행합니다.
  시작 시에는 복구 범위의 누락·실패 월도 점검합니다. 운영 시작 월은 CUSTOMS_START_MONTH로 지정합니다.
- fx_daily_load: 매일 06:00(KST)에 ECOS 일별 환율(currency의 ECOS 코드가 있는 통화)을 최근 2일 받아 fx_daily에 반영합니다.
  새벽에는 당일 환율이 아직 없으므로 전 영업일까지 반영됩니다. 통화마다 마지막 적재일이 더 오래됐으면 그날부터 받아
  주말·휴일·중단으로 빠진 날도 채웁니다 (아직 한 건도 없는 통화는 .env의 FX_START_DATE부터, 없으면 최근 14일).
  켤 때도 한 번 실행합니다. 정기·시작 작업이 겹치면 늦게 시작한 쪽은 건너뜁니다.
- external_refresh: 매월 1일 04:00(KST)에 ML 피처용 외부 데이터(BACI·WDI·Comtrade)를 data/external/ CSV로 갱신합니다.
  BACI 새 판이 아직 없으면(NOT_READY) 다음 달에 다시 확인합니다. 켤 때는 실행하지 않습니다 (Comtrade 호출 한도).
"""
import argparse
import hashlib
import logging
import os
import threading
from pathlib import Path
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import dotenv_values

from backend.app.scheduler.jobs import hs_code_embed as embed
from backend.app.scheduler.jobs import hs_code_refresh as refresh
from backend.app.scheduler.jobs import customs_monthly
from backend.app.scheduler.jobs import external_refresh

ROOT = Path(__file__).resolve().parents[3]
UN_STATE = ROOT / "data/un_data/runtime"
UN_SCOPE = ROOT / "data/un_data/scope.json"
UN_ENV = ROOT / ".env"
UN_RUN_LOCK = threading.Lock()
# 알림 중복 억제만 메모리에서 관리합니다. 영속 취소 표식·재개 판단에는 사용하지 않습니다.
UN_NOTICE_LOCK = threading.Lock()
UN_STOP_NOTICES = {}
log = logging.getLogger(__name__)
KST = refresh.KST
UN_TRIGGER = CronTrigger(day=10, hour=3, minute=0, timezone=KST)
HS_REFRESH_TRIGGER = CronTrigger(month=1, day=1, hour=0, minute=15, timezone=KST)  # 매년
HS_EMBED_TRIGGER = CronTrigger(hour=1, minute=0, timezone=KST)  # 매일
FX_DAILY_TRIGGER = CronTrigger(hour=6, minute=0, timezone=KST)  # 매일 (새벽이라 전 영업일 환율까지)
EXTERNAL_TRIGGER = CronTrigger(day=1, hour=4, minute=0, timezone=KST)  # 매월
RETRY_DAYS = 90
RETRY_JOB_ID = "hs_code_refresh_retry"


def report_un_status(state_dir):
    """실행 잠금 안에서 최신 디스크 상태를 확인하고 중단·복구 알림을 직렬화합니다."""
    from filelock import FileLock, Timeout
    from backend.app.scheduler.jobs.un_data_resume import (
        read_state, recovery_blocked, RECOVERY_FENCE, RECOVERY_GUARD)
    try:
        folder=Path(state_dir).resolve()
        # 다른 실행이 진행 중이면 과거 점검 결과로 중단/복구를 알리지 않습니다.
        # 호출자는 이 잠금을 먼저 잡지 않아야 합니다(별도 FileLock 객체의 중첩 획득 방지).
        with FileLock(str(folder/'continuation.lock'),timeout=0):
            try:state=read_state(folder)
            except (OSError,ValueError):state=None
            status=state.get('status') if isinstance(state,dict) else None
            blocked=recovery_blocked(folder)
            if not blocked and status!='CANCELLED':
                if status in ('ACTIVE','WAITING','COMPLETE','FAILED','RETRY_EXHAUSTED'):
                    with UN_NOTICE_LOCK:
                        if folder in UN_STOP_NOTICES:
                            log.info('UN 차단 해제 확인 | 상태 경로: %s | 현재 상태: %s '
                                     '(갱신 성공 여부는 실행 결과에서 별도 확인)',folder,status)
                            del UN_STOP_NOTICES[folder]
                return 'UNBLOCKED'
            status='CANCELLED' if status=='CANCELLED' else 'RECOVERY_REQUIRED'
            fingerprints=[]
            for name in (RECOVERY_FENCE,RECOVERY_GUARD):
                try:fingerprints.append(hashlib.sha256((folder/name).read_bytes()).hexdigest())
                except FileNotFoundError:fingerprints.append('missing')
                except OSError as error:fingerprints.append(type(error).__name__)
            signature=(status,*fingerprints)
            reason='사용자 취소' if status=='CANCELLED' else '종료 결과 미확정(취소 기록 저장 실패 가능)'
            # 잠금 순서는 continuation → 알림 메모리입니다. 파일은 변경하지 않습니다.
            with UN_NOTICE_LOCK:
                if UN_STOP_NOTICES.get(folder)!=signature:
                    log.warning('UN 갱신 중단 [%s]: %s | 상태 경로: %s | 자동 재개 차단 유지. '
                                '원인·저장 상태 확인 후 동일 설정과 --state-dir로 --resume-now 실행. '
                                '같은 상태의 반복 경고는 생략합니다.',status,reason,folder)
                    UN_STOP_NOTICES[folder]=signature
            return status
    except Timeout:
        return 'ALREADY_RUNNING'
    except (OSError,ValueError):
        # 알림용 조회 실패로 작업 결과를 바꾸거나 복구됐다고 간주하지 않습니다.
        log.debug('UN 알림 상태 확인 실패: 다음 점검에서 재확인',exc_info=True)
        return 'UNKNOWN'


def hs_refresh_job(scheduler, attempt=1):
    status = refresh.run_refresh()
    if status in ("NOT_READY", "FAILED") and attempt < RETRY_DAYS:
        next_run = (datetime.now(KST) + timedelta(days=1)).replace(hour=0, minute=15, second=0, microsecond=0)
        scheduler.add_job(hs_refresh_job, "date", run_date=next_run, args=[scheduler, attempt + 1],
                          id=RETRY_JOB_ID, replace_existing=True)
        refresh.log.info(f"{status}: {next_run:%Y-%m-%d %H:%M} KST에 재시도 ({attempt + 1}/{RETRY_DAYS})")
    elif status in ("NOT_READY", "FAILED"):
        refresh.log.error(f"{status}: 재시도 {RETRY_DAYS}일을 모두 썼습니다. 수동 실행이 필요합니다.")
    return status


# 초기화 여부와 중복 실행을 확인하고, UN 실패가 다른 스케줄 작업을 중단하지 않게 처리합니다.
def un_refresh_job(state_dir=UN_STATE, scope_path=UN_SCOPE, api_env=UN_ENV):
    """UN의 시작/정기 실행을 직렬화하고 실패를 다른 배치 작업에서 격리합니다."""
    if not UN_RUN_LOCK.acquire(blocking=False):
        log.warning('UN 점검이 이미 실행 중입니다. 중복 실행을 건너뜁니다.')
        return 'ALREADY_RUNNING'
    try:
        if not (Path(state_dir)/'checkpoint.json').is_file():
            log.warning('UN 초기 체크포인트가 없습니다. un_data_refresh --initialize-csv --state-dir %s를 먼저 실행하세요.', state_dir)
            return 'NOT_INITIALIZED'
        # UN의 의존성·설정 오류도 기존 HS 작업 등록/실행을 막지 않도록 여기서 import.
        from backend.app.scheduler.jobs.un_data_refresh import execute_once
        result=execute_once(scope_path, state_dir, api_env)
        report_un_status(state_dir)
        if isinstance(result,dict) and result.get('status') in ('CANCELLED','RECOVERY_REQUIRED'):
            return result['status']
        if isinstance(result,dict) and result.get('status') in ('WAITING','DEFERRED','RETRY_EXHAUSTED','FAILED'):
            log.warning('UN 재개 상태: %s, 다음 실행: %s',result['status'],result.get('next_attempt_at'))
            return result['status']
        return 'SUCCESS'
    except Exception:
        log.exception('UN 점검 실패: 다른 배치 작업은 계속 실행됩니다. 정상 점검일 유지 여부를 확인하고 재시도하세요.')
        return 'FAILED'
    finally:
        UN_RUN_LOCK.release()


# 매분 영속 상태의 기한만 확인합니다. 대기 중에는 API/DB 연결을 만들지 않습니다.
def un_resume_job(state_dir=UN_STATE,scope_path=UN_SCOPE,api_env=UN_ENV):
    from backend.app.scheduler.jobs.un_data_resume import read_state,active_is_idle,recovery_blocked
    try:
        if recovery_blocked(state_dir):
            observed=report_un_status(state_dir)
            if observed=='ALREADY_RUNNING':return 'ALREADY_RUNNING'
            if observed=='UNBLOCKED':return 'DEFERRED'
            return 'RECOVERY_REQUIRED'
        state=read_state(state_dir)
        if isinstance(state,dict) and state.get('status')=='CANCELLED':
            report_un_status(state_dir)
            return 'NO_RETRY'
        if state and state['status']=='ACTIVE':
            if not active_is_idle(state_dir):
                return 'ALREADY_RUNNING'
            # 실행 진입점에서 잠금을 다시 획득하고 상태를 재확인하므로 경쟁 실행도 겹치지 않습니다.
            return un_refresh_job(state_dir,scope_path,api_env)
        report_un_status(state_dir)
        if not state or state['status']!='WAITING' or not state.get('next_attempt_at'):
            return 'NO_RETRY'
        if datetime.now(KST)<datetime.fromisoformat(state['next_attempt_at']):
            return 'DEFERRED'
        return un_refresh_job(state_dir,scope_path,api_env)
    except Exception:
        log.exception('UN 자동 재개 상태 확인 실패')
        return 'FAILED'


# 기존 HS 작업에 UN 시작 점검과 매월 10일 03:00 KST 작업을 함께 등록합니다.
def build_scheduler(un_state_dir=UN_STATE, un_scope=UN_SCOPE, un_api_env=UN_ENV, *, env_path=ROOT / ".env"):
    scheduler = BlockingScheduler(timezone=KST)
    # misfire_grace_time: 00:15에 잠깐 멈춰 있어도 6시간 안에 켜지면 실행
    scheduler.add_job(hs_refresh_job, HS_REFRESH_TRIGGER, args=[scheduler], id="hs_code_refresh",
                      coalesce=True, misfire_grace_time=6 * 3600, replace_existing=True)
    # 켤 때마다 점검 실행: 놓친 연간 갱신·일부만 적재된 상태를 채움 (이미 완료면 포털 조회 후 바로 끝남)
    scheduler.add_job(hs_refresh_job, "date", run_date=datetime.now(KST) + timedelta(seconds=10),
                      args=[scheduler], id=RETRY_JOB_ID, replace_existing=True)
    # 매일 미등록 코드 임베딩: 실패한 코드는 컬렉션에 없으므로 다음 날 다시 추가됨 (따로 재시도하지 않음)
    scheduler.add_job(embed.run, HS_EMBED_TRIGGER, id="hs_code_embed", coalesce=True, misfire_grace_time=6 * 3600,
                      replace_existing=True)
    un_args = [un_state_dir, un_scope, un_api_env]
    scheduler.add_job(un_refresh_job, UN_TRIGGER, args=un_args, id='un_data_monthly',
                      max_instances=1, coalesce=True, misfire_grace_time=86400, replace_existing=True)
    scheduler.add_job(un_refresh_job, 'date', run_date=datetime.now(KST) + timedelta(seconds=10),
                      args=un_args, id='un_data_startup', max_instances=1,
                      coalesce=True, misfire_grace_time=86400, replace_existing=True)
    scheduler.add_job(un_resume_job,'interval',minutes=1,args=un_args,id='un_data_resume',
                      max_instances=1,coalesce=True,replace_existing=True)
    from backend.app.scheduler.jobs import customs_monthly
    customs_monthly.register_jobs(scheduler)
    scheduler.add_job(external_refresh.run, EXTERNAL_TRIGGER, id="external_refresh", coalesce=True,
                      max_instances=1, misfire_grace_time=24 * 3600, replace_existing=True)
    from backend.app.scheduler.jobs import fx_daily_load
    # 매일 환율 갱신: 실패해도 다음 실행이 마지막 적재일부터 다시 받으므로 따로 재시도하지 않음
    scheduler.add_job(fx_daily_load.run, FX_DAILY_TRIGGER, id="fx_daily_load", coalesce=True, max_instances=1,
                      misfire_grace_time=6 * 3600, replace_existing=True)
    # 켤 때마다 한 번: 꺼져 있던 동안 빠진 환율을 마지막 적재일부터 채움
    scheduler.add_job(fx_daily_load.run, "date", run_date=datetime.now(KST) + timedelta(seconds=30),
                      id="fx_daily_startup", replace_existing=True)
    # 통합 실행에서는 단신 정기·10초 복구 작업 모두 명시적으로 켭니다. 전용 worker는 독립입니다.
    values = {**dotenv_values(env_path), **os.environ}
    if str(values.get("SHORTNEWS_DAILY_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        from backend.app.scheduler.jobs import shortnews_daily
        shortnews_daily.register_jobs(scheduler)
    return scheduler


# 명령행 설정으로 통합 스케줄러를 구성하고 계속 실행합니다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--un-state-dir', type=Path, default=UN_STATE)
    parser.add_argument('--un-scope', type=Path, default=UN_SCOPE)
    parser.add_argument('--un-api-env', type=Path, default=UN_ENV)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    scheduler = build_scheduler(args.un_state_dir, args.un_scope, args.un_api_env)
    for job in scheduler.get_jobs():
        next_time = (getattr(job, "next_run_time", "pending"))
        when = "일시중지" if next_time is None else job.trigger.get_next_fire_time(None, datetime.now(KST))
        print(f"등록: {job.id} | 다음 실행 {when}")
    scheduler.start()


if __name__ == "__main__":
    main()
