"""채팅 메시지 JSON과 report 행/지속 실행 로그를 사용하는 작업 처리기."""
import json
import os
import tempfile
from datetime import datetime, timedelta
from sqlalchemy import select, update
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


def _log_path(store, report_id):
    return store.db.job_logs / f'{int(report_id)}.json'


def report_execution(store, record):
    path=_log_path(store,record['report_id'])
    if not path.exists(): return None
    state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('version')!=1 or state.get('generation_key')!=record['generation_key']: return None
    # DB가 업무 상태의 기준이다. READY는 로그 상태와 무관하게 다시 실행하지 않는다.
    status={'READY':'SUCCEEDED','DELAYED':'QUEUED'}.get(record['status'],record['status'])
    return {**state,'job_id':record['report_id'],'status':status}


def _save_report(store, job):
    path=_log_path(store,job['job_id']); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
            temporary=f.name
            json.dump(clean(job),f,ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary,path)
        event={k:job.get(k) for k in ('generation_key','job_id','claim_token','attempt_no','round_attempt',
                                      'status','available_at','result_json')}
        event['recorded_at']=now().isoformat()
        with path.with_suffix('.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps(clean(event),ensure_ascii=False)+'\n')
            log.flush(); os.fsync(log.fileno())
    finally:
        if temporary and os.path.exists(temporary): os.unlink(temporary)


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
    pending=store.rows(select(m.c.message_id,m.c.session_id,m.c.created_at).where(m.c.role=='USER',
        m.c.result_json[EXECUTION]['status'].as_string().in_(['QUEUED','RUNNING'])).order_by(m.c.message_id))
    reports=store.rows(select(r.c.report_id,r.c.created_at).where(r.c.status.in_(['QUEUED','DELAYED','RUNNING'])))
    candidates=[(x['created_at'],'CHAT',x['message_id'],x) for x in pending]
    candidates += [(x['created_at'],'REPORT',x['report_id'],x) for x in reports]
    for _,kind,identity,candidate in sorted(candidates,key=lambda x:x[:3]):
        if kind=='CHAT':
            session=store.one(select(s).where(s.c.session_id==candidate['session_id']).with_for_update(skip_locked=True))
            if not session: continue
            row=store.one(select(m).where(m.c.message_id==identity).with_for_update(skip_locked=True))
            job=_chat_job(row) if row else None
            if job and _due(job):
                job=_claimed(job); _save_chat(store,job); return job
            continue
        row=store.one(select(r).where(r.c.report_id==identity).with_for_update(skip_locked=True))
        if not row: continue
        job=report_execution(store,row)
        if job and _due(job):
            job=_claimed(job)
            # 로그를 먼저 원자적으로 보관한다. DB 갱신 실패 후에도 토큰은 증가한다.
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
    immediate=job['round_attempt']==1
    state={**job,'status':'QUEUED','available_at':(now()+timedelta(seconds=0 if immediate else delay_seconds)).isoformat(),
           'round_attempt':job['round_attempt'] if immediate else 0,'result_json':{'step':step,'error':error}}
    _save_report(store,state)
    r=store.t('report')
    store.c.execute(update(r).where(r.c.report_id==job['job_id']).values(status='QUEUED' if immediate else 'DELAYED'))
