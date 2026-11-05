"""Agent API admission under the already-held MySQL session lock."""
from datetime import timezone
from uuid import uuid4
from contextlib import contextmanager
from sqlalchemy import select

from backend.app.agents import budget
from backend.app.agents.clarification import answer_patch
from backend.app.agents.context import ChatContext
from backend.app.agents.execution import ExecutionStore
from backend.app.agents.graph import build_graph,graph_config
from backend.app.agents.runtime import Services
from backend.app.agents.state import AgentInput,AgentState,WAITING_TTL_SECONDS
from backend.app.agents.worker import checkpointer
from backend.app.api import jobs,accounts
from backend.app.api.chat import view_job
from backend.app.api.common import now,mask_text,require,digest


@contextmanager
def request_transaction(db):
    rollback_lock = []
    try:
        with db.engine.begin() as connection:
            connection.info['agent_rollback_lock'] = rollback_lock
            try: yield connection
            finally: connection.info.pop('agent_rollback_lock',None)
    except BaseException:
        for sid,jid in rollback_lock:
            try: db.redis.release_chat(sid,jid)
            except Exception: pass  # The next admission checks the stale owner's MySQL row.
        raise


def accept(store,uid,session,question,key):
    sid=session['session_id']; table=store.t('chat_message')
    direct_key=digest([uid,key])
    sessions=store.t('chat_session')
    repeated=store.one(select(table).join(sessions,sessions.c.session_id==table.c.session_id).where(
        table.c.role=='USER',sessions.c.user_id==uid,
        table.c.result_json[jobs.EXECUTION]['payload_json']['direct_message_keys'][direct_key].as_string().is_not(None)))
    if repeated:
        existing=jobs._chat_job(repeated)
        require(str(existing['target_key'])==str(sid) and existing['payload_json']['direct_message_keys'][direct_key]==digest(question),
                '다른 답에 사용한 client_message_id입니다.','IDEMPOTENCY_CONFLICT',409)
        return {'job':view_job(existing)}
    active=jobs._active_chat(store,sid)
    if active:
        current=jobs.owned_job(store,uid,active,lock=True)
        if current['status']=='NEEDS_INPUT' and pending_state(store,current) is None:
            expire(store,current)
        elif current['status']=='NEEDS_INPUT':
            prompt=current['result_json']['clarification']
            return resume(store,current,{'clarification_id':prompt['clarification_id'],'text':question},client_key=key)
        else: require(False,'이 대화의 작업이 진행 중입니다.','JOB_IN_PROGRESS',409)
    latest=store.one(select(table.c.result_json).where(table.c.session_id==sid,table.c.role=='ASSISTANT')
                     .order_by(table.c.message_id.desc()).limit(1))
    context=ChatContext(store.db.redis)
    cached=context.load(str(sid),session.get('context_json') or {},(latest['result_json'] or {}).get('job_id') if latest else None)
    saved=accounts.interests(store,uid)
    job=jobs.enqueue(store,uid,'API_CHAT',sid,{'question':question},key)
    jid=str(job['job_id'])
    job['payload_json']['input']=AgentInput(job_id=jid,session_id=str(sid),user_id=uid,question=question,context=cached,
        interests={k:saved[k] for k in ('hs_items','country_codes')},first_question=latest is None,received_at=now()).model_dump(mode='json')
    job['agent']=budget.new_round(uuid4().hex,0)
    job['version']=jobs.AGENT_QUEUE_VERSION
    jobs._save_chat(store,job)
    def lookup(owner):
        row=store.one(select(table).where(table.c.session_id==sid,table.c.message_id==owner,table.c.role=='USER'))
        return jobs._chat_job(row) if row else None
    context.acquire_locked(str(sid),jid,None,lookup)
    store.c.info.get('agent_rollback_lock',[]).append((str(sid),jid))
    return {'job':view_job(job)}


def pending_state(store,job):
    execution=ExecutionStore(store.db,job)
    graph=build_graph(Services(execution,store.db.settings),checkpointer(store.db,store.db.settings))
    snapshot=graph.get_state(graph_config(job['job_id']))
    if not snapshot.values or not snapshot.values.get('pending'): return None
    state=AgentState.model_validate(snapshot.values)
    asked=state.pending.asked_at
    if asked.tzinfo: asked=asked.astimezone(timezone.utc).replace(tzinfo=None)
    return state if (now()-asked).total_seconds()<WAITING_TTL_SECONDS else None


def expire(store,job):
    job.update(status='EXPIRED',result_json={'step':'DONE'},finished_at=now().isoformat())
    jobs._save_chat(store,job)
    store.db.redis.release_chat(str(job['target_key']),str(job['job_id']))


def resume(store,job,data,client_key=None):
    data=dict(data)
    if 'text' in data: data['text']=mask_text(data['text'].strip())
    if 'text' in data: require(1<=len(data['text'])<=4000,'직접 입력은 1~4,000자로 작성하세요.')
    identity=data['clarification_id']
    prior=job['payload_json'].get('accepted_clarifications',{})
    signature=digest(data)
    if identity in prior:
        require(prior[identity]==signature,'이미 다른 답을 수락했습니다.','CLARIFICATION_CONFLICT',409)
        return {'job':view_job(job)}
    require(job['status']!='EXPIRED','시간이 지나 다시 질문해 주세요.','CLARIFICATION_EXPIRED',409)
    require(job['status']=='NEEDS_INPUT','현재 되묻기와 일치하지 않습니다.','CLARIFICATION_CONFLICT',409)
    state=pending_state(store,job)
    if state is None:
        expire(store,job)
        # Return the error as a response so expiration commits with the request.
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409,content={'code':'CLARIFICATION_EXPIRED','message':'시간이 지나 다시 질문해 주세요.',
                            'details':[],'retryable':False,'request_id':str(job['job_id'])})
    answer_patch(store,state,data)  # Validate requested mode and any server-owned selections.
    if state.pending.kind=='REPORT_CONFIRM' and 'text' in data:
        from backend.app.api.chat_slots import country_key
        if country_key(data['text']) in ('예','네','응','좋아','동의','확인','적용','생성','아니','아니오','아니요','싫어','거절','취소'):
            # 승인·거절은 버튼으로만 받는다. 그래도 메시지 키는 남겨, 응답 유실 뒤 같은 메시지를 재시도하면
            # 새 작업이나 다음 되묻기의 답이 아니라 이 작업으로 돌아가게 한다.
            if client_key:
                job['payload_json'].setdefault('direct_message_keys',{})[digest([job['subject_user_id'],client_key])]=digest(data['text'])
                jobs._save_chat(store,job)
            return {'job':view_job(job)}
    jobs.remember_prompt(job,job['result_json']['clarification'],data)
    ledger=budget.new_round(uuid4().hex,job['claim_token'])
    ledger['step_results']=job['agent']['step_results']
    if job['agent'].get('progress'):
        ledger['progress']={**job['agent']['progress'],'label':'분석 재개 대기'}
    job.update(agent=ledger,status='QUEUED',available_at=now().isoformat(),round_attempt=0,lease_expires_at=None,result_json={})
    job['version']=jobs.AGENT_QUEUE_VERSION
    job['payload_json'].update(resume=data,accepted_clarifications={**prior,identity:signature})
    if client_key:
        job['payload_json'].setdefault('direct_message_keys',{})[digest([job['subject_user_id'],client_key])]=digest(data.get('text',''))
    jobs._save_chat(store,job)
    store.db.redis.renew_chat(str(job['target_key']),str(job['job_id']))
    return {'job':view_job(job)}
