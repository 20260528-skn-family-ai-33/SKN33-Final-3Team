"""기존 chat_message/report의 JSON에 실행 상태를 원자적으로 저장한다."""
import os
import socket
from datetime import datetime, timedelta
from sqlalchemy import select, update, and_, or_
from backend.app.api.common import ApiError, now, digest, require, clean

EXECUTION = '_execution'
# Deployed v1 workers filter on version == 1 before claiming or reclaiming.
# 3: 작업마다 만든 Redis의 queue_id를 붙인다(D-106). 2까지만 아는 배포 워커는 3을 가져가지 않는다.
AGENT_QUEUE_VERSION = 3
_KNOWN_CHAT_VERSIONS = (1, 2, AGENT_QUEUE_VERSION)  # 2는 유예 시간 동안 아무 워커도 안 가져간 경우에만 넘겨받는다
# TODO(C-32): 주인 없는 에이전트 작업의 유예 시간 임시값. 버전 2는 지나면 넘겨받아 실행하고, 다른 queue의 버전 3은
# 지나면 새 질문 때 만료한다. 임대 시간(10분)과 같게 두었다. API_AGENT_ADOPT_GRACE_SECONDS로 바꾼다
AGENT_ADOPT_GRACE_SECONDS = 600
REPORT_QUEUE_VERSION = 2  # Legacy workers only claim report protocol 1.


def _initial(uid, kind, target, payload, key):
    return {'version':REPORT_QUEUE_VERSION if kind=='API_REPORT' else 1,'job_type':kind,'target_key':str(target),'subject_user_id':uid,
            'payload_json':clean(payload),'request_key':digest([uid,kind,key]),'status':'QUEUED',
            'attempt_no':0,'round_attempt':0,'claim_token':0,'available_at':now().isoformat(),
            'lease_expires_at':None,'result_json':{'step':'QUERY' if kind=='API_REPORT' else 'CONTEXT'}}


def _chat_job(row):
    state=(row['result_json'] or {}).get(EXECUTION)
    if not state or state.get('version') not in _KNOWN_CHAT_VERSIONS: return None
    return {**state,'job_id':row['message_id']}


def queue_id(db):
    """이 프로세스가 쓰는 Redis의 ID. 체크포인트가 Redis에 있으므로 같은 Redis를 쓰는 워커만 작업을 이어 갈 수 있다(D-106).
    ID는 캐시하지 않는다. Redis를 비우거나 새로 띄우면 ID가 바뀌는데, 캐시하면 API와 워커가 서로 다른 ID를 붙잡는다."""
    redis=getattr(db,'redis',None) or getattr(db,'queue_redis',None)
    if redis is None:
        from backend.app.api.redis_store import RedisState
        redis=db.queue_redis=RedisState(db.settings)
    return redis.queue_id()


def same_queue(job, db):
    """이 프로세스의 Redis가 만든 작업인지. queue가 없는 작업(버전 1·2)은 같은 곳으로 본다."""
    return not job.get('queue') or job['queue']==queue_id(db)


def adopt_grace_seconds(db):
    """주인 없는 작업으로 보기 전 유예 시간(C-32). 워커는 run_once가, API는 설정에서 읽는다."""
    value=getattr(db,'agent_adopt_grace_seconds',None)
    if value is None:
        settings=getattr(db,'settings',None)
        value=max(0,int(settings.get('API_AGENT_ADOPT_GRACE_SECONDS',AGENT_ADOPT_GRACE_SECONDS))) if settings else AGENT_ADOPT_GRACE_SECONDS
    return value


