from datetime import datetime, timezone
from sqlalchemy import select, update, func, and_, or_, exists, cast, String
from backend.app.api.common import require, unavailable, now, page_info
from backend.app.api.news import blocked_topics, article_views, article_statement, source_meta
from backend.app.api.results import save_snapshot, read_snapshot


POPUP_KINDS={'report_popup':('REPORT',),'major_issue_popup':('MAJOR_ISSUE',),'spike_popup':('PRODUCT_SPIKE','KEYWORD_SPIKE')}
REPORT_KINDS={'RPT_WEEKLY':'주간','RPT_MONTHLY':'월간','RPT_QUARTERLY':'분기'}


def active_terms(store):
    t=store.t('service_option')
    rows=store.rows(select(t).where(t.c.option_type=='NOTIFICATION_TERMS',t.c.enabled==True).order_by(t.c.sort_order.desc(),t.c.option_code.desc()))
    effective=[]
    for row in rows:
        value=(row['value_json'] or {}).get('effective_at')
        if value:
            try:
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                timestamp=parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).replace(tzinfo=None)
            except (ValueError,TypeError): unavailable('약관 적용 시각 설정을 확인하세요.','FEATURE_NOT_READY')
            if timestamp>now(): continue
        effective.append(row)
    return effective


def terms(store):
    rows=active_terms(store)
    if not rows: unavailable('승인된 활성 알림 약관이 아직 등록되지 않았습니다.','FEATURE_NOT_READY')
    row=rows[0]; data=row['value_json']
    require(isinstance(data.get('body'),str) and bool(data['body'].strip()),'약관 본문이 준비되지 않았습니다.','FEATURE_NOT_READY',503)
    return {'terms_type':'NOTIFICATION','version':row['option_code'],'title':row['label_ko'],'body':data['body'],'required':True}


def settings(store,uid):
    s,c=store.t('notification_setting'),store.t('user_consent')
    row=store.one(select(s).where(s.c.user_id==uid))
    if not row: unavailable('알림 설정 행이 준비되지 않았습니다.','FEATURE_NOT_READY')
    active=active_terms(store); version=active[0]['option_code'] if active else None
    latest=store.one(select(c).where(c.c.user_id==uid).order_by(c.c.acted_at.desc(),c.c.consent_id.desc()).limit(1))
    return {**{k:bool(row[k]) for k in ('notifications_enabled','spike_popup','major_issue_popup','report_popup')},
            'consent_current':bool(version and latest and latest['terms_version']==version and latest['action']=='AGREE'),
            'terms_version':latest['terms_version'] if latest else None}


def popup_kinds(conf):
    """지금 팝업으로 보여 줄 수 있는 알림 종류."""
    if not conf['notifications_enabled'] or not conf['consent_current']: return set()
    return {kind for flag,kinds in POPUP_KINDS.items() if conf[flag] for kind in kinds}


def skip_pending_popups(store,uid,kinds):
    """팝업을 다시 켜기 전에 받은 알림은 띄우지 않는다. 별도 컬럼이 없어 표시 시각으로 함께 기록한다."""
    if not kinds: return
    u,e=store.t('user_alert'),store.t('alert_event')
    store.c.execute(update(u).where(u.c.user_id==uid,u.c.popup_shown_at.is_(None),
        u.c.event_id.in_(select(e.c.event_id).where(e.c.event_type.in_(sorted(kinds))))).values(popup_shown_at=now()))


def patch_settings(store,uid,values):
    before=settings(store,uid)
    if values.get('notifications_enabled'):
        require(before['consent_current'],'현재 알림 약관 동의가 필요합니다.','CONSENT_REQUIRED',409)
    t=store.t('notification_setting')
    if values: store.c.execute(update(t).where(t.c.user_id==uid).values(**values))
    after=settings(store,uid)
    skip_pending_popups(store,uid,popup_kinds(after)-popup_kinds(before))
    return {'settings':after}


