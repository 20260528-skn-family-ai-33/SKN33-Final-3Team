import hashlib
import json
import re
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, update, delete
from backend.app.api.common import require, unavailable, now, digest, period, percentage, clean, page_info, shift_month, month_end
from backend.app.api import trade, news, jobs
from backend.app.api.accounts import interests, validate_scope
from backend.app.api.results import read_snapshot, save_snapshot, result_table, analysis_conditions
from backend.app.api.files import report_pdf, report_docx

TITLES=json.loads((Path(__file__).resolve().parents[1]/'templates/reports/layout.json').read_text(encoding='utf-8'))['sections']


def display_title(record):
    labels={'RPT_ON_DEMAND':'요청형','RPT_WEEKLY':'주간','RPT_MONTHLY':'월간','RPT_QUARTERLY':'분기'}
    p=record['conditions_json'].get('period',{})
    return f"{labels.get(record['report_type'],record['report_type'])} 리포트 · {p.get('start','')} ~ {p.get('end','')}"


def download_filename(store,record,fmt):
    """User-facing name only; storage IDs/paths and document contents stay intact."""
    c=record['conditions_json'];codes=c.get('country_codes') or []
    country=store.t('country')
    names=dict(store.c.execute(select(country.c.country_code,country.c.name_ko)
                               .where(country.c.country_code.in_(codes))).all()) if codes else {}
    hs_names=report_hs_names(store,[record])
    def joined(items,default):
        return '-'.join(items[:3])+(f'-외{len(items)-3}개' if len(items)>3 else '') if items else default
    def safe(value,limit=60):
        value=re.sub(r'[\\/:*?"<>|\x00-\x1f]+','_',str(value))
        value=re.sub(r'\s+','_',value).strip(' ._') or '전체'
        return value.encode('utf-8')[:limit].decode('utf-8',errors='ignore').rstrip(' ._')
    countries=joined((['한국전체'] if c.get('include_korea_total') else [])+[names.get(code,code) for code in codes],'전체국가')
    products=joined([hs_names.get(h['hs_code'],'HS'+h['hs_code']) for h in c.get('hs_items',[])],'전체품목')
    p=c.get('period') or {}
    period_name=f"{p['start']}~{p['end']}" if p.get('start') and p.get('end') else '전체기간'
    flow='수입' if c.get('trade_flow')=='IMPORT' else '수출'
    metric={'AMOUNT':'액분석','GROWTH':'증감분석','SHARE':'비중분석','RANK':'순위분석'}.get(c.get('metric'),'분석')
    topic=c.get('topic') or flow+metric
    created=record.get('completed_at') or record['created_at']
    if isinstance(created,str): created=datetime.fromisoformat(created.replace('Z','+00:00'))
    local=created.replace(tzinfo=created.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=9)))
    return '_'.join(safe(v) for v in (countries,period_name,products,topic))+f'_리포트_{local:%Y%m%d_%H%M%S}.'+fmt


def resolve_comparison(conditions, adjustments):
    """Validate comparison semantics against the final, month-aligned analysis period."""
    mode=conditions['compare']; supplied=conditions['compare_period']; p=conditions['period']
    if mode=='NONE':
        require(supplied is None,'비교 없음에는 비교 기간을 지정할 수 없습니다.')
        return None
    if supplied:
        aligned=period(date.fromisoformat(supplied['start']).replace(day=1),date.fromisoformat(supplied['end']))
        if aligned!=supplied: adjustments.append('비교 기간을 월초·월말로 조정')
        supplied=aligned
    if mode=='CUSTOM':
        require(supplied is not None,'사용자 지정 비교에는 compare_period가 필요합니다.')
        require(supplied['end']<p['start'],'사용자 지정 비교 기간은 분석 기간보다 앞서야 합니다.')
        return supplied
    start,end=date.fromisoformat(p['start']),date.fromisoformat(p['end'])
    if mode=='MOM':
        require(start.replace(day=1)==end.replace(day=1),
                '전월 비교는 한 달을 선택하세요. 여러 달 비교는 사용자 지정 비교를 사용하세요.',
                'UNSUPPORTED_COMPARISON',422)
    offset=-1 if mode=='MOM' else -12
    expected=period(shift_month(start,offset),shift_month(end,offset))
    # A long YOY_PERIOD may overlap the analysis period; it is still shifted by one year.
    require(supplied is None or supplied==expected,
            '비교 기간이 전월·전년 기준과 일치하지 않습니다. 다른 기간은 CUSTOM으로 지정하세요.')
    return expected


