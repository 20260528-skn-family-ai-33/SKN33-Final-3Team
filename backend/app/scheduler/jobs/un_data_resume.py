"""UN 갱신의 영속 재개 제어. 대기는 스케줄러가 수행하며 DB 잠금을 보유하지 않습니다."""
import hashlib
import os
import json
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
from filelock import FileLock, Timeout
from contextlib import contextmanager
from backend.app.scheduler.collectors.un_data.api import ApiRequestError
from backend.app.scheduler.collectors.un_data.continuation import CachedClient,BatchedClient,RequestBudgetExceeded
from backend.app.scheduler.collectors.un_data.pipeline import require
from backend.app.scheduler.collectors.un_data.identity import bind_target
from backend.app.scheduler.collectors.un_data.paths import validate_state_path

CALL_BUDGET=300
BUDGET_DELAY=300
API_DELAY=7200
MAX_API_RESUMES=3
RECOVERY_GUARD='recovery_guard.json'
RECOVERY_FENCE='recovery_fence.json'
RECOVERY_RELEASE='recovery_release.json'


def read_state(state_dir):
    path=Path(state_dir)/'continuation.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


# 삭제 대상과 별개의 실행 번호를 보존합니다. 완료 증명이 없거나 손상되면 자동 재개를 막습니다.
def recovery_blocked(state_dir):
    folder=Path(state_dir)
    if (folder/RECOVERY_GUARD).exists():return True
    fence=folder/RECOVERY_FENCE
    if not fence.exists():return False  # 이전 버전 상태 파일 호환
    try:
        token=json.loads(fence.read_text(encoding='utf-8'))['attempt']
        released=json.loads((folder/RECOVERY_RELEASE).read_text(encoding='utf-8'))['attempt']
        return not isinstance(token,str) or not token or token!=released
    except (OSError,ValueError,KeyError,TypeError):
        return True


def record_cancel(state_dir):
    from backend.app.scheduler.jobs import un_data_refresh as job
    try:
        state=read_state(state_dir)
        if state is not None:
            state.update(status='CANCELLED',reason='user_cancelled',next_attempt_at=None)
            job.save(Path(state_dir)/'continuation.json',state)
    except BaseException:
        # 재차 취소·디스크 오류가 있어도 사전 fence가 남아 자동 재개를 차단합니다.
        job.log.exception('UN 취소 기록 실패: 사전 복구 차단 유지')


def cancel_before_commit(state_dir,attempt):
    from backend.app.scheduler.jobs import un_data_refresh as job
    # replace가 성공한 직후 인터럽트가 나도 메모리 플래그 대신 디스크의 실행 번호로 판정합니다.
    if attempt.get('publishing'):
        try:
            receipt=json.loads((Path(state_dir)/RECOVERY_RELEASE).read_text(encoding='utf-8'))
        except FileNotFoundError:
            receipt={}
        except BaseException:
            # 확인 도중 반복 취소도 원래 작업 오류·첫 인터럽트를 덮지 않습니다.
            job.log.exception('UN 완료 확정 여부 확인 불가: 상태를 덮어쓰지 않고 원래 오류 유지')
            return
        if isinstance(receipt,dict) and receipt.get('attempt')==attempt['token']:
            job.log.warning('UN 완료 확정 후 인터럽트: 확정 결과 유지, CANCELLED로 소급 변경하지 않음')
            return
    record_cancel(state_dir)


