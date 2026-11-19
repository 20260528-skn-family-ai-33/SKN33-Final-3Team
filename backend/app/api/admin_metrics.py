"""월별 관리자 지표: 기존 회원·현재 활성 구독의 시작일 기준 누적."""
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, cast, String
from backend.app.api.common import now, shift_month

KST = ZoneInfo('Asia/Seoul')


def current_counts(store):
    users, subscriptions = store.t('users'), store.t('report_subscription')
    return store.one(select(
        select(func.count()).select_from(users).scalar_subquery().label('total_members'),
        select(func.count()).select_from(subscriptions).where(subscriptions.c.enabled == True).scalar_subquery().label('active_subscriptions'),
    ))


def utc_time(at=None):
    value = at or now()
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def dated_totals(groups, months, naive_utc=False):
    starts, unknown = [], 0
    for group in groups:
        try:
            value = datetime.fromisoformat(group['start'].replace('Z', '+00:00'))
            if value.tzinfo is None:
                if not naive_utc: raise ValueError('Missing time zone')
                value = value.replace(tzinfo=timezone.utc)
            starts.append((value.astimezone(timezone.utc), group['count']))
        except (AttributeError, TypeError, ValueError, OverflowError):
            unknown += group['count']
    totals = {month: sum(count for start, count in starts
        if start < datetime.combine(shift_month(month, 1), time.min, tzinfo=KST).astimezone(timezone.utc))
        for month in months}
    return totals, unknown


def registration_totals(store, months):
    users = store.t('users')
    # 잘못된 기존 날짜도 ORM DateTime 변환 오류 없이 제외한다. 정상 naive 값은 기존 UTC 규칙을 따른다.
    start = cast(users.c.created_at, String)
    groups = store.rows(select(start.label('start'), func.count().label('count')).group_by(start))
    return dated_totals(groups, months, naive_utc=True)


def subscription_start_totals(store, months):
    subscriptions = store.t('report_subscription')
    start = subscriptions.c.filters_json['enabled_since'].as_string()
    groups = store.rows(select(start.label('start'), func.count().label('count'))
        .where(subscriptions.c.enabled == True).group_by(start))
    # 현재 활성 구독의 시작일 누적이며, 해지·재구독 이전 이력은 복원하지 않는다.
    return dated_totals(groups, months)


def monthly_history(store, current, at=None, excluded=None):
    this_month = utc_time(at).astimezone(KST).date().replace(day=1)
    months = [shift_month(this_month, offset) for offset in range(-11, 1)]
    registrations, unknown_members = registration_totals(store, months[:-1])
    subscriptions, unknown_subscriptions = subscription_start_totals(store, months[:-1])
    if excluded is not None:
        excluded.update(total_members=unknown_members, active_subscriptions=unknown_subscriptions)
    result = []
    for month in months:
        is_current = month == this_month
        members = current['total_members'] if is_current else registrations[month]
        active = current['active_subscriptions'] if is_current else subscriptions[month]
        result.append({'month': month.strftime('%Y-%m'), 'is_current': is_current,
            'total_members': members, 'active_subscriptions': active,
            'subscription_source': 'CURRENT' if is_current else 'START_DATE' if active is not None else None,
            'member_source': 'CURRENT' if is_current else 'REGISTRATION' if members is not None else None})
    return result
