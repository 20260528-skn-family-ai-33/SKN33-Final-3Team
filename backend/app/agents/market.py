"""D-70 adapter for the ML team's public bundle interface."""
from functools import lru_cache
from itertools import product
from pathlib import Path
import json
import math

from backend.app.agents.digest import bounded, MAX_DIGEST_ROWS
from backend.app.agents.state import DEFAULT_MARKET_TOP, MAX_MARKET_ROWS, ToolResult, Period
from backend.app.agents.templates import Analysis, api_conditions
from backend.app.api.config import ROOT


def model_paths(settings, *, _root=None):
    configured=settings.get('API_ML_MODEL_PATHS','')
    root=(ROOT / settings.get('API_ML_MODEL_DIRECTORY','data/ml/monthly_models')).resolve()
    from backend.app.storage.objects import enabled, StorageError
    if enabled(settings.values):
        from backend.app.storage.workspace import model_cache
        if configured:
            raise StorageError('API_ML_MODEL_PATHS must be empty in S3 mode; select the published registry')
        root=_root or model_cache(settings)
        return _published_model_paths(root)
    if configured:
        return tuple((ROOT / p.strip()).resolve() for p in configured.split(',') if p.strip())
    return _registry_model_paths(root)


@lru_cache(maxsize=8)
def _published_model_paths(root):
    # root identifies immutable manifest contents; lease-only ETag changes reuse it.
    return _registry_model_paths(root, published=True)


def _registry_model_paths(root, published=False):
    records=[json.loads(p.read_text(encoding='utf-8')) for p in root.glob('model_a_two_stage_*.registry.json')]
    ready=[r for r in records if r.get('status')=='READY' and r.get('model_key')=='model_a']
    if not ready: raise FileNotFoundError('배포된 월별 모델 레지스트리가 없습니다.')
    chosen=max(ready,key=lambda r:(r['data_info']['trade_month'],r['created_at']))
    path=(root/chosen['artifact_path']).resolve()
    if not path.is_relative_to(root): raise ValueError('모델 경로가 배포 디렉터리 밖입니다.')
    if chosen.get('artifact_sha256'):
        if published:
            manifest=json.loads((root/'.snapshot.json').read_text(encoding='utf-8'))
            actual=manifest[path.relative_to(root).as_posix()]['sha256']
        else:
            from backend.app.storage.objects import file_digest
            actual=file_digest(path)
        if actual != chosen['artifact_sha256']:
            raise ValueError('모델 파일과 READY 레지스트리의 해시가 다릅니다.')
    return (path,)


@lru_cache(maxsize=1)
def _load(paths,revision):
    from backend.app.ml.model_registry import sha256 as checksum
    from backend.app.ml.two_stage import load_market_bundle, predict_markets
    versions={'model':','.join(checksum(p) for p in paths)}
    return load_market_bundle([str(p) for p in paths]),predict_markets,versions


def loaded(settings):
    from backend.app.storage.objects import enabled
    if enabled(settings.values):
        from backend.app.storage.model_cache import model_cache_use
        with model_cache_use(settings) as root:
            paths=model_paths(settings, _root=root)
            revision=tuple((info.st_size,info.st_mtime_ns) for info in (p.stat() for p in paths))
            return _load(paths,revision)
    paths=model_paths(settings)
    revision=tuple((info.st_size,info.st_mtime_ns) for info in (p.stat() for p in paths))
    return _load(paths,revision)


# 증감 예상(모델 B, ml/growth_model.py). 화면에는 방향만 보인다(성장률 숫자는 오차가 커서 쓰지 않는다, ML_USAGE).
OUTLOOK_LABELS={'UP':'증가 예상','DOWN':'감소 예상'}


