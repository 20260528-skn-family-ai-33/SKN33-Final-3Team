"""UN API 원본을 9열 un_data로 변환. API/DB 연결은 수행하지 않습니다."""
from datetime import date
import re
from backend.app.scheduler.collectors.un_data.pipeline import VER, HEAD, amount as value, require, share

def scalar(x):return '' if x is None else str(x)

def scope(r):return (r['freqCode'],str(r['period']),str(r['reporterCode']))

# RAW를 세계·한국 쌍으로 묶어 9컬럼 실적으로 변환하고, 계보·보정·이상·누락 기록을 만듭니다.
def transform(raw,cfg,missing=None):
    grouped={};lineage=[]; adjustments=[]
    target_pairs={tuple(pair) for pair in cfg['target_pairs']}
    for i,r in enumerate(raw,2):
        f,p,reporter=scope(r);hs=r['cmdCode']
        require(f in ['M','A'] and len(p)==(6 if f=='M' else 4),'period/frequency')
        dt=date(int(p[:4]),int(p[4:]) if f=='M' else 1,1).isoformat()
        require(r['refYear']==p[:4] and int(r['refMonth'])==(int(p[4:]) if f=='M' else 52) and r['refPeriodId']==dt.replace('-',''),'period fields')
        require(bool(re.fullmatch('[0-9]{6}', hs)) and r['classificationCode']=='H6','HS2022-only classification')
        require(int(p[:4]) >= cfg['start_year'], 'period before start_year')
        require((reporter, hs) in target_pairs, 'country/product scope')
        require(reporter in cfg['country_map'] and reporter in cfg['reporters'][f],'reporter scope')
        for field,expected in {'typeCode':'C','flowCode':'M','partner2Code':'0','customsCode':'C00','motCode':'0','mosCode':'0','aggrLevel':'6','isLeaf':'True','isOriginalClassification':'True','classificationSearchCode':'HS'}.items():
            require(r[field]==expected,'raw constraint '+field)
        partner=r['partnerCode'];require(partner in ['0','410'],'partner scope')
        key=(f,dt,cfg['country_map'][reporter],hs,VER[r['classificationCode']]);pair=grouped.setdefault(key,{})
        require(partner not in pair,'duplicate raw key');pair[partner]=(r,i)
    result=[];problems=[]
    for key,pair in sorted(grouped.items()):
        w=pair.get('0');k=pair.get('410');reference=(w or k)[0]
        primary=value(reference['primaryValue'])
        basis=next((b for b in ['CIF','FOB'] if primary is not None and value(reference[b.lower()+'value'])==primary),None)
        if basis is None:problems.append(list(key)+['VALUATION_UNKNOWN']);continue
        world=value(w[0]['primaryValue']) if w else None
        korea=value(k[0][basis.lower()+'value']) if k else None
        if world is None and korea is None:problems.append(list(key)+['NO_AMOUNT']);continue
        if world is not None and korea is not None and korea>world:problems.append(list(key)+['KOREA_EXCEEDS_WORLD']);continue
        pct=share(world,korea)
        result.append(list(key)+[world,korea,pct,basis])
        if missing is not None and korea is None:
            missing.append(dict(zip(HEAD[:5],key), reason='KOREA_ROW_MISSING' if k is None else 'KOREA_SAME_BASIS_AMOUNT_MISSING',
                                valuation_basis=basis, raw_row=k[1] if k else None,
                                primary_value=k[0]['primaryValue'] if k else None,
                                cif_value=k[0]['cifvalue'] if k else None,
                                fob_value=k[0]['fobvalue'] if k else None))
        if k and korea is not None and korea!=value(k[0]['primaryValue']):adjustments.append(list(key)+[basis,k[0]['primaryValue'],korea,korea-value(k[0]['primaryValue'])])
        for partner,(r,i) in pair.items():lineage.append(list(key)+[partner,i,r['primaryValue'],r['cifvalue'],r['fobvalue'],basis])
    return result,lineage,adjustments,problems