def finalize_attempt(state_dir,attempt):
    from backend.app.scheduler.jobs import un_data_refresh as job
    folder=Path(state_dir)
    (folder/RECOVERY_GUARD).unlink(missing_ok=True)
    # 준비 파일 저장·fsync·임시 파일 정리를 모두 완료한 뒤 마지막 원자적 교체만 수행합니다.
    # 기존 save(release)는 교체 뒤 finally 정리가 남아 완료/취소가 혼동될 수 있었습니다.
    prepared=folder/(RECOVERY_RELEASE+'.prepare.'+uuid.uuid4().hex)
    try:
        job.save(prepared,{'attempt':attempt['token']})
        attempt['publishing']=True
        os.replace(prepared,folder/RECOVERY_RELEASE)  # 완료 확정 지점. 이 뒤에는 파일 정리가 없습니다.
    except BaseException:
        # 실패 경로의 정리 오류는 기존 저장 오류·인터럽트를 덮지 않습니다.
        try:prepared.unlink(missing_ok=True)
        except BaseException:job.log.exception('UN 완료 준비 파일 정리 실패: 원래 오류 유지')
        raise


# ACTIVE라는 이름이나 경과 시간만으로 복구하지 않습니다. OS 파일 잠금이 실제 실행을 판별합니다.
def active_is_idle(state_dir):
    try:
        with FileLock(str(Path(state_dir)/'continuation.lock'),timeout=0):
            state=read_state(state_dir)
            return bool(state and state['status']=='ACTIVE')
    except Timeout:
        return False


# Ctrl+C(KeyboardInterrupt)만 명시적 취소로 취급합니다. 강제 종료는 사전 차단 표식과 ACTIVE를 남겨 수동 확인합니다.
@contextmanager
def continuation_lock(state_dir):
    from backend.app.scheduler.jobs import un_data_refresh as job
    with FileLock(str(Path(state_dir)/'continuation.lock'),timeout=0):
        attempt={'armed':False}
        try:
            yield attempt
        except KeyboardInterrupt:
            cancel_before_commit(state_dir,attempt)
            raise
        except BaseException:
            # 작업 실패가 있으면 정리 중 취소를 포함한 모든 예외보다 원래 오류를 우선합니다.
            try:
                state=read_state(state_dir)
                if attempt['armed'] and state and state['status']=='FAILED':
                    finalize_attempt(state_dir,attempt)
            except BaseException as cleanup_error:
                if isinstance(cleanup_error,KeyboardInterrupt):cancel_before_commit(state_dir,attempt)
                job.log.exception('UN 표식 정리 중 예외: 원래 작업 오류 유지, 완료 여부는 실행 번호로 판정')
            raise
        else:
            if attempt['armed']:
                try:
                    finalize_attempt(state_dir,attempt)
                except KeyboardInterrupt:
                    cancel_before_commit(state_dir,attempt)
                    raise


