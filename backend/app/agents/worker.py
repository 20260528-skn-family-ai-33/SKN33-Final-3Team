"""Claim ownership, invoke the graph, and publish only after durable success."""
import logging
from backend.app.agents import budget
from backend.app.agents.context import ChatContext
from backend.app.agents.execution import ExecutionStore, invoke_graph
from backend.app.agents.graph import build_graph, graph_config
from backend.app.agents.runtime import Services
from datetime import timedelta
from backend.app.agents.state import CHECKPOINT_TTL, WAITING_TTL_SECONDS, AgentState, TurnSummary
from backend.app.api.common import now
from backend.app.api import jobs
from backend.app.api.redis_store import RedisState


def checkpointer(database,settings):
    with database.lock:
        if not hasattr(database,'agent_checkpointer'):
            from redis import Redis
            from langgraph.checkpoint.redis import RedisSaver
            client=Redis.from_url(settings.get('API_REDIS_URL','redis://localhost:6379/0'),
                                  decode_responses=False,socket_timeout=2,socket_connect_timeout=2)
            saver=RedisSaver(redis_client=client,ttl=CHECKPOINT_TTL)
            saver.setup()
            database.agent_checkpointer=saver
        return database.agent_checkpointer


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
            context=state.input.context.model_copy(deep=True)
            primary=state.plan.tasks[0]
            if primary.answer_status in ('PARTIAL','ANSWERED') and primary.conditions:
                context.conditions=primary.conditions
                for decision in primary.resolved.values():
                    if decision.get('interest') in ('APPLIED','GLOBAL'):
                        context.interest_action='APPLIED' if decision['interest']=='APPLIED' else 'DECLINED'
                if state.draft.turn_summary:
                    context=context.remember(TurnSummary(question=state.input.question,intent=primary.intent,
                        conditions=primary.conditions,summary=state.draft.turn_summary,answered_at=now()))
            ChatContext(redis).publish(context,jid)
        except Exception:
            logging.getLogger(__name__).warning('chat_context_publish_failed job_id=%s',jid)
        finally:
            try: redis.release_chat(sid,jid)
            finally: saver.delete_thread(jid)
