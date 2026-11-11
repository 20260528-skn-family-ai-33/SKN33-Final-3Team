from datetime import date
import json
from sqlalchemy import select, func, exists, or_, and_, delete, update, case, false
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
    store.cache[key]=[r['kw_topic'] for r in block_items(store,uid)]
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


def block_items(store,uid):
    t=store.t('notification_setting')
    return store.c.scalar(select(t.c.keyword_blocks_json).where(t.c.user_id==uid)) or []


def blocks(store,uid):
    items=[{**r,'expansion_notice':r['kw_topic']+' 주제어로 통합된 기사가 차단됩니다.'}
           for r in block_items(store,uid)]
    return {'settings':{'items':items,'version':digest(items)}}


def save_blocks(store,uid,keywords,expected):
    u,b=store.t('users'),store.t('notification_setting')
    store.c.execute(select(u.c.user_id).where(u.c.user_id==uid).with_for_update()).first()
    if not store.c.execute(select(b.c.user_id).where(b.c.user_id==uid).with_for_update()).first():
        unavailable('알림 설정 행이 준비되지 않았습니다.','FEATURE_NOT_READY')
    require(expected==blocks(store,uid)['settings']['version'],'설정이 변경되었습니다.','VERSION_CONFLICT',409)
    grouped={}
    for term in keywords: grouped.setdefault(canonical(store,term),[]).append(term)
    store.c.execute(update(b).where(b.c.user_id==uid).values(keyword_blocks_json=[
        {'kw_topic':key,'selected_terms':sorted(set(terms))} for key,terms in sorted(grouped.items())]))
    store.cache.pop(('blocked_topics',uid),None)
    return blocks(store,uid)


TRADE_REGULATION_CATEGORIES=('통상·규제','통상규제')


def major_issue_condition(store):
    """목록 필터와 표시값에서 동일한 주요 이슈 판정을 사용한다."""
    a,e=store.t('article'),store.t('alert_event')
    return or_(and_(a.c.source_code=='KOTRA_MARKET',a.c.category.in_(TRADE_REGULATION_CATEGORIES)),
               exists(select(e.c.event_id).where(e.c.article_id==a.c.article_id,e.c.event_type=='MAJOR_ISSUE')))


def blocked_article(store,blocked):
    """기사에 차단 키워드나 차단 대표어의 동의어 태그가 있는지. 같은 태그 표를 쓰는 바깥 조회와 섞이지 않게 별칭을 쓴다."""
    a,k=store.t('article'),store.t('keyword_topic'); t=store.t('article_tag').alias('blocked_tag')
    return exists(select(t.c.tag_id).select_from(t.outerjoin(k,k.c.keyword_text==t.c.keyword_text)).where(t.c.article_id==a.c.article_id,
        or_(t.c.keyword_text.in_(blocked),k.c.canonical_text.in_(blocked))))


def article_statement(store,uid=None,tab='ALL',q=None,start=None,end=None,scope='GLOBAL',countries=None,hs=None,mode='BROWSE',category_boost=None):
    a,t,k=store.t('article'),store.t('article_tag'),store.t('keyword_topic')
    require(tab in ('ALL','GENERAL','BREAKING','MAJOR_ISSUE') and scope in ('GLOBAL','INTERESTED'),'기사 탭·범위를 확인하세요.')
    require(mode in ('BROWSE','SEARCH'),'검색 모드를 확인하세요.')
    if mode=='SEARCH': require(q and q.strip(),'검색어를 입력하세요.')
    if start and end: require(start<=end,'기사 시작일·종료일을 확인하세요.')
    countries=list(countries or []); hs=list(hs or [])
    explicit_hs=bool(hs)
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
    if hs:
        hs_match=exists(select(t.c.tag_id).where(t.c.article_id==a.c.article_id,
            or_(*(and_(t.c.hs_code.startswith(x['hs_code']),t.c.hs_ver==x['hs_ver']) for x in hs))))
        # 내 관심(저장된 관심 품목 기준): 단신속보는 적재 시 HS 태그를 넣지 않고 주요이슈도 HS 태그가 거의 없어
        # 품목 조건을 걸면 항상 빠진다. 이 두 종류는 관심 국가가 맞으면 포함한다(위 국가 조건이 이미 걸려 있음).
        # 관심 국가가 없거나 품목을 직접 지정한 조회는 지금처럼 품목이 맞아야 한다. 탭과 무관하게 같은 규칙이라
        # 전체기사 = 일반기사 ∪ 단신속보 ∪ 주요이슈가 유지된다.
        if scope=='INTERESTED' and not explicit_hs and countries:
            no_hs_tags=or_(a.c.source_code=='KOTRA_SHORT_NEWS',func.coalesce(major_issue_condition(store),false()))
            stmt=stmt.where(or_(hs_match,no_hs_tags))
        else:
            stmt=stmt.where(hs_match)
    blocked=blocked_topics(store,uid)
    if blocked: stmt=stmt.where(~blocked_article(store,blocked))
    if tab=='MARKET': stmt=stmt.where(a.c.source_code=='KOTRA_MARKET')
    if tab=='BREAKING': stmt=stmt.where(a.c.source_code=='KOTRA_SHORT_NEWS')
    if tab=='MAJOR_ISSUE':
        stmt=stmt.where(major_issue_condition(store))
    # 일반기사 = 단신속보도 주요이슈도 아닌 기사(화면 mapArticle의 general 판정과 같다).
    if tab=='GENERAL':
        # category가 NULL이면 판정식이 NULL이 되므로 거짓으로 바꿔 일반기사에 남긴다.
        stmt=stmt.where(a.c.source_code!='KOTRA_SHORT_NEWS',~func.coalesce(major_issue_condition(store),false()))
    if start: stmt=stmt.where(a.c.published_date>=start)
    if end: stmt=stmt.where(a.c.published_date<=end)
    if q:
        matched=select(k.c.keyword_text).where(or_(k.c.keyword_text.contains(q,autoescape=True),k.c.canonical_text.contains(q,autoescape=True)))
        stmt=stmt.where(or_(a.c.title.contains(q,autoescape=True),a.c.body_masked.contains(q,autoescape=True),
            exists(select(t.c.tag_id).where(t.c.article_id==a.c.article_id,t.c.keyword_text.in_(matched)))))
    # 분야는 후보 필터가 아닌 정렬 보조로만 적용한다.
    if category_boost:
        live=exists(select(t.c.tag_id).select_from(t.join(k,t.c.keyword_text==k.c.keyword_text)).where(t.c.article_id==a.c.article_id,k.c.category_id.in_(category_boost)))
        frozen = a.c.admin_meta_json['keyword_categories'].as_string()
        if store.c.dialect.name == 'mysql':
            saved = or_(*(func.json_contains(frozen, json.dumps({'category_id': identity})) == 1 for identity in category_boost))
        else:
            values = func.json_each(frozen).table_valued('value').alias('saved_categories')
            saved = exists(select(1).select_from(values).where(func.json_extract(values.c.value, '$.category_id').in_(category_boost)))
        boost=case((frozen.is_not(None), saved), else_=live)
        stmt=stmt.order_by(case((boost,0),else_=1))
    return stmt.order_by(a.c.published_date.desc(),a.c.article_id.desc())


