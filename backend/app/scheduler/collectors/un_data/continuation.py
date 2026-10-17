"""요청 단위 이어받기와 국가 묶음 조회. 성공 응답은 현재 발표 조회 회차 안에서만 재사용합니다."""
import hashlib
import json
from pathlib import Path
from decimal import Decimal
from backend.app.scheduler.collectors.un_data.api import ApiRequestError
from backend.app.scheduler.collectors.un_data.pipeline import require


class RequestBudgetExceeded(ApiRequestError):
    def __init__(self):
        super().__init__('UN 실행 호출 예산 소진: 미처리 요청부터 재개합니다',kind='budget',attempts=0)


# 성공한 HTTP 요청을 원자적으로 저장해, 목록 조회나 품목 수집 중 중단돼도 이어받습니다.
class CachedClient:
    def __init__(self, client, folder, save):
        self.client,self.folder,self.save=client,Path(folder),save
        self.cache_hits=0

    def __getattr__(self,name):
        return getattr(self.client,name)

    def get(self,path,params,out):
        request={'path':path,'params':params}
        digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        cache=self.folder/(digest+'.json')
        if cache.exists():
            saved=json.loads(cache.read_text(encoding='utf-8'),parse_float=Decimal)
            require(saved['request']==request and isinstance(saved['rows'],list),'재개 응답 캐시 불일치')
            rows=saved['rows'];self.cache_hits+=1
        else:
            rows=self.client.get(path,params,out)
            self.save(cache,{'request':request,'rows':rows})
        # 각 실행 폴더에도 사용한 응답의 출처를 남깁니다.
        self.save(out,{'path':path,'params':params,'response':{'data':rows,'count':len(rows)}})
        return rows


# 수집 요청만 묶고 기존 국가·주기·기간별 검증/트랜잭션은 그대로 유지합니다.
class BatchedClient:
    def __init__(self,client,cfg,datasets,folder):
        self.client,self.folder=client,Path(folder)
        self.pairs={tuple(p) for p in cfg['target_pairs']}
        self.groups={}
        for d in datasets:
            self.groups.setdefault((d['freqCode'],str(d['period'])),set()).add(str(d['reporterCode']))

    def __getattr__(self,name):
        return getattr(self.client,name)

    def get(self,path,params,out):
        freq=path.split('/')[2];reporter=params['reporterCode'];hs=params['cmdCode']
        peers=sorted(r for r in self.groups[(freq,params['period'])] if (r,hs) in self.pairs)
        require(reporter in peers,'묶음 조회 대상 불일치')
        start=(peers.index(reporter)//100)*100
        chunk=peers[start:start+100]
        grouped=dict(params,reporterCode=','.join(chunk))
        digest=hashlib.sha256(json.dumps([path,grouped],sort_keys=True).encode()).hexdigest()
        rows=self.client.get(path,grouped,self.folder/(digest+'.json'))
        # 범위를 식별할 수 없는 응답은 임의 분배하지 않고 공통 장애로 중단합니다.
        if len(rows)>=500 or any(
            not isinstance(row,dict) or str(row.get('reporterCode')) not in chunk for row in rows):
            raise ApiRequestError('UN 묶음 응답 범위/건수 오류',kind='response')
        # 국가를 식별할 수 있는 중복·금액 오류는 국가별 검증에서 격리합니다.
        return [row for row in rows if str(row['reporterCode'])==reporter]
