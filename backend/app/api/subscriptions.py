"""적재 결과를 읽는 리포트 발행 트리거. 원천 데이터 적재/집계 코드는 실행하지 않는다."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from backend.app.api import trade, jobs
from backend.app.api.common import now, digest, period, month, shift_month, ApiError


def ready_days(store, start, end):
    t=store.t('batch_run')
    for target in ('article','news_product_daily','news_keyword_daily'):
        runs=store.rows(select(t).where(t.c.target_table==target,t.c.period_end>=start,t.c.period_start<=end).order_by(t.c.run_id.desc()))
        cursor=start
        while cursor<=end:
            latest=next((r for r in runs if r['period_start']<=cursor<=r['period_end']),None)
            if latest is None or latest['status']!='SUCCESS': return False
            cursor+=timedelta(days=1)
    return True


def enqueue_due(store, at=None):
    at=at or datetime.now(ZoneInfo('Asia/Seoul'))
    subscription, option, profile = store.t('report_subscription'),store.t('service_option'),store.t('api_user_profile')
    rows=store.rows(select(subscription).join(option,option.c.option_code==subscription.c.period_code)
        .join(profile,profile.c.user_id==subscription.c.user_id)
        .where(subscription.c.enabled==True,option.c.enabled==True,profile.c.account_status=='ACTIVE'))
    if not rows: return 0
    count=0
    for row in rows:
        saved=row['filters_json']; code=row['period_code']
        if code not in ('WEEKLY','MONTHLY','QUARTERLY'): continue
        if not saved.get('enabled_since'):
            # 기존 구독을 임의로 과거 발행하지 않는다. API-50 재저장으로 기준을 확정한다.
            continue
        if code=='WEEKLY':
            monday=at.date()-timedelta(days=at.weekday())
            start,end=monday-timedelta(days=7),monday-timedelta(days=1)
            enabled_day=datetime.fromisoformat(saved['enabled_since'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).date()
            if enabled_day>=monday: continue
            if not ready_days(store,start,end): continue
            periods=[({'start':start.isoformat(),'end':end.isoformat()},None)]
        else:
            try: available=trade.bounds(store)
            except ApiError: continue
            previous=saved.get('last_observed_month')
            ends=[m for m in available if m.month in (3,6,9,12)] if code=='QUARTERLY' else available[-1:]
            # 다음 달까지 적재되어도 구독 시작 기준 이후의 미발행 분기를 복구한다.
            if previous: ends=[m for m in ends if m>month(previous)]
            else: ends=ends[-1:]  # 첫 적재 전 구독에는 과거 전체 기간을 발행하지 않는다.
            periods=[]
            for end in ends:
                start=shift_month(end,-2) if code=='QUARTERLY' else end
                try: trade.resolve_period(store,start.strftime('%Y-%m'),end.strftime('%Y-%m'))
                except ApiError: continue
                periods.append((period(start,end),period(shift_month(start,-12),shift_month(end,-12))))
        if not periods: continue
        uid=row['user_id']; users=store.t('users')
        store.c.execute(select(users.c.user_id).where(users.c.user_id==uid).with_for_update()).first()
        current=store.one(select(subscription).where(subscription.c.subscription_id==row['subscription_id']).with_for_update())
        if not current['enabled'] or current['filters_json']!=saved: continue
        report=store.t('report')
        report_type='RPT_'+code
        if not store.c.scalar(select(option.c.option_code).where(option.c.option_code==report_type,option.c.enabled==True)): continue
        for p,compare in periods:
            key=digest(['SUBSCRIPTION',row['subscription_id'],code,p])
            if store.c.scalar(select(report.c.report_id).where(report.c.generation_key==key)): continue
            conditions={'schema_version':1,'hs_items':saved.get('hs_items',[]),'country_codes':saved.get('countries',[]),
                'period':p,'compare_period':compare,'trade_flow':'EXPORT','compare':'YOY_PERIOD','metric':'AMOUNT',
                'sections':['KPI','TOP_COUNTRIES','GROWTH_RISK','LOCAL_ISSUES'], 'formats':saved.get('formats',['PDF'])}
            rid=store.insert('report',user_id=uid,subscription_id=row['subscription_id'],report_type=report_type,
                generation_key=key,conditions_json=conditions,status='QUEUED',retry_count=0,created_at=now())
            jobs.enqueue(store,uid,'API_REPORT',rid,{'report_id':str(rid)},key)
            count+=1
    return count
