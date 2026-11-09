"""Build charts from stored rows, retaining full labels only in the response."""
from collections import defaultdict
from datetime import date
import re
from backend.app.agents.state import (ChartSpec, ChartSelector, HsProduct, shorten_name,
    shorten_target_names, ToolResult, MAX_CHART_LABELS, MAX_CHART_SERIES, MAX_LABEL_CHARS)
from backend.app.api.common import shift_month
from backend.app.api.results import read_snapshot


def time_series(rows, keys, calendar=None):
    """Aggregate on a shared month axis; an absent month is unknown, never zero."""
    by_month = defaultdict(list)
    for row in rows:
        by_month[row['month']].append(row)
    calendar = sorted(set(calendar if calendar is not None else by_month))
    def group(month, granularity):
        if granularity=='MONTH': return month
        if granularity=='YEAR': return month[:4]
        return f'{month[:4]}-Q{(int(month[5:7])-1)//3+1}'
    for granularity in ('MONTH','QUARTER','YEAR'):
        buckets = defaultdict(list)
        for month in calendar:
            buckets[group(month, granularity)].extend(by_month.get(month) or [{}])
        if len(buckets)<=MAX_CHART_LABELS: break
    else: return None
    labels=sorted(buckets)
    def aggregate(records, key):
        values=[r.get(key) for r in records]
        return None if None in values else sum(values)
    return granularity, labels, [[aggregate(buckets[label],key) for label in labels] for key in keys]


def _unique_labels(names, identifiers):
    """Keep readable names, adding stable identity only when shortened labels collide."""
    labels = [shorten_name(name) for name in names]
    duplicates = {label for label in labels if labels.count(label) > 1}
    used = set(labels) - duplicates
    for i, (name, identity) in enumerate(zip(names, identifiers, strict=True)):
        if labels[i] not in duplicates:
            continue
        suffix = f'({identity})'
        label = shorten_name(name, limit=MAX_LABEL_CHARS-len(suffix)) + suffix
        # Names themselves can resemble an already disambiguated label.
        ordinal = 1
        while label in used:
            suffix = f'({identity}:{ordinal})'
            label = shorten_name(name, limit=MAX_LABEL_CHARS-len(suffix)) + suffix
            ordinal += 1
        labels[i] = label
        used.add(label)
    return labels


def _target_labels(targets):
    labels = shorten_target_names(targets)
    identities = [' '.join(part for part in (code, hs.hs_code if hs else None, hs.hs_ver if hs else None) if part)
                  or str(i+1) for i, (_, _, hs, code) in enumerate(targets)]
    return _unique_labels(labels, identities)


def _trend_order(item):
    _, rows = item
    total = rows[0].get('total_usd')
    if total is None:
        total = rows[0].get('verified_subtotal_usd')
    if total is None:
        total = sum(row.get('amount_usd') or 0 for row in rows)
    # Keep the stored table/digest order for ties, including the top-five boundary.
    return -total


