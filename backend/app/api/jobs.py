"""기존 chat_message/report의 JSON에 실행 상태를 원자적으로 저장한다."""
from datetime import datetime, timedelta
from sqlalchemy import select, update, and_, or_
from backend.app.api.common import now, digest, require, clean

EXECUTION = '_execution'


def _initial(uid, kind, target, payload, key):
    return {'version':1,'job_type':kind,'target_key':str(target),'subject_user_id':uid,
            'payload_json':clean(payload),'request_key':digest([uid,kind,key]),'status':'QUEUED',
            'attempt_no':0,'round_attempt':0,'claim_token':0,'available_at':now().isoformat(),
            'lease_expires_at':None,'result_json':{'step':'QUERY' if kind=='API_REPORT' else 'CONTEXT'}}


def _chat_job(row):
    state=(row['result_json'] or {}).get(EXECUTION)
    if not state or state.get('version')!=1: return None
    return {**state,'job_id':row['message_id']}


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
    if state.get('version')!=1 or state.get('generation_key')!=record['generation_key']: return None
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
        m.c.result_json[EXECUTION]['status'].as_string().in_(['QUEUED','RUNNING']))
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


def claim(store):
    m,s,r=store.t('chat_message'),store.t('chat_session'),store.t('report')
    # 완료 이력을 LIMIT에 포함하지 않는다. 실행 가능한 행에만 키셋/상한을 적용한다.
    moment=now().isoformat()
    cursors=getattr(store.db,'job_scan_cursors',{})
    size=getattr(store.db,'job_scan_size',100)
    state=m.c.result_json[EXECUTION]
    pending_stmt=select(m.c.message_id,m.c.session_id,m.c.created_at).where(m.c.role=='USER',
        state['version'].as_integer()==1,or_(
            and_(state['status'].as_string()=='QUEUED',state['available_at'].as_string()<=moment),
            and_(state['status'].as_string()=='RUNNING',state['lease_expires_at'].as_string()<moment)))
    execution=r.c.content_json[EXECUTION]
    legacy=execution['version'].as_integer().is_(None)
    report_stmt=select(r.c.report_id,r.c.created_at).where(
        or_(legacy,and_(execution['version'].as_integer()==1,execution['generation_key'].as_string()==r.c.generation_key)),
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
            if job and _due(job):
                cursors[kind]=0  # 새로 만료/재시도 가능해진 오래된 작업도 다음 호출에 확인한다.
                job=_claimed(job); _save_chat(store,job); return job
            continue
        row=store.one(select(r).where(r.c.report_id==identity).with_for_update(skip_locked=True))
        if not row: continue
        job=report_execution(store,row)
        if job and _due(job):
            if job['attempt_no']>=getattr(store.db,'report_max_attempts',3):
                job.update(status='FAILED',result_json={'step':'FAILED','error':{
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
    return {**job,'status':'RUNNING','claim_token':job['claim_token']+1,
            'attempt_no':job['attempt_no']+1,'round_attempt':job.get('round_attempt',0)+1,
            'lease_expires_at':(now()+timedelta(minutes=10)).isoformat()}


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
