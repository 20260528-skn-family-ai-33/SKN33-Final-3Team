from datetime import date
from sqlalchemy import select, func, exists, or_, and_, delete, update, case
from backend.app.api.common import require, unavailable, page_info, digest, now, mask_text, percentage
from backend.app.api.accounts import interests, validate_scope


def source_meta(store,source,latest=None):
    return {'data_version':f'{source}-{latest or "empty"}', 'latest_available_month':None,'period':None,
            'sources':[{'code':source,'label':source,'url':None}], 'freshness':'FRESH' if latest else 'UNAVAILABLE',
            'generated_at':now().isoformat()+'Z','warnings':[] if latest else ['데이터 적재 대기']}


def blocked_topics(store,uid):
    if uid is None: return []
    key=('blocked_topics',uid)
    if key in store.cache: return store.cache[key]
    b=store.t('api_keyword_block')
    store.cache[key]=list(store.c.execute(select(b.c.kw_topic).where(b.c.user_id==uid)).scalars())
    return store.cache[key]


def canonical(store,term):
    t=store.t('keyword_topic'); seen=set()
    while term not in seen:
        seen.add(term)
        row=store.one(select(t).where(t.c.keyword_text==term))
        require(row is not None,'등록되지 않은 키워드입니다.','INVALID_KEYWORD')
        if row['status']!='SYNONYM': return term
        term=row['canonical_text']
    unavailable('키워드 대표어 순환을 정비해야 합니다.')


def blocks(store,uid):
    b=store.t('api_keyword_block')
    items=[{'kw_topic':r['kw_topic'],'selected_terms':r['selected_terms'],
            'expansion_notice':r['kw_topic']+' 주제어로 통합된 기사가 차단됩니다.'}
           for r in store.rows(select(b).where(b.c.user_id==uid).order_by(b.c.kw_topic))]
    return {'settings':{'items':items,'version':digest(items)}}


def save_blocks(store,uid,keywords,expected):
    u,b=store.t('users'),store.t('api_keyword_block')
    store.c.execute(select(u.c.user_id).where(u.c.user_id==uid).with_for_update()).first()
    require(expected==blocks(store,uid)['settings']['version'],'설정이 변경되었습니다.','VERSION_CONFLICT',409)
    grouped={}
    for term in keywords: grouped.setdefault(canonical(store,term),[]).append(term)
    store.c.execute(delete(b).where(b.c.user_id==uid))
    for key,terms in grouped.items(): store.insert('api_keyword_block',user_id=uid,kw_topic=key,selected_terms=sorted(set(terms)))
    store.cache.pop(('blocked_topics',uid),None)
    return blocks(store,uid)


def article_statement(store,uid=None,tab='ALL',q=None,start=None,end=None,scope='GLOBAL',countries=None,hs=None,mode='BROWSE',category_boost=None):
    a,t,k=store.t('article'),store.t('article_tag'),store.t('keyword_topic')
    require(tab in ('ALL','BREAKING','MAJOR_ISSUE') and scope in ('GLOBAL','INTERESTED'),'기사 탭·범위를 확인하세요.')
    require(mode in ('BROWSE','SEARCH'),'검색 모드를 확인하세요.')
    if mode=='SEARCH': require(q and q.strip(),'검색어를 입력하세요.')
    if start and end: require(start<=end,'기사 시작일·종료일을 확인하세요.')
    countries=list(countries or []); hs=list(hs or [])
    validate_scope(store,hs,countries)
    stmt=select(a).where(a.c.visibility_status=='VISIBLE')
    if scope=='INTERESTED':
        require(uid is not None,'로그인이 필요합니다.','UNAUTHENTICATED',401)
        saved=interests(store,uid)
        require(saved['hs_items'] or saved['country_codes'],'등록한 관심이 없습니다.','INTERESTS_REQUIRED',409)
        if countries: require(set(countries)<=set(saved['country_codes']),'관심 국가 밖의 조건입니다.')
        else: countries=saved['country_codes']
        if hs:
            hs=[x for x in saved['hs_items'] if any(x['hs_code'].startswith(y['hs_code']) for y in hs)]
            require(hs,'관심 품목 밖의 조건입니다.')
        else: hs=saved['hs_items']
    if countries: stmt=stmt.where(exists(select(t.c.tag_id).where(t.c.article_id==a.c.article_id,t.c.country_code.in_(countries))))
    if hs: stmt=stmt.where(exists(select(t.c.tag_id).where(t.c.article_id==a.c.article_id,
        or_(*(and_(t.c.hs_code.startswith(x['hs_code']),t.c.hs_ver==x['hs_ver']) for x in hs)))))
    blocked=blocked_topics(store,uid)
    if blocked:
        stmt=stmt.where(~exists(select(t.c.tag_id).select_from(t.outerjoin(k,k.c.keyword_text==t.c.keyword_text)).where(t.c.article_id==a.c.article_id,
            or_(t.c.keyword_text.in_(blocked),k.c.canonical_text.in_(blocked)))))
    if tab=='MARKET': stmt=stmt.where(a.c.source_code=='KOTRA_MARKET')
    if tab=='BREAKING': stmt=stmt.where(a.c.source_code=='KOTRA_SHORT_NEWS')
    if tab=='MAJOR_ISSUE':
        e=store.t('alert_event')
        stmt=stmt.where(or_(and_(a.c.source_code=='KOTRA_MARKET',a.c.category.in_(['통상·규제','통상규제'])),
                           exists(select(e.c.event_id).where(e.c.article_id==a.c.article_id,e.c.event_type=='MAJOR_ISSUE'))))
    if start: stmt=stmt.where(a.c.published_date>=start)
    if end: stmt=stmt.where(a.c.published_date<=end)
    if q:
        matched=select(k.c.keyword_text).where(or_(k.c.keyword_text.contains(q,autoescape=True),k.c.canonical_text.contains(q,autoescape=True)))
        stmt=stmt.where(or_(a.c.title.contains(q,autoescape=True),a.c.body_masked.contains(q,autoescape=True),
            exists(select(t.c.tag_id).where(t.c.article_id==a.c.article_id,t.c.keyword_text.in_(matched)))))
    # 분야는 후보 필터가 아닌 정렬 보조로만 적용한다.
    if category_boost:
        boost=exists(select(t.c.tag_id).select_from(t.join(k,t.c.keyword_text==k.c.keyword_text)).where(t.c.article_id==a.c.article_id,k.c.category_id.in_(category_boost)))
        stmt=stmt.order_by(case((boost,0),else_=1))
    return stmt.order_by(a.c.published_date.desc(),a.c.article_id.desc())


