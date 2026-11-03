"""SQL templates return unsaved rows; ResultStore owns the only write boundary."""
from dataclasses import dataclass
from collections import defaultdict
from decimal import Decimal
from itertools import product
from time import monotonic

from sqlalchemy import select, func
from backend.app.agents.digest import bounded, MAX_DIGEST_ROWS
from backend.app.agents.state import ToolResult
from backend.app.api import trade
from backend.app.api.common import period, shift_month, percentage


@dataclass
class Analysis:
    rows: list
    meta: dict
    result: ToolResult
    conditions: dict
    sql: list
    columns: list | None = None


def api_conditions(conditions):
    output = conditions.model_dump(mode='json', exclude={'article_period', 'ranking_group', 'share_basis'})
    for key in ('period', 'compare_period'):
        value = getattr(conditions, key)
        output[key] = period(value.start, value.end) if value else None
    compare=conditions.compare_period
    p=conditions.period
    output['compare']=('NONE' if not compare else 'YOY_PERIOD' if p and compare.start==shift_month(p.start,-12)
                       and compare.end==shift_month(p.end,-12) else 'CUSTOM')
    output['metric']='AMOUNT'
    return output


def months(window):
    if not window: return []
    result, cursor = [], window.start
    while cursor <= window.end:
        result.append(cursor)
        cursor = shift_month(cursor, 1)
    return result


def _names(store):
    countries = {r['country_code']: r['name_ko'] for r in store.rows(select(store.t('country')))}
    products = {(r['hs_code'], r['hs_ver']): r['name_ko'] for r in store.rows(select(store.t('hs_code')))}
    return countries, products


def _base(kind, conditions, meta, rows_total):
    return {'kind': kind, 'period': {'start': conditions.period.start.strftime('%Y-%m'),
        'end': conditions.period.end.strftime('%Y-%m')}, 'trade_flow': conditions.trade_flow,
        'unit': 'USD', 'source': '관세청', 'latest_available_month': meta['latest_available_month'],
        'rows_total': rows_total, 'truncated': False}


