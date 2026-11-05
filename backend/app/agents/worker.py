"""Claim ownership, invoke the graph, and publish only after durable success."""
import logging
from backend.app.agents import budget
from backend.app.agents.context import ChatContext, completed_context
from backend.app.agents.execution import ExecutionStore, invoke_graph
from backend.app.agents.graph import build_graph, graph_config
from backend.app.agents.runtime import Services
from datetime import timedelta
from backend.app.agents.state import CHECKPOINT_TTL, WAITING_TTL_SECONDS, AgentState
from backend.app.api.common import now
from backend.app.api import jobs
from backend.app.api.redis_store import RedisState


REQUIRED_MODULES={'search':'RediSearch','ReJSON':'RedisJSON'}


def require_modules(client):
    """(임시) 체크포인터(langgraph-checkpoint-redis)는 RediSearch·RedisJSON을 쓴다(설계 D-07). Redis 7.x에는 없어
    첫 질문부터 원인 없이 FAILED가 되므로, 연결할 때 모듈부터 확인해 원인을 남긴다."""
    from redis.exceptions import ResponseError
    try:
        modules=client.module_list()
    except ResponseError:
        # ACL·관리형 Redis가 MODULE 명령을 막으면 확인할 수 없다. 막지 않고 setup()의 FT.·JSON. 오류에 맡긴다
        logging.getLogger(__name__).warning('redis_module_list_denied; 모듈 확인을 건너뜁니다')
        return
    names=set()
    for module in modules:
        name=module.get(b'name',module.get('name')) if isinstance(module,dict) else None
        names.add(name.decode() if isinstance(name,bytes) else name)
    missing=[label for name,label in REQUIRED_MODULES.items() if name not in names]
    if missing:
        raise RuntimeError(f'Redis에 {"·".join(missing)} 모듈이 없습니다. 채팅 에이전트는 Redis 8 이상이 필요합니다(설계 D-07).')


def checkpointer(database,settings):
    with database.lock:
        if not hasattr(database,'agent_checkpointer'):
            from redis import Redis
            from langgraph.checkpoint.redis import RedisSaver
            client=Redis.from_url(settings.get('API_REDIS_URL','redis://localhost:6379/0'),
                                  decode_responses=False,socket_timeout=2,socket_connect_timeout=2)
            require_modules(client)
            saver=RedisSaver(redis_client=client,ttl=CHECKPOINT_TTL)
            saver.setup()
            database.agent_checkpointer=saver
        return database.agent_checkpointer


def cleanup_checkpoint(database,settings,job_id):
    try:
        checkpointer(database,settings).delete_thread(str(job_id))
    except Exception:
        logging.getLogger(__name__).warning('chat_checkpoint_cleanup_failed job_id=%s',job_id)


def run(database,settings,job):
    execution=ExecutionStore(database,job)
    services=Services(execution,settings,getattr(database,'agent_client',None))
    saver=checkpointer(database,settings)
    graph=build_graph(services,saver,regeneration_seconds=sum(budget.seconds(settings,node)
                      for node in ('compose','verify','finalize')))
    def claim(ledger):
        if ledger['claim_token']==job['claim_token']: return ledger
        if job['round_attempt']>1:
            return budget.recover(ledger,round_id=ledger['round_id'],previous_claim=ledger['claim_token'],
                claim_token=job['claim_token'],max_recoveries=int(settings.get('API_CHAT_MAX_RECOVERIES',2)))
        return {**ledger,'claim_token':job['claim_token']}
    execution.transition(claim)
    invoke_graph(graph,execution)
    sid,jid=str(job['target_key']),str(job['job_id'])
    state=AgentState.model_validate(graph.get_state(graph_config(jid)).values)
    redis=getattr(database,'redis',None) or RedisState(settings)
    if state.pending or state.report_wait:
        def wait(store,current):
            from backend.app.agents.progress import progress_for
            current['agent']['progress']=progress_for(state,'wait')
            current.update(status=state.status,lease_expires_at=None)
            current['result_json']={'step':'CONTEXT' if state.pending else 'REPORT'}
            if state.pending:
                pending=state.pending
                current['result_json']['clarification']={'clarification_id':pending.clarification_id,'kind':pending.kind,
                    'message':pending.message,'choices':[{'choice_id':c.choice_id,'label':c.label} for c in pending.choices],
                    'allow_text':True,'hint':'다른 질문은 새 채팅을 열거나 이 질문을 중단하세요.',
                    'expires_at':(pending.asked_at+timedelta(seconds=WAITING_TTL_SECONDS)).isoformat()}  # API-37, D-04
                task=next(t for t in state.plan.tasks if t.task_id==pending.task_id)
                prompt=current['result_json']['clarification']
                jobs.remember_prompt(current,prompt)
                if pending.kind=='REPORT_CONFIRM':
                    req=task.report_request
                    from backend.app.api.results import read_snapshot
                    saved_preview=read_snapshot(store,state.input.user_id,req.preview_id,'PREVIEW')
                    saved=saved_preview['conditions']
                    prompt['report_preview']={'preview_id':req.preview_id,'expires_at':req.expires_at.isoformat(),
                        'conditions':saved,'adjustments':saved.get('applied_defaults',[]),'formats':req.formats}
                    if saved_preview.get('source_results'): prompt['report_preview']['source_results']=saved_preview['source_results']
                if pending.kind=='INTEREST_SELECT': prompt['interests']=state.input.interests.model_dump()
            if state.report_wait:
                current['agent']['report_wait']=state.report_wait.model_dump(mode='json')
                current['agent']['progress']['label']='리포트 만드는 중'
        execution.transaction(wait)
        redis.renew_chat(sid,jid)
        return
    if state.status=='SUCCEEDED':
        try:
            context=completed_context(state)
            ChatContext(redis).publish(context,jid)
        except Exception:
            logging.getLogger(__name__).warning('chat_context_publish_failed job_id=%s',jid)
        finally:
            try: redis.release_chat(sid,jid)
            finally: saver.delete_thread(jid)
