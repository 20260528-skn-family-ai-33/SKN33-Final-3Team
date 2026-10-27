import hashlib
import json
from pathlib import Path
from datetime import date, timedelta
from sqlalchemy import select, update, delete
from backend.app.api.common import require, unavailable, now, digest, period, percentage, clean, page_info, shift_month, month_end
from backend.app.api import trade, news, jobs
from backend.app.api.accounts import interests, validate_scope
from backend.app.api.results import read_snapshot, save_snapshot, result_table
from backend.app.api.files import report_pdf, report_docx

TITLES=json.loads((Path(__file__).resolve().parents[1]/'templates/reports/layout.json').read_text(encoding='utf-8'))['sections']


def display_title(record):
    labels={'RPT_ON_DEMAND':'요청형','RPT_WEEKLY':'주간','RPT_MONTHLY':'월간','RPT_QUARTERLY':'분기'}
    p=record['conditions_json'].get('period',{})
    return f"{labels.get(record['report_type'],record['report_type'])} 리포트 · {p.get('start','')} ~ {p.get('end','')}"


def preview(store,uid,data):
    require(bool(data.get('conditions')) != bool(data.get('result_id')),'conditions와 result_id 중 하나만 지정하세요.')
    require(not data.get('period_preset'),'기간 프리셋 입력 예외 계약 DEC-28 확정이 필요합니다.','FEATURE_NOT_READY',503)
    if data.get('result_id'):
        conditions=read_snapshot(store,uid,data['result_id'],'RESULT')['conditions']
    else:
        conditions=data.get('conditions',{})
    require(bool(conditions),'분석 조건 또는 result_id가 필요합니다.')
    from backend.app.api.schemas import Conditions
    conditions=Conditions.model_validate(conditions).model_dump(mode='json')
    require(conditions['trade_flow']=='EXPORT','리포트는 현재 수출 분석만 지원합니다.','UNSUPPORTED_TRADE_FLOW',422)
    validate_scope(store,conditions['hs_items'],conditions['country_codes'])
    p=conditions['period']; latest=trade.bounds(store)[-1]
    adjustments=[]
    if date.fromisoformat(p['start']).day!=1 or date.fromisoformat(p['end'])!=month_end(date.fromisoformat(p['end'])):
        p['start']=date.fromisoformat(p['start']).replace(day=1).isoformat()
        p['end']=month_end(date.fromisoformat(p['end'])).isoformat()
        adjustments.append('월별 통계 기준으로 시작일·종료일을 월초·월말로 조정')
    if date.fromisoformat(p['end'])>month_end(latest): p['end']=month_end(latest).isoformat()
    require(p['start']<=p['end'],'현재 공개 데이터 범위로 조정할 수 없습니다.','PERIOD_NOT_AVAILABLE',422)
    trade.resolve_period(store,p['start'][:7],p['end'][:7])
    if not conditions['compare_period']:
        s=shift_month(date.fromisoformat(p['start']),-12); e=shift_month(date.fromisoformat(p['end']),-12)
        conditions['compare_period']=period(s,e)
    require(conditions['compare_period']['end']<p['start'],'비교 기간은 분석 기간보다 앞서야 합니다.')
    conditions['applied_defaults']=adjustments; conditions['expanded_regions']=[]
    identity=save_snapshot(store,uid,'PREVIEW',{'conditions':conditions},600)
    return {'preview_id':identity,'conditions':conditions,'needs_confirmation':True,
            'suggested_sections':['KPI','TOP_COUNTRIES','GROWTH_RISK','LOCAL_ISSUES'], 'expires_at':(now()+timedelta(minutes=10)).isoformat()+'Z'}


def create(store,uid,data,key):
    require(key and len(key)<=200,'Idempotency-Key가 필요합니다.')
    r,u=store.t('report'),store.t('users')
    store.c.execute(select(u.c.user_id).where(u.c.user_id==uid).with_for_update()).first()
    generation=digest([uid,'REPORT',key])
    existing=store.one(select(r).where(r.c.generation_key==generation))
    if existing:
        require(existing['conditions_json'].get('request_hash')==digest(data.model_dump()),'멱등성 키의 요청 내용이 다릅니다.','IDEMPOTENCY_CONFLICT',409)
        return {'job':status_view(store,existing)}
    p=read_snapshot(store,uid,data.preview_id,'PREVIEW')
    if data.session_id: store.owned('chat_session','session_id',data.session_id,uid)
    opt=store.t('service_option')
    require(store.c.scalar(select(opt.c.option_code).where(opt.c.option_code=='RPT_ON_DEMAND',opt.c.enabled==True)) is not None,
        '요청형 리포트 서비스 옵션 등록이 필요합니다.','FEATURE_NOT_READY',503)
    conditions={'schema_version':1,**p['conditions'],'sections':data.sections or list(TITLES)[1:5],
                'formats':sorted(set(data.formats)),'session_id':data.session_id,'request_hash':digest(data.model_dump())}
    rid=store.insert('report',user_id=uid,report_type='RPT_ON_DEMAND',generation_key=generation,
        conditions_json=conditions,status='QUEUED',retry_count=0,created_at=now())
    jobs.enqueue(store,uid,'API_REPORT',rid,{'report_id':str(rid)},key)
    return {'job':status_view(store,store.owned('report','report_id',rid,uid))}


