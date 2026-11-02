import secrets
from datetime import timedelta, timezone
import re
from sqlalchemy import select
from backend.app.api.common import now, clean, ApiError, page_info, require

LABELS={'amount_usd':'수출액','export_amount_usd':'수출액','import_amount_usd':'수입액',
        'previous_amount_usd':'비교 기간 수출액','comparison_pct':'비교 증감률',
        'mom_pct':'전월 대비','yoy_pct':'전년 대비','export_country_count':'수출 국가 수',
        'top_country_share_pct':'최대 국가 비중','month':'기준월','period':'기간','country':'국가',
        'country_code':'국가 코드','share_pct':'비중','rank':'순위','rank_change':'순위 변화',
        'rank_status':'순위 상태','previous_year_export_usd':'전년 수출액','previous_year_import_usd':'전년 수입액','growth_pct':'증감률',
        'current_amount_usd':'현재 수출액','baseline_amount_usd':'비교 수출액','change_amount_usd':'증감액',
        'growth_status':'증감 상태','severity':'변화 수준','hs_item':'품목','one_off':'일회성 판정','label':'구분'}


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
        payload={**payload,'columns':display_columns(payload['columns'],payload['rows']),
                 'snapshot_created_at':row['created_at']}
    return payload


def display_columns(columns, rows):
    if not any(r.get('hs_item') for r in rows):
        return [c for c in columns if c['key']!='hs_item']
    return columns


def result_filename(payload):
    conditions=payload.get('conditions') or {}
    period=conditions.get('period') or payload.get('meta',{}).get('period') or {}
    parts=[f"{period['start'][:7]}~{period['end'][:7]}"] if period.get('start') and period.get('end') else []
    parts.extend(conditions.get('country_codes') or ['전체국가'])
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
    columns=display_columns(columns,rows)
    payload={'columns':columns,'rows':rows,'meta':clean(meta),'conditions':clean(analysis_conditions(conditions or {})),'sql':sql or [],'total_rows':len(rows)}
    identity=save_snapshot(store,uid,'RESULT',payload)
    return {'result_id':identity,'columns':columns,'rows':rows,'total_rows':len(rows),
            'page':page_info(1,max(1,len(rows)),len(rows)),'meta':meta}


def get_table(store, uid, identity, page=1, size=20):
    p=read_snapshot(store,uid,identity,'RESULT')
    return {'result_id':identity,'columns':p['columns'],'rows':p['rows'][(page-1)*size:page*size],
            'total_rows':p['total_rows'],'page':page_info(page,size,p['total_rows']),'meta':p['meta']}
