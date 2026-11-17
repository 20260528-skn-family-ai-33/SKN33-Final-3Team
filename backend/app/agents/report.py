"""Explicit preview approval and separate report completion polling (D-76/81)."""
from sqlalchemy import select, update
from backend.app.agents.conditions import ask
from backend.app.agents.state import ReportRequest,ReportWait,ToolResult
from backend.app.agents.templates import api_conditions
from backend.app.api import jobs,reports,results
from backend.app.api.chat_slots import SlotRepair
from backend.app.api.common import ApiError,clean,digest,now
from backend.app.api.schemas import ReportCreate


def source_ids(state,task):
    """리포트에 들어가는 결과표 ID. 같은 턴 선행 결과 또는 직전 답변에서 고른 결과표(previous_turn_result)."""
    if task.previous_turn_result:
        return [task.resolved[f'{task.task_id}:previous_result']['previous_result_id']]
    return [state.results[b.source_step_id].result_id for b in task.input_bindings]


def _sources(store,state,task):
    """(snapshot, info, 출처) 목록. 출처는 같은 턴이면 선행 단계, 직전 답변이면 previous_turn 표시다.
    직전 결과표를 조건 확인 뒤 다시 읽는 사이에 만료돼 REPORT_TABLE로 다시 묻는 중이면 None이다."""
    from backend.app.agents.bindings import previous_result, source_versions
    if task.previous_turn_result:
        found=previous_result(store,state,task)
        if found is None: return None
        snapshot,info=found
        return [(snapshot,info,{'previous_turn':True})]
    return [(snapshot,info,{'task_id':b.source_task_id,'step_id':b.source_step_id})
            for b,(snapshot,info) in zip(task.input_bindings,source_versions(store,state,task),strict=True)]


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
        sources=source_ids(state,task)
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
    # 입력 결과표를 미리보기보다 먼저 확인한다. 다시 고르는 중이면 쓰지 않을 미리보기를 만들지 않고 그 되묻기로 돌아간다.
    sources=None
    if task.input_bindings or task.previous_turn_result:
        sources=_sources(store,state,task)
        if sources is None: return
    public=api_conditions(task.conditions)
    frozen=False
    if sources is not None:
        refs=[]; copies=[]
        for snapshot,info,origin in sources:
            refs.append({**info,**origin,'conditions':snapshot['conditions']})
            # 표 종류·순위 기준·기간을 보존해 리포트와 채팅이 같은 열을 표시한다(results.display_columns).
            meta=snapshot['meta']
            copied_meta={k:meta[k] for k in ('kind','group_by','anchor','period','compare_period','extra_compare_periods',
                                             'trade_balance','period_totals')
                         if k in meta}
            if meta['kind']=='LLM_SQL':
                copied_meta['period_verified'] = meta.get('period_verified') is True
                if meta.get('annual_verified') is True:
                    copied_meta['annual_verified'] = True
            copies.append({'result_id':info['result_id'],'kind':meta['kind'],'meta':copied_meta,
                           'rows':snapshot['rows'],'columns':snapshot['columns'],'total_rows':snapshot['total_rows']})
        # 추가 비교 기간은 conditions가 아니라 meta에 있다. 기간이 다른 원본을 한 리포트로 묶지 않는다.
        extras=[snapshot['meta'].get('extra_compare_periods') or [] for snapshot,_,_ in sources]
        if (any(ref['conditions']!=refs[0]['conditions'] for ref in refs[1:])
                or any(extra!=extras[0] for extra in extras[1:])):
            raise SlotRepair('선행 결과의 분석 조건이 다릅니다. 같은 조건으로 다시 분석하세요.')
        source_items=refs[0]['conditions'].get('hs_items', [])
        if public['hs_items']!=source_items:
            raise SlotRepair('선행 결과의 품목과 판본을 변경하려면 새 분석 질문을 입력해 주세요.')
        frozen=any(item['hs_ver']=='HS2017' for item in source_items)
    # 결과표 기반 리포트는 원본의 비교 기간을 순서 그대로 옮긴다. 신규 조회용 "비교 기간은 앞서야" 제한은 첫 비교 기간만
    # 보므로(3월을 4월·2월과 비교하면 거절, 2월·4월이면 통과) 적용하지 않는다.
    created=reports.preview(store,state.input.user_id,{'conditions':public},frozen=frozen,from_results=sources is not None)
    if sources is not None:
        table=store.t('api_snapshot')
        data=results.read_snapshot(store,state.input.user_id,created['preview_id'],'PREVIEW')
        # 조건 변경을 반영하지 않았다는 안내(D-92)·수입 결과를 넣지 않았다는 안내는 승인 화면의 adjustments로 보여야
        # 생성 전에 알 수 있다.
        from backend.app.agents.conditions import PREVIOUS_TABLE_NOTE
        from backend.app.agents.planner import EXPORT_ONLY_REPORT_NOTE
        notes=[n for n in (PREVIOUS_TABLE_NOTE if task.previous_turn_result else None,EXPORT_ONLY_REPORT_NOTE)
               if n and n in task.applied_defaults]
        if notes:
            shown=data['conditions'].get('applied_defaults') or []
            data['conditions']={**data['conditions'],'applied_defaults':[*shown,*(n for n in notes if n not in shown)]}
        store.c.execute(update(table).where(table.c.snapshot_id==created['preview_id']).values(
            payload_json=clean({**data,'source_results':refs,'source_tables':copies})))
    task.report_request=ReportRequest(preview_id=created['preview_id'],expires_at=created['expires_at'],
                                      conditions=task.conditions,formats=['PDF'])
    # 무역수지 결과표만 옮기는 리포트는 승인 문구도 실제 지표로 쓴다(PDF의 요청 지표·표지와 같다).
    balance=reports.balance_copies([snapshot for snapshot,_,_ in sources or []])
    ask(state,task,key,'REPORT_CONFIRM',f"표시된 {'무역수지' if balance else '수출'} 분석 조건과 파일 형식을 확인한 뒤 리포트 생성을 선택하세요.",[])


