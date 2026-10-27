"""검증 가능한 조건·등록 사전만 사용하는 대화 분석 실행기."""
import re
import uuid
from datetime import date
from sqlalchemy import select, update, delete, null, func
from backend.app.api.common import require, now, clean, mask_text, page_info, period, shift_month, ApiError, digest
from backend.app.api import jobs, trade, news
from backend.app.api.accounts import validate_scope, interests
from backend.app.api.results import result_table, read_snapshot


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
        'content_masked':r['content_masked'],'answer':(r['result_json'] or {}).get('answer'),'created_at':r['created_at']} for r in rows],
        'page':page_info(page,size,store.count(stmt))}


def view_job(row):
    p=row['result_json'] or {}
    prompt=p.get('clarification')
    if prompt:
        prompt={**{k:prompt[k] for k in ('clarification_id','message')},
            'kind':'HS_ITEM' if prompt['type']=='HS_SELECTION' else 'INTEREST_CONTEXT','multiple':False,
            'choices':[{'choice_id':c['choice_id'],'label':c['label'],
                        'hs_item':c['value'].get('hs_items',[None])[0] if prompt['type']=='HS_SELECTION' else None,
                        'action':'SELECT_ITEM' if prompt['type']=='HS_SELECTION' else 'USE_INTERESTS' if c['choice_id']=='APPLY' else 'DECLINE_INTERESTS'} for c in prompt['choices']]}
    return {'job_id':str(row['job_id']),'session_id':str(row['target_key']),
        'status':p.get('status',row['status']),'step':p.get('step','CONTEXT'),
        'clarification':prompt,'answer':p.get('answer'),'error':p.get('error'),
        'retry_after_seconds':2 if row['status'] in ('QUEUED','RUNNING') else 0}


def submit(store,uid,identity,data):
    session=store.owned('chat_session','session_id',identity,uid,lock=True)
    question=mask_text(data['question'].strip())
    require(0<len(question)<=4000,'질문은 1~4,000자로 입력하세요.')
    require(bool(data.get('client_message_id')),'client_message_id가 필요합니다.')
    t=store.t('async_job')
    old=store.one(select(t).where(t.c.request_key==digest([uid,'API_CHAT',data['client_message_id']])))
    if old:
        require(old['target_key']==str(identity) and old['payload_json']['question']==question,'다른 메시지에 사용한 client_message_id입니다.','IDEMPOTENCY_CONFLICT',409)
        return {'job':view_job(old)}
    payload={'question':question,'conditions':session['context_json'].get('conditions',{})}
    row=jobs.enqueue(store,uid,'API_CHAT',identity,payload,data['client_message_id'])
    return {'job':view_job(row)}


def clarification(store,uid,identity,data):
    row=jobs.owned_job(store,uid,identity); result=row['result_json'] or {}; prompt=result.get('clarification') or {}
    require(result.get('status')=='NEEDS_INPUT' and prompt.get('clarification_id')==data['clarification_id'],
        '현재 되묻기 요청과 일치하지 않습니다.','CLARIFICATION_CONFLICT',409)
    choices={x['choice_id']:x for x in prompt['choices']}
    require(len(data['choice_ids'])==1 and set(data['choice_ids'])<=set(choices),'허용된 선택지를 하나 선택하세요.')
    conditions=dict(row['payload_json'].get('conditions',{}))
    if prompt['type']=='INTEREST_CONFIRM': require(len(data['choice_ids'])==1,'관심 적용 여부는 하나만 선택하세요.')
    if prompt['type']=='HS_SELECTION':
        conditions['hs_items']=list({x['hs_code']:x for key in data['choice_ids'] for x in choices[key]['value']['hs_items']}.values())
    else:
        for key in data['choice_ids']: conditions.update(choices[key]['value'])
    t=store.t('async_job')
    store.c.execute(update(t).where(t.c.job_id==identity,t.c.status=='SUCCEEDED').values(status='QUEUED',
        payload_json={**row['payload_json'],'conditions':conditions,'clarified':True},result_json=null(),finished_at=None,available_at=now()))
    return {'job':view_job(jobs.owned_job(store,uid,identity))}


