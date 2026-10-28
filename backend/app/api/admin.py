import secrets
from sqlalchemy import select, update, func
from backend.app.api.common import require, now, digest, clean, page_info, unavailable
from backend.app.api.security import administrator, revoke_sessions
from backend.app.api import accounts, notifications, reports, news


def member(store,row):
    s=store.t('report_subscription')
    return {'user_id':str(row['user_id']),'email':row['email'],'name':row['name'],'role':row['role'],
        'account_status':row['account_status'],'login_methods':['EMAIL'],
        'active_subscription_count':store.c.scalar(select(func.count()).select_from(s).where(s.c.user_id==row['user_id'],s.c.enabled==True)),
        'last_login_at':row['last_login_at'],'withdrawn_at':row['withdrawn_at'],'scheduled_delete_at':row['scheduled_delete_at']}


def user_row(store,identity,lock=False):
    u,p=store.t('users'),store.t('api_user_profile')
    stmt=select(u.c.user_id,u.c.email,u.c.landing_page,p.c.name,p.c.role,p.c.account_status,p.c.created_at,
        p.c.last_login_at,p.c.withdrawn_at,p.c.scheduled_delete_at).join(p,p.c.user_id==u.c.user_id).where(u.c.user_id==identity)
    row=store.one(stmt.with_for_update() if lock else stmt)
    require(row is not None,'회원을 찾을 수 없습니다.','NOT_FOUND',404)
    return row


def users(store,q=None,status='ALL',page=1,size=20):
    u,p=store.t('users'),store.t('api_user_profile')
    stmt=select(u.c.user_id).join(p,p.c.user_id==u.c.user_id)
    if q: stmt=stmt.where(u.c.email.contains(q,autoescape=True) | p.c.name.contains(q,autoescape=True))
    if status!='ALL': stmt=stmt.where(p.c.account_status==status)
    total=store.count(stmt)
    ids=store.c.execute(stmt.order_by(u.c.user_id.desc()).offset((page-1)*size).limit(size)).scalars()
    return {'items':[member(store,user_row(store,i)) for i in ids],'page':page_info(page,size,total)}


def detail(store,identity):
    row=user_row(store,identity)
    return {'member':member(store,row),'interests':accounts.interests(store,identity),'settings':notifications.settings(store,identity),
            'subscriptions':reports.subscription_list(store,identity)['items'],'version':digest(row)}


def audit(store,actor,action,target_type,target,before,after,reason):
    require(reason and reason.strip(),'변경 사유가 필요합니다.')
    identity='audit_'+secrets.token_hex(16)
    store.insert('api_audit_event',audit_id=identity,actor_id=actor,action=action,target_type=target_type,target_id=str(target),
        reason=reason.strip(),before_json=clean(before),after_json=clean(after),created_at=now())
    return {'operation':{'operation_id':identity,'status':'SUCCEEDED','audit_id':identity}}


def change_user(store,actor,identity,data,role=False):
    administrator(actor,super_only=role)
    row=user_row(store,identity,lock=True)
    require(data.get('confirmed') is True and data.get('reason','').strip(),'변경 확인과 사유가 필요합니다.')
    require(data.get('expected_version')==digest(row),'회원 정보가 변경되었습니다.','VERSION_CONFLICT',409)
    if role:
        require(row['email'].lower()!='admin@example.com' and row['role']!='SUPER_ADMIN','최고 관리자 권한은 변경할 수 없습니다.','FORBIDDEN',403)
        require(row['account_status']=='ACTIVE' and data['role'] in ('USER','ADMIN'),'정상 회원의 USER/ADMIN 권한만 변경할 수 있습니다.')
        patch={'role':data['role']}
    else:
        if row['role']=='SUPER_ADMIN' or row['user_id']==actor['user_id']:
            unavailable('최고 관리자·자기 계정 제한 정책 DEC-48 합의가 필요합니다.','FEATURE_NOT_READY')
        require(data['status'] in ('ACTIVE','RESTRICTED') and row['account_status']!='WITHDRAWN','상태 변경을 허용할 수 없습니다.')
        patch={'account_status':data['status']}
    p=store.t('api_user_profile'); store.c.execute(update(p).where(p.c.user_id==identity).values(**patch))
    revoke_sessions(store,identity)
    return audit(store,actor['user_id'],'USER_ROLE' if role else 'USER_STATUS','USER',identity,row,{**row,**patch},data['reason'])