def preview(store,uid,data):
    require(bool(data.get('conditions')) != bool(data.get('result_id')),'conditions와 result_id 중 하나만 지정하세요.')
    require(not data.get('period_preset'),'기간 프리셋 입력 예외 계약 DEC-28 확정이 필요합니다.','FEATURE_NOT_READY',503)
    if data.get('result_id'):
        conditions=analysis_conditions(read_snapshot(store,uid,data['result_id'],'RESULT')['conditions'])
        require(conditions.get('trade_flow')!='BOTH',
                '리포트는 수출 분석만 지원합니다. 수출만 선택해 다시 조회한 결과를 사용하세요.',
                'UNSUPPORTED_TRADE_FLOW',422)
    else:
        conditions=data.get('conditions',{})
    require(bool(conditions),'분석 조건 또는 result_id가 필요합니다.')
    from backend.app.api.schemas import ReportConditions
    conditions=ReportConditions.model_validate(conditions).model_dump(mode='json')
    if not conditions['include_korea_total']: conditions.pop('include_korea_total')
    require(conditions['trade_flow']=='EXPORT','리포트는 현재 수출 분석만 지원합니다.','UNSUPPORTED_TRADE_FLOW',422)
    validate_scope(store,conditions['hs_items'],conditions['country_codes'])
    p=conditions['period']; latest=trade.bounds(store)[-1]
    adjustments=[]
    if date.fromisoformat(p['start']).day!=1 or date.fromisoformat(p['end'])!=month_end(date.fromisoformat(p['end'])):
        p['start']=date.fromisoformat(p['start']).replace(day=1).isoformat()
        p['end']=month_end(date.fromisoformat(p['end'])).isoformat()
        adjustments.append('월별 통계 기준으로 시작일·종료일을 월초·월말로 조정')
    if date.fromisoformat(p['end'])>month_end(latest):
        p['end']=month_end(latest).isoformat()
        adjustments.append('종료일을 최신 공개 월의 말일로 조정')
    require(p['start']<=p['end'],'현재 공개 데이터 범위로 조정할 수 없습니다.','PERIOD_NOT_AVAILABLE',422)
    trade.resolve_period(store,p['start'][:7],p['end'][:7])
    conditions['compare_period']=resolve_comparison(conditions,adjustments)
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
    t=store.t('report')
    selected=sorted((store.c.scalar(select(t.c.files_json).where(t.c.report_id==rid)) or {}).keys())
    return sorted(set(selected) | {'XLSX'}) if selected else []


def status_view(store,r):
    job=jobs.report_execution(store,r)
    info=(job or {}).get('result_json') or {}
    return {'report_id':str(r['report_id']),'status':r['status'],'step':info.get('step','DONE' if r['status']=='READY' else 'QUERY'),
        'retry_count':r['retry_count'],'retry_after_seconds':0 if r['status'] in ('READY','FAILED') else 2,
        'error':info.get('error'),'available_formats':formats(store,r['report_id']) if r['status']=='READY' else []}


def report_hs_names(store,rows):
    codes={h['hs_code'] for row in rows for h in row['conditions_json'].get('hs_items',[])
           if h.get('hs_ver','HS2022')=='HS2022'}
    if not codes: return {}
    hs=store.t('hs_code')
    return dict(store.c.execute(select(hs.c.hs_code,hs.c.name_ko).where(
        hs.c.hs_ver=='HS2022',hs.c.hs_code.in_(codes))).all())


def summary_view(store,r,names):
    c=r['conditions_json']
    hs_names={h['hs_code']:names[h['hs_code']] for h in c.get('hs_items',[])
              if h.get('hs_ver','HS2022')=='HS2022' and h['hs_code'] in names}
    return {'report_id':str(r['report_id']),'report_type':r['report_type'],
        'subscription_id':str(r['subscription_id']) if r['subscription_id'] else None,
        'title':display_title(r),'hs_names':hs_names,'conditions':{k:v for k,v in c.items() if k in ('hs_items','country_codes','include_korea_total','period','compare_period','trade_flow','compare','metric','applied_defaults','expanded_regions')},
        'status':r['status'],'created_at':r['created_at'],'completed_at':r['completed_at'],
        'available_formats':formats(store,r['report_id']) if r['status']=='READY' else []}


