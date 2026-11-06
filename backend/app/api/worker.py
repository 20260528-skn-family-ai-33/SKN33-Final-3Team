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
    # The admission flag applies only to new jobs; accepted jobs can still be waiting.
    try:
        from backend.app.agents.report import renew_waiting, wake_ready
        with database.engine.begin() as connection:
            waiting=wake_ready(Store(database,connection),database.job_scan_size)
    except Exception as exc:
        logging.getLogger(__name__).warning('report_wait_scan_failed type=%s',type(exc).__name__)
    else:
        for pending in waiting:
            try: renew_waiting(database,pending)
            except Exception as exc:
                logging.getLogger(__name__).warning('report_wait_renew_failed type=%s',type(exc).__name__)
    with database.engine.begin() as connection:
        job=jobs.claim(Store(database,connection))
    if not job: return False
    store=None
    failed_step='QUERY' if job['job_type']=='API_REPORT' else 'CONTEXT'
    try:
        if job['job_type']=='API_CHAT' and 'input' in job['payload_json']:
            from backend.app.agents.worker import run
            run(database,settings,job)
            return True
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
            cancelled=bool(current and current['status']=='CANCELLED')
            if not cancelled and (not current or current['claim_token']!=job['claim_token'] or current['status']!='RUNNING'): return True
            error={'code':exc.code if isinstance(exc,ApiError) else 'JOB_FAILED',
                   'message':exc.message if isinstance(exc,ApiError) else '작업 처리에 실패했습니다.',
                   'details':[],'retryable':exc.retryable if isinstance(exc,ApiError) else True,'request_id':str(job['job_id'])}
            if cancelled:
                pass  # Preserve cancellation; clean up the graph after closing the transaction.
            elif job['job_type']=='API_REPORT' and (not isinstance(exc,ApiError) or (exc.retryable and exc.code!='NOT_FOUND')):
                jobs.retry_report(store,job,error,failed_step,int(settings.get('API_REPORT_RETRY_DELAY_SECONDS',300)))
            else:
                jobs.finish(store,job,{'step':failed_step,'error':error},'FAILED')
        if job['job_type']=='API_CHAT' and job.get('agent'):
            try:
                from backend.app.api.redis_store import RedisState
                try:
                    redis=getattr(database,'redis',None) or RedisState(settings)
                    redis.release_chat(str(job['target_key']),str(job['job_id']))
                finally:
                    from backend.app.agents.worker import cleanup_checkpoint
                    cleanup_checkpoint(database,settings,job['job_id'])
            except Exception:
                logging.getLogger(__name__).warning('chat_cleanup_failed job_id=%s',job['job_id'])
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


def warm_models(settings):
    """유망시장 모델(A·B)을 ML 스레드에 미리 올린다(모델 A 로드 약 25초, ML 시간 상한 30초). 기다리지 않는다:
    리포트·일반 채팅은 바로 처리하고, 로드 중(재시작 뒤 약 25초) 들어온 유망시장 질문만 '불러오는 중' 안내를 받는다."""
    from backend.app.agents.market import growth, loaded
    from backend.app.agents.runtime import warm_ml
    return warm_ml(lambda: growth(settings, str(loaded(settings)[0].get('anchor') or '')[:7]))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--once',action='store_true'); args=parser.parse_args()
    from backend.app.services.report_browser import ensure_chromium
    try:
        ensure_chromium()
    except Exception as exc:
        # 오프라인·권한 제한 서버에서 설치가 실패해도 채팅 작업은 돌린다. PDF 생성만 기존 재시도 오류로 끝난다.
        logging.getLogger(__name__).warning('chromium_unavailable type=%s; PDF 리포트 생성은 실패합니다',type(exc).__name__)
    settings=Settings(); db=Database(settings.engine())
    if settings.get('API_CHAT_AGENT_ENABLED','false').lower()=='true':
        # 채팅 작업이 FAILED로만 끝나지 않게 시작할 때 체크포인터 Redis(모듈 포함)를 확인해 원인을 남긴다
        from backend.app.agents.worker import checkpointer
        try: checkpointer(db,settings)
        except Exception as exc: logging.getLogger(__name__).error('chat_checkpointer_unavailable %s',exc)
        if not args.once:
            warm_models(settings)
            from backend.app.agents.tools import warm_article_index
            warm_article_index()
    run_loop(db,settings,args.once)


if __name__=='__main__': main()