def build_chart(store, uid, task, result_id, selection=None):
    snapshot = read_snapshot(store, uid, result_id, 'RESULT')
    rows = snapshot['rows']
    kind = snapshot['meta']['kind']
    output = ToolResult(status='OK', result_id=result_id)
    warnings = []
    if not rows: return output, [], warnings
    flow = '수입액' if task.conditions.trade_flow=='IMPORT' else '수출액'
    title, unit, granularity = flow, 'USD', None
    if kind=='TREND':
        grouped=defaultdict(list)
        for row in rows: grouped[(row['country_code'],row['hs_code'],row['hs_ver'])].append(row)
        selected = [identity for identity, _ in sorted(grouped.items(), key=_trend_order)][:MAX_CHART_SERIES]
        if len(grouped)>MAX_CHART_SERIES:
            warnings.append(f'금액 상위 {MAX_CHART_SERIES}개만 차트에 표시했습니다. 전체는 표에서 확인하세요.')
        first=[grouped[k][0] for k in selected]
        targets=[(r['country_name'] if r['country_code'] else None, r['name'] if r['hs_code'] else None,
                  HsProduct(hs_code=r['hs_code'],hs_ver=r['hs_ver']) if r['hs_code'] else None,
                  r['country_code']) for r in first]
        full=[' · '.join(x for x in t[:2] if x) or '전체' for t in targets]
        names=[x or '전체' for x in _target_labels(targets)]
        comparison=bool(task.conditions.compare_period)
        unequal=comparison and first[0]['requested_months']!=first[0]['compare_requested_months']
        if comparison and (len(selected)>1 or unequal):
            kindchart='grouped_bar'
            labels=names
            key='monthly_avg_usd' if unequal else 'total_usd'
            metric='월평균' if unequal else '합계'
            series=[{'name':label+' '+metric,'values':[r[prefix+key] for r in first]}
                    for label,prefix in [('비교 기간','compare_'),('조회 기간','')]]
            if unequal:
                warnings.append('기간 길이가 달라 검증된 월의 월평균 금액으로 비교했습니다. '
                                '실제 기간과 검증 월 수는 표에서 확인하세요.')
        else:
            kindchart='line'
            series=[]
            window=task.conditions.period
            if window:
                calendar=[]
                month=window.start
                while month<=window.end:
                    calendar.append(month.strftime('%Y-%m'))
                    # Avoid shifting past date.max after the final requested month.
                    if month==window.end: break
                    month=shift_month(month,1)
            else:
                calendar=sorted({r['month'] for target in selected for r in grouped[target]})
            for idx,target in enumerate(selected):
                bucket=time_series(grouped[target],
                    ['amount_usd','compare_amount_usd'] if comparison else ['amount_usd'], calendar)
                if not bucket: return output, [], ['표시할 기간이 너무 깁니다.']
                granularity, labels, values=bucket
                series.append({'name':flow if len(selected)==1 else names[idx], 'values':values[0]})
                if comparison: series.append({'name':'비교 기간','values':values[1]})
            full=labels
            if granularity in ('QUARTER','YEAR'):
                interval='분기' if granularity=='QUARTER' else '연도'
                title=f'{flow} ({interval} 합계)'
                warnings.append(f'{interval} 합계는 조회 범위 {calendar[0]}~{calendar[-1]} 안의 월만 포함합니다. '
                                '미집계 월이 있는 구간은 표시하지 않습니다.')
    elif kind=='SHARE':
        kindchart, title, unit='donut','국가별 비중','%'
        full=[r['label'] for r in rows]
        labels=_unique_labels(full, [r.get('country_code') or '기타' for r in rows])
        series=[{'name':'비중','values':[r['share_pct'] for r in rows]}]
    elif kind=='RANKING':
        kindchart='bar'
        top=rows[:MAX_CHART_LABELS]
        if len(rows)>MAX_CHART_LABELS:
            warnings.append(f'순위 상위 {MAX_CHART_LABELS}개만 차트에 표시했습니다. 전체는 표에서 확인하세요.')
        full=[r['name'] for r in top]
        def target(row):
            hs=row.get('hs_item') or ({'hs_code':row['hs_code'],'hs_ver':'HS2022'} if row.get('hs_code') else None)
            return (row['name'] if row.get('country_code') else None,
                row['name'] if hs else None, HsProduct(**hs) if hs else None, row.get('country_code'))
        labels=_target_labels([target(r) for r in top])
        field='import_amount_usd' if task.conditions.trade_flow=='IMPORT' else 'export_amount_usd'
        series=[{'name':flow,'values':[r[field] for r in top]}]
    elif kind=='PREDICTION':
        from backend.app.api.common import require
        group=snapshot['meta']['group_by']
        # 예측 행은 요청 품목의 판본(hs_ver)을 함께 갖는다. 이전 결과(판본 없음)는 HS2022다.
        edition=lambda r: r.get('hs_ver') or 'HS2022'
        countries=list(dict.fromkeys(r['country_code'] for r in rows))
        products=list(dict.fromkeys((r['hs6'],edition(r)) for r in rows))
        if selection is None:
            country=rows[0]['country_code'] if group in ('PAIR','COUNTRY') else None
            hs,ver=(rows[0]['hs6'],edition(rows[0])) if group in ('PAIR','HS6') else (None,None)
        else:
            country,hs,ver=selection.get('country_code'),selection.get('hs_code'),selection.get('hs_ver')
            require(bool(country)==(group in ('PAIR','COUNTRY')) and bool(hs)==(group in ('PAIR','HS6'))
                    and bool(ver)==bool(hs),'선택한 국가·품목 범위를 확인하세요.')
        subset=[r for r in rows if (not country or r['country_code']==country) and (not hs or (r['hs6'],edition(r))==(hs,ver))]
        require(subset,'결과에 없는 대상입니다.')
        ranked=sorted(subset,key=lambda r:r['country_rank'] if country else r['product_rank'])[:10]
        full=[(r.get('name') or (r['hs6'] if country else r['country_code'])) for r in ranked]
        labels=_target_labels([(None,n,HsProduct(hs_code=r['hs6'],hs_ver=edition(r)),None) if country
            else (n,None,None,r['country_code']) for r,n in zip(ranked,full)])
        count=len(rows) if group=='PAIR' else len(countries) if group=='COUNTRY' else len(products)
        if count>1:
            output.chart_selector=ChartSelector(result_id=result_id,group_by=group,
                country_codes=countries if group!='HS6' else [],hs_items=[HsProduct(hs_code=h,hs_ver=v)
                    for h,v in products] if group!='COUNTRY' else [])
        # 모델 A의 예측 증감률은 순위용이라 보이지 않는다(증감 방향은 모델 B, 표의 '증감 예상'). 순위 순서로 실제 수출액을 그린다.
        kindchart,title,unit='bar','유망 대상 최근 12개월 수출액','USD'
        series=[{'name':'최근 12개월 수출액','values':[r['exp_12m'] for r in ranked]}]
    elif kind=='LLM_SQL':
        columns=snapshot['columns']
        text=[c for c in columns if c['type']=='string']
        numeric=[c for c in columns if c['type']=='number']
        if len(rows)>MAX_CHART_LABELS or len(text)!=1 or not 1<=len(numeric)<=MAX_CHART_SERIES:
            return output, [], warnings
        full=[r[text[0]['key']] for r in rows]
        if not all(isinstance(v,str) and v for v in full): return output, [], warnings
        dates=all(re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',v) for v in full)
        if dates:
            if len({len(v) for v in full})!=1: return output, [], warnings
            try:
                for value in full: date.fromisoformat(value+'-01' if len(value)==7 else value)
            except ValueError:
                return output, [], warnings
            rows=sorted(rows,key=lambda row:row[text[0]['key']])
            full=[row[text[0]['key']] for row in rows]
        if not dates and len(numeric)!=1: return output, [], warnings
        if len({c['unit'] for c in numeric})!=1: return output, [], warnings
        kindchart='line' if dates else 'bar'
        labels=[shorten_name(v) for v in full]
        if len(set(labels))!=len(labels): return output, [], warnings
        unit=numeric[0]['unit'] or '값'
        title=snapshot['meta'].get('summary') or '조회 결과'
        series=[{'name':shorten_name(c['label']),'values':[r[c['key']] for r in rows]} for c in numeric]
        if len({s['name'] for s in series})!=len(series): return output, [], warnings
    else: return output, [], warnings
    output.chart=ChartSpec(chart_id=task.task_id+'.chart', task_id=task.task_id, result_id=result_id,
        type=kindchart, title=shorten_name(title), period=task.conditions.period, unit=unit,
        source='ML 모델' if kind=='PREDICTION' else '관세청', granularity=granularity, labels=labels, series=series)
    return output, full, warnings
