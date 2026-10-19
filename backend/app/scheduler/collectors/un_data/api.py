"""발표일 범위 getDa → 대상 데이터셋 재수집. RAW는 파일로만 보관."""
import json
import time
import re
import logging
from email.utils import parsedate_to_datetime
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import requests
from backend.app.scheduler.collectors.un_data.pipeline import require, VER
from backend.app.scheduler.collectors.un_data.transform import scalar


# 국가·품목·주기와 수입/파트너 설정이 지원 범위에 맞는지 확인합니다.
def validate_config(cfg):
    require(cfg.get('classification_policy','HS2022_original_only')=='HS2022_original_only','HS2022-only policy required')
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


# 여러 범위에 영향을 줄 수 있는 통신·HTTP·응답 형식 장애입니다.
# 원문 응답이나 인증 키 대신 분류·상태 코드·시도 횟수만 전달합니다.
class ApiRequestError(RuntimeError):
    def __init__(self, message, *, kind, status_code=None, attempts=1, retry_info=None):
        super().__init__(message)
        self.kind=kind
        self.status_code=status_code
        self.attempts=attempts
        self.retry_info=retry_info or {}


# 서버의 대기 안내는 초 또는 HTTP 날짜로 읽고, 긴 대기는 줄여서 재호출하지 않습니다.
def retry_details(response):
    headers=response.headers
    raw=headers.get('Retry-After')
    seconds=None
    if isinstance(raw,str):
        if re.fullmatch(r'[0-9]+',raw.strip()):
            if len(raw.strip()) > 12:
                raise ApiRequestError('UN API Retry-After 범위 초과: 수동 확인 필요', kind='response', status_code=response.status_code)
            seconds=int(raw.strip())
        else:
            try:
                stamp=parsedate_to_datetime(raw)
                if stamp.tzinfo is not None:
                    seconds=max(0,(stamp-datetime.now(timezone.utc)).total_seconds())
            except (ValueError,TypeError,OverflowError):
                pass
    info={'retry_after_seconds':seconds}
    for name in ('x-ratelimit-limit','x-ratelimit-remaining',
                 'x-ratelimit-operation-limit','x-ratelimit-operation-remaining'):
        value=headers.get(name)
        if isinstance(value,str) and re.fullmatch(r'[0-9]{1,12}',value):info[name]=int(value)
    return info


# APIM quota는 403일 수 있습니다. 헤더만으로 인증 오류를 quota로 바꾸지 않습니다.
def quota_response(response):
    if response.status_code != 403:
        return False
    try:
        body = json.loads(response.text)
    except (ValueError, TypeError):
        return False
    if not isinstance(body, dict):
        return False
    message = body.get('message', '')
    return (body.get('statusCode') in (403, '403') and isinstance(message, str)
            and re.fullmatch(r'(?i)Out of call volume quota\. Quota will be replenished in [0-9:. ]+\.?', message.strip()) is not None
            and retry_details(response)['retry_after_seconds'] is not None)


