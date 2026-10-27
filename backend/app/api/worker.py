"""실행: python -m backend.app.api.worker [--once]. 데이터 수집기는 실행하지 않는다."""
import argparse
import time
import logging
from datetime import timedelta
from sqlalchemy import select, update
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api import jobs, reports, chat
from backend.app.api.common import ApiError, now


def run_once(database,settings):
    with database.engine.begin() as connection:
        job=jobs.claim(Store(database,connection))
        if job and job['job_type']=='API_REPORT':
            report=database.table('report',connection)
            connection.execute(update(report).where(report.c.report_id==job['target_key']).values(status='RUNNING'))
    if not job: return False
    try:
        with database.engine.begin() as connection:
            store=Store(database,connection); t=store.t('async_job')
            current=store.one(select(t).where(t.c.job_id==job['job_id']).with_for_update())
            if current['claim_token']!=job['claim_token'] or current['status']!='RUNNING': return True
            result=reports.generate(store,job,settings) if job['job_type']=='API_REPORT' else chat.execute(store,job)
            jobs.finish(store,job,result)
    except Exception as exc:
        failed_step=getattr(store,'step','QUERY' if job['job_type']=='API_REPORT' else 'CONTEXT')
        logging.getLogger(__name__).warning('job_failed job_id=%s type=%s',job['job_id'],type(exc).__name__)
        with database.engine.begin() as connection:
            store=Store(database,connection); t=store.t('async_job')
            current=store.one(select(t).where(t.c.job_id==job['job_id']).with_for_update())
            if current['claim_token']!=job['claim_token'] or current['status']!='RUNNING': return True
            error={'code':exc.code if isinstance(exc,ApiError) else 'JOB_FAILED',
                   'message':exc.message if isinstance(exc,ApiError) else '작업 처리에 실패했습니다.',
                   'details':[],'retryable':True,'request_id':str(job['job_id'])}
            if job['job_type']=='API_REPORT' and job['attempt_no']<3:
                delay=0 if job['attempt_no']==1 else 300
                store.c.execute(update(t).where(t.c.job_id==job['job_id']).values(status='QUEUED',available_at=now()+timedelta(seconds=delay),lease_expires_at=None,result_json={'step':failed_step,'error':error}))
                r=store.t('report')
                store.c.execute(update(r).where(r.c.report_id==job['target_key']).values(status='DELAYED',retry_count=job['attempt_no']))
            else:
                jobs.finish(store,job,{'step':failed_step,'error':error},'FAILED')
                if job['job_type']=='API_REPORT':
                    r=store.t('report'); store.c.execute(update(r).where(r.c.report_id==job['target_key']).values(status='FAILED'))
    return True


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--once',action='store_true'); args=parser.parse_args()
    settings=Settings(); db=Database(settings.engine())
    next_schedule=0
    while True:
        if time.monotonic()>=next_schedule:
            from backend.app.api.subscriptions import enqueue_due
            with db.engine.begin() as c: enqueue_due(Store(db,c))
            next_schedule=time.monotonic()+60
        worked=run_once(db,settings)
        if args.once: break
        if not worked: time.sleep(2)


if __name__=='__main__': main()