def growth_path(settings, anchor=None, *, _root=None):
    """모델 B 파일. API_ML_GROWTH_MODEL_PATH가 없으면 월별 모델 폴더에서 고른다(모델 A의 model_paths와 같은 방식).

    모델 A와 같은 기준월(anchor, YYYY-MM)을 먼저 찾는다: 1) 월별 학습(candidate_models.run_growth)의 READY 레지스트리
    (data_info.anchor) → 2) 직접 만든 파일 model_b_growth_<YYYYMM>.joblib(growth_model.build). 같은 달이 없으면
    3) READY 중 최신 → 4) 가장 최근 파일(기준월이 달라 runtime이 쓰지 않는다). 각 단계는 최신 리비전. 하나도 없으면 None.
    """
    root=(ROOT / settings.get('API_ML_MODEL_DIRECTORY','data/ml/monthly_models')).resolve()
    configured=settings.get('API_ML_GROWTH_MODEL_PATH','')
    from backend.app.storage.objects import enabled, StorageError
    if enabled(settings.values):
        from backend.app.storage.workspace import model_cache
        if configured:
            raise StorageError('API_ML_GROWTH_MODEL_PATH must be empty in S3 mode')
        root=_root or model_cache(settings)
        return _published_growth_path(root, anchor)
    if configured: return (ROOT / configured).resolve()
    return _select_growth_path(root, anchor)


@lru_cache(maxsize=16)
def _published_growth_path(root, anchor):
    return _select_growth_path(root, anchor)


def _select_growth_path(root, anchor):
    from backend.app.ml.model_registry import version_key
    stamp=tuple(sorted((p.name,p.stat().st_mtime_ns) for p in root.glob('model_b_growth_*.registry.json')))
    ready=_ready_growth(root,stamp)
    files=sorted((p for p in root.glob('model_b_growth_*.joblib') if version_key(p)),key=version_key)
    month=anchor.replace('-','') if anchor else None
    for found in ([p for a,p in ready if anchor and a==anchor],[p for p in files if month and version_key(p)[0]==month],
                  [p for _,p in ready],files):
        if found: return found[-1]
    return None


@lru_cache(maxsize=4)
def _ready_growth(root,stamp):
    """READY 모델 B 레지스트리 [(기준월 YYYY-MM, 파일)], 리비전 순. stamp(이름·수정 시각)가 같으면 다시 읽지 않는다."""
    from backend.app.ml.model_registry import version_key
    ready=[]
    for name,_ in sorted(stamp,key=lambda item:version_key(item[0]) or ('',0)):
        record=json.loads((root/name).read_text(encoding='utf-8'))
        if record.get('status')=='READY' and record.get('model_key')=='model_b':
            path=(root/record['artifact_path']).resolve()
            if path.is_relative_to(root): ready.append((str(record.get('data_info',{}).get('anchor') or '')[:7],path))
    return tuple(ready)


@lru_cache(maxsize=1)
def _load_growth(path,revision):
    import joblib
    from backend.app.ml.model_registry import sha256 as checksum
    data=joblib.load(path)
    latest=data.get('latest') if isinstance(data,dict) else None
    if latest is None or latest.empty or not {'anchor','country_code','hs6','direction'} <= set(latest.columns):
        raise ValueError('증감 예상 파일에 최신 기준월 예측(latest)이 없습니다.')
    if latest.anchor.nunique()!=1 or not set(latest.direction) <= set(OUTLOOK_LABELS):
        raise ValueError('증감 예상 파일의 기준월 또는 방향 값이 올바르지 않습니다.')
    return {'anchor':str(latest.anchor.iloc[0])[:7],
            'directions':dict(zip(zip(latest.country_code,latest.hs6),latest.direction))},checksum(path)


def growth(settings, anchor=None):
    """(증감 예상 {anchor, directions{(국가, 모델 HS6): UP·DOWN}} 또는 None, 버전, 안내 문구 또는 None).
    파일이 없거나 파일·레지스트리를 읽을 수 없으면 증감 예상 없이 답한다(유망 순위는 모델 A만으로 낸다)."""
    try:
        from contextlib import nullcontext
        from backend.app.storage.objects import enabled
        from backend.app.storage.model_cache import model_cache_use
        with model_cache_use(settings) if enabled(settings.values) else nullcontext(None) as root:
            path=growth_path(settings,anchor,_root=root)
            if path is None or not path.exists():
                return None,'none','증감 예상 모델 파일이 없어 증가·감소 예상은 표시하지 않았습니다.'
            info=path.stat()
            data,version=_load_growth(path,(info.st_size,info.st_mtime_ns))
    except Exception:   # 선택 기능이다. 잘린 파일(EOFError)·깨진 피클 등 어떤 읽기 오류도 유망 순위를 막지 않는다
        return None,'invalid','증감 예상 모델 파일을 읽지 못해 증가·감소 예상은 표시하지 않았습니다.'
    return data,version,None