def article_views(store,rows,uid=None):
    if not rows: return []
    t=store.t('article_tag'); ids=[r['article_id'] for r in rows]
    tags=store.rows(select(t).where(t.c.article_id.in_(ids)))
    by_id={i:[] for i in ids}
    for tag in tags: by_id[tag['article_id']].append(tag)
    saved=interests(store,uid) if uid else {'hs_items':[],'country_codes':[]}
    result=[]
    for row in rows:
        ts=by_id[row['article_id']]
        countries=sorted({x['country_code'] for x in ts if x['country_code']})
        hs=[{'hs_code':c,'hs_ver':v} for c,v in sorted({(x['hs_code'],x['hs_ver']) for x in ts if x['hs_code']})]
        matched=(not saved['country_codes'] or bool(set(countries)&set(saved['country_codes']))) and (not saved['hs_items'] or any(x in hs for x in saved['hs_items'])) and bool(saved['country_codes'] or saved['hs_items'])
        result.append({'article_id':str(row['article_id']),'source_code':row['source_code'],'title':row['title'],
            'published_date':row['published_date'],'country_codes':countries,'hs_items':hs,
            'summary':mask_text(row['body_masked'])[:400],'source_url':row['source_url'],
            'is_major_issue':row['source_code']=='KOTRA_MARKET' and row['category'] in ('통상·규제','통상규제'), 'matched_interest':matched})
    return result


def articles(store,uid=None,page=1,size=20,**filters):
    store.require_data('article')
    stmt=article_statement(store,uid,**filters)
    total=store.count(stmt)
    rows=store.rows(stmt.offset((page-1)*size).limit(size))
    a=store.t('article')
    latest=store.c.scalar(select(func.max(a.c.published_date)))
    return {'items':article_views(store,rows,uid),'page':page_info(page,size,total),'meta':source_meta(store,'KOTRA',latest)}


def article_detail(store,identity,uid=None):
    a=store.t('article')
    row=store.one(article_statement(store,uid).where(a.c.article_id==identity))
    require(row is not None,'기사를 찾을 수 없습니다.','NOT_FOUND',404)
    return {'article':article_views(store,[row],uid)[0],'body_masked':mask_text(row['body_masked'])}


def exchange_rates(store,uid=None,scope='GLOBAL',codes=None):
    require(scope in ('GLOBAL','INTERESTED'),'환율 범위를 확인하세요.')
    c,f,country=store.t('currency'),store.t('fx_daily'),store.t('country')
    if scope=='INTERESTED':
        require(uid is not None,'로그인이 필요합니다.','UNAUTHENTICATED',401)
        saved=interests(store,uid)
        allowed=set(store.c.execute(select(country.c.currency_code).where(country.c.country_code.in_(saved['country_codes']))).scalars())-{None}
        if codes: require(set(codes)<=allowed,'관심 국가 통화만 조회할 수 있습니다.')
        codes=sorted(set(codes or allowed))
    else: codes=list(dict.fromkeys(codes or ['USD','EUR','CNY','JPY','GBP']))[:5]
    result=[]; latest=None
    for code in codes:
        currency=store.one(select(c).where(c.c.currency_code==code))
        require(currency is not None,'통화 코드를 확인하세요.')
        rows=store.rows(select(f).where(f.c.currency_code==code).order_by(f.c.base_date.desc()).limit(2))
        first=rows[0] if rows else None; old=rows[1] if len(rows)>1 else None
        if first: latest=max(latest or first['base_date'],first['base_date'])
        result.append({'currency_code':code,'quote_unit':currency['quote_unit'],'rate_krw':first['rate_krw'] if first else None,
            'base_date':first['base_date'] if first else None,'previous_base_date':old['base_date'] if old else None,
            'change_krw':first['rate_krw']-old['rate_krw'] if old else None,
            'change_pct':percentage(first['rate_krw'],old['rate_krw']) if old else None,'availability':'AVAILABLE' if first else 'UNAVAILABLE'})
    return {'items':result,'meta':source_meta(store,'ECOS',latest)}
