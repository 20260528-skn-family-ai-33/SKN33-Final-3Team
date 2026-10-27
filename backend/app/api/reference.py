from sqlalchemy import select, func, or_, case
import logging
from backend.app.api.common import require, unavailable, page_info


def hs_search(store,q,depth=4,limit=5):
    q=q.strip()
    require(bool(q.strip()) and depth==4 and 1<=limit<=5,'검색어·HS4·최대 5개 조건을 확인하세요.')
    store.require_data('hs_code')
    t=store.t('hs_code')
    exact=or_(t.c.hs_code==q,t.c.name_ko==q,t.c.original_name_ko==q) if q.isascii() else or_(t.c.name_ko==q,t.c.original_name_ko==q)
    match=or_(t.c.hs_code.startswith(q,autoescape=True),t.c.name_ko.contains(q,autoescape=True)) if q.isascii() else t.c.name_ko.contains(q,autoescape=True)
    base=select(t.c.hs_code,t.c.hs_ver,t.c.name_ko,t.c.depth).where(t.c.hs_ver=='HS2022',t.c.depth==4)
    rows=store.rows(base.where(exact).order_by(t.c.hs_code).limit(limit))
    if rows: return {'items':rows,'match_type':'EXACT'}
    from backend.app.api.config import Settings
    from backend.app.api.hs_vectors import candidates
    settings=getattr(store.db,'settings',None) or Settings()
    ranked=[]; warnings=[]
    if settings.get('API_HS_VECTOR_ENABLED','true').lower()=='true':
        try:
            ranked=candidates(q,settings,limit)
        except Exception as error:
            logging.getLogger(__name__).warning('hs_vector_unavailable type=%s',type(error).__name__)
            warnings.append('품목 임베딩 검색 연결을 확인하세요. 등록 품목명 검색 결과만 제공합니다.')
    threshold=float(settings.get('API_HS_MAX_DISTANCE',0.70))
    codes=[code for code,distance in ranked if distance<=threshold]
    verified={r['hs_code']:r for r in store.rows(base.where(t.c.hs_code.in_(codes)))} if codes else {}
    rows=[verified[code] for code in dict.fromkeys(codes) if code in verified]
    # 근거 없는 후보는 채택하지 않는다. 벡터 실패/낮은 유사도에는 등록명 부분 일치 추천.
    if len(rows)<limit:
        seen={r['hs_code'] for r in rows}
        rows.extend(r for r in store.rows(base.where(match).order_by(t.c.hs_code).limit(limit)) if r['hs_code'] not in seen)
    result={'items':rows[:limit],'match_type':'SIMILAR' if rows else 'NONE'}
    if warnings: result['warnings']=warnings
    return result


def children(store,code,version):
    require(len(code)==4 and code.isdigit() and version=='HS2022','HS4와 HS2022가 필요합니다.')
    t=store.t('hs_code')
    require(store.c.scalar(select(t.c.hs_code).where(t.c.hs_code==code,t.c.hs_ver==version)) is not None,'품목을 찾을 수 없습니다.','NOT_FOUND',404)
    return {'items':store.rows(select(t.c.hs_code,t.c.hs_ver,t.c.name_ko,t.c.depth).where(t.c.hs_code.startswith(code),t.c.depth==6,t.c.hs_ver==version).order_by(t.c.hs_code))}


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