def runs(store,page=1,size=20,source=None,status=None):
    t=store.t('batch_run'); stmt=select(t)
    if source: stmt=stmt.where(t.c.source_code==source)
    if status: stmt=stmt.where(t.c.status==status)
    rows=store.rows(stmt.order_by(t.c.run_id.desc()).offset((page-1)*size).limit(size))
    return {'items':[{'run_id':str(r['run_id']),**{k:r[k] for k in ('source_code','job_name','period_start','period_end','status','request_count','received_count')},
             'published':(r.get('detail_json') or {}).get('published')} for r in rows],'page':page_info(page,size,store.count(stmt))}


def article_detail(store,identity):
    a=store.t('article'); row=store.one(select(a).where(a.c.article_id==identity))
    require(row is not None,'기사를 찾을 수 없습니다.','NOT_FOUND',404)
    t,k,c=store.t('article_tag'),store.t('keyword_topic'),store.t('keyword_category')
    topics=store.rows(select(k.c.keyword_text,k.c.canonical_text,c.c.category_id,c.c.major_name,c.c.minor_name)
        .select_from(t.join(k,t.c.keyword_text==k.c.keyword_text).outerjoin(c,c.c.category_id==k.c.category_id)).where(t.c.article_id==identity))
    moderation={'article_id':str(identity),'visibility':'VISIBLE' if row['visibility_status']=='VISIBLE' else 'EXCLUDED',
        'original_category':row['category'],'kw_topic':sorted(set(x['canonical_text'] or x['keyword_text'] for x in topics)),
        'kw_sub':None,'categories':list({x['category_id']:{k:x[k] for k in ('category_id','major_name','minor_name')} for x in topics if x['category_id']}.values()),
        'affected_report_ids':[]}
    # 저장 리포트는 변경하지 않고 참조 관계만 검사한다.
    r=store.t('report')
    for report in store.rows(select(r.c.report_id,r.c.content_json).where(r.c.content_json.is_not(None))):
        sections=report['content_json'].get('sections',[])
        if any(str(article.get('article_id'))==str(identity) for section in sections for article in section.get('articles',[])):
            moderation['affected_report_ids'].append(str(report['report_id']))
    return {'article':news.article_views(store,[row])[0],'body_masked':row['body_masked'],'moderation':moderation,'version':digest({'article':row,'topics':topics})}


def change_visibility(store,actor,identity,data):
    a=store.t('article'); store.c.execute(select(a.c.article_id).where(a.c.article_id==identity).with_for_update()).first()
    before=article_detail(store,identity)
    require(data.get('expected_version')==before['version'],'기사 정보가 변경되었습니다.','VERSION_CONFLICT',409)
    require(data.get('confirmed') is True and data.get('reason','').strip(),'확인과 사유가 필요합니다.')
    require(data['visibility'] in ('VISIBLE','EXCLUDED'),'노출 상태를 확인하세요.')
    row=store.one(select(a).where(a.c.article_id==identity)); meta=dict(row['admin_meta_json'])
    meta['visibility']={'reason':data['reason'],'changed_by':str(actor['user_id']),'changed_at':now().isoformat()+'Z'}
    store.c.execute(update(a).where(a.c.article_id==identity).values(visibility_status='VISIBLE' if data['visibility']=='VISIBLE' else 'HIDDEN',admin_meta_json=meta))
    return audit(store,actor['user_id'],'ARTICLE_VISIBILITY','ARTICLE',identity,before['moderation'],{'visibility':data['visibility']},data['reason'])


def audit_view(row):
    return {'audit_id':row['audit_id'],'actor_id':str(row['actor_id']),'action':row['action'],'target_type':row['target_type'],
        'target_id':row['target_id'],'reason':row['reason'],'before':row['before_json'],'after':row['after_json'],
        'impact':'신규 조회에 적용, 저장된 답변·리포트 보존','result':'SUCCEEDED','created_at':row['created_at']}


def audit_list(store,page=1,size=20,target_type=None):
    t=store.t('api_audit_event'); stmt=select(t)
    if target_type: stmt=stmt.where(t.c.target_type==target_type)
    return {'items':[audit_view(r) for r in store.rows(stmt.order_by(t.c.created_at.desc(),t.c.audit_id.desc()).offset((page-1)*size).limit(size))],
        'page':page_info(page,size,store.count(stmt))}
