"""Build charts from stored rows, retaining full labels only in the response."""
from collections import defaultdict
import re
from backend.app.agents.state import ChartSpec, ChartSelector, HsProduct, shorten_name, shorten_target_names, ToolResult
from backend.app.api.results import read_snapshot


def time_series(rows, keys):
    def group(month, granularity):
        if granularity=='MONTH': return month
        if granularity=='YEAR': return month[:4]
        return f'{month[:4]}-Q{(int(month[5:7])-1)//3+1}'
    for granularity in ('MONTH','QUARTER','YEAR'):
        buckets = defaultdict(list)
        for row in rows: buckets[group(row['month'],granularity)].append(row)
        if len(buckets)<=60: break
    else: return None
    labels=sorted(buckets)
    def aggregate(records, key):
        values=[r.get(key) for r in records]
        return None if None in values else sum(values)
    return granularity, labels, [[aggregate(buckets[label],key) for label in labels] for key in keys]


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
        selected = list(grouped)[:5]
        if len(grouped)>5: warnings.append('금액 상위 5개만 차트에 표시했습니다. 전체는 표에서 확인하세요.')
        first=[grouped[k][0] for k in selected]
        targets=[(r['country_name'] if r['country_code'] else None, r['name'] if r['hs_code'] else None,
                  HsProduct(hs_code=r['hs_code'],hs_ver=r['hs_ver']) if r['hs_code'] else None,
                  r['country_code']) for r in first]
        full=[' · '.join(x for x in t[:2] if x) or '전체' for t in targets]
        names=[x or '전체' for x in shorten_target_names(targets)]
        comparison=bool(task.conditions.compare_period)
        unequal=comparison and first[0]['requested_months']!=first[0]['compare_requested_months']
        if comparison and (len(selected)>1 or unequal):
            kindchart='grouped_bar'
            labels=names
            key='monthly_avg_usd' if unequal else 'total_usd'
            metric='월평균' if unequal else '합계'
            series=[{'name':label+' '+metric,'values':[r[prefix+key] for r in first]}
                    for label,prefix in [('비교 기간','compare_'),('조회 기간','')]]
        else:
            kindchart='line'
            series=[]
            for idx,target in enumerate(selected):
                bucket=time_series(grouped[target], ['amount_usd','compare_amount_usd'] if comparison else ['amount_usd'])
                if not bucket: return output, [], ['표시할 기간이 너무 깁니다.']
                granularity, labels, values=bucket
                series.append({'name':flow if len(selected)==1 else names[idx], 'values':values[0]})
                if comparison: series.append({'name':'비교 기간','values':values[1]})
            full=labels
    elif kind=='SHARE':
        kindchart, title, unit='donut','국가별 비중','%'
        full=[r['label'] for r in rows]
        labels=[shorten_name(v) for v in full]
        series=[{'name':'비중','values':[r['share_pct'] for r in rows]}]
    elif kind=='RANKING':
        kindchart='bar'
        top=rows[:10]
        full=[r['name'] for r in top]
        def target(row):
            hs=row.get('hs_item') or ({'hs_code':row['hs_code'],'hs_ver':'HS2022'} if row.get('hs_code') else None)
            return (row['name'] if row.get('country_code') else None,
                row['name'] if hs else None, HsProduct(**hs) if hs else None, row.get('country_code'))
        labels=shorten_target_names([target(r) for r in top])
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
        labels=shorten_target_names([(None,n,HsProduct(hs_code=r['hs6'],hs_ver=edition(r)),None) if country
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
        if len(rows)>60 or len(text)!=1 or not 1<=len(numeric)<=5: return output, [], warnings
        full=[r[text[0]['key']] for r in rows]
        if not all(isinstance(v,str) and v for v in full): return output, [], warnings
        dates=all(re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',v) for v in full)
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
