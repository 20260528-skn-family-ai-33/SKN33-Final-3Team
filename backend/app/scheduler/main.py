"""배치 스케줄러 (APScheduler). 계속 켜 두는 프로세스로 실행합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.main

등록된 잡
- hs_code_refresh: 2028년부터 5년마다 1월 1일 00:15(KST)에 HS 판 갱신(다운로드 → CSV → 적재).
  새 판 파일이 아직 안 올라왔거나(NOT_READY) 실패하면(FAILED) 다음 날 00:15에 다시 시도합니다 (최대 RETRY_DAYS일).
  스케줄러가 꺼져 있어 놓쳤거나 일부만 적재된 경우를 위해, 2028년 이후에는 켤 때마다 바로 한 번 실행합니다.
  완료 여부는 갱신 작업 안에서 xlsx 코드와 DB 코드를 비교해 판단하므로(이미 모두 있으면 ALREADY_LOADED, LLM 호출 없음)
  여기서 따로 판단하지 않습니다.
"""
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.scheduler.jobs import hs_code_refresh as refresh

KST = refresh.KST
HS_REFRESH_TRIGGER = CronTrigger(year="2028-2099/5", month=1, day=1, hour=0, minute=15, timezone=KST)
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


def needs_catch_up(now=None):
    """2028년 이후면 켤 때마다 True. 일부만 적재된 경우까지 놓치지 않도록 완료 판단은 갱신 작업(_refresh)에 맡깁니다."""
    return (now or datetime.now(KST)).year >= 2028


def build_scheduler():
    scheduler = BlockingScheduler(timezone=KST)
    # misfire_grace_time: 00:15에 잠깐 멈춰 있어도 6시간 안에 켜지면 실행
    scheduler.add_job(hs_refresh_job, HS_REFRESH_TRIGGER, args=[scheduler], id="hs_code_refresh",
                      coalesce=True, misfire_grace_time=6 * 3600, replace_existing=True)
    if needs_catch_up():
        scheduler.add_job(hs_refresh_job, "date", run_date=datetime.now(KST) + timedelta(seconds=10),
                          args=[scheduler], id=RETRY_JOB_ID, replace_existing=True)
    return scheduler


def main():
    scheduler = build_scheduler()
    for job in scheduler.get_jobs():
        print(f"등록: {job.id} | 다음 실행 {job.trigger.get_next_fire_time(None, datetime.now(KST))}")
    scheduler.start()


if __name__ == "__main__":
    main()
