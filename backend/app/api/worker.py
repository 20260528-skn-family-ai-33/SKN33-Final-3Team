"""실행: python -m backend.app.api.worker [--once]. 데이터 수집기는 실행하지 않는다."""
import argparse
import time
import logging
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api import jobs, reports, chat
from backend.app.api.common import ApiError


def run_once(database,settings,spawn=None):
    """작업 하나를 가져온다. spawn이 있으면 그 작업을 새 스레드에서 처리하고 바로 돌아온다(D-105)."""
    database.settings=settings
    database.report_max_attempts=max(1,int(settings.get('API_REPORT_MAX_ATTEMPTS',3)))
    database.job_scan_size=max(1,min(1000,int(settings.get('API_JOB_SCAN_SIZE',100))))
    database.agent_adopt_grace_seconds=max(0,int(settings.get('API_AGENT_ADOPT_GRACE_SECONDS',jobs.AGENT_ADOPT_GRACE_SECONDS)))
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
    if spawn:
        spawn(job)
        return True
    process(database,settings,job)
    return True


def process(database,settings,job):
    """가져온 작업 하나를 끝까지 처리한다. 작업마다 저장소·체크포인트 실행기를 따로 만들어 스레드끼리 공유하지 않는다."""
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
    # D-105: 질문·리포트가 들어올 때마다 스레드를 하나씩 띄워 병렬로 처리한다. 모델·색인은 스레드끼리 한 벌을 쓴다.
    # TODO(C-31): 동시 스레드 상한은 임시값이다. OpenAI 요청 한도·RDS 접속 수·메모리를 측정해 정한다.
    limit=max(1,int(settings.get('API_WORKER_MAX_THREADS',16)))
    active=set()
    def spawn(job):
        import threading
        thread=threading.Thread(target=process,args=(db,settings,job),name=f"job-{job['job_type']}-{job['job_id']}")
        thread.start(); active.add(thread)
    try:
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
            active.difference_update({t for t in active if not t.is_alive()})
            if not once and len(active)>=limit:
                time.sleep(0.5)   # 상한만큼 돌고 있으면 새 작업을 가져가지 않는다. 남은 작업은 다른 워커나 다음 차례가 가져간다
                continue
            try:
                worked=run_once(db,settings,None if once else spawn)
            except Exception as exc:
                logging.getLogger(__name__).warning('worker_iteration_failed type=%s',type(exc).__name__)
                if once: raise
                time.sleep(5)
                continue
            if once:
                if schedule_error is not None: raise schedule_error
                return
            if not worked: time.sleep(2)
    finally:
        # 멈출 때(Ctrl+C 등) 처리 중인 작업은 끝까지 마친다. 중간에 끊으면 질문이 실행 중으로 남아 임대 시간(10분) 뒤에야 다시 처리된다
        for thread in list(active): thread.join()


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
    settings=Settings()
    # 스레드 하나가 동시에 접속 2개까지 쓴다(조회 + 저장 트랜잭션). 기본 풀(5+10)이면 병렬 작업이 접속을 기다린다
    threads=max(1,int(settings.get('API_WORKER_MAX_THREADS',16)))
    db=Database(settings.engine(pool_size=threads,max_overflow=threads))
    # 코드를 바꾼 뒤 이전 워커가 남아 있으면 같은 질문도 워커마다 다르게 답한다(2026-11-06). 시작할 때 코드 버전을 남긴다
    import subprocess
    from backend.app.api.config import ROOT
    try: version=subprocess.run(['git','rev-parse','--short','HEAD'],cwd=ROOT,capture_output=True,text=True,timeout=5).stdout.strip() or 'unknown'
    except (OSError,subprocess.SubprocessError): version='unknown'
    logging.getLogger(__name__).warning('worker_started commit=%s pid=%s',version,__import__('os').getpid())
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