def hs6_by_exports(bundle, prefix, countries):
    rows=bundle['predictions']
    rows=rows[rows.hs6.str.startswith(prefix)]
    if countries:
        rows=rows[rows.country_code.isin(countries)]
    return rows.groupby('hs6').exp_12m.sum().sort_values(ascending=False,kind='stable').index.tolist()


def model_codes(store, items):
    """요청 HS6 품목(hs_code·hs_ver) → {(코드, 판본): 모델의 비교용 코드}. 판이 바뀌며 묶인 코드는 묶음 대표 코드(group_code)로
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
        found.update({(r['code'],version):r['group_code'] for r in rows})
    return found


def predict(task,bundle,predict_markets,versions,top=DEFAULT_MARKET_TOP,codes=None,directions=None):
    """codes: {(요청 HS6, 판본): 모델 비교용 코드}(model_codes). 결과 행은 사용자가 요청한 코드·판본(hs_ver)으로 돌려준다.
    directions: 모델 A와 같은 기준월의 증감 예상 {(국가, 모델 HS6): UP·DOWN}(growth). 행에 growth_outlook(증가·감소 예상)을 붙인다.
    모델 A의 예측 증감률(growth_pct)은 순위용이라 행에는 남기되 답변 요약(digest)에는 넣지 않는다(화면 방향은 모델 B)."""
    codes=codes or {}
    directions=directions or {}
    conditions=task.conditions
    # 품목은 코드와 판본을 함께 들고 다닌다. 같은 숫자가 두 판본에 있어도(개정 전후 다른 품목) 섞지 않는다.
    requested=list(product(conditions.country_codes or [None], conditions.hs_items or [None]))
    if requested==[(None,None)]: raise ValueError('국가 또는 HS6 품목이 필요합니다.')
    pair_query = all(requested[0])
    per_target_limit = 1 if pair_query else min(top, MAX_MARKET_ROWS // len(requested))
    rows=[]; targets=[]; missing=[]; seen=set(); anchors=set()
    for country,item in requested:
        hs,ver=(item.hs_code,item.hs_ver) if item else (None,'HS2022')
        model_hs=codes.get((hs,ver),hs) if hs else None
        frame=predict_markets(bundle,country_code=country,hs6=model_hs,top=per_target_limit)
        items=frame.to_dict(orient='records')
        if not items:
            missing.append(' · '.join(v for v in (country,hs) if v)); continue
        for r in items:
            r['anchor']=str(r['anchor'])[:10]
            r['growth_outlook']=OUTLOOK_LABELS.get(directions.get((r['country_code'],r['hs6'])))   # 모델 코드로 찾는다
            if hs and r['hs6']==model_hs: r['hs6']=hs   # 묶음 대표 코드로 예측한 값을 요청한 코드로 보인다
            r['hs_ver']=ver   # 품목명은 이 판본으로 찾는다(국가→품목 결과는 모델 코드라 HS2022)
            key=(r['anchor'],r['country_code'],r['hs6'],r['hs_ver'])
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
        targets.append({'country_code':country,'hs6':hs,'items':[{k:v for k,v in r.items() if k!='growth_pct'}
                        for r in items[:min(10,max(1,MAX_DIGEST_ROWS//len(requested)))]]})
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
    # 묶음 대표 코드로 조회한 것은 결과가 빠진 것이 아니다. 안내는 ML 단계가 적용 조건(applied_defaults)에 남긴다.
    if not pair_query and per_target_limit < top:
        reason.append(f'결과표 최대 {MAX_MARKET_ROWS}행 범위에서 대상마다 최대 {per_target_limit}개를 조회했습니다.')
    return Analysis(rows,{**info,'missing_targets':missing},ToolResult(status='OK',period=period,source='ML 모델',
        digest=dg,completeness='PARTIAL' if reason else 'COMPLETE',limitations=reason),public,[])