def abandoned(job, db):
    """대기·실행 중인데 유예 시간이 지나도록 아무 워커도 손대지 않은 작업. 다른 환경 작업이면 그 워커가 멈췄거나
    그쪽 Redis가 초기화돼 queue ID가 사라진 경우다. 실행하지 않고 만료만 한다(체크포인트가 그쪽에 있을 수 있다)."""
    limit=now()-timedelta(seconds=adopt_grace_seconds(db))
    if job['status']=='QUEUED': moment=job.get('available_at')
    elif job['status']=='RUNNING': moment=job.get('lease_expires_at')
    elif job['status']=='WAITING_REPORT':
        # 리포트 대기는 그쪽 워커가 체크포인트 TTL(30분)을 계속 연장한다. 라운드 시작(available_at)부터 TTL과 유예 시간이
        # 모두 지났으면 그쪽 워커가 없어 체크포인트도 사라진 것이다. 리포트 재시도(3회·5분)는 이 안에 끝난다
        from backend.app.agents.state import WAITING_TTL_SECONDS
        moment=job.get('available_at')
        limit-=timedelta(seconds=WAITING_TTL_SECONDS)
    else: return False
    return bool(moment) and datetime.fromisoformat(moment)<=limit


def mark_agent(job, db):
    """에이전트 대기열에 넣는다. 같은 RDS를 쓰는 다른 환경(배포 서버 등)의 워커가 가져가지 않게 Redis ID를 붙인다(D-106)."""
    job['version']=AGENT_QUEUE_VERSION
    job['queue']=queue_id(db)


def remember_prompt(job, prompt, answer=None, *, store=None, hs_items=()):
    history=job['payload_json'].setdefault('clarification_history',[])
    identity=prompt['clarification_id']
    entry=next((item for item in history if item['id']==identity),None)
    if entry is None:
        entry={'id':identity,'message':prompt['message']}
        history.append(entry)
    if answer is None: return
    if hs_items:
        table=store.t('hs_code')
        labels=[]
        for item in hs_items:
            code=item['hs_code']
            name=store.c.scalar(select(table.c.name_ko).where(
                table.c.hs_code==code,table.c.hs_ver==item['hs_ver']))
            labels.append(f'{name} (HS {code})' if name else f'HS {code}')
        label=', '.join(labels)
    elif 'text' in answer: label=answer['text']
    elif 'choice_ids' in answer:
        labels={c['choice_id']:c['label'] for c in prompt['choices']}
        label=', '.join(labels[k] for k in answer['choice_ids'])
    elif 'hs_item' in answer: label='HS '+answer['hs_item']['hs_code']
    elif 'report_confirmation' in answer:
        r=answer['report_confirmation']
        label='리포트 생성 · '+', '.join(r['formats']) if r['confirm'] else '리포트 생성 취소'
    else:
        s=answer['interest_selection']
        label='선택한 관심 범위: '+', '.join([*(h['hs_code'] for h in s['hs_items']),*s['country_codes']])
    entry['answer']=label


def _save_chat(store, job):
    t=store.t('chat_message')
    payload=dict(job); payload.pop('job_id',None)
    store.c.execute(update(t).where(t.c.message_id==job['job_id']).values(
        result_json=clean({'schema_version':1,EXECUTION:payload})))


def report_execution(store, record):
    state=(record['content_json'] or {}).get(EXECUTION)
    if not state:
        # 파일 기반 구버전의 미완료 행도 로그 파일 없이 재개한다.
        state=_initial(record['user_id'],'API_REPORT',record['report_id'],
                       {'report_id':str(record['report_id'])},record['generation_key'])
        prior_attempt=record['status'] in ('RUNNING','DELAYED') or record['retry_count']>0
        state.update(generation_key=record['generation_key'],attempt_no=record['retry_count']+int(prior_attempt),
                     lease_expires_at=now().isoformat() if record['status']=='RUNNING' else None)
        if record['status']=='READY': state['result_json']={'step':'DONE'}
    if state.get('version') not in (1,REPORT_QUEUE_VERSION) or state.get('generation_key')!=record['generation_key']: return None
    status={'READY':'SUCCEEDED','DELAYED':'QUEUED'}.get(record['status'],record['status'])
    return {**state,'job_id':record['report_id'],'status':status}


def _save_report(store, job):
    t=store.t('report')
    record=store.owned('report','report_id',job['job_id'],job['subject_user_id'],lock=True)
    content=dict(record['content_json'] or {})
    content[EXECUTION]=clean(job)
    store.c.execute(update(t).where(t.c.report_id==job['job_id']).values(content_json=content))


