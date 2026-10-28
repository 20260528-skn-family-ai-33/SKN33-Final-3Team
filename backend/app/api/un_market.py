from datetime import date
from sqlalchemy import select
from backend.app.api.common import require, percentage
from backend.app.api.accounts import validate_scope


def validate(store,code,version,countries,freq):
    require(len(code)==6 and version=='HS2022' and freq in ('M','A'),'HS6·HS2022·M/A를 확인하세요.')
    validate_scope(store,[{'hs_code':code,'hs_ver':version}],countries,6)


def availability(store,code,version,countries,freq):
    validate(store,code,version,countries,freq)
    t=store.t('un_data')
    stmt=select(t.c.country_code,t.c.period_start).where(t.c.hs6_code==code,t.c.hs_ver==version,t.c.freq_code==freq)
    if countries: stmt=stmt.where(t.c.country_code.in_(countries))
    groups={}
    for r in store.rows(stmt.order_by(t.c.country_code,t.c.period_start)): groups.setdefault(r['country_code'],[]).append(r['period_start'])
    return {'items':[{'country_code':k,'hs6_code':code,'hs_ver':version,'freq_code':freq,'periods':v,'latest_verified_at':None} for k,v in groups.items()], 'source_code':'UN_COMTRADE'}


def shares(store,data):
    code,version,countries,freq=data['hs6_code'],data['hs_ver'],data['country_codes'],data['freq_code']
    validate(store,code,version,countries,freq)
    require(countries,'대상 수입국을 하나 이상 지정하세요.')
    try: start,end=date.fromisoformat(data['period_start']),date.fromisoformat(data['period_end'])
    except ValueError:
        from backend.app.api.common import ApiError
        raise ApiError(400,'VALIDATION_ERROR','유효한 YYYY-MM-DD 기간이 필요합니다.')
    require(start<=end and start.day==1 and end.day==1,'기간은 월의 1일, 시작일≤종료일이어야 합니다.')
    require(freq!='A' or start.month==end.month==1,'연간 데이터는 연도별 1월 1일 키를 사용합니다.')
    t=store.t('un_data')
    base=select(t).where(t.c.hs6_code==code,t.c.hs_ver==version,t.c.freq_code==freq)
    if countries: base=base.where(t.c.country_code.in_(countries))
    rows=store.rows(base.where(t.c.period_start>=start,t.c.period_start<=end).order_by(t.c.country_code,t.c.period_start))
    old={(r['country_code'],r['period_start'],r['valuation_basis']):r for r in store.rows(base.where(t.c.period_start>=start.replace(year=start.year-1),t.c.period_start<=end.replace(year=end.year-1)))}
    items=[]; warnings=set()
    from backend.app.api.common import shift_month
    observed={(r['country_code'],r['period_start']) for r in rows}
    cursor=start
    while cursor<=end:
        for country in countries:
            if (country,cursor) not in observed: warnings.add(f'MISSING_OBSERVATION:{country}:{cursor}')
        cursor=shift_month(cursor,12 if freq=='A' else 1)
    for r in rows:
        p=old.get((r['country_code'],r['period_start'].replace(year=r['period_start'].year-1),r['valuation_basis']))
        reason='PREVIOUS_MISSING' if not p else 'DENOMINATOR_ZERO' if p['total_import_usd']==0 or p['korea_import_usd']==0 else None
        if reason: warnings.add(reason)
        items.append({**r,'market_yoy_pct':percentage(r['total_import_usd'],p['total_import_usd']) if p else None,
            'korea_yoy_pct':percentage(r['korea_import_usd'],p['korea_import_usd']) if p else None,
            'share_change_pp':r['korea_share_pct']-p['korea_share_pct'] if p and r['korea_share_pct'] is not None and p['korea_share_pct'] is not None else None,
            'comparison_reason':reason})
    return {'items':items,'source_code':'UN_COMTRADE','warnings':sorted(warnings)}
