"""Compare the reporting-country total with partners without inventing a KR row."""
from sqlalchemy import select
from backend.app.api import trade, results
from backend.app.api.common import shift_month


def comparison_table(store,uid,conditions,start,end):
    codes=conditions.get('country_codes',[])
    names={r['country_code']:r['name_ko'] for r in store.rows(select(store.t('country')))}
    records=trade.aggregate(store,'export_summary',start,end,conditions.get('hs_items',[]),[],'MONTH_COUNTRY')
    totals={}; partners={}
    for row in records:
        month=row['trade_month'].strftime('%Y-%m')
        totals[month]=totals.get(month,0)+row['amount_usd']
        partners[month,row['country_code']]=row['amount_usd']
    columns=[{'key':'month','label':'기준월','type':'string','unit':None},
        {'key':'korea_total_usd','label':'한국(대한민국) 전체 수출액','type':'number','unit':'USD'}]
    columns.extend({'key':f'partner_{i}_usd','label':f'{names.get(code,code)} 수출액','type':'number','unit':'USD'}
                   for i,code in enumerate(codes))
    rows=[]; cursor=start
    while cursor<=end:
        month=cursor.strftime('%Y-%m')
        rows.append({'month':month,'korea_total_usd':totals.get(month,0),
            **{f'partner_{i}_usd':partners.get((month,code),0) for i,code in enumerate(codes)}})
        cursor=shift_month(cursor,1)
    meta=trade.metadata(store,start,end)
    meta.update(kind='REPORT_SCOPE_COMPARISON',overlapping_scopes=True)
    return results.result_table(store,uid,rows,meta,conditions,store.queries,columns=columns)
