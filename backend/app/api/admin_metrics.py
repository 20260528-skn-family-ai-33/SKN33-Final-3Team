"""월별 관리자 지표: 기존 회원·현재 활성 구독의 시작일 기준 누적."""
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, case
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


def registration_totals(store, months):
    users = store.t('users')
    columns = [func.count(case((users.c.created_at.is_(None), 1))).label('unknown')]
    for index, month in enumerate(months):
        boundary = datetime.combine(shift_month(month, 1), time.min, tzinfo=KST)
        cutoff = boundary.astimezone(timezone.utc).replace(tzinfo=None)
        columns.append(func.count(case((users.c.created_at < cutoff, 1))).label(f'm{index}'))
    counts = store.one(select(*columns).select_from(users))
    # Deleted users cannot be reconstructed from current rows.
    return {month: None if counts['unknown'] else counts[f'm{index}'] for index, month in enumerate(months)}


def subscription_start_totals(store, months):
    subscriptions = store.t('report_subscription')
    start = subscriptions.c.filters_json['enabled_since'].as_string()
    groups = store.rows(select(start.label('start'), func.count().label('count'))
        .where(subscriptions.c.enabled == True).group_by(start))
    starts = []
    for group in groups:
        try:
            value = datetime.fromisoformat(group['start'].replace('Z', '+00:00'))
            if value.tzinfo is None: raise ValueError('Missing time zone')
        except (AttributeError, TypeError, ValueError):
            return {month: None for month in months}
        starts.append((value.astimezone(timezone.utc), group['count']))
    # This is the current active cohort, not the full historical subscription population.
    return {month: sum(count for start, count in starts
        if start < datetime.combine(shift_month(month, 1), time.min, tzinfo=KST).astimezone(timezone.utc))
        for month in months}


def monthly_history(store, current, at=None):
    this_month = utc_time(at).astimezone(KST).date().replace(day=1)
    months = [shift_month(this_month, offset) for offset in range(-11, 1)]
    registrations = registration_totals(store, months[:-1])
    subscriptions = subscription_start_totals(store, months[:-1])
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