# 완료·대기·재시도 소진 상태를 구분합니다. 수동 --resume-now만 재시도 횟수를 초기화합니다.
def run_resumable(engine,client,scope_path,state_dir,today=None,*,budget=CALL_BUDGET,now=None,resume_now=False):
    from backend.app.scheduler.jobs import un_data_refresh as job
    require(isinstance(budget,int) and not isinstance(budget,bool) and budget>0,'호출 예산은 양의 정수여야 합니다')
    fixed_clock=now is not None
    now=now or datetime.now(timezone.utc)
    require(now.tzinfo is not None,'재개 시각에는 시간대가 필요합니다')
    today=today or now.astimezone(job.KST).date()
    validate_state_path(state_dir)
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    # 수동·시작·월별·자동 재개 모두 같은 상태 파일을 직렬화합니다.
    with continuation_lock(state_dir) as attempt:
        require((state_dir/'checkpoint.json').is_file(),'초기 체크포인트가 없습니다. --initialize-csv를 먼저 실행하세요')
        checkpoint=json.loads((state_dir/'checkpoint.json').read_text(encoding='utf-8'))
        scope_hash=hashlib.sha256(Path(scope_path).read_bytes()).hexdigest()
        binding=bind_target(engine,checkpoint)
        target_hash=binding['target_sha256']
        require(checkpoint['scope_sha256']==scope_hash and checkpoint['target_sha256']==target_hash,'다른 DB/범위 재개 금지')
        path=state_dir/'continuation.json';state=read_state(state_dir)
        if recovery_blocked(state_dir) and not resume_now:
            if state and state.get('status')=='CANCELLED':
                return dict(state)
            return {'status':'RECOVERY_REQUIRED','reason':'unconfirmed_exit','next_attempt_at':None}
        if state is not None:
            require(state['scope_sha256']==scope_hash and state['target_sha256']==target_hash,'재개 상태 DB/범위 불일치')
            # 완료 직후 취소라도 자동 점검이 사용자 취소를 지우지 않도록 먼저 확인합니다.
            if state['status']=='CANCELLED' and not resume_now:
                return dict(state)
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
        # 취소 시 디스크가 가득 차도 의도가 자동 복구로 바뀌지 않도록 실행 전에 차단합니다.
        attempt['token']=uuid.uuid4().hex
        job.save(state_dir/RECOVERY_FENCE,{'attempt':attempt['token']})
        job.save(state_dir/RECOVERY_GUARD,{'reason':'execution_not_finalized'})
        # 파일 내용뿐 아니라 디렉터리 항목도 실행 전에 영속화합니다(POSIX).
        if os.name=='posix':
            descriptor=os.open(state_dir,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        attempt['armed']=True
        state.update(status='ACTIVE',next_attempt_at=None)
        job.save(path,state)
        folder=state_dir/'continuations'/state['id']
        # 승인 취소 후 과거 실패 RAW 캐시를 다시 읽지 않도록 감사 세대별로 격리합니다.
        journal_path=state_dir/'pending_datasets.json'
        journal=json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
        audit=journal.get('exclusion_audit', [])
        generation=hashlib.sha256(json.dumps(audit,sort_keys=True).encode()).hexdigest() if audit else None
        requests_folder=folder/'requests'
        cached=CachedClient(client,requests_folder,job.save,generation=generation)
        # 예산은 이 실행의 실제 HTTP 시도 수입니다. 캐시 읽기는 포함하지 않습니다.
        initial_count=client.request_count
        old_limit=client.request_limit
        client.request_limit=initial_count+budget

        def factory(watched,cfg,datasets):
            plan=folder/'batch_plan.json'
            if plan.exists():
                planned=json.loads(plan.read_text(encoding='utf-8'))
                keys={(d['freqCode'],str(d['period']),str(d['reporterCode'])) for d in planned}
                additions=[d for d in datasets if (d['freqCode'],str(d['period']),str(d['reporterCode'])) not in keys]
                if additions:
                    planned=planned+additions
                    job.save(plan,planned)
            else:
                planned=datasets;job.save(plan,planned)
            from backend.app.scheduler.jobs.un_data_exclusions import matches
            approved=journal.get('approved_exclusions', {})
            active_keys={job.dataset_key(d,cfg,datetime.fromisoformat(state['published_to']).date()) for d in datasets}
            planned=[d for d in planned if not (
                (key:=job.dataset_key(d,cfg,datetime.fromisoformat(state['published_to']).date())) in approved
                and key not in active_keys and matches(approved[key],d))]
            return BatchedClient(watched,cfg,planned,folder/'batched')

        try:
            result=job.run(engine,cached,scope_path,state_dir,
                           today=datetime.fromisoformat(state['published_to']).date(),collection_factory=factory)
        except ApiRequestError as error:
            failure_time=now if fixed_clock else datetime.now(timezone.utc)
            retryable=(error.kind in ('network','http','quota') and
                       (error.kind in ('network','quota') or error.status_code in (408,429)
                        or error.status_code is not None and 500<=error.status_code<=599))
            if isinstance(error,RequestBudgetExceeded):
                state.update(status='WAITING',reason='budget',next_attempt_at=(failure_time+timedelta(seconds=BUDGET_DELAY)).isoformat())
            elif retryable and state['api_resumes']<MAX_API_RESUMES and (error.retry_info.get('retry_after_seconds') or 0)<=31*86400:
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