def preview_view(store, uid, request):
    saved = results.read_snapshot(store, uid, request.preview_id, 'PREVIEW')
    conditions = saved['conditions']
    view = {'preview_id': request.preview_id, 'expires_at': request.expires_at.isoformat(), 'conditions': conditions,
            'adjustments': conditions.get('applied_defaults', []), 'formats': request.formats}
    if sources := saved.get('source_results'):
        view['source_results'] = sources
    if tables := saved.get('source_tables'):
        # preview에서 원본들의 기간 일치를 확인했으므로 첫 사본이 가진 기간을 표시한다.
        if periods := (tables[0].get('meta') or {}).get('extra_compare_periods'):
            view['extra_compare_periods'] = periods
    return view


def submit(store,state,task,step):
    request=task.report_request
    if not request or not request.confirmed:
        return ToolResult(status='NO_DATA',error='리포트 생성을 확인해 주세요.')
    data=ReportCreate(preview_id=request.preview_id,confirm_conditions=True,formats=request.formats,
                      session_id=state.input.session_id)
    key=f'chat:{state.input.job_id}:{step.step_id}'
    identity=digest({'conditions':api_conditions(task.conditions),'preview_id':request.preview_id,
                     'formats':sorted(data.formats),'sections':data.sections,
                     'sources':source_ids(state,task)})
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
        rid=reports.create(store,state.input.user_id,data,key,chat_approval=True)['job']['report_id']
        record=store.owned('report','report_id',rid,state.input.user_id,lock=True)
        content=dict(record.get('content_json') or {})
        if approved.get('source_tables'):
            content.update(source_results=approved['source_results'],source_tables=approved['source_tables'])
        # 리포트 워커는 READY 저장 때 content_json을 완성 문서로 바꾼다. 완료 후 답변의 부분 결과(PARTIAL) 판단에 쓸
        # 승인 사본의 누락 사유는 바뀌지 않는 conditions_json에 둔다(직전 턴 결과표는 depends_on 밖이라 따로 전파되지 않는다).
        from backend.app.agents.state import summarize_limitations
        limits=summarize_limitations([l for r in approved.get('source_results') or []
                                      if r.get('completeness')=='PARTIAL' for l in r.get('limitations') or []])
        # 무역수지 결과표만 옮긴 리포트라는 표시도 conditions_json에 둔다(완료 뒤 파일 이름·제목이 수출로 돌아가지 않게).
        tables=approved.get('source_tables') or []
        balance=reports.balance_copies(tables)
        store.c.execute(update(table).where(table.c.report_id==rid).values(content_json=clean(content),conditions_json=clean({
            **record['conditions_json'],'chat_input_fingerprint':identity,'chat_approval':request.model_dump(mode='json'),
            'chat_sources':source_ids(state,task),'chat_limitations':limits,
            **({'chat_metric':'BALANCE'} if balance else {})})))
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
        if report['status'] not in ('READY','FAILED') and not report['conditions_json'].get('deleted_at'):
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
