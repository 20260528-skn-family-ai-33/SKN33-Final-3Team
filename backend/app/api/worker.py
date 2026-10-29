"""실행: python -m backend.app.api.worker [--once]. 데이터 수집기는 실행하지 않는다."""
import argparse
import time
import logging
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api import jobs, reports, chat
from backend.app.api.common import ApiError


def run_once(database,settings):
    database.settings=settings
    database.report_max_attempts=max(1,int(settings.get('API_REPORT_MAX_ATTEMPTS',3)))
    database.job_scan_size=max(1,min(1000,int(settings.get('API_JOB_SCAN_SIZE',100))))
    with database.engine.begin() as connection:
        job=jobs.claim(Store(database,connection))
    if not job: return False
    store=None
    failed_step='QUERY' if job['job_type']=='API_REPORT' else 'CONTEXT'
    try:
        with database.engine.begin() as connection:
            store=Store(database,connection)
            current=jobs.locked_current(store,job)
            if not current or current['claim_token']!=job['claim_token'] or current['status']!='RUNNING': return True
            result=reports.generate(store,job,settings) if job['job_type']=='API_REPORT' else chat.execute(store,job)
            jobs.finish(store,job,result)
    except Exception as exc:
        failed_step=getattr(store,'step',failed_step)
        logging.getLogger(__name__).warning('job_failed job_id=%s type=%s',job['job_id'],type(exc).__name__)
        with database.engine.begin() as connection:
            store=Store(database,connection)
            try: current=jobs.locked_current(store,job)
            except ApiError as gone:
                if gone.code=='NOT_FOUND': return True
                raise
            if not current or current['claim_token']!=job['claim_token'] or current['status']!='RUNNING': return True
            error={'code':exc.code if isinstance(exc,ApiError) else 'JOB_FAILED',
                   'message':exc.message if isinstance(exc,ApiError) else '작업 처리에 실패했습니다.',
                   'details':[],'retryable':exc.retryable if isinstance(exc,ApiError) else True,'request_id':str(job['job_id'])}
            if job['job_type']=='API_REPORT' and (not isinstance(exc,ApiError) or (exc.retryable and exc.code!='NOT_FOUND')):
                jobs.retry_report(store,job,error,failed_step,int(settings.get('API_REPORT_RETRY_DELAY_SECONDS',300)))
            else:
                jobs.finish(store,job,{'step':failed_step,'error':error},'FAILED')
    return True


def run_loop(db,settings,once=False):
    db.settings=settings  # The first subscription check runs before run_once.
    next_schedule=0
    while True:
        schedule_error=None
        if time.monotonic()>=next_schedule:
            try:
                from backend.app.api.subscriptions import enqueue_due
                with db.engine.begin() as c: enqueue_due(Store(db,c))
            except Exception as exc:
                schedule_error=exc
                logging.getLogger(__name__).warning('subscription_schedule_failed type=%s',type(exc).__name__)
                next_schedule=time.monotonic()+5
            else:
                next_schedule=time.monotonic()+60
        try:
            worked=run_once(db,settings)
        except Exception as exc:
            logging.getLogger(__name__).warning('worker_iteration_failed type=%s',type(exc).__name__)
            if once: raise
            time.sleep(5)
            continue
        if once:
            if schedule_error is not None: raise schedule_error
            return
        if not worked: time.sleep(2)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--once',action='store_true'); args=parser.parse_args()
    settings=Settings(); db=Database(settings.engine())
    run_loop(db,settings,args.once)


if __name__=='__main__': main()