def consent(store,uid,data):
    require(data['terms_type']=='NOTIFICATION' and data['action'] in ('AGREE','DECLINE','REVOKE'),'약관 종류·응답을 확인하세요.')
    current=terms(store)
    require(current['version']==data['terms_version'],'약관 버전이 변경되었습니다.','TERMS_VERSION_CHANGED',409)
    t=store.t('notification_setting')
    setting=store.one(select(t).where(t.c.user_id==uid).with_for_update())
    if not setting: unavailable('알림 설정 행이 준비되지 않았습니다.','FEATURE_NOT_READY')
    before=popup_kinds(settings(store,uid))
    enabled=bool(data.get('enable_notifications')) and data['action']=='AGREE'
    require(not data.get('enable_notifications') or data['action']=='AGREE','동의해야 알림을 켤 수 있습니다.')
    c=store.t('user_consent')
    latest=store.one(select(c).where(c.c.user_id==uid).order_by(c.c.acted_at.desc(),c.c.consent_id.desc()).limit(1).with_for_update())
    if latest and latest['terms_version']==current['version'] and latest['action']==data['action']=='AGREE':
        at=latest['acted_at']
    else:
        at=now()
        store.insert('user_consent',user_id=uid,terms_version=current['version'],action=data['action'],acted_at=at)
    notifications_enabled=bool(setting['notifications_enabled'])
    if data['action']!='AGREE' or enabled:
        store.c.execute(update(t).where(t.c.user_id==uid).values(notifications_enabled=enabled))
        notifications_enabled=enabled
    skip_pending_popups(store,uid,popup_kinds(settings(store,uid))-before)
    return {'consent':{'terms_type':'NOTIFICATION','terms_version':data['terms_version'],'action':data['action'],
                       'acted_at':at,'notifications_enabled':notifications_enabled}}


def target_blocked(store,uid,payload):
    blocked=set(blocked_topics(store,uid)); target=payload.get('target',{})
    return target.get('keyword_text') in blocked or target.get('kw_topic') in blocked


def event_allowed(store,uid,event):
    payload=event['payload_json']
    if target_blocked(store,uid,payload): return False
    ids=payload.get('article_ids',[]) or ([event['article_id']] if event['article_id'] else [])
    if ids:
        a=store.t('article')
        visible=store.c.execute(article_statement(store,uid).with_only_columns(a.c.article_id).where(a.c.article_id.in_(ids))).scalars().all()
        return bool(visible)
    return True


def issue_view(store,event,uid=None,articles=None):
    p=event['payload_json']; target=p.get('target',{})
    ids=p.get('article_ids',[]) or ([event['article_id']] if event['article_id'] else [])
    if articles is None:
        a=store.t('article')
        rows=store.rows(article_statement(store,uid).where(a.c.article_id.in_(ids))) if ids else []
        articles=article_views(store,rows,uid)
    return {'event_id':str(event['event_id']),'event_type':event['event_type'],
        'label':p.get('label') or target.get('keyword_text') or target.get('hs_code') or '주요 이슈',
        'reason':p.get('reason',event['event_type']),'current_count':p.get('recent_week_count',p.get('day_count')),
        'baseline_average':p.get('baseline_weekly',p.get('baseline_daily')),'ratio':p.get('ratio'),
        'window_start':p.get('window_start'),'window_end':p.get('window_end'),'articles':articles}


def notice_text(event_type,p):
    """API 공통스키마 Notification의 유형별 표시 문구. 판정 사건은 근거 값으로 만든다."""
    if event_type=='REPORT':
        kind=REPORT_KINDS.get(p.get('report_type'))
        period=p.get('period') or {}
        title=f'{kind} 리포트가 발행되었습니다.' if kind else '리포트가 발행되었습니다.'
        return title,(f"{period['start']} ~ {period['end']} 기간 리포트를 확인하세요." if period else '저장된 리포트를 확인하세요.')
    label=p.get('label')
    if event_type=='MAJOR_ISSUE' and p.get('reason')=='WEEKLY_NEWS_SPIKE':
        return f'주요 이슈: {label}',f"최근 7일 기사 {p['recent_week_count']}건으로 직전 8주 주평균의 {p['ratio']:.1f}배입니다."
    if event_type=='MAJOR_ISSUE' and p.get('reason')=='KOTRA_TRADE_REGULATION':
        return f'주요 이슈: {label}','KOTRA 통상·규제 기사가 새로 등록되었습니다.'
    return '관심 기사·주요 이슈 알림','관련 기사를 확인하세요.'


