"""검증 가능한 조건·등록 사전만 사용하는 대화 분석 실행기."""
import uuid
from datetime import date
from sqlalchemy import select, update
from backend.app.api.common import require, now, clean, mask_text, page_info, percentage, ApiError
from backend.app.api import jobs, trade, news
from backend.app.api.accounts import validate_scope, interests


def session_view(row):
    context=row['context_json'].get('conditions') or None
    return {'session_id':str(row['session_id']),'title':row['title'],'updated_at':row['updated_at'],'context':context}


def sessions(store,uid,page=1,size=20):
    t=store.t('chat_session'); stmt=select(t).where(t.c.user_id==uid)
    return {'items':[session_view(r) for r in store.rows(stmt.order_by(t.c.updated_at.desc(),t.c.session_id.desc()).offset((page-1)*size).limit(size))],
            'page':page_info(page,size,store.count(stmt))}


def create(store,uid,conditions):
    if conditions: validate_scope(store,conditions.get('hs_items',[]),conditions.get('country_codes',[]))
    identity=store.insert('chat_session',user_id=uid,title='새 대화',context_json={'conditions':conditions or {},'interest_action':'NONE'},updated_at=now())
    return {'session':session_view(store.owned('chat_session','session_id',identity,uid))}


def messages(store,uid,identity,page=1,size=20):
    session=store.owned('chat_session','session_id',identity,uid)
    t=store.t('chat_message'); stmt=select(t).where(t.c.session_id==identity)
    rows=store.rows(stmt.order_by(t.c.message_id).offset((page-1)*size).limit(size))
    return {'session':session_view(session),'items':[{'message_id':str(r['message_id']),'role':r['role'],
        'content_masked':r['content_masked'],'answer':(r['result_json'] or {}).get('answer'),
        'job':view_job(jobs._chat_job(r)) if jobs._chat_job(r) else None,'created_at':r['created_at']} for r in rows],
        'page':page_info(page,size,store.count(stmt))}


def view_job(row):
    p=row['result_json'] or {}
    prompt=p.get('clarification')
    if prompt:
        prompt={**{k:prompt[k] for k in ('clarification_id','message')},
            'kind':'HS_ITEM' if prompt['type']=='HS_SELECTION' else 'INTEREST_CONTEXT','multiple':False,
            'browse_url':prompt.get('browse_url'),
            'choices':[{'choice_id':c['choice_id'],'label':c['label'],
                        'hs_item':c['value'].get('hs_items',[None])[0] if prompt['type']=='HS_SELECTION' else None,
                        'action':'SELECT_ITEM' if prompt['type']=='HS_SELECTION' else 'USE_INTERESTS' if c['choice_id']=='APPLY' else 'DECLINE_INTERESTS'} for c in prompt['choices']]}
    return {'job_id':str(row['job_id']),'session_id':str(row['target_key']),
        'status':p.get('status',row['status']),'step':p.get('step','CONTEXT'),
        'clarification':prompt,'answer':p.get('answer'),'error':p.get('error'),
        'retry_after_seconds':2 if row['status'] in ('QUEUED','RUNNING') else 0}


def submit(store,uid,identity,data):
    u=store.t('users')
    store.c.execute(select(u.c.user_id).where(u.c.user_id==uid).with_for_update()).first()
    session=store.owned('chat_session','session_id',identity,uid,lock=True)
    question=mask_text(data['question'].strip())
    require(0<len(question)<=4000,'질문은 1~4,000자로 입력하세요.')
    require(bool(data.get('client_message_id')),'client_message_id가 필요합니다.')
    old=jobs.find_chat(store,uid,data['client_message_id'])
    if old:
        require(old['target_key']==str(identity) and old['payload_json']['question']==question,'다른 메시지에 사용한 client_message_id입니다.','IDEMPOTENCY_CONFLICT',409)
        return {'job':view_job(old)}
    payload={'question':question,'conditions':session['context_json'].get('conditions',{})}
    row=jobs.enqueue(store,uid,'API_CHAT',identity,payload,data['client_message_id'])
    return {'job':view_job(row)}


