import secrets
from datetime import timedelta, timezone
import re
from sqlalchemy import select
from backend.app.api.common import now, clean, ApiError, page_info, require, period_label

LABELS={'amount_usd':'수출액','export_amount_usd':'수출액','import_amount_usd':'수입액',
        'previous_amount_usd':'비교 기간 수출액','comparison_pct':'비교 증감률',
        'mom_pct':'전월 대비','yoy_pct':'전년 대비','export_country_count':'수출 국가 수',
        'top_country_share_pct':'최대 국가 비중','month':'기준월','period':'기간','country':'국가',
        'country_code':'국가 코드','share_pct':'비중','rank':'순위','rank_change':'순위 변화',
        'rank_status':'순위 상태','previous_year_export_usd':'전년 수출액','previous_year_import_usd':'전년 수입액','growth_pct':'증감률',
        'current_amount_usd':'현재 수출액','baseline_amount_usd':'비교 수출액','change_amount_usd':'증감액',
        'growth_status':'증감 상태','severity':'변화 수준','hs_item':'품목','hs_name':'품목명','one_off':'일회성 판정','label':'구분'}
LABELS.update({'country_name':'국가','hs_code':'HS 코드','trade_month':'기준월',
    'total_usd':'기간 합계','compare_total_usd':'비교 기간 합계','change_pct':'증감률',
    'monthly_avg_usd':'월평균 금액','compare_monthly_avg_usd':'비교 기간 월평균',
    'compare_amount_usd':'비교 기간 금액','compare_month':'비교 기준월','verified_months':'집계된 개월 수',
    'requested_months':'조회 개월 수','compare_verified_months':'비교 집계 개월 수',
    'compare_requested_months':'비교 조회 개월 수','verified_subtotal_usd':'집계된 금액',
    'compare_verified_subtotal_usd':'비교 집계 금액'})


def save_snapshot(store, uid, kind, payload, ttl=None):
    identity=kind.lower()+'_'+secrets.token_hex(16)
    store.insert('api_snapshot',snapshot_id=identity,user_id=uid,kind=kind,
                 payload_json=clean(payload),created_at=now(),expires_at=now()+timedelta(seconds=ttl) if ttl else None)
    return identity


def read_snapshot(store, uid, identity, kind=None):
    row=store.owned('api_snapshot','snapshot_id',identity,uid)
    require(kind is None or row['kind']==kind,'대상을 찾을 수 없습니다.','NOT_FOUND',404)
    if row['expires_at'] and row['expires_at']<=now():
        if kind=='PREVIEW':
            raise ApiError(409,'PREVIEW_EXPIRED','리포트 조건 확인이 만료되었습니다. 조건을 다시 확인해 주세요.')
        raise ApiError(410,'RESULT_EXPIRED','저장 결과가 만료되었습니다.')
    payload=row['payload_json']
    # Legacy report previews can reference a RESULT containing only conditions.
    if row['kind']=='RESULT' and 'columns' in payload and 'rows' in payload:
        payload={**payload,'columns':display_columns(payload['columns'],payload['rows'],payload.get('meta')),
                 'snapshot_created_at':row['created_at']}
    return payload


def display_columns(columns, rows, meta=None):
    if (meta or {}).get('kind') == 'PREDICTION':
        return prediction_columns(columns, meta.get('group_by'))
    if (meta or {}).get('kind') == 'LLM_SQL':
        # 자유 SQL의 열은 요청에 맞춰 모델이 고른 결과다(판본별 품목 수의 hs_ver 등). 템플릿용 숨김 규칙을 쓰지 않는다.
        return [{**c,'label':c.get('label') or LABELS.get(c['key'],c['key'])} for c in columns]
    hidden={'hs_ver'}
    labels={**LABELS,'name':'품목' if any(r.get('hs_code') for r in rows) else '국가' if any(r.get('country_code') for r in rows) else '명칭'}
    if not any(r.get('hs_item') for r in rows): hidden.add('hs_item')
    if any(r.get('country_name') for r in rows): hidden.add('country_code')
    if any('hs_code' in r for r in rows) and not any(r.get('hs_code') for r in rows): hidden.update(('hs_code','name'))
    # Monthly rows also carry aggregate metadata for chart/digest consumers.
    extras=len((meta or {}).get('extra_compare_periods') or [])
    labels.update(_period_labels(meta or {}))
    if any('month' in r and 'verified_months' in r for r in rows):
        # 비교 기간마다 따로 정한다. 길이가 같으면 월별 비교 금액을 보이고, 다르면(D-84) 월별 금액이 없으므로
        # 총액·월평균·검증 월 수를 비교 근거로 보인다. 어느 기간이든 길이가 다르면 조회 기간의 총액 열도 보인다.
        aggregate=('total_usd','monthly_avg_usd','verified_months','requested_months','verified_subtotal_usd')
        any_period=False
        for prefix,change in [('compare_','change_pct'),*((f'compare{n}_',f'change{n}_pct') for n in range(2,extras+2))]:
            monthly=any(r.get(prefix+'amount_usd') is not None for r in rows)
            by_period=not monthly and any(r.get(prefix+'requested_months')
                                          and r.get(prefix+'requested_months')!=r.get('requested_months') for r in rows)
            any_period|=by_period
            if not by_period: hidden.update((change,*(prefix+field for field in aggregate)))
            if not monthly: hidden.update((prefix+'amount_usd',prefix+'month'))
        if not any_period: hidden.update(aggregate)
    return [{**c,'label':labels.get(c['key'],c.get('label',c['key']))
             if c.get('label',c['key'])==c['key'] else c['label']} for c in columns if c['key'] not in hidden]