# UN API 요청 간격·재시도·응답 검증과 원본 응답 파일 저장을 담당합니다.
class Client:
    def __init__(self, api_key=None, session=None, interval=2.0):
        self.api_key=api_key
        self.session=session or requests.Session()
        self.interval=interval
        self.last=0.0
        self.request_count=0
        self.request_limit=None

    # 네트워크·서버 오류와 429는 제한적으로 재시도하고, 인증 오류는 즉시 중단합니다.
    def get(self,path,params,out):
        base='https://comtradeapi.un.org/'+('data' if self.api_key else 'public')+'/v1/'
        headers={'Ocp-Apim-Subscription-Key':self.api_key} if self.api_key else {}
        last_error=None
        for attempt in range(3):
            # 재시도도 실제 HTTP 호출 예산에 포함합니다. 캐시 재사용은 여기로 오지 않습니다.
            if self.request_limit is not None and self.request_count>=self.request_limit:
                # 재시도 중 예산이 끝나면 API 장애와 대기 정책을 보존합니다.
                if last_error is not None:
                    raise last_error
                from backend.app.scheduler.collectors.un_data.continuation import RequestBudgetExceeded
                raise RequestBudgetExceeded()
            time.sleep(max(0,self.last+self.interval-time.monotonic()))
            self.last=time.monotonic();self.request_count+=1
            try:
                response=self.session.get(base+path,params=params,headers=headers,timeout=(10,60))
            except requests.RequestException:
                last_error=ApiRequestError('UN API 네트워크 실패: 미처리 범위를 보존하고 중단합니다',
                                           kind='network',attempts=attempt+1)
                if attempt==2:raise last_error from None
                continue
            status=response.status_code
            quota = quota_response(response)
            if status==429 or quota:
                info=retry_details(response)
                delay=max(self.interval,(info['retry_after_seconds'] if info['retry_after_seconds'] is not None else 2**(attempt+1))+1)
                info.update(attempt=attempt+1,wait_seconds=delay,will_retry=attempt<2 and delay<=60)
                logging.getLogger(__name__).warning('UN API quota/rate limit: %s',info)
                # 응답 원문·요청 헤더·인증 키는 기록하지 않습니다. 복구 성공 후에도 진단을 남깁니다.
                diagnostic=Path(str(out)+'.retry.jsonl')
                try:
                    diagnostic.parent.mkdir(parents=True,exist_ok=True)
                    with diagnostic.open('a',encoding='utf-8') as handle:
                        handle.write(json.dumps(info,ensure_ascii=False)+'\n')
                except OSError:
                    logging.getLogger(__name__).exception('UN API 재시도 진단 저장 실패')
                    raise ApiRequestError('UN API 호출 한도 및 진단 저장 실패: 중단합니다',kind='storage',
                                          status_code=status,attempts=attempt+1,retry_info=info)
                last_error=ApiRequestError('UN API 호출 한도: 재시도 한도 또는 호출 예산 도달',kind='quota' if quota else 'http',
                                           status_code=status,attempts=attempt+1,retry_info=info)
                if info['will_retry']:
                    time.sleep(delay)
                    continue
                raise ApiRequestError('UN API 호출 한도: 재시도 한도 또는 대기 한도 초과',kind='quota' if quota else 'http',
                                      status_code=status,attempts=attempt+1,retry_info=info)
            # 요청 하나에서만 최대 3회 재시도합니다. 계속 실패하면 다음 범위를 호출하지 않습니다.
            if (status==408 or 500<=status<=599) and attempt<2:
                last_error=ApiRequestError(f'UN API HTTP {status}: 미처리 범위를 보존하고 중단합니다',
                                           kind='http',status_code=status,attempts=attempt+1)
                continue
            if status!=200:
                raise ApiRequestError(f'UN API HTTP {status}: 미처리 범위를 보존하고 중단합니다',
                                      kind='http',status_code=status,attempts=attempt+1)
            try:
                payload=json.loads(response.text,parse_float=Decimal)
            except (ValueError,TypeError):
                raise ApiRequestError('UN API 응답 JSON 오류: 실행을 중단합니다',
                                      kind='response',status_code=status,attempts=attempt+1) from None
            if not (isinstance(payload,dict) and isinstance(payload.get('data'),list)
                    and payload.get('count')==len(payload['data']) and not payload.get('error')):
                raise ApiRequestError('UN API 응답 구조 오류: 실행을 중단합니다',
                                      kind='response',status_code=status,attempts=attempt+1)
            out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps({'path':path,'params':params,'response':payload},ensure_ascii=False,default=str),encoding='utf-8')
            return payload['data']
        raise ApiRequestError('UN API 재시도 소진',kind='network',attempts=3)


