"""UN 갱신의 영속 재개 제어. 대기는 스케줄러가 수행하며 DB 잠금을 보유하지 않습니다."""
import hashlib
import json
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from filelock import FileLock
from backend.app.scheduler.collectors.un_data.api import ApiRequestError
from backend.app.scheduler.collectors.un_data.continuation import CachedClient,BatchedClient,RequestBudgetExceeded
from backend.app.scheduler.collectors.un_data.pipeline import require

CALL_BUDGET=300
BUDGET_DELAY=300
API_DELAY=7200
MAX_API_RESUMES=3


def read_state(state_dir):
    path=Path(state_dir)/'continuation.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


# 완료·대기·재시도 소진 상태를 구분합니다. 수동 --resume-now만 재시도 횟수를 초기화합니다.
def run_resumable(engine,client,scope_path,state_dir,today=None,*,budget=CALL_BUDGET,now=None,resume_now=False):
    from backend.app.scheduler.jobs import un_data_refresh as job
    require(isinstance(budget,int) and not isinstance(budget,bool) and budget>0,'호출 예산은 양의 정수여야 합니다')
    fixed_clock=now is not None
    now=now or datetime.now(timezone.utc)
    require(now.tzinfo is not None,'재개 시각에는 시간대가 필요합니다')
    today=today or now.astimezone(job.KST).date()
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    # 수동·시작·월별·자동 재개 모두 같은 상태 파일을 직렬화합니다.
    with FileLock(str(state_dir/'continuation.lock'),timeout=0):
        require((state_dir/'checkpoint.json').is_file(),'초기 체크포인트가 없습니다. --initialize-csv를 먼저 실행하세요')
        checkpoint=json.loads((state_dir/'checkpoint.json').read_text(encoding='utf-8'))
        scope_hash=hashlib.sha256(Path(scope_path).read_bytes()).hexdigest()
        target_hash=hashlib.sha256(engine.url.render_as_string(hide_password=True).encode()).hexdigest()
        require(checkpoint['scope_sha256']==scope_hash and checkpoint['target_sha256']==target_hash,'다른 DB/범위 재개 금지')
        path=state_dir/'continuation.json';state=read_state(state_dir)
        if state is not None:
            require(state['scope_sha256']==scope_hash and state['target_sha256']==target_hash,'재개 상태 DB/범위 불일치')
            if state['published_from']!=checkpoint['last_successful_check_date']:
                require(state['published_to']==checkpoint['last_successful_check_date'],'재개 상태와 체크포인트 불일치')
                state=None  # commit·점검일 저장 후 제어 파일 저장이 실패한 경우도 새 회차로 시작합니다.
        if state is None or state['status'] in ('COMPLETE','FAILED'):
            state={'version':1,'id':uuid.uuid4().hex,'scope_sha256':scope_hash,'target_sha256':target_hash,
                   'published_from':checkpoint['last_successful_check_date'],'published_to':str(today),
                   'status':'ACTIVE','api_resumes':0,'reason':None}
        require(state['version']==1,'지원하지 않는 재개 상태 버전')
        if resume_now:
            state.update(api_resumes=0,next_attempt_at=None,status='ACTIVE',reason=None)
        elif state['status']=='RETRY_EXHAUSTED':
            return dict(state)
        elif state.get('next_attempt_at') and now<datetime.fromisoformat(state['next_attempt_at']):
            return dict(state,status='DEFERRED')
        if state.get('reason')=='api' and state['status']=='WAITING':
            state['api_resumes']+=1
        state.update(status='ACTIVE',next_attempt_at=None)
        job.save(path,state)
        folder=state_dir/'continuations'/state['id']
        cached=CachedClient(client,folder/'requests',job.save)
        # 예산은 이 실행의 실제 HTTP 시도 수입니다. 캐시 읽기는 포함하지 않습니다.
        initial_count=client.request_count
        old_limit=client.request_limit
        client.request_limit=initial_count+budget

        def factory(watched,cfg,datasets):
            plan=folder/'batch_plan.json'
            if plan.exists():
                planned=json.loads(plan.read_text(encoding='utf-8'))
            else:
                planned=datasets;job.save(plan,planned)
            return BatchedClient(watched,cfg,planned,folder/'batched')

        try:
            result=job.run(engine,cached,scope_path,state_dir,
                           today=datetime.fromisoformat(state['published_to']).date(),collection_factory=factory)
        except ApiRequestError as error:
            failure_time=now if fixed_clock else datetime.now(timezone.utc)
            retryable=(error.kind in ('network','http') and
                       (error.kind=='network' or error.status_code in (408,429)
                        or error.status_code is not None and 500<=error.status_code<=599))
            if isinstance(error,RequestBudgetExceeded):
                state.update(status='WAITING',reason='budget',next_attempt_at=(failure_time+timedelta(seconds=BUDGET_DELAY)).isoformat())
            elif retryable and state['api_resumes']<MAX_API_RESUMES:
                delay=max(API_DELAY,error.retry_info.get('retry_after_seconds') or 0)
                state.update(status='WAITING',reason='api',next_attempt_at=(failure_time+timedelta(seconds=delay)).isoformat())
            else:
                state.update(status='RETRY_EXHAUSTED' if retryable else 'FAILED',reason='api' if retryable else 'non_retryable',next_attempt_at=None)
            state.update(error_type=type(error).__name__,error=str(error),http_status=error.status_code,
                         request_count=client.request_count-initial_count,cache_hits=cached.cache_hits)
            job.save_error_record(path,state,error,state)
            return dict(state)
        except BaseException as error:
            # 데이터 오류는 다음 수동 실행에서 새 발표/응답으로 확인합니다. 취소는 캐시를 보존합니다.
            if isinstance(error,Exception):
                state.update(status='FAILED',reason='data_or_storage',next_attempt_at=None)
                try:job.save(path,state)
                except Exception:job.log.exception('UN 재개 오류 상태 저장 실패 (원래 오류 유지)')
            raise
        else:
            state.update(status='COMPLETE',reason=None,next_attempt_at=None,request_count=client.request_count-initial_count,cache_hits=cached.cache_hits)
            cycle_id=state['id']
            # 오래 대기한 회차를 마치면 그 이후 발표도 다음 회차로 따라잡습니다.
            if state['published_to']<str(today):
                completed_to=state['published_to']
                finished_at=now if fixed_clock else datetime.now(timezone.utc)
                state.update(id=uuid.uuid4().hex,published_from=completed_to,published_to=str(today),
                             status='WAITING',reason='catch_up',api_resumes=0,
                             next_attempt_at=(finished_at+timedelta(seconds=BUDGET_DELAY)).isoformat())
            job.save(path,state)
            return dict(result,request_count=state['request_count'],cache_hits=cached.cache_hits,continuation_id=cycle_id)
        finally:
            client.request_limit=old_limit