def clarification(store,uid,identity,data):
    row=jobs.owned_job(store,uid,identity,lock=True); result=row['result_json'] or {}; prompt=result.get('clarification') or {}
    require(result.get('status')=='NEEDS_INPUT' and prompt.get('clarification_id')==data['clarification_id'],
        '현재 되묻기 요청과 일치하지 않습니다.','CLARIFICATION_CONFLICT',409)
    if data.get('hs_item'):
        require(prompt['type']=='HS_SELECTION' and not data.get('choice_ids'),'품목 되묻기에서 직접 선택만 제출하세요.')
        item=data['hs_item']; validate_scope(store,[item],[])
        conditions=select_hs(row,[item])
        return {'job':view_job(jobs.requeue_chat(store,row,conditions))}
    require(bool(data.get('choice_ids')),'선택지 또는 직접 선택한 품목이 필요합니다.')
    choices={x['choice_id']:x for x in prompt['choices']}
    require(len(data['choice_ids'])==1 and set(data['choice_ids'])<=set(choices),'허용된 선택지를 하나 선택하세요.')
    conditions=dict(row['payload_json'].get('conditions',{}))
    if prompt['type']=='INTEREST_CONFIRM': require(len(data['choice_ids'])==1,'관심 적용 여부는 하나만 선택하세요.')
    if prompt['type']=='HS_SELECTION':
        conditions=select_hs(row,[x for key in data['choice_ids'] for x in choices[key]['value']['hs_items']])
    else:
        for key in data['choice_ids']: conditions.update(choices[key]['value'])
    return {'job':view_job(jobs.requeue_chat(store,row,conditions))}


def select_hs(job,items):
    """Resolve one expression per clarification, preserving selections for other expressions."""
    payload=job['payload_json']; pending=payload.get('hs_selection')
    selected=items
    if pending:
        selected=list({(x['hs_code'],x['hs_ver']):x for x in pending['selected']+items}.values())
        payload['hs_selection']={'remaining':pending['remaining'][1:],'selected':selected}
    return {**payload.get('conditions',{}),'hs_items':selected}


def hs_prompt(store,job,conditions):
    from backend.app.api.reference import chat_hs_search
    pending=job['payload_json']['hs_selection']; expression=pending['remaining'][0]
    search=chat_hs_search(store,expression); candidates=search['items']
    job['payload_json']['conditions']=conditions; jobs._save_chat(store,job)
    return {'status':'NEEDS_INPUT','step':'CONTEXT','clarification':{'clarification_id':uuid.uuid4().hex,
        'type':'HS_SELECTION','message':f'“{expression}”에 해당하는 품목을 선택하세요. 남은 품목 {len(pending["remaining"])}개. '+
            (search.get('message','품목 설명을 확인하고 선택하세요.') if candidates else '후보가 없습니다. 분류에서 직접 선택하거나 품목명을 바꿔 새 질문을 입력하세요.'),
        'browse_url':'/api/v1/reference/hs-codes/ROOT/children?hs_ver=HS2022',
        'choices':[{'choice_id':r['hs_code'],'label':r['name_ko'],
            'value':{'hs_items':[{'hs_code':r['hs_code'],'hs_ver':r['hs_ver']}]}} for r in candidates]},'answer':None}


