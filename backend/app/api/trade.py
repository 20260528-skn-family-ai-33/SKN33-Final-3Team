"""고정 SQLAlchemy 쿼리로 통계 계산. LLM 작성 SQL을 실행하지 않는다."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func, and_, or_
from backend.app.api.common import (require, unavailable, month, shift_month, month_end,
                                    period, percentage, digest, now, page_info, clean)
from backend.app.api.accounts import interests, validate_scope
from backend.app.api.results import result_table


def verified_months(store, table):
    key=('verified_months',table)
    if key in store.cache: return store.cache[key]
    t,b=store.t(table),store.t('batch_run')
    # 최신 실행이 실패/진행 중인 범위는 공개하지 않는다. 이전 정상 범위를 0으로 덮지 않는다.
    runs=store.rows(select(b.c.run_id,b.c.period_start,b.c.period_end,b.c.status)
                    .where(b.c.target_table==table).order_by(b.c.run_id.desc()))
    store.cache[('trade_revision',table)]=digest(runs)
    # 거래 행이 전혀 없는 월도 완료 배치가 보장하면 공개 가능한 0 거래 월이다.
    months=set()
    for run in runs:
        cursor=run['period_start'].replace(day=1)
        while cursor<=run['period_end']:
            if run['period_start']<=cursor and month_end(cursor)<=run['period_end']: months.add(cursor)
            cursor=shift_month(cursor,1)
    verified=[]
    for m in months:
        latest=next((r for r in runs if r['period_start']<=m and r['period_end']>=month_end(m)),None)
        if latest and latest['status']=='SUCCESS': verified.append(m)
    store.cache[key]=sorted(verified)
    return store.cache[key]


def bounds(store):
    if 'trade_bounds' in store.cache: return store.cache['trade_bounds']
    months=sorted(set(verified_months(store,'export_summary')) & set(verified_months(store,'import_summary')))
    if not months: unavailable('수출·수입 집계와 정상 완료 배치 기록이 모두 준비되어야 합니다.')
    store.cache['trade_bounds']=months
    return months


def availability(store):
    months=bounds(store)
    a,f=store.t('article'),store.t('fx_daily')
    return {'trade_start_month':months[0].strftime('%Y-%m'),'trade_end_month':months[-1].strftime('%Y-%m'),
            'news_latest_date':store.c.scalar(select(func.max(a.c.published_date))),
            'fx_latest_date':store.c.scalar(select(func.max(f.c.base_date))),
            'meta':metadata(store,months[0],months[-1])}


def resolve_period(store, start=None, end=None, months=3):
    available=bounds(store)
    end=month(end) if end else available[-1]
    start=month(start) if start else shift_month(end,-months+1)
    require(start<=end,'시작 월이 종료 월보다 늦습니다.')
    require(end<=available[-1],'최신 공개 월을 초과합니다.','PERIOD_NOT_AVAILABLE',422)
    cursor=start
    while cursor<=end:
        if cursor not in available: unavailable('요청 기간에 미수집 또는 검증 미완료 월이 있습니다.')
        cursor=shift_month(cursor,1)
    return start,end


def metadata(store,start,end,source='CUSTOMS',warnings=None,latest_month=None):
    b=store.t('batch_run')
    key=('data_version',source)
    if key not in store.cache:
        store.cache[key]=store.c.scalar(select(func.max(b.c.run_id)).where(b.c.source_code==source,b.c.status=='SUCCESS'))
    version=store.cache[key]
    return {'data_version':f'{source}-{version or "unverified"}',
            'latest_available_month':(latest_month or bounds(store)[-1]).strftime('%Y-%m') if source=='CUSTOMS' else None,
        'period':period(start,end) if start else None,
        'sources':[{'code':source,'label':'관세청' if source=='CUSTOMS' else source,'url':None}],
        'freshness':'STALE' if warnings else 'FRESH','generated_at':now().isoformat()+'Z','warnings':warnings or []}


def scope_filters(store,uid,scope='GLOBAL',hs_code=None,hs_ver=None,countries=None):
    require(scope in ('GLOBAL','INTERESTED'),'scope를 확인하세요.')
    hs=[]
    if hs_code:
        require(hs_ver=='HS2022','품목을 지정하면 hs_ver=HS2022가 필요합니다.')
        hs=[{'hs_code':hs_code,'hs_ver':hs_ver}]
    countries=list(countries or [])
    validate_scope(store,hs,countries)
    if scope=='INTERESTED':
        saved=interests(store,uid)
        require(saved['hs_items'] or saved['country_codes'],'등록한 관심이 없습니다.','INTERESTS_REQUIRED',409)
        if hs:
            hs=[x for x in saved['hs_items'] if x['hs_code'].startswith(hs_code)]
            require(hs,'등록한 관심 품목 범위 밖입니다.')
        else: hs=saved['hs_items']
        if countries:
            require(set(countries)<=set(saved['country_codes']),'등록한 관심 국가 범위 밖입니다.')
        else: countries=saved['country_codes']
    return hs,countries


def hs_filter(t, hs):
    # Equality permits a full HS6 index lookup; prefix matching is only for HS4.
    return or_(*(and_(t.c.hs6_code==x['hs_code'] if len(x['hs_code'])==6 else t.c.hs6_code.startswith(x['hs_code']),
                     t.c.hs_ver==x['hs_ver']) for x in hs))


def trade_plan(stmt,t,hs,countries):
    # For nationwide scans MySQL chose country-first nested loops over all history.
    # Existing month-leading PK restricts the scan to the requested period instead.
    if not hs and not countries:
        stmt=stmt.with_hint(t,'FORCE INDEX (PRIMARY)',dialect_name='mysql')
    return stmt


def query_rows(store,stmt,table):
    compiled=str(stmt.compile(dialect=store.c.dialect,compile_kwargs={'literal_binds':True}))
    store.queries.append(compiled)
    if store.c.dialect.name!='mysql': return store.rows(stmt)
    revision_table=table.replace('_monthly','_summary')
    verified_months(store,revision_table)
    key=(compiled,store.cache[('trade_revision',revision_table)])
    return store.db.trade_query_cache.get_or_load(key,lambda:store.rows(stmt))


def aggregate(store,table,start,end,hs=None,countries=None,group='COUNTRY'):
    t,c=store.t(table),store.t('country')
    groups={'COUNTRY':[t.c.country_code], 'HS6':[t.c.hs6_code,t.c.hs_ver],
            'MONTH':[t.c.trade_month], 'PAIR':[t.c.country_code,t.c.hs6_code,t.c.hs_ver],
            'MONTH_COUNTRY':[t.c.trade_month,t.c.country_code]}
    cols=groups[group]
    stmt=select(*cols,func.sum(t.c.amount_usd).label('amount_usd')).select_from(t.join(c,t.c.country_code==c.c.country_code))
    stmt=stmt.where(c.c.country_role=='COUNTRY',t.c.hs_ver=='HS2022',t.c.trade_month>=start,t.c.trade_month<=end)
    if hs: stmt=stmt.where(hs_filter(t,hs))
    if countries: stmt=stmt.where(t.c.country_code.in_(countries))
    stmt=stmt.group_by(*cols).order_by(*cols)
    return query_rows(store,trade_plan(stmt,t,hs,countries),table)


def one_off(store,start,end,hs=None,countries=None):
    t=store.t('export_monthly')
    stmt=select(t.c.anomaly_code).where(t.c.trade_month>=start,t.c.trade_month<=end,t.c.hs_ver=='HS2022')
    if hs: stmt=stmt.where(hs_filter(t,hs))
    if countries: stmt=stmt.where(t.c.country_code.in_(countries))
    values=set(store.c.execute(stmt.distinct()).scalars())
    return anomaly_view(values)


def anomaly_view(values):
    codes=sorted(set('_'.join(v for v in values if v and v!='NONE').split('_'))-{''})
    mapping={'R1':'SINGLE_MONTH_SPIKE','R2':'LARGE_ITEM','R3':'UNIT_PRICE_ANOMALY'}
    return {'suspected':bool(codes),'rules':[mapping.get(v,v) for v in codes],
        'reason':'저장된 관세청 일회성 추정 규칙: '+', '.join(codes) if codes else None,
        'status':'NOT_EVALUATED' if not values or None in values else 'EVALUATED'}


def anomaly_map(store,start,end,hs=None,countries=None,group='COUNTRY',flow='EXPORT'):
    t,c=store.t('export_monthly' if flow=='EXPORT' else 'import_monthly'),store.t('country')
    cols=[t.c.country_code] if group=='COUNTRY' else [t.c.hs6_code] if group=='HS6' else [t.c.country_code,t.c.hs6_code]
    stmt=select(*cols,t.c.anomaly_code).join(c,c.c.country_code==t.c.country_code).where(
        c.c.country_role=='COUNTRY',t.c.trade_month>=start,t.c.trade_month<=end,t.c.hs_ver=='HS2022')
    if hs: stmt=stmt.where(hs_filter(t,hs))
    if countries: stmt=stmt.where(t.c.country_code.in_(countries))
    values=defaultdict(set)
    for row in query_rows(store,trade_plan(stmt.distinct(),t,hs,countries),t.name):
        key=row['country_code'] if group=='COUNTRY' else row['hs6_code'] if group=='HS6' else (row['country_code'],row['hs6_code'])
        values[key].add(row['anomaly_code'])
    return {k:anomaly_view(v) for k,v in values.items()}


def kpis(store,uid,selected=None):
    start,end=resolve_period(store,selected,selected,1)
    rows=aggregate(store,'export_summary',start,end)
    total=sum(r['amount_usd'] for r in rows)
    existing=set(verified_months(store,'export_summary'))
    def total_at(delta):
        m=shift_month(start,delta)
        return sum(r['amount_usd'] for r in aggregate(store,'export_summary',m,m)) if m in existing else None
    kpi={'amount_usd':total,'mom_pct':percentage(total,total_at(-1)),'yoy_pct':percentage(total,total_at(-12)),
         'export_country_count':sum(r['amount_usd']>0 for r in rows),
            'top_country_share_pct':max((r['amount_usd'] for r in rows),default=0)*100/total if total else None}
    meta=metadata(store,start,end)
    table=result_table(store,uid,[kpi],meta,{'period':period(start,end),'hs_items':[],'country_codes':[]},store.queries)
    return {'kpi':kpi,'result_id':table['result_id'],'meta':meta}


def trends(store,uid,start=None,end=None,months=24,hs=None,countries=None,flow='BOTH',save=True):
    require(flow in ('EXPORT','IMPORT','BOTH'),'수출입 방향을 확인하세요.')
    start,end=resolve_period(store,start,end,months)
    ex={r['trade_month']:r['amount_usd'] for r in aggregate(store,'export_summary',start,end,hs,countries,'MONTH')}
    im={r['trade_month']:r['amount_usd'] for r in aggregate(store,'import_summary',start,end,hs,countries,'MONTH')}
    verified=set(verified_months(store,'export_summary'))
    old={r['trade_month']:r['amount_usd'] for r in aggregate(store,'export_summary',shift_month(start,-12),shift_month(end,-12),hs,countries,'MONTH') if r['trade_month'] in verified}
    import_verified=set(verified_months(store,'import_summary'))
    old_import={r['trade_month']:r['amount_usd'] for r in aggregate(store,'import_summary',shift_month(start,-12),shift_month(end,-12),hs,countries,'MONTH') if r['trade_month'] in import_verified}
    items=[]; cursor=start
    while cursor<=end:
        prior=shift_month(cursor,-12)
        item={'month':cursor.strftime('%Y-%m'),'export_amount_usd':ex.get(cursor,Decimal(0)), 'import_amount_usd':im.get(cursor,Decimal(0)),
              'previous_year_export_usd':old.get(prior,Decimal(0)) if prior in verified else None,
              'previous_year_import_usd':old_import.get(prior,Decimal(0)) if prior in import_verified else None}
        items.append(item); cursor=shift_month(cursor,1)
    meta=metadata(store,start,end)
    out={'items':items,'meta':meta}
    if save:
        table=result_table(store,uid,items,meta,{'period':period(start,end),'hs_items':hs or [],'country_codes':countries or [],'trade_flow':flow},store.queries)
        out['result_id']=table['result_id']
    return out


def rankings(store,uid,start=None,end=None,hs=None,countries=None,group='COUNTRY',flow='EXPORT',page=1,size=5):
    require(group in ('COUNTRY','HS6') and flow in ('EXPORT','IMPORT'),'순위 기준을 확인하세요.')
    start,end=resolve_period(store,start,end,3)
    key=lambda r: r['country_code'] if group=='COUNTRY' else r['hs6_code']
    ex={key(r):r['amount_usd'] for r in aggregate(store,'export_summary',start,end,hs,countries,group)}
    im={key(r):r['amount_usd'] for r in aggregate(store,'import_summary',start,end,hs,countries,group)}
    primary={k:(ex if flow=='EXPORT' else im).get(k,Decimal(0)) for k in ex.keys()|im.keys()}
    if group=='COUNTRY':
        for code in countries or []: primary.setdefault(code,Decimal(0))
    total=sum(primary.values())
    previous={}; yoy={}; old_rank={}; rank_comparable=False
    if start==end:
        source='export_summary' if flow=='EXPORT' else 'import_summary'
        available=set(verified_months(store,source))
        rank_comparable=shift_month(start,-1) in available
        for offset,target in ((-1,previous),(-12,yoy)):
            m=shift_month(start,offset)
            if m in available:
                target.update({key(r):r['amount_usd'] for r in aggregate(store,source,m,m,hs,countries,group)})
        old_rank={k:n for n,k in enumerate(sorted((k for k in previous if previous[k]>0),key=lambda k:(-previous[k],k)),1)}
    flags=anomaly_map(store,start,end,hs,countries,group,flow)
    items=[]
    for rank,k in enumerate(sorted(primary,key=lambda k:(-primary[k],k)),1):
        h=[{'hs_code':k,'hs_ver':'HS2022'}] if group=='HS6' else hs
        country=[k] if group=='COUNTRY' else countries
        items.append({'rank':rank,'country_code':k if group=='COUNTRY' else None,
            'hs_item':h[0] if group=='HS6' else None,'export_amount_usd':ex.get(k,Decimal(0)),'import_amount_usd':im.get(k,Decimal(0)),
            'share_pct':primary[k]*100/total if total else None,
            'rank_change':old_rank[k]-rank if k in old_rank and primary[k]>0 else None,
            'rank_status':'NOT_APPLICABLE' if not rank_comparable or primary[k]<=0 else 'NEW' if k not in old_rank else 'UP' if old_rank[k]>rank else 'DOWN' if old_rank[k]<rank else 'SAME',
            'mom_pct':percentage(primary[k],previous.get(k)), 'yoy_pct':percentage(primary[k],yoy.get(k)),
            'one_off':flags.get(k,anomaly_view(set()))})
    meta=metadata(store,start,end)
    conditions={'hs_items':hs or [],'country_codes':countries or [],'period':period(start,end),'trade_flow':flow}
    table=result_table(store,uid,items,meta,conditions,store.queries)
    return {'items':items[(page-1)*size:page*size],'page':page_info(page,size,len(items)), 'result_id':table['result_id'],'meta':meta}


def market_shares(store,uid,conditions,trends_mode=False):
    p=conditions['period']; hs=conditions.get('hs_items',[])
    validate_scope(store,hs,conditions.get('country_codes',[]))
    start,end=resolve_period(store,p['start'][:7],p['end'][:7],12)
    # 분모는 관심 국가로 좁히지 않는다.
    source='import_summary' if conditions.get('trade_flow')=='IMPORT' else 'export_summary'
    rows=aggregate(store,source,start,end,hs,None,'MONTH_COUNTRY' if trends_mode else 'COUNTRY')
    if not rows: unavailable('비중을 계산할 수출 관측이 없습니다.')
    names={r['country_code']:r['name_ko'] for r in store.rows(select(store.t('country')))}
    totals=defaultdict(Decimal)
    for r in rows: totals[r['country_code']]+=r['amount_usd']
    top=sorted(totals,key=lambda k:(-totals[k],k))[:10]
    groups=defaultdict(list)
    annual=trends_mode and (end.year-start.year)*12+end.month-start.month+1>12
    for r in rows:
        label=(str(r['trade_month'].year) if annual else r['trade_month'].strftime('%Y-%m')) if trends_mode else 'ALL'
        groups[label].append(r)
    items=[]
    for label,rs in sorted(groups.items()):
        amounts=defaultdict(Decimal)
        for r in rs: amounts[r['country_code']]+=r['amount_usd']
        denominator=sum(amounts.values())
        for k in top+['OTHER']:
            amount=sum(v for code,v in amounts.items() if code not in top) if k=='OTHER' else amounts.get(k)
            if amount is None: continue
            item={'country_code':None if k=='OTHER' else k,'label':'기타' if k=='OTHER' else names[k],
                  'amount_usd':amount,'share_pct':amount*100/denominator if denominator else None}
            if trends_mode: item['period']=label
            items.append(item)
    meta=metadata(store,start,end)
    table=result_table(store,uid,items,meta,conditions,store.queries)
    if trends_mode:
        return {'table':table,'granularity':'YEAR' if annual else 'MONTH','chart':{'chart_type':'LINE','result_id':table['result_id'],'x_key':'period','series_keys':['share_pct'],'title':'국가별 비중 추이','positive_color':'#16A34A','negative_color':'#DC2626'},'meta':meta}
    return {'items':items,'denominator_usd':sum(totals.values()),'table':table,'meta':meta}
