"""배치 스케줄러 (APScheduler). 계속 켜 두는 프로세스로 실행합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.main

등록된 잡
- un_data_monthly: 매월 1일 03:00(KST) UN 변경분 갱신, 시작 10초 후에도 1회 점검.
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
"""
import argparse
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.scheduler.jobs import hs_code_embed as embed
from backend.app.scheduler.jobs import hs_code_refresh as refresh
from backend.app.scheduler.jobs import customs_monthly

ROOT = Path(__file__).resolve().parents[3]
UN_STATE = ROOT / "data/un_data/runtime"
UN_SCOPE = ROOT / "data/un_data/scope.json"
UN_ENV = ROOT / ".env"
UN_RUN_LOCK = threading.Lock()
log = logging.getLogger(__name__)
KST = refresh.KST
UN_TRIGGER = CronTrigger(day=1, hour=3, minute=0, timezone=KST)
HS_REFRESH_TRIGGER = CronTrigger(month=1, day=1, hour=0, minute=15, timezone=KST)  # 매년
HS_EMBED_TRIGGER = CronTrigger(hour=1, minute=0, timezone=KST)  # 매일
RETRY_DAYS = 90
RETRY_JOB_ID = "hs_code_refresh_retry"


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
    from backend.app.scheduler.jobs.un_data_resume import read_state
    try:
        state=read_state(state_dir)
        if not state or state['status']!='WAITING' or not state.get('next_attempt_at'):
            return 'NO_RETRY'
        if datetime.now(KST)<datetime.fromisoformat(state['next_attempt_at']):
            return 'DEFERRED'
        return un_refresh_job(state_dir,scope_path,api_env)
    except Exception:
        log.exception('UN 자동 재개 상태 확인 실패')
        return 'FAILED'


# 기존 HS 작업에 UN 시작 점검과 매월 1일 03:00 KST 작업을 함께 등록합니다.
def build_scheduler(un_state_dir=UN_STATE, un_scope=UN_SCOPE, un_api_env=UN_ENV):
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
    customs_monthly.register_jobs(scheduler)
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
        print(f"등록: {job.id} | 다음 실행 {job.trigger.get_next_fire_time(None, datetime.now(KST))}")
    scheduler.start()


if __name__ == "__main__":
    main()