def notification_view(store,row,uid,articles=None):
    p=row['payload_json']; report=row['event_type']=='REPORT'
    title,message=notice_text(row['event_type'],p)
    issue=None if report else issue_view(store,row,uid,articles)
    if report: target=('REPORT',row['report_id'])
    else:
        # 이 회원이 열 수 있는 근거 기사로 이동한다. 대표 기사를 우선하고, 근거가 없으면 알림 상세(ANALYSIS, event_id)로 연다.
        visible=[a['article_id'] for a in issue['articles']]
        article=str(row['article_id']) if str(row['article_id']) in visible else visible[0] if visible else None
        target=('ARTICLE',article) if article else ('ANALYSIS',row['event_id'])
    return {'user_alert_id':str(row['user_alert_id']),'event_id':str(row['event_id']),'event_type':row['event_type'],
        'title':p.get('title',title),'message':p.get('message',message),
        'created_at':row['received_at'],'read_at':row['read_at'],'target_type':target[0],'target_id':str(target[1]),
        'matched_interest':bool(row['is_interest_match']),'issue':issue}


def event_allowed_sql(store,uid):
    e,a=store.t('alert_event'),store.t('article')
    blocked=blocked_topics(store,uid)
    target=e.c.payload_json['target']
    conditions=[]
    if blocked:
        conditions.extend(func.coalesce(target[key].as_string(),'').not_in(blocked) for key in ('keyword_text','kw_topic'))
    ids=func.json_extract(e.c.payload_json,'$.article_ids')
    if store.c.dialect.name=='sqlite':
        entries=func.json_each(ids).table_valued('key','value').alias('event_articles')
        array_match=exists(select(1).select_from(entries).where(cast(entries.c.value,String)==cast(a.c.article_id,String)).correlate(e,a))
        count=func.coalesce(func.json_array_length(ids),0)
    else:
        array_match=or_(func.json_contains(ids,func.json_array(a.c.article_id))==1,
                        func.json_contains(ids,func.json_array(cast(a.c.article_id,String)))==1)
        count=func.coalesce(func.json_length(ids),0)
    related=or_(and_(count>0,array_match),and_(count==0,e.c.article_id==a.c.article_id))
    visible=article_statement(store,uid).with_only_columns(a.c.article_id).order_by(None).where(related).correlate(e)
    conditions.append(or_(and_(count==0,e.c.article_id.is_(None)),exists(visible)))
    return and_(*conditions)


def notification_statement(store,uid,kind='ALL',unread=False):
    require(kind in ('ALL','REPORT','MAJOR_ISSUE','SPIKE','PRODUCT_SPIKE','KEYWORD_SPIKE'),'알림 종류를 확인하세요.')
    u,e=store.t('user_alert'),store.t('alert_event')
    stmt=select(e,u.c.user_alert_id,u.c.read_at,u.c.popup_shown_at,u.c.is_interest_match,u.c.created_at.label('received_at')).join(u,u.c.event_id==e.c.event_id).where(u.c.user_id==uid)
    if kind=='SPIKE': stmt=stmt.where(e.c.event_type.in_(['PRODUCT_SPIKE','KEYWORD_SPIKE']))
    elif kind!='ALL': stmt=stmt.where(e.c.event_type==kind)
    if unread: stmt=stmt.where(u.c.read_at.is_(None))
    return stmt.where(event_allowed_sql(store,uid))


def notification_rows(store,uid,kind='ALL',unread=False,page=1,size=20):
    u=store.t('user_alert')
    return store.rows(notification_statement(store,uid,kind,unread).order_by(u.c.created_at.desc(),u.c.user_alert_id.desc()).offset((page-1)*size).limit(size))


def notification_views(store,rows,uid):
    ids={identity for row in rows if row['event_type']!='REPORT' for identity in
         (row['payload_json'].get('article_ids',[]) or ([row['article_id']] if row['article_id'] else []))}
    a=store.t('article')
    articles=article_views(store,store.rows(article_statement(store,uid).where(a.c.article_id.in_(ids))),uid) if ids else []
    by_id={item['article_id']:item for item in articles}
    return [notification_view(store,row,uid,[by_id[str(identity)] for identity in
            (row['payload_json'].get('article_ids',[]) or ([row['article_id']] if row['article_id'] else []))
            if str(identity) in by_id]) for row in rows]


