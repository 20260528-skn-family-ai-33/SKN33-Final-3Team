"""Explicit preview approval and separate report completion polling (D-76/81)."""
from sqlalchemy import select, update
from backend.app.agents.conditions import ask
from backend.app.agents.state import ReportRequest,ReportWait,ToolResult
from backend.app.agents.templates import api_conditions
from backend.app.api import jobs,reports,results
from backend.app.api.chat_slots import SlotRepair
from backend.app.api.common import ApiError,clean,digest,now
from backend.app.api.schemas import ReportCreate


def preview(store,state,task):
    key=f'{task.task_id}:report_confirm'
    request=task.report_request
    decision=task.resolved.get(key,{}).get('report_confirmation')
    step=next(s for s in task.steps if s.tool=='generate_report')
    generation=digest([state.input.user_id,'REPORT',f'chat:{state.input.job_id}:{step.step_id}'])
    table=store.t('report')
    record=store.one(select(table.c.conditions_json).where(table.c.user_id==state.input.user_id,table.c.generation_key==generation))
    if record:
        approved=record['conditions_json'].get('chat_approval')
        if not approved: raise SlotRepair('기존 리포트의 승인 정보를 확인할 수 없습니다. 새 질문으로 다시 요청하세요.')
        previous=ReportRequest.model_validate(approved)
        sources=[state.results[b.source_step_id].result_id for b in task.input_bindings]
        if previous.conditions!=task.conditions or sources!=record['conditions_json'].get('chat_sources'):
            raise SlotRepair('이전 리포트 승인 조건과 다릅니다. 새 질문으로 다시 요청하세요.')
        task.report_request=previous
        return
    if decision and not decision['confirm']: raise SlotRepair('리포트 생성 요청을 취소했습니다.')
    if request and request.confirmed and decision and decision['preview_id']==request.preview_id:
        try: results.read_snapshot(store,state.input.user_id,request.preview_id,'PREVIEW')
        except ApiError as exc:
            if exc.code!='PREVIEW_EXPIRED': raise
            task.resolved.pop(key,None)
        else: return
    public=api_conditions(task.conditions)
    created=reports.preview(store,state.input.user_id,{'conditions':public})
    if task.input_bindings:
        from backend.app.agents.bindings import source_versions
        inputs=source_versions(store,state,task)
        refs=[]; copies=[]
        for binding,(snapshot,info) in zip(task.input_bindings,inputs,strict=True):
            refs.append({**info,'task_id':binding.source_task_id,'step_id':binding.source_step_id,
                         'conditions':snapshot['conditions']})
            copy={'result_id':info['result_id'],'kind':snapshot['meta']['kind'],
                'rows':snapshot['rows'],'columns':snapshot['columns'],'total_rows':snapshot['total_rows']}
            # 예측 표는 순위 기준(group_by)이 있어야 리포트가 채팅과 같은 순위를 보인다(행 모양으로 추측하지 않는다).
            if snapshot['meta'].get('group_by'):
                copy['meta']={k:snapshot['meta'][k] for k in ('kind','group_by','anchor') if k in snapshot['meta']}
            copies.append(copy)
        if any(ref['conditions']!=refs[0]['conditions'] for ref in refs[1:]):
            raise SlotRepair('선행 결과의 분석 조건이 다릅니다. 같은 조건으로 다시 분석하세요.')
        table=store.t('api_snapshot')
        data=results.read_snapshot(store,state.input.user_id,created['preview_id'],'PREVIEW')
        store.c.execute(update(table).where(table.c.snapshot_id==created['preview_id']).values(
            payload_json=clean({**data,'source_results':refs,'source_tables':copies})))
    task.report_request=ReportRequest(preview_id=created['preview_id'],expires_at=created['expires_at'],
                                      conditions=task.conditions,formats=['PDF'])
    ask(state,task,key,'REPORT_CONFIRM','표시된 수출 분석 조건과 파일 형식을 확인한 뒤 리포트 생성을 선택하세요.',[])


def submit(store,state,task,step):
    request=task.report_request
    if not request or not request.confirmed:
        return ToolResult(status='NO_DATA',error='리포트 생성을 확인해 주세요.')
    data=ReportCreate(preview_id=request.preview_id,confirm_conditions=True,formats=request.formats,
                      session_id=state.input.session_id)
    key=f'chat:{state.input.job_id}:{step.step_id}'
    identity=digest({'conditions':api_conditions(task.conditions),'preview_id':request.preview_id,
                     'formats':sorted(data.formats),'sections':data.sections,
                     'sources':[state.results[b.source_step_id].result_id for b in task.input_bindings]})
    table=store.t('report')
    existing=store.one(select(table).where(table.c.user_id==state.input.user_id,
                              table.c.generation_key==digest([state.input.user_id,'REPORT',key])).with_for_update())
    if existing:
        if existing['conditions_json'].get('chat_input_fingerprint')!=identity:
            return ToolResult(status='NO_DATA',error='이전 리포트 승인 조건과 다릅니다. 새 질문에서 미리보기를 확인하세요.')
        rid=existing['report_id']
    else:
        approved=results.read_snapshot(store,state.input.user_id,request.preview_id,'PREVIEW')
        public=api_conditions(task.conditions)
        if any(approved['conditions'].get(k)!=v for k,v in public.items()):
            return ToolResult(status='NO_DATA',error='미리보기와 분석 조건이 다릅니다. 새 질문에서 확인하세요.')
        rid=reports.create(store,state.input.user_id,data,key)['job']['report_id']
        record=store.owned('report','report_id',rid,state.input.user_id,lock=True)
        content=dict(record.get('content_json') or {})
        if approved.get('source_tables'):
            content.update(source_results=approved['source_results'],source_tables=approved['source_tables'])
        store.c.execute(update(table).where(table.c.report_id==rid).values(content_json=clean(content),conditions_json=clean({
            **record['conditions_json'],'chat_input_fingerprint':identity,'chat_approval':request.model_dump(mode='json'),
            'chat_sources':[state.results[b.source_step_id].result_id for b in task.input_bindings]})))
    return ReportWait(step_id=step.step_id,report_id=str(rid),started_at=now())


def wake_ready(store,size):
    waiting=[]
    table=store.t('chat_message'); path=table.c.result_json[jobs.EXECUTION]
    stmt=select(table).where(table.c.role=='USER',path['status'].as_string()=='WAITING_REPORT').order_by(table.c.message_id)
    cursor=getattr(store.db,'agent_report_cursor',0)
    rows=store.rows(stmt.where(table.c.message_id>cursor).limit(size))
    if cursor and not rows: rows=store.rows(stmt.limit(size))
    for row in rows:
        store.db.agent_report_cursor=row['message_id']
        job=jobs._chat_job(row)
        wait=job['agent']['report_wait']
        report=store.owned('report','report_id',wait['report_id'],job['subject_user_id'])
        if report['status'] not in ('READY','FAILED'):
            waiting.append(job)
            continue
        current=jobs.locked_current(store,job)
        if current['status']!='WAITING_REPORT' or current['agent']['report_wait']!=wait: continue
        current.update(status='QUEUED',available_at=now().isoformat(),round_attempt=0)
        jobs._save_chat(store,current)
    return waiting


def renew_waiting(database,job):
    from backend.app.agents.worker import checkpointer
    from backend.app.agents.graph import graph_config
    from backend.app.api.redis_store import RedisState
    redis=getattr(database,'redis',None) or RedisState(database.settings)
    redis.renew_chat(str(job['target_key']),str(job['job_id']))
    checkpointer(database,database.settings).get_tuple(graph_config(job['job_id']))
