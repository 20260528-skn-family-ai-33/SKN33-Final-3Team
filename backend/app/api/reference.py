from sqlalchemy import select, func, or_, case
import logging
import unicodedata
from backend.app.api.common import require, unavailable, page_info


def hs_search(store,q,depth=4,limit=5):
    require(depth==4,'화면 검색은 HS4만 허용합니다.')
    return _search_depth(store,q,depth,limit)


def chat_hs_search(store,q):
    """Prefer a clear HS6 match; ambiguous natural-language searches ask for HS4 confirmation."""
    if q.isascii() and q.isdigit() and len(q) in (4,6):
        return _search_depth(store,q,len(q),5)
    specific=_search_depth(store,q,6,5)
    if ((specific['match_type']=='EXACT' and len(specific['items'])==1)
            or specific['confidence']=='MEDIUM'):
        return specific
    broader=hs_search(store,q)
    result={**broader,'message':'HS6 세부품목을 확정하기 어렵습니다. HS4 분류를 확인하거나 분류에서 HS6를 직접 선택하세요.'}
    warnings=list(dict.fromkeys(specific.get('warnings',[])+broader.get('warnings',[])))
    if warnings: result['warnings']=warnings
    return result


def _search_depth(store,q,depth,limit):
    q=' '.join(unicodedata.normalize('NFKC',q).split()).casefold()
    require(len(q)<=120 and depth in (4,6) and 1<=limit<=5,'검색어 120자 이하·HS4/HS6·최대 5개 조건을 확인하세요.')
    if len(q)<2:
        return {'items':[],'match_type':'NONE','confidence':'LOW','message':'2글자 이상 입력하세요.',**browse_hint()}
    from backend.app.api.config import Settings
    settings=getattr(store.db,'settings',None) or Settings()
    key=(q,depth,limit,*(settings.get(k) for k in ('API_HS_VECTOR_ENABLED','API_HS_CHROMA_PATH',
        'API_HS_MAX_DISTANCE','API_HS_CONFIDENT_DISTANCE','API_HS_MIN_MARGIN','API_HS_CACHE_VERSION')))
    return store.db.hs_search_cache.get_or_load(key,lambda:_hs_search(store,q,limit,settings,depth),
        cache_if=lambda result:not result.get('warnings'))


def browse_hint():
    return {'requires_selection':True,'browse':{'label':'분류에서 직접 선택',
        'url':'/api/v1/reference/hs-codes/ROOT/children?hs_ver=HS2022','levels':[2,4,6]}}


def _hs_search(store,q,limit,settings,depth=4):
    store.require_data('hs_code')
    t=store.t('hs_code')
    compact=''.join(q.split())
    name=func.replace(t.c.name_ko,' ',''); original=func.replace(t.c.original_name_ko,' ','')
    exact=or_(t.c.hs_code==q,t.c.name_ko==q,t.c.original_name_ko==q) if q.isascii() else or_(name==compact,original==compact)
    match=or_(t.c.hs_code.startswith(q,autoescape=True),t.c.name_ko.contains(q,autoescape=True)) if q.isascii() else name.contains(compact,autoescape=True)
    base=select(t.c.hs_code,t.c.hs_ver,t.c.name_ko,t.c.depth).where(t.c.hs_ver=='HS2022',t.c.depth==depth)
    rows=store.rows(base.where(exact).order_by(t.c.hs_code).limit(limit))
    if rows: return {'items':rows,'match_type':'EXACT','confidence':'EXACT',**browse_hint()}
    from backend.app.api.hs_vectors import candidates
    ranked=[]; warnings=[]
    if settings.get('API_HS_VECTOR_ENABLED','true').lower()=='true':
        try:
            ranked=candidates(q,settings,limit+1,depth=depth)
        except Exception as error:
            logging.getLogger(__name__).warning('hs_vector_unavailable type=%s',type(error).__name__)
            warnings.append('품목 임베딩 검색 연결을 확인하세요. 등록 품목명 검색 결과만 제공합니다.')
    threshold=float(settings.get('API_HS_MAX_DISTANCE',0.70))
    ranked=sorted(ranked,key=lambda item:item[1])
    codes=[code for code,distance in ranked if 0<=distance<=threshold]
    verified={r['hs_code']:r for r in store.rows(base.where(t.c.hs_code.in_(codes)))} if codes else {}
    rows=[verified[code] for code in dict.fromkeys(codes) if code in verified]
    # 근거 없는 후보는 채택하지 않는다. 벡터 실패/낮은 유사도에는 등록명 부분 일치 추천.
    if len(rows)<limit:
        seen={r['hs_code'] for r in rows}
        rows.extend(r for r in store.rows(base.where(match).order_by(t.c.hs_code).limit(limit)) if r['hs_code'] not in seen)
    # Confidence belongs to the candidates actually returned, not stale/missing vector IDs.
    valid_distances={}
    for code,distance in ranked:
        if code in verified and 0<=distance<=threshold:
            valid_distances.setdefault(code,distance)  # ranked is ascending; keep the best per code.
    distances=list(valid_distances.values())
    best=distances[0] if distances else None
    margin=distances[1]-best if len(distances)>1 else None
    confident=best is not None and margin is not None and best<=float(settings.get('API_HS_CONFIDENT_DISTANCE',0.45)) and margin>=float(settings.get('API_HS_MIN_MARGIN',0.08))
    result={'items':rows[:limit],'match_type':'SIMILAR' if rows else 'NONE',
        'confidence':'MEDIUM' if confident and rows else 'LOW',
        'message':'비슷한 후보입니다. 품목 설명을 확인하거나 분류에서 직접 선택하세요.',**browse_hint()}
    if warnings: result['warnings']=warnings
    return result