def formats(store,rid):
    t=store.t('api_report_file')
    selected=sorted(store.c.execute(select(t.c.format).where(t.c.report_id==rid)).scalars())
    return selected+['XLSX'] if selected else []


def status_view(store,r):
    job=jobs.report_execution(store,r)
    info=(job or {}).get('result_json') or {}
    return {'report_id':str(r['report_id']),'status':r['status'],'step':info.get('step','DONE' if r['status']=='READY' else 'QUERY'),
        'retry_count':r['retry_count'],'retry_after_seconds':0 if r['status'] in ('READY','FAILED') else 2,
        'error':info.get('error'),'available_formats':formats(store,r['report_id']) if r['status']=='READY' else []}


def summary_view(store,r):
    c=r['conditions_json']; content=r['content_json'] or {}
    return {'report_id':str(r['report_id']),'report_type':r['report_type'],
        'subscription_id':str(r['subscription_id']) if r['subscription_id'] else None,
        'title':display_title(r),'conditions':{k:v for k,v in c.items() if k in ('hs_items','country_codes','period','compare_period','trade_flow','compare','metric','applied_defaults','expanded_regions')},
        'status':r['status'],'created_at':r['created_at'],'completed_at':r['completed_at'],
        'available_formats':formats(store,r['report_id']) if r['status']=='READY' else []}


def listing(store,uid,q=None,kind=None,status=None,page=1,size=20):
    t=store.t('report'); stmt=select(t).where(t.c.user_id==uid)
    if kind: stmt=stmt.where(t.c.report_type==kind)
    if status: stmt=stmt.where(t.c.status==status)
    rows=store.rows(stmt.order_by(t.c.created_at.desc(),t.c.report_id.desc()))
    if q: rows=[r for r in rows if q in display_title(r)]
    return {'items':[summary_view(store,r) for r in rows[(page-1)*size:page*size]],'page':page_info(page,size,len(rows))}


def read_report(store,uid,rid):
    r=store.owned('report','report_id',rid,uid)
    require(r['status']=='READY','리포트가 아직 준비되지 않았습니다.','REPORT_NOT_READY',409)
    content=r['content_json']
    require(content.get('schema_version')==1,'이전 리포트 JSON 변환 계약 확인이 필요합니다.','FEATURE_NOT_READY',503)
    return {'report':summary_view(store,r),'sections':content['sections'],'meta':content['meta']}


