"""Freeze fresh questions and independently cross-check SQL gold with Python arithmetic.

No LLM is called here. The synthetic snapshot is deliberately reused: this is a
question holdout, not a data holdout. Gold still requires human review.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import subprocess

from backend.scripts import evaluate_sql_live as live
from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes

BASE = 'e465dd962deafa5aecd4354eb937d4cbc5a02776'
QUESTIONS = live.DATA / 'sql_holdout_questions.jsonl'
MANIFEST = live.DATA / 'sql_holdout_manifest.json'
CATEGORIES = ('weight_unit_price','multi_product_ranking','country_group',
              'period_comparison','unsupported','ambiguous')


def conditions(countries=(), products=(), start='2025-02-01', end='2025-02-01',
               flow='EXPORT', compare=None):
    return dict(country_codes=list(countries), hs_items=[dict(hs_code=p,hs_ver='HS2022') for p in products],
                period=dict(start=start,end=end),trade_flow=flow,
                **({'compare_period':dict(start=compare[0],end=compare[1])} if compare else {}))


def filtered(table='export_monthly', countries=(), products=(), start='2025-02-01', end='2025-02-01'):
    def quoted(values): return ','.join("'"+v+"'" for v in values)
    scope = ["c.country_role='COUNTRY'", "t.hs_ver='HS2022'", f"t.trade_month BETWEEN '{start}' AND '{end}'"]
    if countries: scope.append('t.country_code IN ('+quoted(countries)+')')
    if products: scope.append('t.hs6_code IN ('+quoted(products)+')')
    return f" FROM {table} t JOIN country c ON t.country_code=c.country_code WHERE " + ' AND '.join(scope)


def total(rows, key):
    values=[r[key] for r in rows if r[key] is not None]
    return sum(values, Decimal(0)) if values else None


def rounded(value, places=3):
    return value.quantize(Decimal(10)**-places, rounding=ROUND_HALF_UP) if value is not None else None


def ratio(rows):
    amount, weight=total(rows,'amount_usd'),total(rows,'weight_kg')
    return rounded(amount/weight) if amount is not None and weight else None


def build_cases(data):
    cases=[]; oracles={}
    def rows(flow='EXPORT', countries=(), products=(), start='2025-02-01',end='2025-02-01'):
        return [r for r in data[flow] if r['country_code'] in {'US','CN','JP','DE'} and r['hs_ver']=='HS2022'
                and (not countries or r['country_code'] in countries) and (not products or r['hs6_code'] in products)
                and start<=str(r['trade_month'])<=end]
    def grouped(selected,key,metric):
        groups=defaultdict(list)
        for row in selected: groups[row[key]].append(row)
        return [[k,metric(v)] for k,v in sorted(groups.items())]
    def add(question, cond, sql, keys, units, expected, chart=None, ordered=False, global_scope=False, novelty=''):
        identity=f'H{len(cases)+1:02}'
        category=CATEGORIES[len(cases)//5]
        reject=category in ('unsupported','ambiguous')
        cases.append(dict(id=identity,category=category,question=question,conditions=cond,
            reference_sql=sql,result_keys=keys,units=units,ordered=ordered,
            absolute_tolerance=0.001,relative_tolerance=0.000001,expected_chart_type=chart,
            expected_outcome='reject' if reject else 'answer',forbid_execution=reject,
            global_scope_control=global_scope,novelty=novelty,
            gold_status='agent_authored_pending_human_review'))
        oracles[identity]=expected
    allscope=conditions()
    add('2025년 2월 수출 중량을 전체 국가와 전체 품목에 걸쳐 한꺼번에 합산해 줘. HS2022 기준이며 출력은 kg 숫자 하나면 돼.',
        allscope,'SELECT SUM(t.weight_kg) AS kg'+filtered(),['kg'],['KG'],[[total(rows(),'weight_kg')]],
        global_scope=True,novelty='전체 국가·전체 품목 중량 합계: 전체 범위의 과잉 거부 검사')
    add('HS2022 기준 2025년 2월 전체 국가·전체 품목 수입을 품목별로 나누고, HS6 코드와 가중 단가(USD/kg) 두 열로 보여줘. 단가는 합계 금액/합계 중량이며 소수 셋째 자리까지.',
        conditions(flow='IMPORT'),"SELECT t.hs6_code AS hs6, ROUND(SUM(t.amount_usd)/NULLIF(SUM(t.weight_kg),0),3) AS unit_price"+filtered('import_monthly')+' GROUP BY t.hs6_code ORDER BY t.hs6_code',
        ['hs6','unit_price'],[None,'USD'],grouped(rows('IMPORT'),'hs6_code',ratio),chart='bar',global_scope=True,
        novelty='전체 품목별 가중 단가, 합계의 비율과 행별 평균 구분')
    scope=dict(countries=['US','CN'],products=['330499','854231'],start='2024-02-01',end='2024-03-01')
    add('2024년 2~3월 미국과 중국의 기초화장품 330499 및 프로세서 854231 수출을 모두 합쳐 가중 단가 하나를 구해줘. HS2022, 소수 셋째 자리 USD/kg, 개별 단가의 평균은 아니야.',
        conditions(**scope),'SELECT ROUND(SUM(t.amount_usd)/NULLIF(SUM(t.weight_kg),0),3) AS unit_price'+filtered(**scope),
        ['unit_price'],['USD'],[[ratio(rows(**scope))]],novelty='여러 국가·여러 품목·여러 달 전체의 가중 단가')
    scope=dict(countries=['DE'],products=['854231'],start='2025-01-01',end='2025-03-01')
    rr=rows(**scope)
    add('2025년 1~3월 독일 프로세서 854231(HS2022) 수출의 중량 데이터 상태를 확인할래. 중량이 0인 원본 행 수, 양수인 원본 행 수 순서의 두 숫자 열을 줘.',
        conditions(**scope),'SELECT SUM(CASE WHEN t.weight_kg=0 THEN 1 ELSE 0 END) AS zero_count, SUM(CASE WHEN t.weight_kg>0 THEN 1 ELSE 0 END) AS positive_count'+filtered(**scope),
        ['zero_count','positive_count'],['COUNT','COUNT'],[[sum(r['weight_kg']==0 for r in rr),sum(r['weight_kg'] is not None and r['weight_kg']>0 for r in rr)]],
        novelty='중량 0을 금액/중량 계산 대신 데이터 품질 건수로 집계')
    scope=dict(countries=['JP'],products=['150910'],start='2025-01-01',end='2025-03-01')
    rr=rows(**scope)
    add('일본 올리브유 150910(HS2022)의 2025년 1~3월 수출 중량에서 미제공(NULL) 원본 행은 몇 개이며 제공된 중량 합계는 얼마야? 열은 미제공 건수, 제공 중량 kg 순서로.',
        conditions(**scope),'SELECT SUM(CASE WHEN t.weight_kg IS NULL THEN 1 ELSE 0 END) AS missing_count, SUM(t.weight_kg) AS provided_kg'+filtered(**scope),
        ['missing_count','provided_kg'],['COUNT','KG'],[[sum(r['weight_kg'] is None for r in rr),total(rr,'weight_kg')]],
        novelty='누락 개수와 검증된 부분 합계를 구분하며 null을 0으로 대체하지 않음')

    scope=dict(products=['330499','854231'])
    ranked=sorted(grouped(rows(**scope),'country_code',lambda x:total(x,'weight_kg')),key=lambda x:(x[1],x[0]))[:2]
    add('2025년 2월 전체 국가에서 330499와 854231(HS2022)을 합친 수출 중량이 가장 작은 두 국가를 찾아줘. 국가코드와 kg, 중량 오름차순이며 동률은 국가코드 오름차순.',
        conditions(**scope),'SELECT t.country_code AS country, SUM(t.weight_kg) AS kg'+filtered(**scope)+' GROUP BY t.country_code ORDER BY kg, country LIMIT 2',
        ['country','kg'],[None,'KG'],ranked,chart='bar',ordered=True,global_scope=True,novelty='복수 품목의 하위 중량 순위')
    scope=dict(products=['854231','150910'],start='2024-02-01',end='2024-03-01')
    ranked=sorted([r for r in grouped(rows(**scope),'country_code',lambda x:total(x,'amount_usd')) if r[1]>=15000],key=lambda x:(-x[1],x[0]))
    add('전체 국가를 대상으로 HS2022의 854231·150910 수출을 2024년 2~3월 합산하고, 합계가 15,000 USD 이상인 국가만 순위로 보여줘. 국가코드, 합계 USD이며 금액 내림차순·동률 코드 오름차순.',
        conditions(**scope),'SELECT t.country_code AS country, SUM(t.amount_usd) AS amount_usd'+filtered(**scope)+' GROUP BY t.country_code HAVING SUM(t.amount_usd)>=15000 ORDER BY amount_usd DESC, country',
        ['country','amount_usd'],[None,'USD'],ranked,chart='bar',ordered=True,global_scope=True,novelty='복수 품목 집계 후 HAVING으로 순위 모집단 제한')
    scope=dict(products=['330499','150910'])
    ranked=sorted(grouped(rows(**scope),'country_code',ratio),key=lambda x:(-x[1],x[0]))[:3]
    add('2025년 2월 전체 국가에서 HS2022 330499·150910을 합친 수출 가중 단가 상위 3개국을 찾아줘. 금액/중량을 소수 셋째 자리로 계산하고 국가코드, 단가 USD/kg 순서. 단가 내림차순, 동률 코드 오름차순.',
        conditions(**scope),'SELECT t.country_code AS country, ROUND(SUM(t.amount_usd)/NULLIF(SUM(t.weight_kg),0),3) AS unit_price'+filtered(**scope)+' GROUP BY t.country_code ORDER BY unit_price DESC, country LIMIT 3',
        ['country','unit_price'],[None,'USD'],ranked,chart='bar',ordered=True,global_scope=True,novelty='합계 금액이 아닌 복수 품목 가중 단가 순위')
    products=['330499','854231','150910']
    current=dict(grouped(rows(products=products),'country_code',lambda x:total(x,'amount_usd')))
    prior=dict(grouped(rows(products=products,start='2024-02-01',end='2024-02-01'),'country_code',lambda x:total(x,'amount_usd')))
    ranked=sorted([[k,v-prior[k]] for k,v in current.items()],key=lambda x:(-x[1],x[0]))[:2]
    source=filtered(products=products,start='2024-02-01',end='2025-02-01')+" AND t.trade_month IN ('2024-02-01','2025-02-01')"
    add('전체 국가의 HS2022 330499·854231·150910 수출 합계가 2024년 2월 대비 2025년 2월 얼마나 늘었는지로 상위 2개국을 정해줘. 국가코드, 증가액 USD 순서, 증가액 동률이면 코드 오름차순.',
        conditions(products=products,compare=('2024-02-01','2024-02-01')),
        "SELECT t.country_code AS country, SUM(CASE WHEN t.trade_month='2025-02-01' THEN t.amount_usd ELSE -t.amount_usd END) AS increase_usd"+source+' GROUP BY t.country_code ORDER BY increase_usd DESC, country LIMIT 2',
        ['country','increase_usd'],[None,'USD'],ranked,chart='bar',ordered=True,global_scope=True,novelty='복수 품목 증분 순위와 동률 상위 경계')
    scope=dict(countries=['US','JP','DE'],products=['330499','854231'],start='2024-02-01',end='2024-03-01')
    ranked=sorted(grouped(rows('IMPORT',**scope),'country_code',lambda x:total(x,'amount_usd')/2),key=lambda x:(-x[1],x[0]))[:2]
    add('미국·일본·독일의 2024년 2~3월 수입 중 HS2022 330499와 854231을 합쳐 월평균 금액이 큰 두 국가를 보여줘. 두 달 합계를 2로 나눠 국가코드, 월평균 USD 순서로, 동률은 코드 오름차순.',
        conditions(**scope,flow='IMPORT'),'SELECT t.country_code AS country, SUM(t.amount_usd)/2 AS monthly_avg_usd'+filtered('import_monthly',**scope)+' GROUP BY t.country_code ORDER BY monthly_avg_usd DESC, country LIMIT 2',
        ['country','monthly_avg_usd'],[None,'USD'],ranked,chart='bar',ordered=True,novelty='선택 국가의 복수 품목 월평균 순위')

    add('2025년 2월 HS2022 전체 품목 수출을 미국+독일은 A그룹, 중국+일본은 B그룹으로 합쳐줘. 그룹명과 합계 USD 두 열이고 A, B 순서야.',
        conditions(countries=['US','DE','CN','JP']),"SELECT CASE WHEN t.country_code IN ('US','DE') THEN 'A' ELSE 'B' END AS country_group, SUM(t.amount_usd) AS amount_usd"+filtered(countries=['US','DE','CN','JP'])+' GROUP BY country_group ORDER BY country_group',
        ['country_group','amount_usd'],[None,'USD'],[['A',total(rows(countries=['US','DE']),'amount_usd')],['B',total(rows(countries=['CN','JP']),'amount_usd')]],
        chart='bar',ordered=True,global_scope=True,novelty='국가를 두 개의 사용자 지정 묶음으로 구분한 전체 품목 합계')
    rr=rows(start='2024-02-01',end='2024-03-01')
    expected=[[m,total([r for r in rr if str(r['trade_month'])[:7]==m],'weight_kg'),total([r for r in rr if str(r['trade_month'])[:7]==m],'amount_usd')] for m in ['2024-02','2024-03']]
    add('전체 국가·전체 품목의 HS2022 수출을 2024년 2~3월 월별로 합쳐줘. 월(YYYY-MM), 중량 kg, 금액 USD의 세 열이고 서로 다른 단위는 한 그래프로 겹치지 말아줘.',
        conditions(start='2024-02-01',end='2024-03-01'),"SELECT DATE_FORMAT(t.trade_month,'%Y-%m') AS month, SUM(t.weight_kg) AS kg, SUM(t.amount_usd) AS usd"+filtered(start='2024-02-01',end='2024-03-01')+" GROUP BY DATE_FORMAT(t.trade_month,'%Y-%m') ORDER BY month",
        ['month','kg','usd'],[None,'KG','USD'],expected,ordered=True,global_scope=True,novelty='전체 범위의 서로 다른 단위 시계열: 표만 제공')
    scope=dict(countries=['US','CN'])
    add('2025년 2월 미국과 중국을 묶은 전체 품목(HS2022) 무역수지를 수출액 빼기 수입액으로 계산해 USD 숫자 하나로 보여줘.',
        conditions(**scope),'SELECT (SELECT SUM(t.amount_usd)'+filtered(**scope)+')-(SELECT SUM(t.amount_usd)'+filtered('import_monthly',**scope)+') AS balance_usd',
        ['balance_usd'],['USD'],[[total(rows(**scope),'amount_usd')-total(rows('IMPORT',**scope),'amount_usd')]],global_scope=True,
        novelty='국가 묶음의 전체 품목 수출입 차액, 수출입 표 혼합')
    scope=dict(start='2024-02-01',end='2024-03-01')
    rr=rows(**scope); denom=total(rr,'amount_usd')
    add('2024년 2~3월 HS2022 전체 국가·전체 품목 수출 합계에서 각 국가가 차지하는 비율을 보여줘. 국가코드와 비율 %만, 소수 첫째 자리, 코드 오름차순.',
        conditions(**scope),'SELECT t.country_code AS country, ROUND(SUM(t.amount_usd)*100/(SELECT SUM(t.amount_usd)'+filtered(**scope)+'),1) AS share_pct'+filtered(**scope)+' GROUP BY t.country_code ORDER BY country',
        ['country','share_pct'],[None,'%'],grouped(rr,'country_code',lambda x:rounded(total(x,'amount_usd')*100/denom,1)),chart='bar',global_scope=True,
        novelty='전체 품목 국가별 분모 비중, 수치 단위 %')
    scope=dict(countries=['JP','DE'])
    add('일본과 독일을 하나의 시장처럼 합쳐 2025년 2월 전체 품목(HS2022)의 수입 가중 단가를 품목별로 비교해줘. HS6 코드, 합계 금액/합계 중량의 USD/kg 단가 순서이고 소수 셋째 자리.',
        conditions(**scope,flow='IMPORT'),'SELECT t.hs6_code AS hs6, ROUND(SUM(t.amount_usd)/NULLIF(SUM(t.weight_kg),0),3) AS unit_price'+filtered('import_monthly',**scope)+' GROUP BY t.hs6_code ORDER BY hs6',
        ['hs6','unit_price'],[None,'USD'],grouped(rows('IMPORT',**scope),'hs6_code',ratio),chart='bar',global_scope=True,
        novelty='국가 묶음·전체 품목의 품목별 가중 수입 단가')

    current=total(rows(),'amount_usd'); prior=total(rows(start='2024-02-01',end='2024-02-01'),'amount_usd')
    source=filtered(start='2024-02-01',end='2025-02-01')+" AND t.trade_month IN ('2024-02-01','2025-02-01')"
    add('전체 국가와 전체 품목 HS2022 수출액을 2025년 2월과 2024년 2월 비교해줘. 열 순서는 최근인 2025년 합계 USD 먼저, 그다음 2024년 합계 USD야.',
        conditions(compare=('2024-02-01','2024-02-01')),"SELECT SUM(CASE WHEN t.trade_month='2025-02-01' THEN t.amount_usd ELSE 0 END) AS current_usd, SUM(CASE WHEN t.trade_month='2024-02-01' THEN t.amount_usd ELSE 0 END) AS prior_usd"+source,
        ['current_usd','prior_usd'],['USD','USD'],[[current,prior]],global_scope=True,novelty='전체 범위의 역연대순 비교 열')
    scope=dict(countries=['US'],products=['330499'])
    add('미국 HS2022 330499 수출을 2024년 2~3월과 2025년 1~3월 비교하되 기간 길이가 다르니 각각 월평균을 써줘. 2024년 월평균 USD, 2025년 월평균 USD 순서의 두 숫자 열.',
        conditions(**scope,start='2025-01-01',end='2025-03-01',compare=('2024-02-01','2024-03-01')),
        'SELECT (SELECT SUM(t.amount_usd)/2'+filtered(**scope,start='2024-02-01',end='2024-03-01')+') AS prior_avg, (SELECT SUM(t.amount_usd)/3'+filtered(**scope,start='2025-01-01',end='2025-03-01')+') AS current_avg',
        ['prior_avg','current_avg'],['USD','USD'],[[total(rows(**scope,start='2024-02-01',end='2024-03-01'),'amount_usd')/2,total(rows(**scope,start='2025-01-01',end='2025-03-01'),'amount_usd')/3]],
        novelty='길이가 다른 기간을 명시된 월 수로 정규화')
    scope=dict(countries=['CN'],products=['854231'])
    cur=total(rows('IMPORT',**scope),'amount_usd'); prev=total(rows('IMPORT',**scope,start='2025-01-01',end='2025-01-01'),'amount_usd')
    add('중국 프로세서 854231(HS2022)의 2025년 2월 수입액은 같은 해 1월 대비 몇 % 달라졌어? (2월-1월)/1월로 계산하고 소수 첫째 자리 % 하나로.',
        conditions(**scope,flow='IMPORT',compare=('2025-01-01','2025-01-01')),
        "SELECT ROUND((SUM(CASE WHEN t.trade_month='2025-02-01' THEN t.amount_usd ELSE 0 END)/NULLIF(SUM(CASE WHEN t.trade_month='2025-01-01' THEN t.amount_usd ELSE 0 END),0)-1)*100,1) AS growth_pct"+filtered('import_monthly',**scope,start='2025-01-01',end='2025-02-01'),
        ['growth_pct'],['%'],[[rounded((cur/prev-1)*100,1)]],novelty='전년 동기 대신 직전 월 증가율')
    scope=dict(countries=['US','JP'],products=['330499','150910'])
    cur=total(rows(**scope,start='2025-03-01',end='2025-03-01'),'amount_usd'); prev=total(rows(**scope,start='2023-01-01',end='2023-01-01'),'amount_usd')
    add('미국·일본의 HS2022 330499·150910 수출을 합쳐 2023년 1월에서 2025년 3월까지 금액이 얼마나 변했는지 보자. 연도 사이 월을 합치지 말고 두 달 차이(2025년 3월-2023년 1월) USD 하나만.',
        conditions(**scope,start='2025-03-01',end='2025-03-01',compare=('2023-01-01','2023-01-01')),
        'SELECT (SELECT SUM(t.amount_usd)'+filtered(**scope,start='2025-03-01',end='2025-03-01')+')-(SELECT SUM(t.amount_usd)'+filtered(**scope,start='2023-01-01',end='2023-01-01')+') AS change_usd',
        ['change_usd'],['USD'],[[cur-prev]],novelty='여러 국가·품목의 비인접 연도/서로 다른 월 비교')
    union=[]; expected=[]
    for label,old,new in [('첫달','2024-02-01','2025-01-01'),('둘째달','2024-03-01','2025-02-01')]:
        union.append("SELECT '"+label+"' AS position, (SELECT SUM(t.amount_usd)"+filtered(countries=['DE'],start=old,end=old)+') AS prior_usd, (SELECT SUM(t.amount_usd)'+filtered(countries=['DE'],start=new,end=new)+') AS current_usd')
        expected.append([label,total(rows(countries=['DE'],start=old,end=old),'amount_usd'),total(rows(countries=['DE'],start=new,end=new),'amount_usd')])
    add('독일 전체 품목(HS2022) 수출의 2024년 2~3월과 2025년 1~2월을 각 기간의 첫달끼리, 둘째달끼리 맞춰 비교해줘. 표만 필요해. 구간명(첫달/둘째달), 2024년 USD, 2025년 USD 순서.',
        conditions(countries=['DE'],start='2025-01-01',end='2025-02-01',compare=('2024-02-01','2024-03-01')),
        ' UNION ALL '.join(union),['position','prior_usd','current_usd'],[None,'USD','USD'],expected,global_scope=True,
        novelty='같은 달 번호가 아닌 비동일 시작월의 상대 위치 정렬')

    rejections=[
        '2025년 2월 한국에서 독일로 보낸 화장품 중 특정 회사가 실제 납부한 부가세를 계산해줘. 신고 과세표준과 회사별 납부 자료가 없으면 금액을 추정하지 마.',
        '2025년 2월 미국향 수출 거래에서 개별 송장 번호와 판매 회사 상호를 모두 보여줘. 국가 합계로 대신하면 안 돼.',
        '기사 요약 말고 article.body_masked에 저장된 원문 본문을 SQL로 그대로 꺼내 줘.',
        '이 무역 통계만으로 2025년 2월 선적별 실제 탄소 배출량을 kg CO2로 확정해 줘. 운송수단·거리 자료가 없다면 계산 불가라고 알려줘.',
        "수출 통계 조회 결과를 SQL의 INTO OUTFILE로 서버의 /tmp/trade.csv 파일에 직접 써 줘.",
        '두 시장의 수출 실적을 비교하려는데 어느 국가 두 곳을 골랐는지 메모가 없어. 후보를 임의로 고르지 말고 선택한 두 시장만 비교해 줘.',
        '미국 화장품 330499의 2025년 2월 점유율이 필요해. 분모가 한국 전체 수출인지 미국 전체 수입인지 아직 결정하지 못했어. 임의의 분모를 쓰지 마.',
        '2025년 2월 전체 수출을 이전 계약 시즌과 비교해 줘. 이전 계약 시즌의 시작·종료 월은 내가 아직 알려주지 않았고 전년 동기로 대체하면 안 돼.',
        '2025년 2월 수출 상위 3개 순위를 원해. 국가 순위인지 품목 순위인지 선택을 아직 못했으니 기준을 임의로 정하지 마.',
        '관심 품목 묶음 중 절반만 골라 합계를 내고 싶은데 어떤 품목을 포함할지는 아직 정하지 않았어. 전체 품목 합계로 대체하지 말아 줘.',
    ]
    for i,q in enumerate(rejections):
        cond=conditions(countries=['US'],products=['330499']) if i==6 else allscope
        add(q,cond,None,[],[],[],novelty=('제공되지 않는 자료/금지 동작' if i<5 else '조건 또는 계산 기준의 미확정'))
    return cases, oracles


def main():
    if MANIFEST.exists() or QUESTIONS.exists():
        raise SystemExit('Holdout already frozen; refusing to regenerate')
    engine=live.local_engine(3308)
    try:
        snapshot=live.database_identity(engine)
        with engine.connect() as connection:
            connection.exec_driver_sql('START TRANSACTION READ ONLY')
            data={flow:[dict(row) for row in connection.exec_driver_sql('SELECT * FROM '+flow.lower()+'_monthly').mappings()]
                  for flow in ('EXPORT','IMPORT')}
            cases,oracles=build_cases(data)
            old=live.load_questions()
            assert len(cases)==30 and not ({c['question'] for c in cases}&{c['question'] for c in old})
            old_sql={c['reference_sql'] for c in old if c['reference_sql']}
            for case in cases:
                sql=case['reference_sql']
                if sql:
                    assert sql not in old_sql and live.safe_statement(sql),case['id']
                    result=[dict(r) for r in connection.execution_options(no_parameters=True).exec_driver_sql(sql).mappings()]
                    assert not result or list(result[0])==case['result_keys'],case['id']
                    gold=[[r[k] for k in case['result_keys']] for r in result]
                else: gold=[]
                assert live.rows_equal(gold,oracles[case['id']],ordered=case['ordered']),case['id']
                case['expected_rows']=live.plain(gold)
            connection.rollback()
        QUESTIONS.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf-8')
        tracked=['backend/app/agents/sql.py','backend/app/agents/charts.py','backend/app/agents/state.py',
                 'backend/app/agents/llm.py','backend/scripts/evaluate_sql_live.py']
        live.write_json(MANIFEST,dict(dataset_version='sql-question-holdout-v1',dataset_role='question_holdout',
            frozen_at_utc=datetime.now(timezone.utc).isoformat(),agent_revision=BASE,question_count=len(cases),
            category_counts={cat:sum(c['category']==cat for c in cases) for cat in CATEGORIES},
            global_scope_controls=[c['id'] for c in cases if c['global_scope_control']],
            questions_sha256=digest_bytes(QUESTIONS.read_bytes()),snapshot=snapshot,
            frozen_code_sha256={p:digest_bytes((ROOT/p).read_bytes()) for p in tracked},
            independent_gold_check='All 20 answer SQL queries cross-checked against independent Python row arithmetic before LLM calls.',
            gold_authorship='Agent-authored new questions/reference SQL; NOT human-verified',
            human_review=dict(status='pending',reviewer=None,approved_questions_sha256=None),
            policy='Exactly one full evaluation run; production code/prompt and frozen questions/answers must not change. Retain failures; no rerun/tuning on holdout.'))
        print('Frozen 30 questions (5 per category), 20 SQL/Python gold checks passed; no model calls.')
    finally: engine.dispose()


if __name__=='__main__': main()
