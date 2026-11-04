"""D-70 adapter for the ML team's public bundle interface."""
from functools import lru_cache
from hashlib import sha256
from itertools import product
from pathlib import Path
import json
import math

from backend.app.agents.digest import bounded, MAX_DIGEST_ROWS
from backend.app.agents.state import DEFAULT_MARKET_TOP, MAX_MARKET_ROWS, ToolResult, Period
from backend.app.agents.templates import Analysis, api_conditions
from backend.app.api.config import ROOT


def model_paths(settings):
    configured=settings.get('API_ML_MODEL_PATHS','')
    root=(ROOT / settings.get('API_ML_MODEL_DIRECTORY','data/ml/monthly_models')).resolve()
    if configured: paths=tuple((ROOT / p.strip()).resolve() for p in configured.split(',') if p.strip())
    else:
        records=[json.loads(p.read_text(encoding='utf-8')) for p in root.glob('model_a_two_stage_*.registry.json')]
        ready=[r for r in records if r.get('status')=='READY' and r.get('model_key')=='model_a']
        if not ready: raise FileNotFoundError('배포된 월별 모델 레지스트리가 없습니다.')
        chosen=max(ready,key=lambda r:(r['data_info']['trade_month'],r['created_at']))
        path=(root/chosen['artifact_path']).resolve()
        if not path.is_relative_to(root): raise ValueError('모델 경로가 배포 디렉터리 밖입니다.')
        paths=(path,)
    return paths


@lru_cache(maxsize=1)
def _load(paths,revision):
    from backend.app.ml.two_stage import load_market_bundle, predict_markets
    def checksum(path):
        with path.open('rb') as stream:
            digest=sha256()
            for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
            return digest.hexdigest()
    versions={'model':','.join(checksum(p) for p in paths)}
    return load_market_bundle([str(p) for p in paths]),predict_markets,versions


def loaded(settings):
    paths=model_paths(settings)
    revision=tuple((info.st_size,info.st_mtime_ns) for info in (p.stat() for p in paths))
    return _load(paths,revision)


def hs6_by_exports(bundle, prefix, countries):
    rows=bundle['predictions']
    rows=rows[rows.hs6.str.startswith(prefix)]
    if countries:
        rows=rows[rows.country_code.isin(countries)]
    return rows.groupby('hs6').exp_12m.sum().sort_values(ascending=False,kind='stable').index.tolist()


def model_codes(store, items):
    """요청 HS6 품목(hs_code·hs_ver) → 모델의 비교용 코드. 판이 바뀌며 묶인 코드는 묶음 대표 코드(group_code)로
    학습돼 있다(ml.features.hs_key_map과 같은 규칙: HS2022는 new_code, HS2017은 old_code로 찾는다). 대응표에 없으면 자기 코드다."""
    from sqlalchemy import select
    from backend.app.api.common import ApiError
    try: t=store.t('hs_code_correlation')
    except ApiError as exc:
        if exc.code!='FEATURE_NOT_READY': raise
        return {}   # 대응표가 아직 없으면 묶인 코드 없이 요청 코드로 조회한다
    found={}
    for version,column in (('HS2022',t.c.new_code),('HS2017',t.c.old_code)):
        codes=sorted({p.hs_code for p in items if p.hs_ver==version})
        if not codes: continue
        rows=store.rows(select(column.label('code'),t.c.group_code).where(
            t.c.new_ver=='HS2022',t.c.old_ver=='HS2017',column.in_(codes)).distinct())
        found.update({r['code']:r['group_code'] for r in rows})
    return found