def find_chat(store, uid, key):
    m,s=store.t('chat_message'),store.t('chat_session')
    row=store.one(select(m).join(s,s.c.session_id==m.c.session_id).where(s.c.user_id==uid,
        m.c.role=='USER',m.c.result_json[EXECUTION]['request_key'].as_string()==digest([uid,'API_CHAT',key])).with_for_update())
    return _chat_job(row) if row else None


def _active_chat(store, session_id, exclude=None):
    m=store.t('chat_message')
    stmt=select(m.c.message_id).where(m.c.session_id==session_id,m.c.role=='USER',
        m.c.result_json[EXECUTION]['status'].as_string().in_(['QUEUED','RUNNING','NEEDS_INPUT','WAITING_REPORT']))
    if exclude is not None: stmt=stmt.where(m.c.message_id!=exclude)
    return store.c.scalar(stmt.with_for_update())


def enqueue(store,uid,kind,target,payload,key):
    state=_initial(uid,kind,target,payload,key)
    if kind=='API_REPORT':
        r=store.owned('report','report_id',target,uid,lock=True)
        state.update(job_id=r['report_id'],generation_key=r['generation_key'])
        _save_report(store,state)
        return state
    require(kind=='API_CHAT','지원하지 않는 작업입니다.')
    old=find_chat(store,uid,key)
    if old:
        require(old['target_key']==str(target) and old['payload_json']==clean(payload),
            '같은 요청 키에 다른 내용이 전달되었습니다.','IDEMPOTENCY_CONFLICT',409)
        return old
    require(_active_chat(store,target) is None,'이 대화의 작업이 진행 중입니다.','JOB_IN_PROGRESS',409)
    identity=store.insert('chat_message',session_id=int(target),role='USER',content_masked=payload['question'],
        result_json=clean({'schema_version':1,EXECUTION:state}),created_at=now())
    return {**state,'job_id':identity}


def owned_job(store,uid,identity,lock=False):
    m,s=store.t('chat_message'),store.t('chat_session')
    row=store.one(select(m).join(s,s.c.session_id==m.c.session_id).where(
        m.c.message_id==identity,s.c.user_id==uid,m.c.role=='USER'))
    job=_chat_job(row) if row else None
    require(job is not None,'작업을 찾을 수 없습니다.','NOT_FOUND',404)
    if lock:
        store.owned('chat_session','session_id',job['target_key'],uid,lock=True)
        row=store.one(select(m).where(m.c.message_id==identity).with_for_update())
        require(row is not None,'작업을 찾을 수 없습니다.','NOT_FOUND',404)
        job=_chat_job(row)
    return job


def requeue_chat(store, job, conditions):
    require(_active_chat(store,job['target_key'],job['job_id']) is None,
        '이 대화의 다른 작업이 진행 중입니다.','JOB_IN_PROGRESS',409)
    job={**job,'status':'QUEUED','payload_json':{**job['payload_json'],'conditions':conditions,'clarified':True},
         'result_json':None,'available_at':now().isoformat(),'lease_expires_at':None}
    _save_chat(store,job)
    return job


def _due(job):
    if job['status']=='QUEUED': return datetime.fromisoformat(job['available_at'])<=now()
    if job['status']=='RUNNING':
        return bool(job.get('lease_expires_at') and datetime.fromisoformat(job['lease_expires_at'])<now())
    return False


def _claimable(job, queue, stale):
    """claim의 SQL 대기열 조건을 잠근 행에 다시 적용한다. stale은 버전 2 유예 기준 시각(isoformat)."""
    if job['version']==1: return True
    if not queue: return False   # Redis를 못 읽으면 에이전트 작업은 가져가지 않는다
    if job['version']==AGENT_QUEUE_VERSION: return job.get('queue')==queue
    moment=job.get('available_at') if job['status']=='QUEUED' else job.get('lease_expires_at')
    return job['version']==2 and bool(moment) and (moment<=stale if job['status']=='QUEUED' else moment<stale)