def listing(store,uid,q=None,kind=None,status=None,page=1,size=20):
    t=store.t('report'); stmt=select(t).where(t.c.user_id==uid)
    if kind: stmt=stmt.where(t.c.report_type==kind)
    if status: stmt=stmt.where(t.c.status==status)
    rows=store.rows(stmt.order_by(t.c.created_at.desc(),t.c.report_id.desc()))
    if q: rows=[r for r in rows if q in display_title(r)]
    visible=rows[(page-1)*size:page*size]
    names=report_hs_names(store,visible)
    return {'items':[summary_view(store,r,names) for r in visible],'page':page_info(page,size,len(rows))}


def read_report(store,uid,rid):
    r=store.owned('report','report_id',rid,uid)
    require(r['status']=='READY','리포트가 아직 준비되지 않았습니다.','REPORT_NOT_READY',409)
    content=r['content_json']
    require(content.get('schema_version')==1,'이전 리포트 JSON 변환 계약 확인이 필요합니다.','FEATURE_NOT_READY',503)
    return {'report':summary_view(store,r,report_hs_names(store,[r])),'sections':content['sections'],'meta':content['meta']}


def section(code,text,tables=None,articles=None,status='READY',kpi=None):
    return {'section_code':code,'title':TITLES[code],'text':text,'kpi':kpi,'tables':tables or [],'charts':[],
        'articles':articles or [],'verification':{'status':'PASS' if status=='READY' else 'NEEDS_REVIEW','sentence_checks':None,'regeneration_count':None},'content_status':status}


def article_sources(articles):
    from backend.app.services.report_context import source_entries
    return source_entries(article['source_code'] for article in articles)


PROMISING_UNAVAILABLE='예측 모델 또는 기준월 스냅샷을 사용할 수 없어 유망시장 예측을 넣지 못했습니다.'


def promising_scope(bundle,countries,items,max_countries,max_items):
    """리포트 조건 → 유망시장 예측 대상(국가, HS6 품목)과 안내 문구.
    채팅 유망시장과 같은 한도(국가 20개·품목 5개)로 계산하고, 넘치면 앞에서부터 쓰고 알린다.
    HS4는 모델이 HS6로 예측하므로 선택 국가 기준 최근 12개월 수출액이 큰 하위 HS6로 바꾼다(채팅의 HS4 처리와 같은 순서)."""
    from backend.app.agents.market import hs6_by_exports
    notes=[]
    if len(countries)>max_countries:
        notes.append(f'유망시장 예측은 국가 {max_countries}개까지 계산합니다. 선택한 {len(countries)}개국 중 앞의 {max_countries}개국만 예측했습니다.')
        countries=countries[:max_countries]
    hs=[]; seen=set(); expanded=[]
    def add(code,ver):
        if (code,ver) in seen: return False
        seen.add((code,ver)); hs.append({'hs_code':code,'hs_ver':ver}); return True
    for item in items:
        code,ver=item['hs_code'],item.get('hs_ver','HS2022')
        if len(code)==6:
            add(code,ver); continue
        children=[x for x in hs6_by_exports(bundle,code,countries) if len(x)==6][:max_items]
        if not children:
            notes.append(f'HS {code}에는 유망시장 예측 대상인 HS6 품목이 없습니다.'); continue
        expanded.append((code,[x for x in children if add(x,'HS2022')]))
    total=len(hs)
    if total>max_items:
        notes.append(f'유망시장 예측은 품목 {max_items}개까지 계산합니다. 대상 품목 {total}개 중 앞의 {max_items}개만 예측했습니다.')
        hs=hs[:max_items]
    kept={h['hs_code'] for h in hs if h['hs_ver']=='HS2022'}
    for code,children in expanded:
        used=[x for x in children if x in kept]
        if used:
            notes.append(f'HS {code}는 최근 12개월 수출액이 큰 하위 HS6 품목({", ".join(used)})으로 예측했습니다.')
    return countries,hs,notes