def _period_labels(meta):
    """비교 기간이 여럿이면(meta.extra_compare_periods) 비교 열 이름에 기간을 붙여 구분한다. 하나뿐이면 기존 이름을 쓴다."""
    if not meta.get('extra_compare_periods'): return {}
    windows=[(meta.get('compare_period'),'compare_','change_pct'),
             *((w,f'compare{n}_',f'change{n}_pct') for n,w in enumerate(meta['extra_compare_periods'],start=2))]
    out={}
    if meta.get('period'): out['amount_usd']=period_label(meta['period']['start'],meta['period']['end'])+' 금액'
    for window,prefix,change in windows:
        if not window: continue
        label=period_label(window['start'],window['end'])
        out.update({prefix+'amount_usd':label+' 금액', prefix+'month':label+' 기준월', prefix+'total_usd':label+' 합계',
            prefix+'monthly_avg_usd':label+' 월평균', prefix+'verified_months':label+' 집계 개월 수',
            prefix+'requested_months':label+' 조회 개월 수', prefix+'verified_subtotal_usd':label+' 집계 금액',
            change:label+' 대비 증감률'})
    return out


def prediction_columns(columns, group_by):
    by_key = {column['key']: column for column in columns}
    rank_fields = {
        'COUNTRY': [('country_rank', '품목 순위')],
        'HS6': [('product_rank', '국가 순위')],
        'PAIR': [('country_rank', '국가 내 품목 순위'), ('product_rank', '품목 내 국가 순위')],
    }
    country = ('country_name', '국가') if 'country_name' in by_key else ('country_code', '국가 코드')
    product_key = 'product_name' if 'product_name' in by_key or group_by == 'HS6' else 'name'
    fields = rank_fields.get(group_by, rank_fields['PAIR']) + [
        country, ('hs6', 'HS 코드'), (product_key, '품목명'),
        ('growth_outlook', '증감 예상'), ('exp_12m', '최근 12개월 수출액'), ('anchor', '예측 기준월'),
    ]
    # 모델 A의 예측 증감률(growth_pct)은 순위용이라 표에 보이지 않는다. 증감 방향은 모델 B(growth_outlook)로 보인다.
    units = {'exp_12m': 'USD'}
    return [{**by_key[key], 'label': label, 'unit': units.get(key, by_key[key].get('unit'))}
            for key, label in fields if key in by_key]


def result_filename(payload, store=None):
    conditions=payload.get('conditions') or {}
    period=conditions.get('period') or payload.get('meta',{}).get('period') or {}
    parts=[f"{period['start'][:7]}~{period['end'][:7]}"] if period.get('start') and period.get('end') else []
    codes=conditions.get('country_codes') or []
    names={}
    if store is not None and codes:
        country=store.t('country')
        names={r['country_code']:r['name_ko'] for r in store.rows(
            select(country.c.country_code,country.c.name_ko).where(country.c.country_code.in_(codes)))}
    parts.extend([names.get(c,c) for c in codes] or ['전체국가'])
    parts.extend(h['hs_code'] for h in conditions.get('hs_items',[]))
    flow={'EXPORT':'수출','IMPORT':'수입'}.get(conditions.get('trade_flow'))
    if flow: parts.append(flow)
    created=payload['snapshot_created_at']
    local=created.replace(tzinfo=created.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=9)))
    prefix=re.sub(r'[^\w~.-]+','_','_'.join(parts))[:140]
    return f"{prefix}_{local:%Y%m%d_%H%M%S}.xlsx"


def analysis_conditions(conditions):
    """Keep public analysis fields separate from report/execution metadata in stored results."""
    from backend.app.api.schemas import Conditions
    require(isinstance(conditions,dict),'저장된 분석 조건 형식을 확인하세요.')
    return {key:value for key,value in conditions.items() if key in Conditions.model_fields}


def result_table(store, uid, rows, meta, conditions=None, sql=None, columns=None):
    rows=clean(rows)
    keys=list(rows[0]) if rows else []
    columns=columns or [{'key':k,'label':LABELS.get(k,k),'type':'number' if isinstance(rows[0][k],(int,float)) or k.endswith(('_usd','_pct')) else 'string',
                        'unit':'USD' if k.endswith('_usd') else '%' if k.endswith('_pct') else None} for k in keys]
    columns=display_columns(columns,rows,meta)
    payload={'columns':columns,'rows':rows,'meta':clean(meta),'conditions':clean(analysis_conditions(conditions or {})),'sql':sql or [],'total_rows':len(rows)}
    identity=save_snapshot(store,uid,'RESULT',payload)
    return {'result_id':identity,'columns':columns,'rows':rows,'total_rows':len(rows),
            'page':page_info(1,max(1,len(rows)),len(rows)),'meta':meta}


def get_table(store, uid, identity, page=1, size=20):
    p=read_snapshot(store,uid,identity,'RESULT')
    return {'result_id':identity,'columns':p['columns'],'rows':p['rows'][(page-1)*size:page*size],
            'total_rows':p['total_rows'],'page':page_info(page,size,p['total_rows']),'meta':p['meta']}