def claim(store):
    m,s,r=store.t('chat_message'),store.t('chat_session'),store.t('report')
    # 완료 이력을 LIMIT에 포함하지 않는다. 실행 가능한 행에만 키셋/상한을 적용한다.
    moment=now().isoformat()
    cursors=getattr(store.db,'job_scan_cursors',{})
    size=getattr(store.db,'job_scan_size',100)
    state=m.c.result_json[EXECUTION]
    # Redis를 못 읽으면 에이전트 작업만 건너뛴다(체크포인트도 못 쓴다). 리포트·단순 채팅은 계속 가져간다.
    try: queue=queue_id(store.db)
    except ApiError: queue=None
    versions=state['version'].as_integer()==1
    if queue:
        # 버전 2 작업은 2만 아는 워커가 가져가게 둔다. 유예 시간이 지나도 아무도 손대지 않았으면 그런 워커가 없는 것이므로
        # 이 워커가 넘겨받는다. 그대로 두면 _active_chat이 계속 진행 중으로 봐서 대화방이 막힌다.
        # 다른 queue의 버전 3은 넘겨받지 않는다. 그쪽 Redis가 정상이면 체크포인트가 거기 있다(D-106). 오래 멈춘 작업은
        # 그 대화방에 새 질문이 들어올 때 만료한다(admission.accept, abandoned).
        stale=(now()-timedelta(seconds=adopt_grace_seconds(store.db))).isoformat()
        versions=or_(versions,
            and_(state['version'].as_integer()==AGENT_QUEUE_VERSION,state['queue'].as_string()==queue),
            and_(state['version'].as_integer()==2,or_(
                and_(state['status'].as_string()=='QUEUED',state['available_at'].as_string()<=stale),
                and_(state['status'].as_string()=='RUNNING',state['lease_expires_at'].as_string()<stale))))
    pending_stmt=select(m.c.message_id,m.c.session_id,m.c.created_at).where(m.c.role=='USER',versions,or_(
            and_(state['status'].as_string()=='QUEUED',state['available_at'].as_string()<=moment),
            and_(state['status'].as_string()=='RUNNING',state['lease_expires_at'].as_string()<moment)))
    execution=r.c.content_json[EXECUTION]
    legacy=execution['version'].as_integer().is_(None)
    report_stmt=select(r.c.report_id,r.c.created_at).where(
        or_(legacy,and_(execution['version'].as_integer().in_((1,REPORT_QUEUE_VERSION)),execution['generation_key'].as_string()==r.c.generation_key)),
        or_(and_(r.c.status.in_(['QUEUED','DELAYED']),or_(legacy,execution['available_at'].as_string()<=moment)),
            and_(r.c.status=='RUNNING',or_(legacy,execution['lease_expires_at'].as_string()<moment))))

    def window(stmt,pk,kind):
        cursor=cursors.get(kind,0)
        rows=store.rows(stmt.where(pk>cursor).order_by(pk).limit(size))
        if not rows and cursor:
            cursors[kind]=0
            rows=store.rows(stmt.order_by(pk).limit(size))
        return rows

    pending=window(pending_stmt,m.c.message_id,'CHAT')
    reports=window(report_stmt,r.c.report_id,'REPORT')
    store.db.job_scan_cursors=cursors
    candidates=[(x['created_at'],'CHAT',x['message_id'],x) for x in pending]
    candidates += [(x['created_at'],'REPORT',x['report_id'],x) for x in reports]
    for _,kind,identity,candidate in sorted(candidates,key=lambda x:x[:3]):
        # 잠긴 후보가 상한만큼 있어도 다음 호출은 그 뒤의 작업을 확인한다.
        cursors[kind]=identity
        if kind=='CHAT':
            session=store.one(select(s).where(s.c.session_id==candidate['session_id']).with_for_update(skip_locked=True))
            if not session: continue
            row=store.one(select(m).where(m.c.message_id==identity).with_for_update(skip_locked=True))
            job=_chat_job(row) if row else None
            # 후보 조회는 잠그지 않고 읽었다. 잠근 뒤 다시 읽은 행으로 대기열 조건(버전·queue·유예)을 다시 확인한다.
            # 그사이 다른 환경이 넘겨받아 queue를 바꿨으면 그 작업의 체크포인트는 그쪽 Redis에 있다
            if job and _due(job) and _claimable(job,queue,stale if queue else None):
                cursors[kind]=0  # 새로 만료/재시도 가능해진 오래된 작업도 다음 호출에 확인한다.
                if job['version']==2: mark_agent(job,store.db)   # 위 유예 조건을 통과한 버전 2 작업만 여기 온다
                job=_claimed(job); _save_chat(store,job); return job
            continue
        row=store.one(select(r).where(r.c.report_id==identity).with_for_update(skip_locked=True))
        if not row: continue
        job=report_execution(store,row)
        if job and _due(job):
            job['worker']={'host':socket.gethostname(),'pid':os.getpid(),'report_protocol':REPORT_QUEUE_VERSION}
            if job['attempt_no']>=getattr(store.db,'report_max_attempts',3):
                job.update(status='FAILED',finished_at=now().isoformat(),result_json={'step':'FAILED','error':{
                    'code':'RETRY_EXHAUSTED','message':'리포트 최대 시도 횟수를 초과했습니다.','retryable':False}})
                _save_report(store,job)
                store.c.execute(update(r).where(r.c.report_id==identity).values(status='FAILED'))
                continue
            cursors[kind]=0
            job=_claimed(job)
            _save_report(store,job)
            store.c.execute(update(r).where(r.c.report_id==identity).values(status='RUNNING',
                retry_count=row['retry_count']+(1 if job['attempt_no']>1 else 0)))
            return job
    return None