def parse(store,question,previous,article_mode=False):
    conditions=dict(previous)
    countries=store.rows(select(store.t('country')).where(store.t('country').c.country_role=='COUNTRY'))
    found=[r['country_code'] for r in countries if r['name_ko'] in question or re.search(r'(?<![A-Za-z])'+re.escape(r['country_code'])+r'(?![A-Za-z])',question)]
    if found: conditions['country_codes']=sorted(set(found))
    if '전체 국가' in question or '전 세계' in question: conditions['country_codes']=[]
    codes=re.findall(r'(?<!\d)(\d{6}|\d{4})(?!\d)',question)
    codes=[x for x in codes if not (len(x)==4 and 1900<=int(x)<=2100)]
    if codes: conditions['hs_items']=[{'hs_code':c,'hs_ver':'HS2022'} for c in sorted(set(codes))]
    if '전체 품목' in question: conditions['hs_items']=[]
    conditions.setdefault('hs_items',[]); conditions.setdefault('country_codes',[])
    matches=re.findall(r'(20\d{2})[년.\-/ ]+\s*(1[0-2]|0?[1-9])\s*월?',question)
    if matches:
        starts=[date(int(y),int(m),1) for y,m in matches]
        conditions['period']=period(starts[0],starts[-1])
    elif re.search(r'20\d{2}년',question):
        year=int(re.search(r'(20\d{2})년',question)[1])
        conditions['period']=period(date(year,1,1),date(year,12,1))
    elif 'period' not in conditions or '최근' in question:
        if article_mode:
            a=store.t('article')
            latest=store.c.scalar(select(func.max(a.c.published_date)).where(a.c.visibility_status=='VISIBLE'))
            require(latest is not None,'기사 데이터 적재를 기다리고 있습니다.','DATA_NOT_READY',503)
        else:
            latest=trade.bounds(store)[-1]
        count=re.search(r'최근\s*(\d+)\s*개월',question)
        count=int(count[1]) if count else (3 if article_mode else 12)
        require(1<=count<=120,'기간은 1~120개월로 입력하세요.')
        conditions['period']=period(shift_month(latest,-count+1),latest)
    conditions['trade_flow']='IMPORT' if '수입' in question and '수출' not in question else conditions.get('trade_flow','EXPORT')
    conditions.setdefault('compare','YOY_PERIOD'); conditions.setdefault('metric','AMOUNT'); conditions.setdefault('compare_period',None)
    validate_scope(store,conditions['hs_items'],conditions['country_codes'])
    return conditions