def listing(store,uid,kind='ALL',unread=False,page=1,size=20):
    total=store.count(notification_statement(store,uid,kind,unread))
    rows=notification_rows(store,uid,kind,unread,page,size)
    return {'items':notification_views(store,rows,uid),'page':page_info(page,size,total)}


def unread_count(store,uid):
    return store.count(notification_statement(store,uid,unread=True))


def notification_one(store,uid,identity):
    row=store.one(notification_statement(store,uid).where(store.t('user_alert').c.user_alert_id==identity))
    require(row is not None,'알림을 찾을 수 없습니다.','NOT_FOUND',404)
    return row


def read_one(store,uid,identity):
    row=notification_one(store,uid,identity)
    t=store.t('user_alert'); at=row['read_at'] or now()
    store.c.execute(update(t).where(t.c.user_alert_id==identity,t.c.user_id==uid,t.c.read_at.is_(None)).values(read_at=at))
    return {'read_at':at,'unread_count':unread_count(store,uid)}


def read_all(store,uid,through,kind):
    timestamp=datetime.fromisoformat(through.replace('Z','+00:00'))
    require(timestamp.tzinfo is not None,'through에 시간대를 포함하세요.')
    timestamp=timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    t=store.t('user_alert')
    selected=notification_statement(store,uid,kind,True).with_only_columns(t.c.user_alert_id).where(t.c.created_at<=timestamp).distinct().subquery()
    # MySQL 동일 테이블 UPDATE의 대상 하위 질의는 파생 테이블로 감싼다.
    count=store.c.execute(update(t).where(t.c.user_id==uid,t.c.user_alert_id.in_(select(selected.c.user_alert_id)),t.c.read_at.is_(None)).values(read_at=now())).rowcount
    return {'updated_count':count,'unread_count':unread_count(store,uid)}


def popups(store,uid):
    conf=settings(store,uid)
    kinds=popup_kinds(conf)
    if not kinds:
        return {'groups':[],'next_cursor':None,'retry_after_seconds':30}
    u,e=store.t('user_alert'),store.t('alert_event')
    rows=store.rows(notification_statement(store,uid).where(u.c.popup_shown_at.is_(None),u.c.read_at.is_(None),e.c.event_type.in_(sorted(kinds)))
                    .order_by(u.c.created_at.desc(),u.c.user_alert_id.desc()).limit(100)); grouped={}
    for r in rows:
        flag='report_popup' if r['event_type']=='REPORT' else 'major_issue_popup' if r['event_type']=='MAJOR_ISSUE' else 'spike_popup'
        if r['popup_shown_at'] is None and conf[flag]: grouped.setdefault(r['event_type'],[]).append(r)
    groups=[]
    for kind,rs in grouped.items():
        identity=save_snapshot(store,uid,'DELIVERY',{'ids':[str(r['user_alert_id']) for r in rs]},300)
        groups.append({'delivery_id':identity,'event_type':kind,'notifications':notification_views(store,rs,uid),'created_at':now()})
    return {'groups':groups,'next_cursor':None,'retry_after_seconds':30}


def acknowledge(store,uid,identity):
    p=read_snapshot(store,uid,identity,'DELIVERY'); t=store.t('user_alert')
    store.c.execute(update(t).where(t.c.user_id==uid,t.c.user_alert_id.in_(p['ids']),t.c.popup_shown_at.is_(None)).values(popup_shown_at=now()))


def major_issues(store,uid=None,page=1,size=20):
    e=store.t('alert_event')
    rows=store.rows(select(e).where(e.c.event_type=='MAJOR_ISSUE').order_by(e.c.created_at.desc(),e.c.event_id.desc()))
    if uid: rows=[r for r in rows if event_allowed(store,uid,r)]
    return {'items':[issue_view(store,r,uid) for r in rows[(page-1)*size:page*size]],'page':page_info(page,size,len(rows)),
            'meta':source_meta(store,'KOTRA',rows[0]['created_at'] if rows else None)}