def article_views(store,rows,uid=None):
    if not rows: return []
    t=store.t('article_tag'); ids=[r['article_id'] for r in rows]
    tags=store.rows(select(t).where(t.c.article_id.in_(ids)))
    a=store.t('article')
    major_ids=set(store.c.execute(select(a.c.article_id).where(a.c.article_id.in_(ids),major_issue_condition(store))).scalars())
    by_id={i:[] for i in ids}
    for tag in tags: by_id[tag['article_id']].append(tag)
    saved=interests(store,uid) if uid else {'hs_items':[],'country_codes':[]}
    result=[]
    for row in rows:
        ts=by_id[row['article_id']]
        countries=sorted({x['country_code'] for x in ts if x['country_code']})
        hs=[{'hs_code':c,'hs_ver':v} for c,v in sorted({(x['hs_code'],x['hs_ver']) for x in ts if x['hs_code']})]
        country_ok=bool(set(countries)&set(saved['country_codes']))
        # 목록(article_statement)과 같은 규칙: 단신속보·주요이슈는 HS 태그가 없어 관심 국가가 맞으면 일치로 본다.
        no_hs_tags=row['source_code']=='KOTRA_SHORT_NEWS' or row['article_id'] in major_ids
        hs_ok=not saved['hs_items'] or any(x in hs for x in saved['hs_items']) or (no_hs_tags and country_ok)
        matched=(not saved['country_codes'] or country_ok) and hs_ok and bool(saved['country_codes'] or saved['hs_items'])
        result.append({'article_id':str(row['article_id']),'source_code':row['source_code'],'title':row['title'],
            'published_date':row['published_date'],'country_codes':countries,'hs_items':hs,
            'summary':mask_text(row['body_masked'])[:400],'source_url':row['source_url'],
            'is_major_issue':row['article_id'] in major_ids, 'matched_interest':matched})
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
    currencies={row['currency_code']:row for row in store.rows(select(c).where(c.c.currency_code.in_(codes)))}
    require(all(code in currencies for code in codes),'통화 코드를 확인하세요.')
    ranked=select(f,func.row_number().over(partition_by=f.c.currency_code,order_by=f.c.base_date.desc()).label('position')).where(f.c.currency_code.in_(codes)).subquery()
    rates={code:[] for code in codes}
    for row in store.rows(select(ranked).where(ranked.c.position<=2).order_by(ranked.c.currency_code,ranked.c.base_date.desc())):
        rates[row['currency_code']].append(row)
    result=[]; latest=None
    for code in codes:
        currency=currencies[code]
        rows=rates[code]
        first=rows[0] if rows else None; old=rows[1] if len(rows)>1 else None
        if first: latest=max(latest or first['base_date'],first['base_date'])
        result.append({'currency_code':code,'quote_unit':currency['quote_unit'],'rate_krw':first['rate_krw'] if first else None,
            'base_date':first['base_date'] if first else None,'previous_base_date':old['base_date'] if old else None,
            'change_krw':first['rate_krw']-old['rate_krw'] if old else None,
            'change_pct':percentage(first['rate_krw'],old['rate_krw']) if old else None,'availability':'AVAILABLE' if first else 'UNAVAILABLE'})
    return {'items':result,'meta':source_meta(store,'ECOS',latest)}
