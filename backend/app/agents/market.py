"""D-70 adapter for the ML team's public bundle interface."""
from functools import lru_cache
from hashlib import sha256
from itertools import product
from pathlib import Path
import json
import math

from backend.app.agents.digest import bounded, MAX_DIGEST_ROWS
from backend.app.agents.state import ToolResult, Period
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
    snapshot=(ROOT/settings.get('API_ML_SNAPSHOT_PATH','data/ml/features_snapshot_py311_latest_v2.pkl')).resolve()
    return paths,snapshot


@lru_cache(maxsize=1)
def _load(paths,snapshot,revision):
    from backend.app.ml.two_stage import load_market_bundle, predict_markets
    def checksum(path):
        with path.open('rb') as stream:
            digest=sha256()
            for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
            return digest.hexdigest()
    versions={'model':','.join(checksum(p) for p in paths),'snapshot':checksum(snapshot)}
    return load_market_bundle([str(p) for p in paths],str(snapshot)),predict_markets,versions


def loaded(settings):
    paths,snapshot=model_paths(settings)
    revision=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in (*paths,snapshot))
    return _load(paths,snapshot,revision)


def predict(task,bundle,predict_markets,versions,top=10):
    conditions=task.conditions
    requested=list(product(conditions.country_codes or [None], [p.hs_code for p in conditions.hs_items] or [None]))
    if requested==[(None,None)]: raise ValueError('국가 또는 HS6 품목이 필요합니다.')
    rows=[]; targets=[]; missing=[]; seen=set(); anchors=set()
    for country,hs in requested:
        frame=predict_markets(bundle,country_code=country,hs6=hs,top=top)
        items=frame.to_dict(orient='records')
        if not items:
            missing.append(' · '.join(v for v in (country,hs) if v)); continue
        for r in items:
            r['anchor']=str(r['anchor'])[:10]
            key=(r['anchor'],r['country_code'],r['hs6'])
            if (key in seen or len(r['hs6'])!=6 or not r['hs6'].isascii() or not r['hs6'].isdigit()
                    or (country and r['country_code']!=country) or (hs and r['hs6']!=hs)
                    or not 1<=r['country_rank']<=r['country_size'] or not 1<=r['product_rank']<=r['product_size']
                    or not math.isfinite(r['growth_pct']) or not math.isfinite(r['exp_12m']) or r['exp_12m']<0):
                raise ValueError('예측 결과의 대상·순위·금액이 유효하지 않습니다.')
            seen.add(key); anchors.add(r['anchor']); rows.append(r)
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
    return Analysis(rows,{**info,'missing_targets':missing},ToolResult(status='OK',period=period,source='ML 모델',
        digest=dg,completeness='PARTIAL' if reason else 'COMPLETE',limitations=reason),public,[])