def promising_section(store,uid,c):
    """GROWTH_RISK = 유망시장(증가·감소 예상). 채팅 유망시장(PROMISING_MARKET)과 같은 계산을 리포트 국가·품목으로 한다.
    모델 A가 유망 순위를, 모델 B가 증가·감소 예상을 낸다. 모델을 쓸 수 없어도 리포트 생성은 실패시키지 않는다."""
    from types import SimpleNamespace
    countries=list(c.get('country_codes') or [])
    items=[h for h in c.get('hs_items',[]) if len(h['hs_code']) in (4,6)]
    if not countries and not items:
        return section('GROWTH_RISK','국가 또는 HS4·HS6 품목을 1개 이상 선택하면 유망시장 예측을 함께 넣습니다.',status='NO_DATA')
    settings=store.db.settings
    try:
        from backend.app.agents import budget
        from backend.app.agents.market import growth, loaded, model_codes, predict
        from backend.app.agents.runtime import bounded_ml
        from backend.app.agents.state import MAX_COUNTRIES, MAX_HS_ITEMS, Conditions
        from backend.app.agents.templates import _names
        seconds=budget.seconds(settings,'ml')
        def load():
            bundle,provider,versions=loaded(settings)
            anchor=str(bundle.get('anchor') or '')[:7]
            return bundle,provider,versions,anchor,growth(settings,anchor)
        # 채팅과 같은 ML 전용 스레드에서 돌린다(모델을 메모리에 두 번 올리지 않고, 멈춰도 워커를 붙잡지 않는다).
        bundle,provider,versions,anchor,(outlook,_,note)=bounded_ml(load,seconds)
    except (ImportError,OSError,MemoryError,ValueError,TimeoutError):
        return section('GROWTH_RISK',PROMISING_UNAVAILABLE,status='NO_DATA')
    # 선택 범위(리포트 한도 품목 100·국가 250)는 모델 장애가 아니다. 예측 한도에 맞추고 무엇을 계산했는지 알린다.
    countries,hs,scope=promising_scope(bundle,countries,items,MAX_COUNTRIES,MAX_HS_ITEMS)
    if not countries and not hs:
        return section('GROWTH_RISK',' '.join(['선택한 품목에는 유망시장 예측 대상이 없습니다.',*scope]),status='NO_DATA')
    try:
        if outlook and outlook['anchor']!=anchor:
            outlook,note=None,f"증감 예상 모델의 기준월({outlook['anchor']})이 유망시장 모델({anchor})과 달라 증가·감소 예상은 표시하지 않았습니다."
        conditions=Conditions(country_codes=countries,hs_items=hs)
        codes=model_codes(store,conditions.hs_items)
        analysis=bounded_ml(lambda:predict(SimpleNamespace(conditions=conditions),bundle,provider,versions,codes=codes,
                                           directions=outlook['directions'] if outlook else None),seconds)
    except (ImportError,OSError,MemoryError,ValueError,TimeoutError):
        return section('GROWTH_RISK',PROMISING_UNAVAILABLE,status='NO_DATA')
    if analysis.result.status!='OK':
        return section('GROWTH_RISK',' '.join(['선택한 국가·품목은 유망시장 예측 대상이 아닙니다(최근 12개월 수출 10만 달러 이상만 예측).',*scope]),status='NO_DATA')
    names,products=_names(store)
    for row in analysis.rows:
        row['country_name']=names.get(row['country_code'],row['country_code'])
        row['product_name']=products.get((row['hs6'],row.get('hs_ver','HS2022')),row['hs6'])
        row['name']=row['product_name'] if analysis.meta['group_by']!='HS6' else row['country_name']
    meta={k:v for k,v in analysis.meta.items() if k!='missing_targets'}
    table=result_table(store,uid,analysis.rows,meta,c,store.queries)
    text=(f"{analysis.meta['anchor']} 기준 모델 예측입니다. 순위는 다음 12개월 수출 성장 가능성이 높은 순서이고, "
          '증가·감소 예상은 다음 12개월 수출이 최근 12개월보다 늘지 줄지에 대한 예측 방향입니다.')
    notes=[n for n in [*scope,note,*analysis.result.limitations] if n]
    return section('GROWTH_RISK',' '.join([text,*notes]),[table],status='READY' if not notes else 'NEEDS_REVIEW')


