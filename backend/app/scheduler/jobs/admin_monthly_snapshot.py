"""Capture admin month-end counts at 00:00 KST. No historical backfill.

Standalone: python -m backend.app.scheduler.jobs.admin_monthly_snapshot
The API and this collector must share the same API_DB_TARGET configuration.
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from backend.app.api.config import Settings, ROOT
from backend.app.api.database import Database, Store
from backend.app.api.admin_metrics import capture_month_end, KST

log = logging.getLogger(__name__)


def run(env_path=ROOT / '.env'):
    settings = Settings(values={**dotenv_values(env_path), **os.environ})
    engine = settings.engine()
    try:
        with engine.begin() as connection:
            saved = capture_month_end(Store(Database(engine), connection))
        log.info('Admin month-end snapshot: %s', 'saved' if saved else 'skipped')
    finally:
        engine.dispose()


def register_jobs(scheduler, *, env_path=ROOT / '.env'):
    scheduler.add_job(run, CronTrigger(day=1, hour=0, minute=0, second=0, timezone=KST),
        kwargs={'env_path': str(env_path)}, id='admin_monthly_snapshot',
        replace_existing=True, coalesce=True, max_instances=1, misfire_grace_time=30)


def main():
    logging.basicConfig(level=logging.INFO)
    scheduler = BlockingScheduler(timezone=KST)
    register_jobs(scheduler)
    job = scheduler.get_job('admin_monthly_snapshot')
    log.info('Next month-end snapshot: %s', job.trigger.get_next_fire_time(None, datetime.now(KST)))
    scheduler.start()


if __name__ == '__main__':
    main()