def predict(task,bundle,predict_markets,versions,top=DEFAULT_MARKET_TOP,codes=None):
    """codes: 요청 HS6 → 모델 비교용 코드(model_codes). 결과 행은 사용자가 요청한 코드로 돌려준다."""
    codes=codes or {}
    conditions=task.conditions
    requested=list(product(conditions.country_codes or [None], [p.hs_code for p in conditions.hs_items] or [None]))
    if requested==[(None,None)]: raise ValueError('국가 또는 HS6 품목이 필요합니다.')
    pair_query = all(requested[0])
    per_target_limit = 1 if pair_query else min(top, MAX_MARKET_ROWS // len(requested))
    rows=[]; targets=[]; missing=[]; seen=set(); anchors=set()
    merged=[]
    for country,hs in requested:
        model_hs=codes.get(hs,hs) if hs else None
        if model_hs!=hs and hs not in merged: merged.append(hs)
        frame=predict_markets(bundle,country_code=country,hs6=model_hs,top=per_target_limit)
        items=frame.to_dict(orient='records')
        if not items:
            missing.append(' · '.join(v for v in (country,hs) if v)); continue
        for r in items:
            r['anchor']=str(r['anchor'])[:10]
            if hs and r['hs6']==model_hs: r['hs6']=hs   # 묶음 대표 코드로 예측한 값을 요청한 코드로 보인다
            key=(r['anchor'],r['country_code'],r['hs6'])
            if (key in seen or len(r['hs6'])!=6 or not r['hs6'].isascii() or not r['hs6'].isdigit()
                    or (country and r['country_code']!=country) or (hs and r['hs6']!=hs)
                    or not 1<=r['country_rank']<=r['country_size'] or not 1<=r['product_rank']<=r['product_size']
                    or not math.isfinite(r['growth_pct']) or not math.isfinite(r['exp_12m']) or r['exp_12m']<0):
                raise ValueError('예측 결과의 대상·순위·금액이 유효하지 않습니다.')
            seen.add(key); anchors.add(r['anchor'])
        if country and not hs:
            items.sort(key=lambda row: (row['country_rank'], row['hs6']))
        elif hs and not country:
            items.sort(key=lambda row: (row['product_rank'], row['country_code']))
        items = items[:per_target_limit]
        rows.extend(items)
        targets.append({'country_code':country,'hs6':hs,'items':items[:min(10,max(1,MAX_DIGEST_ROWS//len(requested)))]})
    public=api_conditions(conditions)
    if not rows: return Analysis([],{},ToolResult(status='NO_DATA',error='요청한 대상은 예측 대상이 아닙니다.'),public,[])
    if len(anchors)!=1: raise ValueError('예측 기준월이 서로 다릅니다.')
    anchor=next(iter(anchors))[:7]
    period=Period(start=anchor+'-01',end=anchor+'-01')
    info={'kind':'PREDICTION','anchor':anchor,'model_version':versions['model'],'snapshot_month':anchor,'hs_ver':'HS2022',
          'group_by':'PAIR' if all(requested[0]) else 'COUNTRY' if requested[0][0] else 'HS6'}
    dg=bounded({**info,'period':{'start':anchor,'end':anchor},'trade_flow':'EXPORT','unit':'USD','source':'ML 모델',
             'latest_available_month':anchor,'rows_total':len(rows),'targets':targets[:MAX_DIGEST_ROWS],
             'truncated':sum(len(t['items']) for t in targets[:MAX_DIGEST_ROWS])<len(rows)})
    reason=[f'{len(missing)}개 대상은 예측 대상이 아닙니다.'] if missing else []
    # 묶음 대표 코드로 조회한 것은 결과가 빠진 것이 아니다. 안내는 조건 확인이 적용 조건(applied_defaults)에 남긴다.
    if not pair_query and per_target_limit < top:
        reason.append(f'결과표 최대 {MAX_MARKET_ROWS}행 범위에서 대상마다 최대 {per_target_limit}개를 조회했습니다.')
    return Analysis(rows,{**info,'missing_targets':missing,'merged_codes':{hs:codes[hs] for hs in merged}},ToolResult(status='OK',period=period,source='ML 모델',
        digest=dg,completeness='PARTIAL' if reason else 'COMPLETE',limitations=reason),public,[])
