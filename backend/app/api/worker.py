"""실행: python -m backend.app.api.worker [--once]. 데이터 수집기는 실행하지 않는다."""
import argparse
import time
import logging
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api import jobs, reports, chat
from backend.app.api.common import ApiError


def run_once(database,settings):
    database.job_logs=settings.files / '_execution' / 'reports'
    with database.engine.begin() as connection:
        job=jobs.claim(Store(database,connection))
    if not job: return False
    try:
        with database.engine.begin() as connection:
            store=Store(database,connection)
            current=jobs.locked_current(store,job)
            if not current or current['claim_token']!=job['claim_token'] or current['status']!='RUNNING': return True
            result=reports.generate(store,job,settings) if job['job_type']=='API_REPORT' else chat.execute(store,job)
            jobs.finish(store,job,result)
    except Exception as exc:
        if isinstance(exc,ApiError) and exc.code=='NOT_FOUND': return True  # 삭제된 대화는 재생성하지 않는다.
        failed_step=getattr(store,'step','QUERY' if job['job_type']=='API_REPORT' else 'CONTEXT')
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
            if job['job_type']=='API_REPORT' and (not isinstance(exc,ApiError) or exc.retryable):
                jobs.retry_report(store,job,error,failed_step,int(settings.get('API_REPORT_RETRY_DELAY_SECONDS',300)))
            else:
                jobs.finish(store,job,{'step':failed_step,'error':error},'FAILED')
    return True


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--once',action='store_true'); args=parser.parse_args()
    settings=Settings(); db=Database(settings.engine())
    db.job_logs=settings.files / '_execution' / 'reports'
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