def prediction_group(conditions):
    """예측 결과의 순위 기준(group_by). market.predict와 같은 규칙: 국가·품목 둘 다 → PAIR, 국가만 → COUNTRY, 품목만 → HS6."""
    countries,items=(conditions or {}).get('country_codes') or [],(conditions or {}).get('hs_items') or []
    return 'PAIR' if countries and items else 'COUNTRY' if countries else 'HS6' if items else None


def build_document(store,uid,record):
    c=record['conditions_json']; hs=c.get('hs_items',[]); countries=c.get('country_codes',[])
    p=c['period']
    # Korea's total already includes selected partners. Never sum overlapping scopes.
    if c.get('include_korea_total'): countries=[]
    source=(record.get('content_json') or {}).get('source_tables')
    if source:
        meta={'sources':[],'period':p,'warnings':[],'freshness':'FROZEN'}
        sections=[section('SUMMARY','승인한 분석 결과표를 기준으로 작성했습니다.')]
        kinds={'TREND':'KPI','LLM_SQL':'KPI','RANKING':'TOP_COUNTRIES','SHARE':'TOP_COUNTRIES','PREDICTION':'GROWTH_RISK'}
        refs=record['content_json'].get('source_results') or []
        source=[dict(table) for table in source]
        for i,table in enumerate(source):
            # 채팅 결과 복사본의 예측 표는 원래 순위 기준(group_by)을 그대로 쓴다. 예전에 저장한 복사본은 원본 조건으로 되살린다.
            if table['kind']=='PREDICTION' and not (table.get('meta') or {}).get('group_by'):
                group=prediction_group(refs[i].get('conditions') if i<len(refs) else None)
                if group: table['meta']={**(table.get('meta') or {}),'kind':'PREDICTION','group_by':group}
        for code in c['sections']:
            tables=[table for table in source if kinds.get(table['kind'])==code]
            sections.append(section(code,'승인한 분석 결과입니다.' if tables else '해당 분석 결과에 포함되지 않음',tables))
        for ref in record['content_json']['source_results']: meta['warnings'].extend(ref['limitations'])
    elif record['report_type']=='RPT_WEEKLY':
        meta=news.source_meta(store,'NEWS',date.fromisoformat(p['end']))
        articles=news.articles(store,uid,start=date.fromisoformat(p['start']),end=date.fromisoformat(p['end']),hs=hs,countries=countries,size=20)['items']
        meta['sources']=article_sources(articles)
        sections=[section('NEWS','지난주 관심 범위에 해당하는 기사 본문 발췌입니다. RAG 요약은 미연결 상태입니다.',articles=articles,status='POLICY_PENDING')]
    else:
        start,end=trade.resolve_period(store,p['start'][:7],p['end'][:7])
        meta=trade.metadata(store,start,end)
        rows=trade.aggregate(store,'export_summary',start,end,hs,countries)
        # resolve_period가 검증한 월의 누락 거래는 미수집이 아니라 0이다.
        total=sum(r['amount_usd'] for r in rows)
        comparison=c.get('compare','YOY_PERIOD')
        prev=c.get('compare_period') if comparison!='NONE' else None; old=None
        if prev:
            try:
                old_s,old_e=trade.resolve_period(store,prev['start'][:7],prev['end'][:7])
                previous=trade.aggregate(store,'export_summary',old_s,old_e,hs,countries)
                old=sum(r['amount_usd'] for r in previous)
            except Exception as exc:
                from backend.app.api.common import ApiError
                if not isinstance(exc,ApiError) or exc.code not in ('DATA_NOT_READY','PERIOD_NOT_AVAILABLE'): raise
                meta['warnings'].append('비교 기간 데이터 미준비: 증감률은 null')
                meta['freshness']='STALE'
        growth=percentage(total,old); count=sum(r['amount_usd']>0 for r in rows)
        kpi={'amount_usd':total,'previous_amount_usd':old,'comparison_pct':growth,
             'mom_pct':growth if comparison=='MOM' else None,
             'yoy_pct':growth if comparison in ('YOY','YOY_PERIOD') else None,'export_country_count':count,
             'top_country_share_pct':max(r['amount_usd'] for r in rows)*100/total if total else None}
        text=f'분석 기간 수출액은 {total:,.2f} USD입니다. 수출 실적이 있는 국가는 {count}개국입니다. '
        if comparison!='NONE':
            text+=f'이전 비교 기간 대비 증감률은 {growth:,.2f}%입니다.' if growth is not None else '이전 비교 기간 증감률은 계산할 수 없습니다.'
        if prev:
            current_months=(end.year-start.year)*12+end.month-start.month+1
            previous_start=date.fromisoformat(prev['start']).replace(day=1)
            previous_end=date.fromisoformat(prev['end']).replace(day=1)
            previous_months=(previous_end.year-previous_start.year)*12+previous_end.month-previous_start.month+1
            if previous_months!=current_months:
                verified={m for m in trade.verified_months(store,'export_summary') if previous_start<=m<=previous_end}
                monthly=trade.aggregate(store,'export_summary',previous_start,previous_end,hs,countries,'MONTH')
                subtotal=sum(r['amount_usd'] for r in monthly if r['trade_month'] in verified)
                previous_avg=subtotal/len(verified) if verified else None
                current_avg=total/current_months
                kpi.update(monthly_avg_usd=current_avg,compare_monthly_avg_usd=previous_avg,
                    monthly_avg_change_pct=percentage(current_avg,previous_avg),verified_months=current_months,
                    requested_months=current_months,compare_verified_months=len(verified),compare_requested_months=previous_months)
                avg_note=f'두 기간의 길이가 다릅니다. 조회 기간 월평균은 {current_avg:,.2f} USD({current_months}/{current_months}개월)입니다.'
                avg_note+=(f' 비교 기간 월평균은 {previous_avg:,.2f} USD({len(verified)}/{previous_months}개월)입니다.'
                           if previous_avg is not None else ' 비교 기간에 검증된 월이 없어 월평균은 미집계입니다.')
                meta['monthly_average_note']=avg_note
                text+=' '+avg_note
        sections=[section('SUMMARY',text)]
        selected=c.get('sections',list(TITLES)[1:5])
        if 'KPI' in selected:
            table=result_table(store,uid,[kpi],meta,c,store.queries)
            observed={r['trade_month']:r['amount_usd'] for r in trade.aggregate(store,'export_summary',start,end,hs,countries,'MONTH')}
            monthly=[]; cursor=start
            while cursor<=end:
                monthly.append({'month':cursor.strftime('%Y-%m'),'amount_usd':observed.get(cursor,0)})
                cursor=shift_month(cursor,1)
            trend=result_table(store,uid,monthly,meta,c,store.queries)
            block=section('KPI',text,[table,trend],kpi=clean(kpi))
            block['charts']=[{'chart_type':'LINE','result_id':trend['result_id'],'x_key':'month','series_keys':['amount_usd'],'title':'월별 수출 추이 (USD)','positive_color':'#16A34A','negative_color':'#DC2626'}]
            if c.get('include_korea_total'):
                from backend.app.services.report_scope_comparison import comparison_table
                comparison_table_data=comparison_table(store,uid,c,start,end)
                block['tables'].append(comparison_table_data)
                block['charts'].append({'chart_type':'LINE','result_id':comparison_table_data['result_id'],
                    'x_key':'month','series_keys':[col['key'] for col in comparison_table_data['columns'] if col['key']!='month'],
                    'title':'한국 전체와 교역 상대국 수출액 비교 (USD)'})
                block['text']+=' 한국 전체는 상대국별 수출액을 포함하므로 비교 대상 간 금액을 합산하지 않습니다.'
            sections.append(block)
        if 'TOP_COUNTRIES' in selected:
            names={r['country_code']:r['name_ko'] for r in store.rows(select(store.t('country')))}
            active_rows=[r for r in rows if r['amount_usd']>0]
            top=[{'country':names[r['country_code']],'amount_usd':r['amount_usd'],'share_pct':r['amount_usd']*100/total if total else None} for r in sorted(active_rows,key=lambda r:(-r['amount_usd'],r['country_code']))[:10]]
            table=result_table(store,uid,top,meta,c,store.queries)
            block=section('TOP_COUNTRIES','분석 조건 내 수출액 상위 10개국입니다.',[table])
            block['charts']=[{'chart_type':'HORIZONTAL_BAR','result_id':table['result_id'],'x_key':'country','series_keys':['amount_usd'],'title':'주요 수출국 (USD)','positive_color':'#16A34A','negative_color':'#DC2626'}]
            sections.append(block)
        if 'GROWTH_RISK' in selected: sections.append(promising_section(store,uid,c))
        if 'LOCAL_ISSUES' in selected:
            articles=news.articles(store,uid,start=start,end=month_end(end),hs=hs,countries=countries,size=5)['items']
            sections.append(section('LOCAL_ISSUES','분석 조건과 기간에 해당하는 기사 본문 발췌입니다. RAG 요약·인과 분석 모듈은 미연결 상태입니다.',articles=articles,status='POLICY_PENDING'))
            meta['sources'].extend(article_sources(articles))
    title='수출 시장 분석 리포트' if record['report_type']!='RPT_WEEKLY' else '주간 해외시장 뉴스 리포트'
    subtitle=f"HS {', '.join(x['hs_code'] for x in hs) or '전체'} · {', '.join(countries) or '전체 국가'} · {p['start']} ~ {p['end']} · USD"
    return clean({'schema_version':1,'title':title,'subtitle':subtitle,'generated_at':now().isoformat()+'Z','sections':sections,'meta':meta,'sql':store.queries})


