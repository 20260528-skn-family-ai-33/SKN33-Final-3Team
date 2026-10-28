"""판정 담당자가 확정한 alert_event를 회원 알림함에 전달하는 연결 함수."""
from sqlalchemy import select
from backend.app.api.common import require, now
from backend.app.api import notifications, accounts, news


def deliver_event(store, event_id):
    events, alerts = store.t('alert_event'), store.t('user_alert')
    event=store.one(select(events).where(events.c.event_id==event_id).with_for_update())
    require(event is not None,'알림 사건이 없습니다.','NOT_FOUND',404)
    p=store.t('api_user_profile'); users=store.rows(select(p.c.user_id).where(p.c.account_status=='ACTIVE'))
    if event['event_type']=='REPORT':
        r=store.t('report'); report=store.one(select(r).where(r.c.report_id==event['report_id'],r.c.status=='READY'))
        if report is None or not report['subscription_id']: return 0
        users=[u for u in users if u['user_id']==report['user_id']]
    count=0
    for user in users:
        uid=user['user_id']; conf=notifications.settings(store,uid)
        if not conf['notifications_enabled'] or not conf['consent_current']: continue
        if not notifications.event_allowed(store,uid,event): continue
        matched=False
        if event['event_type'] in ('PRODUCT_SPIKE','KEYWORD_SPIKE'):
            scope=accounts.interests(store,uid)
            if not scope['hs_items'] and not scope['country_codes']: continue
            ids=event['payload_json'].get('article_ids',[]) or ([event['article_id']] if event['article_id'] else [])
            if not ids: continue
            a=store.t('article')
            matching=store.c.execute(news.article_statement(store,uid,scope='INTERESTED').where(a.c.article_id.in_(ids)).limit(1)).first()
            if matching is None: continue
            matched=True
        if store.c.scalar(select(alerts.c.user_alert_id).where(alerts.c.user_id==uid,alerts.c.event_id==event_id)): continue
        store.insert('user_alert',user_id=uid,event_id=event_id,is_interest_match=matched,created_at=now())
        count+=1
    return count