def repair_answer(store,uid,sid,question,message):
    return save_answer(store,uid,sid,question,{'intent':'STATISTICS','answer_status':'NO_DATA',
        'text':message,'conditions':None,'tables':[],'charts':[],'articles':[],
        'verification':{'status':'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},
        'actions':[],'report_id':None,'warnings':[message]})


def execute(store,job):
    uid=job['subject_user_id']; sid=job['target_key']; question=job['payload_json']['question']
    session=store.owned('chat_session','session_id',sid,uid,lock=True)
    from backend.app.api.intent import OpenAIIntentProvider, IntentSlots
    from backend.app.api.chat_slots import resolve, SlotRepair
    from backend.app.api.config import Settings
    import logging
    payload=job['payload_json']
    try:
        raw=payload.get('intent_slots')
        if raw is None:
            provider=getattr(store.db,'intent_provider',None) or OpenAIIntentProvider(getattr(store.db,'settings',None) or Settings())
            raw=provider.extract(question=question,previous_conditions=payload.get('conditions',{}))
        slots=IntentSlots.model_validate(raw)
        job['payload_json']={**payload,'intent_slots':slots.model_dump()}
        jobs._save_chat(store,job)
    except Exception as exc:
        logging.getLogger(__name__).warning('intent_unavailable type=%s',type(exc).__name__)
        return repair_answer(store,uid,sid,question,'질문을 해석하지 못했습니다. 국가·품목·기간을 명시해 다시 입력해 주세요.')
    if slots.intent in ('ML','RAG'):
        return repair_answer(store,uid,sid,question,f'{slots.intent} 담당 모듈 연결을 기다리고 있습니다.')
    article_mode=slots.intent=='LOCAL_ISSUES'
    if slots.intent=='OUT_OF_SCOPE':
        return save_answer(store,uid,sid,question,{'intent':'OUT_OF_SCOPE','answer_status':'OUT_OF_SCOPE',
            'text':'수출입 통계·시장 비중·KOTRA 기사에 관한 질문을 입력하세요.','conditions':None,'tables':[],'charts':[],'articles':[],
            'verification':{'status':'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},'actions':[],'report_id':None,'warnings':[]})
    warnings=[]; expressions=[]
    try:
        if payload.get('clarified'):
            conditions=payload['conditions']
            warnings=payload.get('slot_warnings',[])
            validate_scope(store,conditions.get('hs_items',[]),conditions.get('country_codes',[]))
        else:
            conditions,warnings,expressions=resolve(store,question,payload.get('conditions',{}),slots)
            job['payload_json']['slot_warnings']=warnings
        if slots.intent=='RANKING' and conditions.get('ranking_group') not in ('COUNTRY','HS6'):
            raise SlotRepair('국가 순위와 품목 순위 중 어떤 순위를 조회할지 알려 주세요.')
        validate_scope(store,conditions.get('hs_items',[]),conditions.get('country_codes',[]))
    except (SlotRepair,ApiError) as exc:
        return repair_answer(store,uid,sid,question,str(exc) if isinstance(exc,SlotRepair) else exc.message)
    saved=interests(store,uid)
    if not job['payload_json'].get('conditions') and not job['payload_json'].get('clarified') and not slots.all_products and not slots.all_countries and not expressions and not conditions['hs_items'] and not conditions['country_codes'] and (saved['hs_items'] or saved['country_codes']):
        job['payload_json']['conditions']=conditions; jobs._save_chat(store,job)
        return {'status':'NEEDS_INPUT','step':'CONTEXT','clarification':{'clarification_id':uuid.uuid4().hex,
            'type':'INTEREST_CONFIRM','message':'저장한 관심 품목·국가를 이번 대화에 적용할까요?',
            'choices':[{'choice_id':'APPLY','label':'관심 범위 적용','value':{'hs_items':saved['hs_items'],'country_codes':saved['country_codes']}},
                       {'choice_id':'GLOBAL','label':'전체 범위','value':{'hs_items':[],'country_codes':[]}}]},'answer':None}
    if expressions and not slots.all_products and not payload.get('clarified'):
        job['payload_json']['hs_selection']={'remaining':list(dict.fromkeys(expressions)),'selected':[]}
    if job['payload_json'].get('hs_selection',{}).get('remaining'):
        return hs_prompt(store,job,conditions)
    p=conditions['period']; hs=conditions['hs_items']; countries=conditions['country_codes']; tables=[]; articles=[]
    try:
        if article_mode:
            output=news.articles(store,uid,hs=hs,countries=countries,
                start=date.fromisoformat(p['start']),end=date.fromisoformat(p['end']),size=5)
            articles=output['items']; text=f'조건에 맞는 KOTRA 기사 {len(articles)}건의 본문 발췌입니다. RAG 요약은 연결 대기 중입니다.'; intent='LOCAL_ISSUES'
        elif slots.intent=='MARKET_SHARE':
            output=trade.market_shares(store,uid,conditions); tables=[output['table']]
            text='전체 수출국 합계를 분모로 계산한 국가별 비중입니다.'; intent='MARKET_SHARE'
        elif slots.intent=='RANKING':
            output=trade.rankings(store,uid,p['start'][:7],p['end'][:7],hs,countries,
                group=conditions['ranking_group'],flow=conditions['trade_flow'],size=100)
            from backend.app.api.results import get_table
            tables=[get_table(store,uid,output['result_id'],1,1000)]; text=f'선택한 기간의 {"품목" if conditions["ranking_group"]=="HS6" else "국가"}별 수출입 금액 순위입니다.'; intent='RANKING'
        else:
            from backend.app.api.results import get_table
            metric='export_amount_usd' if conditions['trade_flow']=='EXPORT' else 'import_amount_usd'
            prior_metric='previous_year_export_usd' if conditions['trade_flow']=='EXPORT' else 'previous_year_import_usd'
            descriptions=[]
            for item_scope in ([[item] for item in hs] if len(hs)>1 else [hs]):
                output=trade.trends(store,uid,p['start'][:7],p['end'][:7],hs=item_scope,countries=countries,flow=conditions['trade_flow'])
                table=get_table(store,uid,output['result_id'],1,1000)
                if len(hs)>1: table['hs_item']=item_scope[0]
                tables.append(table)
                total=sum(r[metric] for r in output['items'])
                label=f'HS {item_scope[0]["hs_code"]}: ' if len(hs)>1 else ''
                description=f'{label}선택 기간의 {"수출" if metric.startswith("export") else "수입"}액은 {total:,.2f} USD입니다.'
                if conditions.get('compare_period'):
                    if any(r[prior_metric] is None for r in output['items']):
                        raise ApiError(503,'DATA_NOT_READY','전년 비교 기간의 검증된 데이터가 없습니다.')
                    baseline=sum(r[prior_metric] for r in output['items'])
                    growth=percentage(total,baseline)
                    description+=f' 전년 동기 {baseline:,.2f} USD 대비 증감액은 {total-baseline:,.2f} USD이며, '+(
                        f'증감률은 {growth:,.2f}%입니다.' if growth is not None else '전년 금액이 0이므로 증감률은 계산하지 않습니다.')
                descriptions.append(description)
            text='\n'.join(descriptions); intent='STATISTICS'
        article_mode=intent=='LOCAL_ISSUES'
        answer={'intent':'EXPLAIN' if article_mode else 'STATISTICS','answer_status':'PARTIAL' if article_mode else 'ANSWERED','text':text,'conditions':conditions,'tables':tables,'charts':[],
            'articles':articles,'verification':{'status':'NEEDS_REVIEW' if article_mode else 'PASS','sentence_checks':None,'regeneration_count':0},
            'actions':(['SAVE_REPORT','VIEW_TABLE','EXPORT_XLSX']+(['ADD_INTEREST'] if all(len(x['hs_code'])==6 for x in hs) else [])) if tables else [],'report_id':None,'warnings':warnings+(['RAG 모듈 미연결: 본문 발췌'] if article_mode else [])}
    except ApiError as exc:
        if exc.code not in ('DATA_NOT_READY','PERIOD_NOT_AVAILABLE'): raise
        answer={'intent':'STATISTICS','answer_status':'NO_DATA','text':exc.message,'conditions':conditions,'tables':[],'charts':[],
            'articles':[],'verification':{'status':'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},'actions':[],'report_id':None,'warnings':[]}
    return save_answer(store,uid,sid,question,answer)


def save_answer(store,uid,sid,question,answer):
    # 질문은 접수 시 저장한다. 되묻기와 재시도에서도 같은 USER 행을 유지한다.
    store.insert('chat_message',session_id=int(sid),role='ASSISTANT',content_masked=answer['text'],
        intent_json=clean({'conditions':answer['conditions'],'intent':answer['intent']}),result_json=clean({'schema_version':1,'answer':answer}),created_at=now())
    t=store.t('chat_session'); row=store.owned('chat_session','session_id',sid,uid)
    context=row['context_json']
    if answer['answer_status'] in ('ANSWERED','PARTIAL') and answer['conditions'] is not None:
        context={'conditions':clean(answer['conditions']),'interest_action':'NONE'}
    store.c.execute(update(t).where(t.c.session_id==sid).values(title=question[:160] if row['title']=='새 대화' else row['title'],
        updated_at=now(),context_json=context))
    return {'status':'SUCCEEDED','step':'DONE','answer':answer,'clarification':None,'error':None}