def children(store,code,version):
    require(version=='HS2022' and (code=='ROOT' or (len(code) in (2,4) and code.isascii() and code.isdigit())),
            'ROOT 또는 HS2/HS4와 HS2022가 필요합니다.')
    t=store.t('hs_code')
    if code=='ROOT':
        return {'items':store.rows(select(t.c.hs_code,t.c.hs_ver,t.c.name_ko,t.c.depth)
            .where(t.c.depth==2,t.c.hs_ver==version).order_by(t.c.hs_code))}
    require(store.c.scalar(select(t.c.hs_code).where(t.c.hs_code==code,t.c.hs_ver==version)) is not None,'품목을 찾을 수 없습니다.','NOT_FOUND',404)
    return {'items':store.rows(select(t.c.hs_code,t.c.hs_ver,t.c.name_ko,t.c.depth).where(t.c.hs_code.startswith(code),t.c.depth==len(code)+2,t.c.hs_ver==version).order_by(t.c.hs_code))}


def countries(store,q=None,region_id=None):
    store.require_data('country')
    c=store.t('country')
    stmt=select(c.c.country_code,c.c.name_ko,c.c.region_id,c.c.currency_code).where(c.c.country_role=='COUNTRY')
    if q: stmt=stmt.where(or_(c.c.name_ko.contains(q,autoescape=True),c.c.country_code==q.upper()) if q.isascii() else c.c.name_ko.contains(q,autoescape=True))
    if region_id is not None:
        r=store.t('market_region')
        require(store.c.scalar(select(r.c.region_id).where(r.c.region_id==region_id)) is not None,'지역이 없습니다.')
        stmt=stmt.where(c.c.region_id.in_(select(r.c.region_id).where(or_(r.c.region_id==region_id,r.c.parent_id==region_id))))
    return {'items':store.rows(stmt.order_by(c.c.name_ko,c.c.country_code))}


def regions(store):
    store.require_data('market_region')
    r=store.t('market_region')
    rows=store.rows(select(r).order_by(r.c.region_id))
    return {'items':[{'region_id':x['region_id'],'parent_id':x['parent_id'],'name_ko':x['name_ko'],'level':x['region_level'],
                     'country_codes':[c['country_code'] for c in countries(store,region_id=x['region_id'])['items']]} for x in rows]}


def categories(store):
    store.require_data('keyword_category')
    t=store.t('keyword_category')
    return {'items':store.rows(select(t.c.category_id,t.c.major_name,t.c.minor_name).order_by(t.c.category_id))}


def topics(store,q,page=1,size=20):
    k,c,t=store.t('keyword_topic'),store.t('keyword_category'),store.t('article_tag')
    count=select(func.count(func.distinct(t.c.article_id))).where(t.c.keyword_text==k.c.keyword_text).scalar_subquery()
    stmt=select(k,c.c.major_name,c.c.minor_name,count.label('article_count')).outerjoin(c,c.c.category_id==k.c.category_id)
    if q: stmt=stmt.where(or_(k.c.keyword_text.contains(q,autoescape=True),k.c.canonical_text.contains(q,autoescape=True)))
    total=store.count(stmt)
    rows=store.rows(stmt.order_by(k.c.keyword_text).offset((page-1)*size).limit(size))
    return {'items':[{'keyword_text':r['keyword_text'],'canonical_text':r['canonical_text'],'status':r['status'],
        'category':{'category_id':r['category_id'],'major_name':r['major_name'],'minor_name':r['minor_name']} if r['category_id'] else None,
        'article_count':r['article_count']} for r in rows],'page':page_info(page,size,total)}
