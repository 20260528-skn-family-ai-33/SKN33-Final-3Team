"""발표일 범위 getDa → 대상 데이터셋 재수집. RAW는 파일로만 보관."""
import json
import time
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
import requests
from backend.app.scheduler.collectors.un_data.pipeline import require, VER
from backend.app.scheduler.collectors.un_data.transform import scalar


def validate_config(cfg):
    require(cfg['start_year']==2022,'start_year must be 2022')
    require(set(cfg['reporters'])=={'M','A'},'M/A scopes required')
    require(set(cfg['reporters']['M']).isdisjoint(cfg['reporters']['A']),'reporter frequency overlap')
    require(len(set(cfg['country_map'].values()))==len(cfg['country_map']),'country mapping collision')
    for f in ('M','A'):
        require(all(r in cfg['country_map'] for r in cfg['reporters'][f]),'missing country mapping')
    require(all(re.fullmatch('[0-9]{6}',h) for h in cfg['products']),'invalid product')
    require(all(re.fullmatch('[A-Z]{2}',c) for c in cfg['country_map'].values()),'invalid iso2')
    require(len(cfg['target_pairs'])==len({tuple(p) for p in cfg['target_pairs']}),'duplicate target pair')
    require(all(r in cfg['country_map'] and h in cfg['products'] for r,h in cfg['target_pairs']),'invalid target pair')
    require(cfg['partners']==[0,410] and cfg['flow']=='M','only import world/Korea supported')


class Client:
    def __init__(self, api_key=None, session=None, interval=1.1):
        self.api_key=api_key
        self.session=session or requests.Session()
        self.interval=interval
        self.last=0.0
        self.request_count=0

    def get(self,path,params,out):
        base='https://comtradeapi.un.org/'+('data' if self.api_key else 'public')+'/v1/'
        headers={'Ocp-Apim-Subscription-Key':self.api_key} if self.api_key else {}
        for attempt in range(3):
            time.sleep(max(0,self.last+self.interval-time.monotonic()))
            self.last=time.monotonic();self.request_count+=1
            try:
                response=self.session.get(base+path,params=params,headers=headers,timeout=(10,60))
            except requests.RequestException:
                if attempt==2:raise RuntimeError('UN API 네트워크 실패') from None
                continue
            if response.status_code in (401,403,429):raise RuntimeError(f'UN API HTTP {response.status_code}: 인증/할당량 확인 후 재실행')
            if response.status_code>=500 and attempt<2:continue
            if response.status_code!=200:raise RuntimeError(f'UN API HTTP {response.status_code}')
            payload=json.loads(response.text,parse_float=Decimal)
            require(isinstance(payload.get('data'),list) and payload.get('count')==len(payload['data']) and not payload.get('error'),'invalid API envelope')
            out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps({'path':path,'params':params,'response':payload},ensure_ascii=False,default=str))
            return payload['data']
        raise RuntimeError('API retry exhausted')


def discover(client,cfg,start,end,run_dir):
    require(start<=end,'publication range reversed');validate_config(cfg)
    datasets={}
    for freq in ('M','A'):
        periods=([f'{y}{m:02d}' for y in range(2022,end.year+1) for m in range(1,13) if (y,m)<=(end.year,end.month)]
                 if freq=='M' else [str(y) for y in range(2022,end.year+1)])
        for i in range(0,len(periods),12):
            chunk=periods[i:i+12]
            params={'period':','.join(chunk),'publishedDateFrom':start.isoformat(),'publishedDateTo':end.isoformat()}
            rows=client.get(f'getDa/C/{freq}/HS',params,Path(run_dir)/'availability'/f'{freq}_{i}.json')
            require(len(rows)<10000,'availability cap reached')
            for row in rows:
                require(row['freqCode']==freq and str(row['period']) in chunk,'availability query mismatch')
                reporter=str(row['reporterCode'])
                if reporter not in cfg['reporters'][freq] or row['isOriginalClassification'] is not True:continue
                released=date.fromisoformat(row['lastReleased'][:10])
                if not start<=released<=end:continue
                require(row['classificationCode'] in VER,'unknown HS edition')
                key=(freq,str(row['period']),reporter)
                if key in datasets:require(datasets[key]['classificationCode']==row['classificationCode'],'conflicting original classifications')
                datasets[key]=row
    return [datasets[k] for k in sorted(datasets)]


def collect(client,cfg,datasets,run_dir):
    catalog={(r['freqCode'],str(r['period']),str(r['reporterCode'])):r for r in datasets}
    pairs={tuple(p) for p in cfg['target_pairs']};raw=[];seen=set();request_no=0
    for freq,period in sorted({k[:2] for k in catalog}):
        for hs in cfg['products']:
            reporters=sorted(r for f,p,r in catalog if (f,p)==(freq,period) and (r,hs) in pairs)
            for i in range(0,len(reporters),100):
                chunk=reporters[i:i+100]
                params={'period':period,'reporterCode':','.join(chunk),'cmdCode':hs,'flowCode':'M',
                        'partnerCode':'0,410','partner2Code':'0','customsCode':'C00','motCode':'0',
                        'maxRecords':500,'includeDesc':'true'}
                endpoint='get' if client.api_key else 'preview'
                rows=client.get(f'{endpoint}/C/{freq}/HS',params,Path(run_dir)/'responses'/f'{request_no:05d}.json');request_no+=1
                require(len(rows)<500 and len(rows)<=2*len(chunk),'data cap/dimensions exceeded')
                for row in rows:
                    r={k:scalar(v) for k,v in row.items()};key=(r['freqCode'],r['period'],r['reporterCode'])
                    require(key in catalog and key[:2]==(freq,period) and key[2] in chunk and r['cmdCode']==hs,'data query mismatch')
                    require(r['classificationCode']==catalog[key]['classificationCode'],'availability/data edition changed; retry')
                    rawkey=key+(hs,r['classificationCode'],r['partnerCode'])
                    require(rawkey not in seen,'duplicate raw key');seen.add(rawkey);raw.append(r)
    return raw