# 오류 항목도 국가·주기·기간을 안전하게 식별할 수 있어야 개별 보류할 수 있습니다.
def discovery_scope_key(row,cfg,end):
    freq=row['freqCode'];period=str(row['period']);reporter=str(row['reporterCode'])
    require(freq in ('M','A'),'availability frequency invalid')
    require(re.fullmatch(r'[0-9]{6}' if freq=='M' else r'[0-9]{4}',period),'availability period invalid')
    start=date(int(period[:4]),int(period[4:]) if freq=='M' else 1,1)
    require(2022<=start.year and start<=end,'availability period out of range')
    require(reporter in cfg['reporters'][freq],'availability reporter out of scope')
    return f'{freq}_{period}_{reporter}'


# 발표 목록의 범위별 오류는 별도 반환하고, 식별 불가·조회 장애는 전체 중단합니다.
# errors를 생략한 독립 호출은 오류 발생 시 예외를 유지해 조용한 누락을 막습니다.
def discover(client,cfg,start,end,run_dir,errors=None,excluded=None):
    require(start<=end,'publication range reversed');validate_config(cfg)
    # KST 점검일과 UTC 발표일 경계에서 빠진 발표를 다음 실행이 다시 확인합니다.
    query_start=max(date(2022,1,1),start-timedelta(days=2))
    datasets={};failures={};released_times={}
    for freq in ('M','A'):
        periods=([f'{y}{m:02d}' for y in range(2022,end.year+1) for m in range(1,13) if (y,m)<=(end.year,end.month)]
                 if freq=='M' else [str(y) for y in range(2022,end.year+1)])
        for i in range(0,len(periods),12):
            chunk=periods[i:i+12]
            params={'period':','.join(chunk),'publishedDateFrom':query_start.isoformat(),'publishedDateTo':end.isoformat()}
            rows=client.get(f'getDa/C/{freq}/HS',params,Path(run_dir)/'availability'/f'{freq}_{i}.json')
            require(len(rows)<10000,'availability cap reached')
            for row in rows:
                require(isinstance(row,dict),'availability row invalid')
                require(row['freqCode']==freq and str(row['period']) in chunk,'availability query mismatch')
                reporter=str(row['reporterCode'])
                if reporter not in cfg['reporters'][freq]:continue
                key=discovery_scope_key(row,cfg,end)
                try:
                    original=row['isOriginalClassification']
                    require(isinstance(original,bool),'original classification flag invalid')
                    if not original:continue
                    released=datetime.fromisoformat(row['lastReleased'].replace('Z','+00:00'))
                    if not query_start<=released.date()<=end:continue
                    require(row['classificationCode'] in VER,'unknown HS edition')
                    if key in datasets:
                        require(datasets[key]['classificationCode']==row['classificationCode'],
                                'conflicting original classifications')
                    # 같은 판본의 중복 발표는 응답 순서와 무관하게 가장 최근 시각을 선택합니다.
                    stamp=released.replace(tzinfo=timezone.utc) if released.tzinfo is None else released
                    if key not in released_times or stamp>released_times[key]:
                        datasets[key]=row;released_times[key]=stamp
                except (KeyError,TypeError,ValueError,AttributeError) as error:
                    record=failures.setdefault(key,{'scope':{'freqCode':freq,'period':str(row['period']),
                                                           'reporterCode':reporter},'errors':[]})
                    record['errors'].append({'error_type':type(error).__name__,'error':str(error)})
    # 충돌 범위는 정상처럼 보이는 다른 항목이 있어도 선택하지 않습니다.
    if errors is None:
        require(not failures,'발표 목록 범위 오류: '+', '.join(sorted(failures)))
    else:
        errors.extend(failures[key] for key in sorted(failures))
    selected=[]
    for key in sorted(datasets):
        if key in failures:continue
        row=datasets[key]
        if row['classificationCode']!='H6':
            if excluded is not None:
                excluded.append({'dataset':row,'scope':key,'classificationCode':row['classificationCode'],
                                 'lastReleased':row['lastReleased'],'reason':'HS2022_ONLY'})
            continue
        selected.append(row)
    return selected


# 선택한 데이터셋의 세계·한국 수입 RAW를 수집하며, 응답 범위·판본·중복을 검사합니다.
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
