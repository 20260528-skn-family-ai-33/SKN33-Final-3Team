"""배치 스케줄러 (APScheduler). 계속 켜 두는 프로세스로 실행합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.main

등록된 잡
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
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.scheduler.jobs import hs_code_embed as embed
from backend.app.scheduler.jobs import hs_code_refresh as refresh
from backend.app.scheduler.jobs import customs_monthly

KST = refresh.KST
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


def build_scheduler():
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
    customs_monthly.register_jobs(scheduler)
    return scheduler


def main():
    scheduler = build_scheduler()
    for job in scheduler.get_jobs():
        print(f"등록: {job.id} | 다음 실행 {job.trigger.get_next_fire_time(None, datetime.now(KST))}")
    scheduler.start()


if __name__ == "__main__":
    main()
