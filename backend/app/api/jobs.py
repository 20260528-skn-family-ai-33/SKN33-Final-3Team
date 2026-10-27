from datetime import timedelta
from sqlalchemy import select, update, or_
from backend.app.api.common import now, digest, require, clean


def enqueue(store,uid,kind,target,payload,key):
    t=store.t('async_job')
    request_key=digest([uid,kind,key])
    old=store.one(select(t).where(t.c.request_key==request_key))
    if old:
        require(old['target_key']==str(target) and old['payload_json']==clean(payload),'같은 요청 키에 다른 내용이 전달되었습니다.','IDEMPOTENCY_CONFLICT',409)
        return old
    active=store.one(select(t.c.job_id).where(t.c.lock_key==digest([uid,kind,target]),t.c.status.in_(['QUEUED','RUNNING'])))
    require(active is None,'이 대상의 작업이 진행 중입니다.','JOB_IN_PROGRESS',409)
    identity=store.insert('async_job',job_type=kind,target_key=str(target),requested_by=uid,subject_user_id=uid,
        status='QUEUED',payload_json=clean(payload),request_key=request_key,lock_key=digest([uid,kind,target]),
        attempt_no=0,claim_token=0,created_at=now(),available_at=now(),result_json={'step':'QUERY' if kind=='API_REPORT' else 'CONTEXT'})
    return store.one(select(t).where(t.c.job_id==identity))


def owned_job(store,uid,identity):
    t=store.t('async_job')
    row=store.one(select(t).where(t.c.job_id==identity,t.c.subject_user_id==uid,t.c.job_type=='API_CHAT'))
    require(row is not None,'작업을 찾을 수 없습니다.','NOT_FOUND',404)
    store.owned('chat_session','session_id',row['target_key'],uid)
    return row


def claim(store):
    t=store.t('async_job')
    row=store.one(select(t).where(t.c.job_type.in_(['API_REPORT','API_CHAT']),
         or_((t.c.status=='QUEUED') & (t.c.available_at<=now()),(t.c.status=='RUNNING') & (t.c.lease_expires_at<now())))
         .order_by(t.c.job_id).with_for_update(skip_locked=True).limit(1))
    if not row: return None
    token=row['claim_token']+1
    store.c.execute(update(t).where(t.c.job_id==row['job_id']).values(status='RUNNING',claim_token=token,
        attempt_no=row['attempt_no']+1,started_at=now(),lease_expires_at=now()+timedelta(minutes=10)))
    return {**row,'claim_token':token,'attempt_no':row['attempt_no']+1,'status':'RUNNING'}


def finish(store,job,result,status='SUCCEEDED'):
    t=store.t('async_job')
    changed=store.c.execute(update(t).where(t.c.job_id==job['job_id'],t.c.status=='RUNNING',t.c.claim_token==job['claim_token'])
        .values(status=status,result_json=clean(result),finished_at=now(),lease_expires_at=None)).rowcount
    require(changed==1,'작업 소유권이 다른 실행기로 이전되었습니다.','JOB_CLAIM_LOST',409)