def attach_weekly_summary(doc):
    """PDF 양식의 급증 키워드·주요 이슈를 웹·DOCX에도 같은 값으로 싣는다. 양식을 만들지 않은 리포트는 건너뛴다."""
    if not doc.get('template_pdf'): return
    from backend.app.services.report_context import source_entries, weekly_highlights
    ctx=doc['template_pdf']['context']
    news_section=doc['sections'][0]; news_section['text']=weekly_highlights(ctx)+'\n'+news_section['text']
    # 출처는 본문 카드 20건이 아니라 요약·급증·주요 이슈에 실제로 쓴 기사 기준이다(PDF와 같은 목록).
    if 'source_codes' in ctx: doc['meta']['sources']=source_entries(ctx['source_codes'])


def generate(store,job,settings):
    jobs.report_step(store,job,'QUERY')
    uid=job['subject_user_id']; rid=job['payload_json']['report_id']
    record=store.owned('report','report_id',rid,uid,lock=True)
    doc=build_document(store,uid,record)
    if not (record.get('content_json') or {}).get('source_tables') and record['report_type'] in ('RPT_MONTHLY','RPT_WEEKLY','RPT_QUARTERLY','RPT_ON_DEMAND'):
        from backend.app.services.report_context import build_context
        doc['template_pdf']={'version':1,'report_type':record['report_type'],
                             'context':build_context(store,uid,record,doc)}
        doc['sql']=list(store.queries)
    if record['report_type']=='RPT_WEEKLY': attach_weekly_summary(doc)
    from backend.app.api.report_storage import report_storage
    storage=report_storage(settings)
    prefix=f'{uid}/{rid}/{job["claim_token"]}'
    selected=record['conditions_json'].get('formats',['PDF'])
    jobs.report_step(store,job,'PDF')
    pdf=report_pdf(doc,settings)
    archive=storage.write(prefix+'/report.pdf',pdf)
    files={}
    for fmt in selected:
        jobs.report_step(store,job,fmt)
        content=pdf if fmt=='PDF' else report_docx(doc,settings)
        files[fmt]=archive if fmt=='PDF' else storage.write(prefix+'/report.'+fmt.lower(),content)
    from backend.app.api.files import xlsx_bytes
    tables=[t for section in doc['sections'] for t in section['tables']]
    files['XLSX']=storage.write(prefix+'/report.xlsx',xlsx_bytes(tables,doc['meta'],doc.get('sql',[])))
    t=store.t('report')
    store.c.execute(update(t).where(t.c.report_id==rid).values(status='READY',
        content_json={**doc,jobs.EXECUTION:clean(job)},files_json=files,pdf_uri=archive['key'],completed_at=now()))
    if record['subscription_id']:
        from backend.app.api.notifications import settings as notification_settings
        conf=notification_settings(store,uid)
        if conf['notifications_enabled'] and conf['consent_current']:
            event=store.insert('alert_event',event_type='REPORT',report_id=int(rid),event_key=digest(['REPORT',rid]),
                payload_json={'destination':'REPORT_DETAIL','report_type':record['report_type'],'period':record['conditions_json']['period']},created_at=now())
            store.insert('user_alert',user_id=uid,event_id=event,is_interest_match=False,created_at=now())
    return {'report_id':str(rid),'step':'DONE'}