def _claimed(job):
    claimed={**job,'status':'RUNNING','claim_token':job['claim_token']+1,
            'attempt_no':job['attempt_no']+1,'round_attempt':job.get('round_attempt',0)+1,
            'lease_expires_at':(now()+timedelta(minutes=10)).isoformat()}
    if job['job_type']=='API_REPORT':
        claimed['result_json']={'step':job.get('result_json',{}).get('step','QUERY')}
    return claimed


def locked_current(store,job):
    if job['job_type']=='API_CHAT':
        return owned_job(store,job['subject_user_id'],job['job_id'],lock=True)
    record=store.owned('report','report_id',job['job_id'],job['subject_user_id'],lock=True)
    return report_execution(store,record)


def report_step(store,job,step):
    store.step=step
    job['result_json']={'step':step}
    _save_report(store,job)


def finish(store,job,result,status='SUCCEEDED'):
    current=locked_current(store,job)
    # 리포트 생성은 같은 트랜잭션에서 먼저 READY를 기록한다.
    allowed=('RUNNING','SUCCEEDED') if job['job_type']=='API_REPORT' and status=='SUCCEEDED' else ('RUNNING',)
    require(current and current['status'] in allowed and current['claim_token']==job['claim_token'],
        '작업 소유권이 다른 실행기로 이전되었습니다.','JOB_CLAIM_LOST',409)
    done={**current,'status':status,'result_json':clean(result),'finished_at':now().isoformat()}
    if job['job_type']=='API_CHAT': _save_chat(store,done)
    else:
        _save_report(store,done)
        if status=='FAILED':
            r=store.t('report'); store.c.execute(update(r).where(r.c.report_id==job['job_id']).values(status='FAILED'))


def retry_report(store,job,error,step,delay_seconds):
    if job['attempt_no']>=getattr(store.db,'report_max_attempts',3):
        finish(store,job,{'step':step,'error':{**error,'retryable':False}},'FAILED')
        return
    immediate=job['round_attempt']==1
    state={**job,'status':'QUEUED','available_at':(now()+timedelta(seconds=0 if immediate else delay_seconds)).isoformat(),
           'round_attempt':job['round_attempt'] if immediate else 0,'result_json':{'step':step,'error':error}}
    _save_report(store,state)
    r=store.t('report')
    store.c.execute(update(r).where(r.c.report_id==job['job_id']).values(status='QUEUED' if immediate else 'DELAYED'))
