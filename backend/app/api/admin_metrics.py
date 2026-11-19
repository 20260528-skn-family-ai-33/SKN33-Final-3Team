"""Monthly admin totals. Never backfill unknown month ends from today's state."""
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, case
from sqlalchemy.exc import IntegrityError
from backend.app.api.common import ApiError, now, shift_month

KST = ZoneInfo('Asia/Seoul')
TABLE = 'api_admin_monthly_snapshot'


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
    # Stored month-end snapshots take priority; deleted users cannot be reconstructed from current rows.
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
    try:
        t = store.t(TABLE)
    except ApiError as exc:
        if exc.code != 'FEATURE_NOT_READY': raise
        snapshots = []
    else:
        snapshots = store.rows(select(t).where(t.c.month >= months[0], t.c.month < this_month).order_by(t.c.month))
    by_month = {row['month']: row for row in snapshots}
    registrations = registration_totals(store, months[:-1])
    subscriptions = subscription_start_totals(store, months[:-1])
    result = []
    for month in months:
        is_current = month == this_month
        recorded = by_month.get(month)
        source = current if is_current else recorded or {}
        members = source.get('total_members') if is_current or recorded is not None else registrations[month]
        active = source.get('active_subscriptions') if is_current or recorded is not None else subscriptions[month]
        result.append({'month': month.strftime('%Y-%m'), 'is_current': is_current,
            'total_members': members, 'active_subscriptions': active,
            'subscription_source': 'CURRENT' if is_current else 'SNAPSHOT' if recorded is not None else 'START_DATE' if active is not None else None,
            'member_source': 'CURRENT' if is_current else 'SNAPSHOT' if recorded is not None else 'REGISTRATION' if members is not None else None})
    return result



def capture_month_end(store, at=None):
    observed = utc_time(at)
    local = observed.astimezone(KST)
    # Month-boundary observation only. A restart later in the month cannot recreate history.
    if local.day != 1 or local.hour != 0 or local.minute != 0:
        return False
    month = shift_month(local.date().replace(day=1), -1)
    t = store.t(TABLE)
    if store.one(select(t.c.month).where(t.c.month == month)):
        return False
    counts = current_counts(store)
    try:
        with store.c.begin_nested():
            store.c.execute(t.insert().values(month=month, **counts, observed_at=observed.replace(tzinfo=None)))
    except IntegrityError:
        # A second worker may have saved this month while this worker was reading counts.
        if not store.one(select(t.c.month).where(t.c.month == month)): raise
        return False
    return True