def subscription_rows(store,uid,*,lock=False):
    t=store.t('report_subscription')
    stmt=select(t).where(t.c.user_id==uid).order_by(t.c.subscription_id)
    return store.rows(stmt.with_for_update() if lock else stmt)


def subscription_view(rows):
    items=[]
    for r in rows:
        p=r['filters_json']
        items.append({'subscription_id':str(r['subscription_id']),'period_code':r['period_code'],'enabled':bool(r['enabled']),
            'hs_items':p.get('hs_items',[]),'country_codes':p.get('countries',[]),'topic':p.get('topic'),
            'formats':p.get('formats',['PDF']),'version':digest(r)})
    return {'items':items,'version':digest(items)}


def subscription_list(store,uid):
    return subscription_view(subscription_rows(store,uid))


def lock_subscriber(store,uid):
    users=store.t('users')
    store.c.execute(select(users.c.user_id).where(users.c.user_id==uid).with_for_update()).one()


def cancel_subscription(store,uid,subscription_id):
    lock_subscriber(store,uid)
    row=store.owned('report_subscription','subscription_id',subscription_id,uid,lock=True)
    t=store.t('report_subscription')
    store.c.execute(update(t).where(t.c.user_id==uid,t.c.period_code==row['period_code']).values(enabled=False))


