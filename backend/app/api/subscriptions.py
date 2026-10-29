"""한국 시간의 정기 발행일과 적재 완료를 확인하는 리포트 발행 트리거."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from backend.app.api import trade, jobs
from backend.app.api.common import now, digest, period, shift_month, ApiError

NEWS_SOURCES = ('KOTRA_MARKET', 'KOTRA_SHORT_NEWS', 'HANKYUNG', 'FNNEWS')
KST = ZoneInfo('Asia/Seoul')


def scheduled_periods(code, enabled_since, at):
    """Only publication dates after signup; missed dates remain eligible for retry.

    Publication starts at 00:00 KST, so signup on that date starts next cycle.
    Data availability must never move a publication date forward or skip a cycle.
    """
    enabled = datetime.fromisoformat(enabled_since.replace('Z', '+00:00'))
    if enabled.tzinfo is None:
        enabled = enabled.replace(tzinfo=ZoneInfo('UTC'))
    enabled_day = enabled.astimezone(KST).date()
    today = at.date()
    if code == 'WEEKLY':
        due = enabled_day + timedelta(days=7-enabled_day.weekday())
        while due <= today:
            yield ({'start': (due-timedelta(days=7)).isoformat(),
                    'end': (due-timedelta(days=1)).isoformat()}, None)
            due += timedelta(days=7)
    else:
        cursor = enabled_day.replace(day=1)
        while cursor <= today:
            due = cursor.replace(day=15 if code == 'MONTHLY' else 20)
            if enabled_day < due <= today and (code == 'MONTHLY' or cursor.month in (1,4,7,10)):
                end = shift_month(cursor, -1)
                start = shift_month(end, -2) if code == 'QUARTERLY' else end
                yield period(start,end), period(shift_month(start,-12),shift_month(end,-12))
            cursor = shift_month(cursor, 1)


def ready_days(store, start, end):
    # Collectors atomically load articles/tags/daily aggregates, then record a single
    # target_table='article' batch. Requiring separate daily-table runs blocks forever.
    # Check each enabled news provider independently: one provider's success must not
    # hide another provider's missing/failed/running load for the same dates.
    t,options=store.t('batch_run'),store.t('service_option')
    sources=list(store.c.execute(select(options.c.option_code).where(
        options.c.option_code.in_(NEWS_SOURCES),options.c.enabled==True,
        options.c.option_type.in_(('SOURCE','ARTICLE_SOURCE')))).scalars())
    if not sources: return False
    for source in sources:
        runs=store.rows(select(t.c.run_id,t.c.period_start,t.c.period_end,t.c.status).where(
            t.c.target_table=='article',t.c.source_code==source,
            t.c.period_end>=start,t.c.period_start<=end).order_by(t.c.run_id.desc()))
        cursor=start
        while cursor<=end:
            latest=next((r for r in runs if r['period_start']<=cursor<=r['period_end']),None)
            if latest is None or latest['status']!='SUCCESS': return False
            cursor+=timedelta(days=1)
    return True


def enqueue_due(store, at=None, *, user_id=None):
    # 한 Store로 여러 스케줄 회차를 호출해도 회차 사이 적재 완료를 다시 확인한다.
    store.cache.clear()
    at=at or datetime.now(KST)
    at=at.replace(tzinfo=KST) if at.tzinfo is None else at.astimezone(KST)
    subscription, option, profile = store.t('report_subscription'),store.t('service_option'),store.t('users')
    stmt=(select(subscription).join(option,option.c.option_code==subscription.c.period_code)
        .join(profile,profile.c.user_id==subscription.c.user_id)
        .where(subscription.c.enabled==True,option.c.option_type=='REPORT_PERIOD',
               option.c.enabled==True,profile.c.account_status=='ACTIVE'))
    if user_id is not None: stmt=stmt.where(subscription.c.user_id==user_id)
    rows=store.rows(stmt)
    if not rows: return 0
    count=0
    for row in rows:
        saved=row['filters_json']; code=row['period_code']
        if code not in ('WEEKLY','MONTHLY','QUARTERLY'): continue
        if not saved.get('enabled_since'):
            # 기존 구독을 임의로 과거 발행하지 않는다. API-50 재저장으로 기준을 확정한다.
            continue
        report=store.t('report')
        existing=set(store.c.execute(select(report.c.generation_key).where(
            report.c.subscription_id==row['subscription_id'])).scalars())
        periods=[]
        for p,compare in scheduled_periods(code,saved['enabled_since'],at):
            if digest(['SUBSCRIPTION',row['subscription_id'],code,p]) in existing: continue
            if code=='WEEKLY':
                if not ready_days(store,datetime.fromisoformat(p['start']).date(),datetime.fromisoformat(p['end']).date()): continue
            else:
                try: trade.resolve_period(store,p['start'][:7],p['end'][:7])
                except ApiError: continue
            periods.append((p,compare))
        if not periods: continue
        uid=row['user_id']; users=store.t('users')
        store.c.execute(select(users.c.user_id).where(users.c.user_id==uid).with_for_update()).first()
        current=store.one(select(subscription).where(subscription.c.subscription_id==row['subscription_id']).with_for_update())
        if not current['enabled'] or current['filters_json']!=saved: continue
        report=store.t('report')
        report_type='RPT_'+code
        if not store.c.scalar(select(option.c.option_code).where(option.c.option_code==report_type,
            option.c.option_type=='REPORT_TYPE',option.c.enabled==True)): continue
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
