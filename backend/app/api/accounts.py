from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from backend.app.api.common import ApiError, now, digest, require, unavailable
from backend.app.api.security import password_hash, password_matches, issue_session, revoke_sessions
from backend.app.api.terms import record_signup, signup_consents, signup_versions


def interests(store, user_id, *, lock=False):
    t,u=store.t('user_interest'),store.t('users')
    user_query=select(u.c.landing_page).where(u.c.user_id==user_id)
    rows_query=select(t).where(t.c.user_id==user_id).order_by(t.c.hs_code,t.c.country_code)
    landing=store.c.scalar(user_query.with_for_update() if lock else user_query)
    rows=store.rows(rows_query.with_for_update() if lock else rows_query)
    result={'hs_items':[{'hs_code':r['hs_code'],'hs_ver':r['hs_ver']} for r in rows if r['hs_code']],
            'country_codes':sorted(r['country_code'] for r in rows if r['country_code']), 'landing_page':landing}
    result['version']=digest(result)
    return result


def validate_scope(store, hs_items, countries, depth=None):
    hs, country=store.t('hs_code'),store.t('country')
    for item in hs_items:
        code,ver=item['hs_code'],item['hs_ver']
        require(ver=='HS2022', '판본은 HS2022만 허용합니다.')
        require(code.isdigit() and len(code) in ([depth] if depth else [4,6]), '허용되지 않은 HS 단위입니다.', 'INVALID_HS_DEPTH')
        require(store.c.scalar(select(hs.c.hs_code).where(hs.c.hs_code==code,hs.c.hs_ver==ver)) is not None,'등록된 HS 코드가 아닙니다.','INVALID_HS_CODE')
    for code in countries:
        require(store.c.scalar(select(country.c.country_code).where(country.c.country_code==code,country.c.country_role=='COUNTRY')) is not None,'등록된 일반 국가 코드가 아닙니다.','INVALID_COUNTRY')


def user_view(store, user):
    scope=interests(store,user['user_id'])
    has=bool(scope['hs_items'] or scope['country_codes'])
    effective=user['landing_page'] if has else 'GLOBAL'
    return {'user_id':str(user['user_id']),'email':user['email'],'name':user['name'],
            'role':user['role'],'landing_page':user['landing_page'],'effective_landing_page':effective,
            'has_interests':has,
            'post_login_path':'/admin' if user['role']=='ADMIN' else '/?view=interest' if effective=='INTERESTED' else '/',
            # 가입·로그인·내 계정 조회가 같은 형태를 돌려주도록 가입 약관 동의 기록(최신순)을 함께 싣는다.
            'terms_consents':signup_consents(store,user['user_id'])}


def get_user(store, user_id=None, email=None, lock=False):
    u=store.t('users')
    stmt=select(u)
    # Keep the native UNIQUE email lookup indexable before and after migration.
    # An old case-insensitive column can find a different spelling: reject it
    # in Python without applying COLLATE to the indexed/locked SQL expression.
    stmt=stmt.where(u.c.user_id==user_id) if user_id is not None else stmt.where(u.c.email==email)
    user=store.one(stmt.with_for_update() if lock else stmt)
    return user if user_id is not None or not user or user['email']==email else None


def signup(store, data, response, settings):
    email=str(data.email).strip()
    require(email.casefold()!='admin@example.com','사전 관리 계정은 공개 가입할 수 없습니다.','RESERVED_ACCOUNT',403)
    hashed=password_hash(data.password,data.password_confirm)
    # 추가 저장소의 존재를 먼저 확인하여 가입이 일부만 기록되는 것을 방지한다.
    for name in ('users','notification_setting','user_consent'): store.t(name)
    if get_user(store,email=email):
        raise ApiError(409,'EMAIL_ALREADY_EXISTS','이미 등록된 이메일입니다.')
    versions=signup_versions(store,data)
    try:
        uid=store.insert('users',email=email,password_hash=hashed,landing_page='GLOBAL',
            name=data.name,role='USER',account_status='ACTIVE',created_at=now())
    except IntegrityError as error:
        # users has one client-supplied UNIQUE key: email. Handle both an old
        # case-insensitive index and a concurrent signup; other constraints
        # keep their original error. The request transaction rolls back.
        duplicate = ((store.c.dialect.name == 'mysql' and error.orig.args[:1] == (1062,))
                     or getattr(error.orig,'sqlite_errorcode',None) == 2067)
        if duplicate:
            raise ApiError(409,'EMAIL_ALREADY_EXISTS','이미 등록된 이메일입니다.') from None
        raise
    store.insert('notification_setting',user_id=uid,notifications_enabled=False,report_popup=True,major_issue_popup=True,spike_popup=True)
    record_signup(store,uid,versions)
    return {'user':user_view(store,get_user(store,uid)), 'next_screen':'BS03',
            'csrf_token':issue_session(store,uid,response,settings)}


def login(store, data, response, settings):
    user=get_user(store,email=str(data.email).strip(),lock=True)
    if not user or not password_matches(data.password,user['password_hash']):
        raise ApiError(401,'INVALID_CREDENTIALS','이메일 또는 비밀번호가 일치하지 않습니다.')
    require(user['account_status']=='ACTIVE','이용이 제한되거나 탈퇴한 계정입니다.','ACCOUNT_UNAVAILABLE',403)
    p=store.t('users')
    store.c.execute(update(p).where(p.c.user_id==user['user_id']).values(last_login_at=now()))
    return {'user':user_view(store,user),'csrf_token':issue_session(store,user['user_id'],response,settings)}


def save_interests(store, uid, data):
    u,t=store.t('users'),store.t('user_interest')
    current=interests(store,uid,lock=True)
    require(data.expected_version==current['version'],'다른 화면에서 관심정보를 저장했습니다.','VERSION_CONFLICT',409)
    hs=[x.model_dump() for x in data.hs_items]
    validate_scope(store,hs,data.country_codes,6)
    hs=list({(x['hs_code'],x['hs_ver']):x for x in hs}.values())
    countries=sorted(set(data.country_codes))
    landing=data.landing_page or current['landing_page']
    if not hs and not countries: landing='GLOBAL'
    store.c.execute(delete(t).where(t.c.user_id==uid))
    for x in hs: store.insert('user_interest',user_id=uid,**x)
    for code in countries: store.insert('user_interest',user_id=uid,country_code=code)
    store.c.execute(update(u).where(u.c.user_id==uid).values(landing_page=landing))
    return {'interests':interests(store,uid,lock=True)}


def change_password(store, user, data):
    # 임시 비밀번호 재발급과 같은 사용자 행 잠금을 사용한다.
    user=get_user(store,user_id=user['user_id'],lock=True)
    require(str(data.email).strip()==user['email'] and password_matches(data.current_password,user['password_hash']),
            '현재 비밀번호가 일치하지 않습니다.','CURRENT_PASSWORD_INVALID',403)
    hashed=password_hash(data.new_password,data.new_password_confirm)
    u=store.t('users')
    store.c.execute(update(u).where(u.c.user_id==user['user_id']).values(password_hash=hashed))
    revoke_sessions(store,user['user_id'])
