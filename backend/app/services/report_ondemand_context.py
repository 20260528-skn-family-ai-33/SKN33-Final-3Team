"""Requested PDF metrics from the saved scope; never use template sample data."""
from collections import defaultdict

from backend.app.api import trade
from backend.app.api.common import percentage, month_end
from backend.app.services.report_context import months


METRICS = {'AMOUNT': '수출액', 'GROWTH': '증감률', 'SHARE': '비중', 'RANK': '순위'}
BASES = {'NONE': '비교 없음', 'YOY': '전년 같은 달', 'YOY_PERIOD': '전년 같은 기간',
         'MOM': '전월', 'CUSTOM': '사용자 지정 기간'}


def period_view(start, end):
    label = f'{start:%Y.%m} ~ {end:%Y.%m}'
    full_year = start.year == end.year and start.month == 1 and end.month == 12
    short = str(start.year) if full_year else f'{start:%Y.%m}' if start == end else f'{start:%y.%m}~{end:%y.%m}'
    return {'start': start.isoformat(), 'end': month_end(end).isoformat(), 'label': label,
            'months': len(list(months(start, end))),
            'title_label': f'{start.year}년 연간' if full_year else label,
            'short': short}


def populate(store, saved, ctx, names, start, end):
    from datetime import date
    start, end = start.replace(day=1), end.replace(day=1)
    window = list(months(start, end))
    available = set(trade.bounds(store))
    mode = saved.get('compare', 'YOY_PERIOD')
    comparison = saved.get('compare_period') if mode != 'NONE' else None
    previous_window = []
    if comparison:
        first, last = (date.fromisoformat(comparison[k]).replace(day=1) for k in ('start', 'end'))
        previous_window = list(months(first, last))
    ready = bool(previous_window) and all(m in available for m in previous_window)
    paired = ready and mode in ('YOY', 'YOY_PERIOD', 'MOM') and len(window) == len(previous_window)
    sections = saved.get('sections', ['KPI', 'TOP_COUNTRIES', 'GROWTH_RISK', 'LOCAL_ISSUES'])
    c = ctx['conditions']
    c.update(period=period_view(start, end), sections=sections,
             request_channel='CHAT' if saved.get('session_id') else 'FORM',
             metric={'code': saved.get('metric', 'AMOUNT'), 'label': METRICS[saved.get('metric', 'AMOUNT')]},
             compare={'mode': mode, 'status': 'READY' if ready else 'NONE' if mode == 'NONE' else 'NOT_READY',
                      'period_label': period_view(previous_window[0], previous_window[-1])['label'] if previous_window else '',
                      'short': period_view(previous_window[0], previous_window[-1])['short'] if previous_window else '',
                      'basis_label': BASES[mode], 'monthly_paired': paired})

    # OR-based HS scope handles overlapping HS4/HS6 selections without double counting.
    # One monthly/country query supplies both periods and all export metrics.
    first = min(window + previous_window) if ready else start
    rows = trade.aggregate(store, 'export_summary', first, end, saved.get('hs_items', []),
                           saved.get('country_codes', []), 'MONTH_COUNTRY')
    values = {(r['trade_month'], r['country_code']): r['amount_usd'] for r in rows}
    codes = sorted({code for _, code in values})

    def amount(period, selected=None):
        return sum(values.get((m, code), 0) for m in period for code in (codes if selected is None else selected))

    current = {code: amount(window, [code]) for code in codes}
    previous = {code: amount(previous_window, [code]) for code in codes} if ready else {}
    active = sorted((code for code in codes if current[code] > 0), key=lambda code: (-current[code], code))
    old_active = sorted((code for code in codes if previous.get(code, 0) > 0), key=lambda code: (-previous[code], code))
    old_ranks = {code: i for i, code in enumerate(old_active, 1)}
    total = amount(window)
    old_total = amount(previous_window) if ready else None
    monthly = [amount([m]) for m in window]
    top_share = lambda n: sum(current[code] for code in active[:n]) * 100 / total if total else None
    old_share = lambda n: sum(previous[code] for code in old_active[:n]) * 100 / old_total if old_total else None
    imported = old_imported = None
    if 'KPI' in sections:
        imports = trade.aggregate(store, 'import_summary', first, end, saved.get('hs_items', []),
                                  saved.get('country_codes', []), 'MONTH')
        import_values = {r['trade_month']: r['amount_usd'] for r in imports}
        imported = sum(import_values.get(m, 0) for m in window)
        old_imported = sum(import_values.get(m, 0) for m in previous_window) if ready else None
    peak = max(range(len(window)), key=lambda i: monthly[i]) if total else None
    kpi = {'amount_usd': total, 'prev_amount_usd': old_total,
           'change_usd': total-old_total if ready else None, 'change_pct': percentage(total, old_total),
           'country_count': len(active), 'prev_country_count': len(old_active) if ready else None,
           'country_count_diff': len(active)-len(old_active) if ready else None,
           'top1_name': names.get(active[0], active[0]) if active else None, 'top1_share_pct': top_share(1),
           'prev_top1_name': names.get(old_active[0], old_active[0]) if old_active else None,
           'prev_top1_share_pct': old_share(1), 'top3_share_pct': top_share(3), 'prev_top3_share_pct': old_share(3),
           'import_usd': imported, 'import_change_pct': percentage(imported, old_imported) if imported is not None else None,
           'trade_balance_usd': total-imported if imported is not None else None,
           'monthly_avg_usd': total/len(window), 'peak_month_label': f'{window[peak]:%Y.%m}' if peak is not None else None,
           'peak_month_usd': monthly[peak] if peak is not None else None}
    ctx['summary'] = {'kpi': kpi, 'sentences': [], 'check_points': []}
    if 'KPI' in sections:
        prior_values = [amount([m]) for m in previous_window] if paired else [None] * len(window)
        ctx['monthly'] = {'labels': [f'{m:%y.%m}' for m in window], 'current': monthly, 'previous': prior_values,
                          'rows': [{'label': f'{m:%Y.%m}', 'amount_usd': value, 'prev_amount_usd': old,
                                    'change_usd': value-old if old is not None else None, 'change_pct': percentage(value, old)}
                                   for m, value, old in zip(window, monthly, prior_values)],
                          'quarters': [], 'notes': []}
        groups = defaultdict(list)
        for i, m in enumerate(window): groups[(m.year, (m.month-1)//3+1)].append(i)
        for (year, quarter), indices in groups.items():
            # Do not call a partial quarter a quarterly total; MOM pairs months, not quarters.
            if len(indices) != 3 or mode == 'MOM': continue
            value = sum(monthly[i] for i in indices)
            old = sum(prior_values[i] for i in indices) if paired else None
            ctx['monthly']['quarters'].append({'label': f'{year}년 {quarter}분기', 'amount_usd': value,
                                               'prev_amount_usd': old, 'change_pct': percentage(value, old)})
    if 'TOP_COUNTRIES' in sections:
        def metrics(selected):
            value = amount(window, selected)
            old = amount(previous_window, selected) if ready else None
            return {'amount_usd': value, 'prev_amount_usd': old,
                    'share_pct': value*100/total if total else None, 'change_pct': percentage(value, old),
                    'is_new': ready and old == 0 and value > 0}
        ctx['top_countries'] = [{'rank': i, 'name': names.get(code, code), **metrics([code]),
                                 'rank_move': old_ranks[code]-i if code in old_ranks else 'NEW' if ready else None}
                                for i, code in enumerate(active[:10], 1)]
        others = [code for code in codes if code not in active[:10]]
        ctx['others'] = metrics(others) if others else None
        ctx['concentration'] = {**{f'top{n}': {'current': top_share(n), 'previous': old_share(n)} for n in (1, 3, 10)},
                                'countries': {'current': len(active), 'previous': len(old_active) if ready else None},
                                'note': None}
    scope = '선택 국가 내 집계' if saved.get('country_codes') else '전체 국가 집계'
    ctx['meta']['data_basis'] = f'관세청 수출입무역통계 · HS2022 · {scope}'
    ctx['sources'] = [{'label': '수출입 통계', 'provider': '관세청', 'scope': f'{scope} · 검증 완료 월',
                       'used_in': '핵심 요약·선택한 통계 섹션'}]
    if 'LOCAL_ISSUES' in sections:
        ctx['sources'].append({'label': '기사', 'provider': ', '.join(dict.fromkeys(a['source_label'] for a in ctx['articles'])) or '관련 기사 없음',
                               'scope': ctx['article_policy_note'], 'used_in': '현지 이슈'})
