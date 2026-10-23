"""AWS 상주 프로세스용 단신속보 진입점. 기존 등록 함수와 복구 흐름을 사용합니다."""
import argparse
import logging
import signal
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from filelock import FileLock

from backend.app.scheduler.jobs import shortnews_daily

ROOT = Path(__file__).resolve().parents[3]


def build_scheduler():
    scheduler = BlockingScheduler(timezone=shortnews_daily.KST)
    shortnews_daily.register_jobs(scheduler)
    return scheduler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='예약 확인 후 종료. API·DB 호출 없음')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    scheduler = build_scheduler()
    for job in scheduler.get_jobs():
        print(f'{job.id}: {job.trigger.get_next_fire_time(None, datetime.now(shortnews_daily.KST))}', flush=True)
    if args.check:
        return
    lock = ROOT / 'data/shortnews/worker.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)

    def stop(signum, frame):
        if scheduler.running:
            scheduler.shutdown(wait=True)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with FileLock(str(lock), timeout=0):
        scheduler.start()


if __name__ == '__main__': main()