def section(code,text,tables=None,articles=None,status='READY',kpi=None):
    return {'section_code':code,'title':TITLES[code],'text':text,'kpi':kpi,'tables':tables or [],'charts':[],
        'articles':articles or [],'verification':{'status':'PASS' if status=='READY' else 'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},'content_status':status}


def build_document(store,uid,record):
    c=record['conditions_json']; hs=c.get('hs_items',[]); countries=c.get('country_codes',[])
    p=c['period']
    if record['report_type']=='RPT_WEEKLY':
        meta=news.source_meta(store,'KOTRA',date.fromisoformat(p['end']))
        articles=news.articles(store,uid,start=date.fromisoformat(p['start']),end=date.fromisoformat(p['end']),hs=hs,countries=countries,size=20)['items']
        sections=[section('NEWS','지난주 관심 범위에 해당하는 기사 본문 발췌입니다. RAG 요약은 미연결 상태입니다.',articles=articles,status='POLICY_PENDING')]
    else:
        start,end=trade.resolve_period(store,p['start'][:7],p['end'][:7])
        meta=trade.metadata(store,start,end)
        rows=trade.aggregate(store,'export_summary',start,end,hs,countries)
        if not rows: unavailable('리포트 조건에 맞는 수출 통계가 없습니다.')
        total=sum(r['amount_usd'] for r in rows)
        prev=c.get('compare_period'); old=None
        if prev:
            try:
                old_s,old_e=trade.resolve_period(store,prev['start'][:7],prev['end'][:7])
                previous=trade.aggregate(store,'export_summary',old_s,old_e,hs,countries)
                if previous: old=sum(r['amount_usd'] for r in previous)
            except Exception as exc:
                from backend.app.api.common import ApiError
                if not isinstance(exc,ApiError) or exc.code not in ('DATA_NOT_READY','PERIOD_NOT_AVAILABLE'): raise
                meta['warnings'].append('비교 기간 데이터 미준비: 증감률은 null')
                meta['freshness']='STALE'
        growth=percentage(total,old); count=sum(r['amount_usd']>0 for r in rows)
        kpi={'amount_usd':total,'mom_pct':None,'yoy_pct':growth,'export_country_count':count,
             'top_country_share_pct':max(r['amount_usd'] for r in rows)*100/total if total else None}
        text=f'분석 기간 수출액은 {total:,.2f} USD입니다. 수출 실적이 있는 국가는 {count}개국입니다. '
        text+=f'이전 비교 기간 대비 증감률은 {growth:,.2f}%입니다.' if growth is not None else '이전 비교 기간 증감률은 계산할 수 없습니다.'
        sections=[section('SUMMARY',text)]
        selected=c.get('sections',list(TITLES)[1:5])
        if 'KPI' in selected:
            table=result_table(store,uid,[kpi],meta,c,store.queries)
            monthly=[{'month':r['trade_month'].strftime('%Y-%m'),'amount_usd':r['amount_usd']} for r in trade.aggregate(store,'export_summary',start,end,hs,countries,'MONTH')]
            trend=result_table(store,uid,monthly,meta,c,store.queries)
            block=section('KPI',text,[table,trend],kpi=clean(kpi))
            block['charts']=[{'chart_type':'LINE','result_id':trend['result_id'],'x_key':'month','series_keys':['amount_usd'],'title':'월별 수출 추이 (USD)','positive_color':'#16A34A','negative_color':'#DC2626'}]
            sections.append(block)
        if 'TOP_COUNTRIES' in selected:
            names={r['country_code']:r['name_ko'] for r in store.rows(select(store.t('country')))}
            top=[{'country':names[r['country_code']],'amount_usd':r['amount_usd'],'share_pct':r['amount_usd']*100/total if total else None} for r in sorted(rows,key=lambda r:(-r['amount_usd'],r['country_code']))[:10]]
            table=result_table(store,uid,top,meta,c,store.queries)
            block=section('TOP_COUNTRIES','분석 조건 내 수출액 상위 10개국입니다.',[table])
            block['charts']=[{'chart_type':'HORIZONTAL_BAR','result_id':table['result_id'],'x_key':'country','series_keys':['amount_usd'],'title':'주요 수출국 (USD)','positive_color':'#16A34A','negative_color':'#DC2626'}]
            sections.append(block)
        if 'GROWTH_RISK' in selected: sections.append(section('GROWTH_RISK','유망시장·ML 선정 기준은 팀 협의 보류 상태입니다.',status='POLICY_PENDING'))
        if 'LOCAL_ISSUES' in selected:
            articles=news.articles(store,uid,start=start,end=month_end(end),hs=hs,countries=countries,size=5)['items']
            sections.append(section('LOCAL_ISSUES','분석 조건과 기간에 해당하는 기사 본문 발췌입니다. RAG 요약·인과 분석 모듈은 미연결 상태입니다.',articles=articles,status='POLICY_PENDING'))
            meta['sources'].append({'code':'KOTRA','label':'KOTRA 해외시장뉴스·단신속보','url':None})
    title='수출 시장 분석 리포트' if record['report_type']!='RPT_WEEKLY' else '주간 해외시장 뉴스 리포트'
    subtitle=f"HS {', '.join(x['hs_code'] for x in hs) or '전체'} · {', '.join(countries) or '전체 국가'} · {p['start']} ~ {p['end']} · USD"
    return clean({'schema_version':1,'title':title,'subtitle':subtitle,'generated_at':now().isoformat()+'Z','sections':sections,'meta':meta,'sql':store.queries})


def generate(store,job,settings):
    jobs.report_step(store,job,'QUERY')
    uid=job['subject_user_id']; rid=job['payload_json']['report_id']
    record=store.owned('report','report_id',rid,uid,lock=True)
    doc=build_document(store,uid,record)
    directory=settings.files/str(uid)/str(rid)/str(job['claim_token']); directory.mkdir(parents=True,exist_ok=True)
    files=store.t('api_report_file'); selected=record['conditions_json'].get('formats',['PDF'])
    # 기존 report_chk_1을 유지하기 위해 PDF를 보관용 원본으로 생성한다. 공개 다운로드는 선택 양식만.
    jobs.report_step(store,job,'PDF')
    pdf=report_pdf(doc,settings); archive=directory/'report.pdf'; archive.write_bytes(pdf)
    for fmt in selected:
        jobs.report_step(store,job,fmt)
        content=pdf if fmt=='PDF' else report_docx(doc,settings)
        path=directory/('report.'+fmt.lower()); path.write_bytes(content)
        store.c.execute(delete(files).where(files.c.report_id==rid,files.c.format==fmt))
        store.insert('api_report_file',report_id=int(rid),format=fmt,storage_path=str(path),sha256=hashlib.sha256(content).hexdigest(),created_at=now())
    t=store.t('report')
    store.c.execute(update(t).where(t.c.report_id==rid).values(status='READY',content_json=doc,pdf_uri=str(archive),completed_at=now()))
    if record['subscription_id']:
        from backend.app.api.notifications import settings as notification_settings
        conf=notification_settings(store,uid)
        if conf['notifications_enabled'] and conf['consent_current']:
            event=store.insert('alert_event',event_type='REPORT',report_id=int(rid),event_key=digest(['REPORT',rid]),
                payload_json={'destination':'REPORT_DETAIL'},created_at=now())
            store.insert('user_alert',user_id=uid,event_id=event,is_interest_match=False,created_at=now())
    return {'report_id':str(rid),'step':'DONE'}


def subscription_list(store,uid):
    t=store.t('report_subscription')
    rows=store.rows(select(t).where(t.c.user_id==uid).order_by(t.c.subscription_id))
    items=[]
    for r in rows:
        p=r['filters_json']
        items.append({'subscription_id':str(r['subscription_id']),'period_code':r['period_code'],'enabled':bool(r['enabled']),
            'hs_items':p.get('hs_items',[]),'country_codes':p.get('countries',[]),'topic':p.get('topic'),
            'formats':p.get('formats',['PDF']),'version':digest(r)})
    return {'items':items,'version':digest(items)}


def save_subscriptions(store,uid,data):
    u,t=store.t('users'),store.t('report_subscription')
    store.c.execute(select(u.c.user_id).where(u.c.user_id==uid).with_for_update()).first()
    require(subscription_list(store,uid)['version']==data['expected_version'],'구독 설정이 변경되었습니다.','VERSION_CONFLICT',409)
    selected=set(data['period_codes']); formats=set(data.get('formats',['PDF']))
    require(selected,'주기를 한 개 이상 선택하세요.')
    require(data.get('use_saved_interests') is True,'use_saved_interests는 true만 허용합니다.')
    require(formats and formats<={'PDF','DOCX'},'PDF/DOCX를 1개 이상 선택하세요.')
    o=store.t('service_option')
    allowed=set(store.c.execute(select(o.c.option_code).where(o.c.option_type=='REPORT_PERIOD',o.c.enabled==True)).scalars())
    require(selected<=allowed,'비활성 또는 미등록 구독 주기입니다.','FEATURE_NOT_AVAILABLE',422)
    scope=interests(store,uid) if data.get('use_saved_interests',True) else data
    hs=scope.get('hs_items',[]); countries=scope.get('country_codes',[]); validate_scope(store,hs,countries,6)
    require(hs or countries,'구독하려면 관심 등록이 필요합니다.','INTERESTS_REQUIRED',409)
    try: last_month=trade.bounds(store)[-1].strftime('%Y-%m')
    except Exception as exc:
        from backend.app.api.common import ApiError
        if not isinstance(exc,ApiError): raise
        last_month=None
    payload={'hs_items':hs,'countries':countries,'topic':data.get('topic','시장 동향'),'formats':sorted(formats),
             'enabled_since':now().isoformat()+'Z','last_observed_month':last_month}
    rows=store.rows(select(t).where(t.c.user_id==uid))
    require(len({r['period_code'] for r in rows})==len(rows),'같은 주기의 복수 조건 구독 정합화가 필요합니다.','FEATURE_NOT_READY',503)
    for r in rows:
        store.c.execute(update(t).where(t.c.subscription_id==r['subscription_id']).values(enabled=r['period_code'] in selected,
            filters_json=payload if r['period_code'] in selected else r['filters_json']))
    existing={r['period_code'] for r in rows}
    for code in selected-existing: store.insert('report_subscription',user_id=uid,period_code=code,filters_json=payload,enabled=True)
    return subscription_list(store,uid)