def execute(store,job):
    uid=job['subject_user_id']; sid=job['target_key']; question=job['payload_json']['question']
    session=store.owned('chat_session','session_id',sid,uid,lock=True)
    if any(term in question for term in ('유망시장','유망 시장','성장 패턴','머신러닝','군집')):
        raise ApiError(503,'FEATURE_NOT_READY','ML 담당 모듈 연결을 기다리고 있습니다.')
    if any(term in question for term in ('원인','왜 ','요약해','요약 해','인과')):
        raise ApiError(503,'FEATURE_NOT_READY','RAG 담당 모듈 연결을 기다리고 있습니다.')
    article_mode='기사' in question or '이슈' in question
    known=article_mode or any(word in question for word in ('수출','수입','무역','비중','시장','추이','통계','순위','리포트'))
    if not known:
        return save_answer(store,uid,sid,question,{'intent':'OUT_OF_SCOPE','answer_status':'OUT_OF_SCOPE',
            'text':'수출입 통계·시장 비중·KOTRA 기사에 관한 질문을 입력하세요.','conditions':None,'tables':[],'charts':[],'articles':[],
            'verification':{'status':'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},'actions':[],'report_id':None,'warnings':[]})
    conditions=parse(store,question,job['payload_json'].get('conditions',{}),article_mode=article_mode)
    saved=interests(store,uid)
    if not job['payload_json'].get('conditions') and not job['payload_json'].get('clarified') and '전체' not in question and not conditions['hs_items'] and not conditions['country_codes'] and (saved['hs_items'] or saved['country_codes']):
        return {'status':'NEEDS_INPUT','step':'CONTEXT','clarification':{'clarification_id':uuid.uuid4().hex,
            'type':'INTEREST_CONFIRM','message':'저장한 관심 품목·국가를 이번 대화에 적용할까요?',
            'choices':[{'choice_id':'APPLY','label':'관심 범위 적용','value':{'hs_items':saved['hs_items'],'country_codes':saved['country_codes']}},
                       {'choice_id':'GLOBAL','label':'전체 범위','value':{'hs_items':[],'country_codes':[]}}]},'answer':None}
    # 품목명을 코드로 임의 추정하지 않는다. 등록 사전 후보를 확인받는다.
    if not conditions['hs_items'] and '전체 품목' not in question and not job['payload_json'].get('clarified'):
        hs=store.t('hs_code')
        words=[w for w in re.findall(r'[가-힣]{2,}',question) if w not in ('수출','수입','통계','추이','알려줘','미국','중국','일본','분석','시장','최근','순위')]
        candidates=[]
        for word in words:
            candidates+=store.rows(select(hs.c.hs_code,hs.c.hs_ver,hs.c.name_ko).where(hs.c.depth==4,hs.c.hs_ver=='HS2022',hs.c.name_ko.contains(word,autoescape=True)).limit(5))
        if candidates:
            candidates=list({r['hs_code']:r for r in candidates}.values())[:5]
            return {'status':'NEEDS_INPUT','step':'CONTEXT','clarification':{'clarification_id':uuid.uuid4().hex,
                'type':'HS_SELECTION','message':'분석할 품목을 선택하세요.','choices':[{'choice_id':r['hs_code'],'label':r['name_ko'],
                    'value':{'hs_items':[{'hs_code':r['hs_code'],'hs_ver':r['hs_ver']}]}} for r in candidates]},'answer':None}
    p=conditions['period']; hs=conditions['hs_items']; countries=conditions['country_codes']; tables=[]; articles=[]
    try:
        if article_mode:
            output=news.articles(store,uid,hs=hs,countries=countries,
                start=date.fromisoformat(p['start']),end=date.fromisoformat(p['end']),size=5)
            articles=output['items']; text=f'조건에 맞는 KOTRA 기사 {len(articles)}건의 본문 발췌입니다. RAG 요약은 연결 대기 중입니다.'; intent='LOCAL_ISSUES'
        elif '비중' in question:
            output=trade.market_shares(store,uid,conditions); tables=[output['table']]
            text='전체 수출국 합계를 분모로 계산한 국가별 비중입니다.'; intent='MARKET_SHARE'
        elif '순위' in question:
            output=trade.rankings(store,uid,p['start'][:7],p['end'][:7],hs,countries,flow=conditions['trade_flow'],size=100)
            from backend.app.api.results import get_table
            tables=[get_table(store,uid,output['result_id'],1,1000)]; text='선택한 기간의 수출입 금액 순위입니다.'; intent='RANKING'
        else:
            output=trade.trends(store,uid,p['start'][:7],p['end'][:7],hs=hs,countries=countries,flow=conditions['trade_flow'])
            from backend.app.api.results import get_table
            tables=[get_table(store,uid,output['result_id'],1,1000)]
            metric='export_amount_usd' if conditions['trade_flow']=='EXPORT' else 'import_amount_usd'
            total=sum(r[metric] for r in output['items']); text=f'선택 기간의 {"수출" if metric.startswith("export") else "수입"}액은 {total:,.2f} USD입니다.'; intent='STATISTICS'
        article_mode=intent=='LOCAL_ISSUES'
        answer={'intent':'EXPLAIN' if article_mode else 'STATISTICS','answer_status':'PARTIAL' if article_mode else 'ANSWERED','text':text,'conditions':conditions,'tables':tables,'charts':[],
            'articles':articles,'verification':{'status':'NEEDS_REVIEW' if article_mode else 'PASS','sentence_checks':None,'regeneration_count':0},
            'actions':(['SAVE_REPORT','VIEW_TABLE','EXPORT_XLSX']+(['ADD_INTEREST'] if all(len(x['hs_code'])==6 for x in hs) else [])) if tables else [],'report_id':None,'warnings':['RAG 모듈 미연결: 본문 발췌'] if article_mode else []}
    except ApiError as exc:
        if exc.code not in ('DATA_NOT_READY','PERIOD_NOT_AVAILABLE'): raise
        answer={'intent':'STATISTICS','answer_status':'NO_DATA','text':exc.message,'conditions':conditions,'tables':[],'charts':[],
            'articles':[],'verification':{'status':'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},'actions':[],'report_id':None,'warnings':[]}
    return save_answer(store,uid,sid,question,answer)


def save_answer(store,uid,sid,question,answer):
    store.insert('chat_message',session_id=int(sid),role='USER',content_masked=question,created_at=now())
    store.insert('chat_message',session_id=int(sid),role='ASSISTANT',content_masked=answer['text'],
        intent_json=clean({'conditions':answer['conditions'],'intent':answer['intent']}),result_json=clean({'schema_version':1,'answer':answer}),created_at=now())
    t=store.t('chat_session'); row=store.owned('chat_session','session_id',sid,uid)
    store.c.execute(update(t).where(t.c.session_id==sid).values(title=question[:160] if row['title']=='새 대화' else row['title'],
        updated_at=now(),context_json={'conditions':clean(answer['conditions'] or {}),'interest_action':'NONE'}))
    return {'status':'SUCCEEDED','step':'DONE','answer':answer,'clarification':None,'error':None}
