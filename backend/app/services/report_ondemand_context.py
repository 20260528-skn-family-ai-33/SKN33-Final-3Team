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


KINDS = {'TREND': '월별 추이', 'RANKING': '순위', 'SHARE': '비중', 'PREDICTION': '유망시장 예측', 'LLM_SQL': '사용자 지정 조회'}
SECTION_LABELS = {'KPI': '주요 지표·월별 추이', 'TOP_COUNTRIES': '주요 수출국', 'GROWTH_RISK': '수출 증가·감소 시장',
                  'LOCAL_ISSUES': '현지 이슈'}
RESULT_ROW_LIMIT = 200   # PDF에 싣는 결과표 행 수. 전체 행은 함께 만드는 XLSX에 있다.
CHART_SERIES_LIMIT = 5   # 채팅 차트와 같은 시리즈 상한(D-59)
CHART_LABEL_LIMIT = 60


def _cell(value, column):
    """결과표 값을 표시용 글자로. 값은 바꾸지 않고(반올림 표시만) 없으면 —. 일회성 판정·상태 코드는 DOCX와 같은 말로.
    비율과 소수가 있는 금액은 리포트 공통 소수 2자리(report_templates.REPORT_DIGITS). 정수 금액(USD 원값)·개수는 정수로 둔다."""
    from backend.app.api.files import readable
    from backend.app.services.report_templates import REPORT_DIGITS
    value = readable(value, column.get('key'))
    if value is None or value == '':
        return '—'
    if column.get('type') == 'number' and isinstance(value, (int, float)) and not isinstance(value, bool):
        if column.get('unit') == '%':
            return f'{value:.{REPORT_DIGITS}f}%'
        return f'{value:,.0f}' if float(value).is_integer() else f'{value:,.{REPORT_DIGITS}f}'
    return str(value)


SERIES_KEYS = ('country_name', 'name')  # 추이 결과의 국가·품목 표시 열. 웹 ReportView.trendChart와 같은 기준


def _trend_chart(table):
    """월별 추이 결과의 꺾은선 그래프. 기준월 열과 월별 금액(amount_usd) 열이 있을 때만, 국가·품목 열로 시리즈를 나눈다.
    total_usd 같은 기간 합계 열이나 compare_month 같은 비교 열은 쓰지 않는다(월마다 같은 값·시리즈 분할 오류)."""
    keys = [c['key'] for c in table['columns']]
    if table.get('kind') != 'TREND' or 'month' not in keys or 'amount_usd' not in keys:
        return None
    amount = 'amount_usd'
    names = [k for k in SERIES_KEYS if k in keys]
    labels = sorted({r['month'] for r in table['rows'] if r.get('month')})
    if not labels or len(labels) > CHART_LABEL_LIMIT:
        return None
    series = {}
    for r in table['rows']:
        name = ' · '.join(str(r.get(k)) for k in names if r.get(k)) or '수출액'
        series.setdefault(name, {})[r['month']] = r.get(amount)
    if len(series) > CHART_SERIES_LIMIT:
        return None
    return {'labels': labels, 'series': [{'name': name, 'values': [values.get(m) for m in labels]} for name, values in series.items()]}


def populate_results(saved, ctx, start, end, content):
    """승인한 채팅 분석 결과로 만드는 요청형 리포트(D-90). 저장된 결과표 사본만 쓰고 통계를 다시 조회하지 않는다.
    결과표에 없는 요청형 섹션은 만들지 않고 '해당 분석 결과에 포함되지 않음'으로 알린다."""
    from datetime import date
    start, end = start.replace(day=1), end.replace(day=1)
    sections = saved.get('sections', ['KPI', 'TOP_COUNTRIES', 'GROWTH_RISK', 'LOCAL_ISSUES'])
    mode = saved.get('compare', 'NONE') if saved.get('compare_period') else 'NONE'
    previous = [date.fromisoformat(saved['compare_period'][k]) for k in ('start', 'end')] if mode != 'NONE' else []
    c = ctx['conditions']
    c.update(period=period_view(start, end), sections=sections, request_channel='CHAT', result_mode=True,
             metric={'code': saved.get('metric', 'AMOUNT'), 'label': METRICS[saved.get('metric', 'AMOUNT')]},
             compare={'mode': mode, 'status': 'NONE' if mode == 'NONE' else 'READY', 'monthly_paired': False,
                      'period_label': period_view(*previous)['label'] if previous else '',
                      'short': period_view(*previous)['short'] if previous else '', 'basis_label': BASES[mode]})
    refs = {ref['result_id']: ref for ref in content.get('source_results', [])}
    results, checks = [], []
    for i, table in enumerate(content.get('source_tables', []), 1):
        columns = [{'key': col['key'], 'label': col['label'] + (f" ({col['unit']})" if col.get('unit') else ''),
                    'numeric': col.get('type') == 'number'} for col in table['columns']]
        rows = [[_cell(r.get(col['key']), col) for col in table['columns']] for r in table['rows'][:RESULT_ROW_LIMIT]]
        total = table.get('total_rows', len(table['rows']))
        note = f'전체 {total:,}행' + (f' 중 {len(rows):,}행 표시 · 전체는 XLSX 파일' if total > len(rows) else '')
        title = f"결과 {i} · {KINDS.get(table.get('kind'), table.get('kind'))}"
        results.append({'result_id': table['result_id'], 'title': title, 'columns': columns, 'rows': rows, 'note': note,
                        'chart': _trend_chart(table)})
        ref = refs.get(table['result_id'], {})
        checks.append({'target': title, 'content': f"승인한 결과표 {total:,}행 · 결과 ID {table['result_id']}",
                       'basis': '승인 시점 사본 · ' + ('일부 누락' if ref.get('completeness') == 'PARTIAL' else '재조회 없음')})
        checks.extend({'target': title, 'content': text, 'basis': '누락·제한 사유'} for text in ref.get('limitations', []))
    from backend.app.api.reports import RESULT_SECTIONS  # 웹·DOCX 문서와 같은 섹션 대응
    covered = {RESULT_SECTIONS.get(t.get('kind')) for t in content.get('source_tables', [])}
    missing = [SECTION_LABELS[s] for s in sections if s in SECTION_LABELS and s not in covered]
    if missing:
        checks.append({'target': '포함되지 않은 항목', 'content': ' · '.join(missing) + ': 해당 분석 결과에 포함되지 않음',
                       'basis': '결과표 밖 내용은 다시 조회해 채우지 않음'})
    ctx['results'] = results
    ctx['summary'] = {'kpi': {'amount_usd': None, 'country_count': None}, 'sentences': [], 'check_points': checks}
    ctx['meta']['data_basis'] = '승인한 AI 분석 결과표 사본 · 관세청 수출입무역통계 · 재조회 없음'
    ctx['sources'] = [{'label': '분석 결과표', 'provider': '관세청 (AI 분석 대화 결과)',
                       'scope': f'결과 {len(results)}개 · 승인 시점에 저장한 사본', 'used_in': '승인한 분석 결과'}]


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
