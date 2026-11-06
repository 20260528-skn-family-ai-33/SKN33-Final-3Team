"""Presentation-only pivot of owned saved trade results; no SQL re-execution."""
from backend.app.api.results import read_snapshot
from backend.app.api.common import require


def country_comparison(store,uid,result_ids):
    require(isinstance(result_ids,list) and 1<=len(result_ids)<=3
            and all(isinstance(x,str) for x in result_ids) and len(set(result_ids))==len(result_ids),
            '비교할 결과표를 1~3개 선택하세요.')
    sources=[read_snapshot(store,uid,rid,'RESULT') for rid in result_ids]
    conditions=sources[0]['conditions']
    # This view compares the current-month amount only. A source's optional
    # previous-period calculation must not split identical current data.
    signature=lambda c:[c.get('period'),c.get('trade_flow','EXPORT'),c.get('metric','AMOUNT')]
    require(all(s['meta'].get('kind')=='TREND' and signature(s['conditions'])==signature(conditions) for s in sources),
            '같은 기간·방향·지표의 추이 결과만 비교할 수 있습니다.')
    countries={};products={};matrix={};sql=[]
    for source in sources:
        sql.extend(source.get('sql',[]))
        for row in source['rows']:
            require('month' in row and 'amount_usd' in row,'월별 금액 결과가 필요합니다.')
            code=row.get('country_code') or '__ALL__'
            countries.setdefault(code,row.get('country_name') or ('전체 국가' if code=='__ALL__' else code))
            hs=(row.get('hs_code'),row.get('hs_ver'))
            products.setdefault(hs,row.get('name') or '전체 품목')
            key=(*hs,row['month'])
            values=matrix.setdefault(key,{})
            require(code not in values or values[code]==row['amount_usd'],
                    '같은 국가·품목·월의 값이 서로 달라 비교할 수 없습니다.')
            values[code]=row['amount_usd']
    require(len(countries)<=20 and len(matrix)<=1000,'비교 표는 국가 20개·1,000행까지 지원합니다.','EXPORT_LIMIT_EXCEEDED',422)
    flow='수입액' if conditions.get('trade_flow')=='IMPORT' else '수출액'
    columns=[{'key':'hs_code','label':'HS 코드','type':'string','unit':None},
             {'key':'name','label':'품목','type':'string','unit':None},
             {'key':'month','label':'기준월','type':'string','unit':None}]
    country_keys={code:f'country_{i}_amount_usd' for i,code in enumerate(countries)}
    columns.extend({'key':country_keys[code],'label':f'{name} {flow}','type':'number','unit':'USD'}
                   for code,name in countries.items())
    rows=[{'hs_code':key[0],'name':products[key[:2]],'month':key[2],
           **{country_keys[code]:values.get(code) for code in countries}}
          for key,values in sorted(matrix.items(),key=lambda pair:(pair[0][2],pair[0][0] or '',pair[0][1] or ''))]
    return {'columns':columns,'rows':rows,'total_rows':len(rows),
            'meta':{**sources[0]['meta'],'kind':'COUNTRY_COMPARISON','source_result_ids':result_ids},
            'conditions':{**conditions,'country_codes':[code for code in countries if code!='__ALL__'],
                'hs_items':[{'hs_code':code,'hs_ver':version} for code,version in products if code]},
            'sql':list(dict.fromkeys(sql)),
            'snapshot_created_at':max(s['snapshot_created_at'] for s in sources)}