def run_template(store, uid, task, step, timeout=30):
    conditions = task.conditions
    if not conditions or not conditions.period:
        raise ValueError('SQL requires confirmed trade conditions')
    started = monotonic()
    def guard():
        if monotonic() - started >= timeout: raise TimeoutError('SQL template time budget exhausted')
    common = dict(start=conditions.period.start.strftime('%Y-%m'), end=conditions.period.end.strftime('%Y-%m'),
        hs=[p.model_dump() for p in conditions.hs_items], countries=conditions.country_codes,
        flow=conditions.trade_flow)
    template = step.args['template']
    simple_country_rank = (template == 'RANKING' and step.args.get('amount_only')
                           and conditions.ranking_group in ('COUNTRY', 'HS6'))
    if template in ('TREND', 'RANKING') and not simple_country_rank:
        country_names, product_names = _names(store)
    public = api_conditions(conditions)
    limitations = []
    if template == 'TREND':
        source = 'export_summary' if conditions.trade_flow == 'EXPORT' else 'import_summary'
        field = 'export_amount_usd' if conditions.trade_flow == 'EXPORT' else 'import_amount_usd'
        verified = set(trade.verified_months(store, source))
        requested, compared = months(conditions.period), months(conditions.compare_period)
        multiple=len(conditions.hs_items or [None])*len(conditions.country_codes or [None])>1
        def collect(window):
            values=defaultdict(lambda:defaultdict(Decimal))
            if not window: return values
            group=('MONTH_PAIR' if conditions.hs_items and conditions.country_codes else
                   'MONTH_HS' if conditions.hs_items else 'MONTH_COUNTRY')
            records=trade.aggregate(store,source,window[0],window[-1],common['hs'],common['countries'],group)
            for row in records:
                guard()
                hs_codes=[p.hs_code for p in conditions.hs_items if p.hs_ver==row.get('hs_ver')
                          and row.get('hs6_code','').startswith(p.hs_code)] if conditions.hs_items else [None]
                for code in hs_codes:
                    values[code,row.get('country_code')][row['trade_month']]+=row['amount_usd']
            return values
        grouped,comparison=(collect(requested),collect(compared)) if multiple else ({},{})
        targets = []
        for hs, country in product(conditions.hs_items or [None], conditions.country_codes or [None]):
            guard()
            scoped = {**common, 'hs': [hs.model_dump()] if hs else [], 'countries': [country] if country else []}
            if multiple:
                target=(hs.hs_code if hs else None,country)
                amounts={m.strftime('%Y-%m'):v for m,v in grouped.get(target,{}).items()}
                previous=comparison.get(target,{})
            else:
                current = trade.trends(store, uid, **scoped, save=False)
                amounts = {r['month']: r[field] for r in current['items']}
                previous = {}
            if compared and not multiple:
                guard()
                previous = {r['trade_month']: r['amount_usd'] for r in trade.aggregate(store, source,
                    conditions.compare_period.start, conditions.compare_period.end,
                    scoped['hs'], scoped['countries'], 'MONTH')}
            values = [amounts.get(m.strftime('%Y-%m'), Decimal(0)) if m in verified else None for m in requested]
            old = [previous.get(m, Decimal(0)) if m in verified else None for m in compared]
            def stats(data):
                observed = [v for v in data if v is not None]
                subtotal = sum(observed, Decimal(0))
                total = subtotal if data and len(observed) == len(data) else None
                return total, subtotal / len(observed) if observed else None, len(observed), subtotal
            total, avg, count, subtotal = stats(values)
            compare_total, compare_avg, compare_count, compare_subtotal = stats(old)
            detail = {'hs_code': hs.hs_code if hs else None, 'hs_ver': hs.hs_ver if hs else None,
                'name': product_names.get((hs.hs_code, hs.hs_ver), hs.hs_code) if hs else '전체 품목',
                'country_code': country, 'country_name': country_names.get(country, country) if country else '전체 국가',
                'total_usd': total, 'compare_total_usd': compare_total,
                'change_pct': percentage(total, compare_total), 'verified_months': count,
                'requested_months': len(values), 'monthly_avg_usd': avg, 'compare_monthly_avg_usd': compare_avg,
                'compare_verified_months': compare_count, 'compare_requested_months': len(old),
                'verified_subtotal_usd': subtotal, 'compare_verified_subtotal_usd': compare_subtotal,
                'monthly': [{'month': m.strftime('%Y-%m'), 'amount_usd': value,
                    'compare_amount_usd': old[i] if len(old) == len(values) else None}
                    for i, (m, value) in enumerate(zip(requested, values))]}
            targets.append(detail)
            if count != len(values) or (old and compare_count != len(old)):
                limitations.append('기간 내 미집계 월이 있습니다.')
        targets.sort(key=lambda t: -(t['total_usd'] if t['total_usd'] is not None else t['verified_subtotal_usd']))
        kept = targets[:1000 // len(requested)]
        rows = [{**{k: v for k, v in target.items() if k != 'monthly'}, **monthly}
                for target in kept for monthly in target['monthly']]
        meta = trade.metadata(store, conditions.period.start, conditions.period.end)
        meta.update(kind='TREND', targets=[{k: v for k, v in t.items() if k != 'monthly'} for t in kept],
                    truncated_targets=len(targets)-len(kept), compare_period=public['compare_period'])
        if len(kept) < len(targets): limitations.append(f'표에는 금액 상위 {len(kept)}개 대상만 담았습니다.')
        selected = kept[:10]
        digest = {**_base('TREND', conditions, meta, len(rows)), 'compare_period': public['compare_period'],
                  'targets': [{**t, 'monthly': t['monthly'][-(MAX_DIGEST_ROWS // len(selected)):]}
                              for t in selected],
                  'truncated': len(selected) < len(targets) or len(requested)*len(selected) > MAX_DIGEST_ROWS}
    elif simple_country_rank:
        flow=conditions.trade_flow
        source='export_summary' if flow=='EXPORT' else 'import_summary'
        table,country=store.t(source),store.t('country')
        hs_table=store.t('hs_code')  # Resolve reflected composite FK for SQL compilation.
        amount_key='export_amount_usd' if flow=='EXPORT' else 'import_amount_usd'
        amount=func.sum(table.c.amount_usd).label(amount_key)
        group=conditions.ranking_group
        dimension=country if group=='COUNTRY' else hs_table
        code=country.c.country_code if group=='COUNTRY' else dimension.c.hs_code
        joined=table.join(country,table.c.country_code==country.c.country_code)
        if group=='HS6':
            joined=joined.join(dimension,(table.c.hs6_code==dimension.c.hs_code)&(table.c.hs_ver==dimension.c.hs_ver))
        stmt=(select(code,dimension.c.name_ko.label('name'),amount)
              .select_from(joined)
              .where(country.c.country_role=='COUNTRY',table.c.hs_ver=='HS2022',
                     table.c.trade_month.between(conditions.period.start,conditions.period.end)))
        if common['hs']: stmt=stmt.where(trade.hs_filter(table,common['hs']))
        if common['countries']: stmt=stmt.where(table.c.country_code.in_(common['countries']))
        stmt=(stmt.group_by(code,dimension.c.name_ko)
              .order_by(amount.desc(),code).limit(step.args['top']))
        records=trade.query_rows(store,stmt,source)
        rows=[{**r,'rank':i} for i,r in enumerate(records,1)]
        meta=trade.metadata(store,conditions.period.start,conditions.period.end)
        meta.update(kind='RANKING',group=group,requested_top=step.args['top'])
        digest={**_base('RANKING',conditions,meta,len(rows)),'group':group,
                'top':[{'rank':r['rank'],'code':r['country_code' if group=='COUNTRY' else 'hs_code'],'name':r['name'],'amount_usd':r[amount_key]}
                       for r in rows[:10]],'truncated':len(rows)>10}
    elif template == 'RANKING':
        group = conditions.ranking_group
        data = trade.rankings(store, uid, **common, group=group, page=1, size=1000, save=False)
        rows, meta = data['items'], data['meta']
        # Ranking-screen flags and its duplicate name column are not chat data.
        rows=[{k:v for k,v in row.items() if k not in ('has_data','hs_name')} for row in rows]
        field = 'export_amount_usd' if conditions.trade_flow == 'EXPORT' else 'import_amount_usd'
        def ranked(row):
            hs = row['hs_item']
            code = row['country_code'] if group == 'COUNTRY' else hs['hs_code']
            name = country_names.get(code, code) if group == 'COUNTRY' else product_names.get((code, hs['hs_ver']), code)
            return {'rank': row['rank'], 'code': code, 'name': name, 'amount_usd': row[field],
                    'share_pct': row['share_pct'], 'yoy_pct': row['yoy_pct'], 'mom_pct': row['mom_pct']}
        rows = [{**r, 'name': ranked(r)['name']} for r in rows[:step.args.get('top',10)]]
        if meta['truncated']: limitations.append('표에는 금액 상위 1000개 항목만 담았습니다.')
        total = sum(r['amount_usd'] for r in trade.aggregate(store,
            'export_summary' if conditions.trade_flow == 'EXPORT' else 'import_summary',
            conditions.period.start, conditions.period.end, common['hs'], common['countries']))
        digest = {**_base('RANKING', conditions, meta, len(rows)), 'group': group, 'total_usd': total,
                  'top': [ranked(r) for r in rows[:10]], 'truncated': meta['rows_total'] > 10}
        meta.update(kind='RANKING', group=group)
    elif template == 'SHARE':
        if conditions.share_basis != 'KOREA_TRADE_PARTNER':
            raise ValueError('Share denominator must be confirmed before SQL')
        data = trade.market_shares(store, uid, public, save=False)
        rows, meta = data['items'], data['meta']
        digest = {**_base('SHARE', conditions, meta, len(rows)), 'share_basis': conditions.share_basis,
            'denominator_usd': data['denominator_usd'], 'items': [
                {'code': r['country_code'], 'name': r['label'], 'amount_usd': r['amount_usd'],
                 'share_pct': r['share_pct']} for r in rows]}
        meta.update(kind='SHARE', share_basis=conditions.share_basis, denominator_usd=data['denominator_usd'])
    else:
        raise ValueError('Unsupported template')
    guard()
    limitations = list(dict.fromkeys(limitations))
    result = ToolResult(status='OK' if rows else 'NO_DATA', period=conditions.period, source='관세청',
        digest=bounded(digest), completeness='PARTIAL' if limitations else 'COMPLETE', limitations=limitations)
    return Analysis(rows, meta, result, public, list(store.queries))