def save_subscriptions(store,uid,data):
    t=store.t('report_subscription')
    lock_subscriber(store,uid)
    # 인증 조회가 만든 MySQL 스냅샷 대신 앞선 저장이 커밋한 최신 행으로 버전을 검사한다.
    rows=subscription_rows(store,uid,lock=True)
    require(subscription_view(rows)['version']==data['expected_version'],'구독 설정이 변경되었습니다.','VERSION_CONFLICT',409)
    selected=set(data['period_codes']); formats=set(data.get('formats',['PDF']))
    require(selected,'주기를 한 개 이상 선택하세요.')
    require(data.get('use_saved_interests') is True,'use_saved_interests는 true만 허용합니다.')
    require(formats and formats<={'PDF','DOCX'},'PDF/DOCX를 1개 이상 선택하세요.')
    o=store.t('service_option')
    allowed=set(store.c.execute(select(o.c.option_code).where(o.c.option_type=='REPORT_PERIOD',o.c.enabled==True)).scalars())
    require(selected<=allowed,'비활성 또는 미등록 구독 주기입니다.','FEATURE_NOT_AVAILABLE',422)
    scope=interests(store,uid,lock=True)
    hs=scope.get('hs_items',[]); countries=scope.get('country_codes',[]); validate_scope(store,hs,countries,6)
    require(hs or countries,'구독하려면 관심 등록이 필요합니다.','INTERESTS_REQUIRED',409)
    try: last_month=trade.bounds(store)[-1].strftime('%Y-%m')
    except Exception as exc:
        from backend.app.api.common import ApiError
        if not isinstance(exc,ApiError): raise
        last_month=None
    payload={'hs_items':hs,'countries':countries,'topic':data.get('topic','시장 동향'),'formats':sorted(formats),
             'enabled_since':now().isoformat()+'Z','last_observed_month':last_month}
    remaining=selected.copy()
    # 활성 행 중 가장 오래된 ID를 유지해 기존 발행 기록과의 연결을 보존한다.
    for row in sorted(rows,key=lambda r:not r['enabled']):
        code=row['period_code']
        stmt=update(t).where(t.c.subscription_id==row['subscription_id'])
        if code in remaining:
            # 형식·관심만 바꿔 다시 저장해도 이미 구독 중인 주기의 시작일은 유지한다. 아직 발행 못 한 회차가 사라지면 안 된다.
            since=row['filters_json'].get('enabled_since') if row['enabled'] else None
            store.c.execute(stmt.values(enabled=True,filters_json={**payload,'enabled_since':since or payload['enabled_since']}))
            remaining.remove(code)
        elif row['enabled']:
            store.c.execute(stmt.values(enabled=False))
    for code in sorted(remaining):
        store.insert('report_subscription',user_id=uid,period_code=code,filters_json=payload,enabled=True)
    return subscription_view(subscription_rows(store,uid,lock=True))
