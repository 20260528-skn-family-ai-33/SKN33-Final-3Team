"""매월 발표일 변경분 수집 → RAW 검증/변환 → un_data만 교체 → 정상 점검일 저장."""
import argparse
import csv
import hashlib
import json
import logging
import os
import uuid
from datetime import date,datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from filelock import FileLock
from sqlalchemy import select, func
from dotenv import dotenv_values
from backend.app.db.session import make_engine
from backend.app.db.crud import CRUD
from backend.app.scheduler.collectors.un_data.locking import db_lock
from backend.app.models.un_data import UnData
from backend.app.scheduler.collectors.un_data.api import ApiRequestError,Client,discover,collect,validate_config,discovery_scope_key
from backend.app.scheduler.collectors.un_data.transform import transform
from backend.app.scheduler.collectors.un_data.pipeline import HEAD, VER, require
from backend.app.scheduler.jobs.un_data_load import read_rows,to_rows,replace_scopes,preflight,parents,verify,DEFAULT_CSV

ROOT=Path(__file__).resolve().parents[4]
DEFAULT_SCOPE=ROOT/'data/un_data/scope.json'
DEFAULT_STATE=ROOT/'data/un_data/runtime'
KST=ZoneInfo('Asia/Seoul')
log=logging.getLogger('un_data_refresh')


# JSON을 UTF-8 임시 파일에 완전히 쓴 후 교체하여 중간 내용이 상태 파일로 남지 않게 합니다.
def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            json.dump(obj,f,ensure_ascii=False,indent=2,default=str);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


# 실패 내용을 기록하다 다시 실패하면 원래 작업 예외를 최상위로 유지하고 저장 오류를 연결합니다.
def save_error_record(path,obj,original_error,manifest):
    try:
        save(path,obj)
    except Exception as recording_error:
        manifest.setdefault('recording_errors',[]).append({
            'path':str(path),'error_type':type(recording_error).__name__,'error':str(recording_error)})
        log.exception('UN 오류 기록 저장 실패 (%s). 원래 작업 오류 유지: %s: %s',
                      path,type(original_error).__name__,original_error)
        raise original_error from recording_error


# 수집·변환 결과를 확인용 CSV로 보관합니다. 이 함수는 DB에 쓰지 않습니다.
def write_csv(path,head,rows):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(head);w.writerows(rows)


# Original successful collection initial_20261015: checkpoint + SUCCESS summary,
# matching delivered CSV and scope SHA256. Never derive this date from CSV periods.
SNAPSHOT = {
    'last_successful_check_date': '2026-10-15',
    'source_run': 'initial_20261015',
    'csv_sha256': '485985a82ca11825140bcbf12bad7a7a55f5fc69c21d894211384105b4ea621a',
    'scope_sha256': '71e1c9f769a3effa7d29d603eaecc48e78f2da1d26aa7b05e30c9d041213517a',
    'rows': 76191,
}


# 전달 스냅샷을 검증·적재·대조한 뒤 확인된 정상 점검일로 초기 체크포인트를 연결합니다.
def initialize_csv(engine, csv_path=DEFAULT_CSV, scope_path=DEFAULT_SCOPE,
                   state_dir=DEFAULT_STATE, today=None):
    """검증된 전달 스냅샷 적재 후 대상 DB 전용 초기 체크포인트 생성."""
    today = today or datetime.now(KST).date()
    require(date.fromisoformat(SNAPSHOT['last_successful_check_date']) <= today,
            '스냅샷 정상 점검일보다 이전에는 초기화할 수 없습니다')
    require(hashlib.sha256(Path(csv_path).read_bytes()).hexdigest() == SNAPSHOT['csv_sha256'],
            '전달 스냅샷 CSV 해시 불일치')
    scope_hash = hashlib.sha256(Path(scope_path).read_bytes()).hexdigest()
    require(scope_hash == SNAPSHOT['scope_sha256'], '전달 스냅샷 수집 범위 해시 불일치')
    validate_config(json.loads(Path(scope_path).read_text(encoding='utf-8')))
    rows = read_rows(csv_path)
    require(len(rows) == SNAPSHOT['rows'], '스냅샷 행 수 불일치')
    state_dir = Path(state_dir); state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(state_dir/'refresh.lock'), timeout=0), db_lock(engine) as guard:
        checkpoint_path = state_dir/'checkpoint.json'
        require(not checkpoint_path.exists(), '체크포인트가 이미 존재합니다. 초기 적재 대신 갱신을 실행하세요')
        preflight(engine)
        with guard.transaction() as session:
            parents(session, engine, rows)
            count = session.scalar(select(func.count()).select_from(UnData))
            if count == 0:
                CRUD(UnData).upsert_many(session, rows, batch=500)
            else:
                # Allows recovery after DB commit / checkpoint write failure, without stale overwrites.
                require(count == len(rows), '기존 un_data가 스냅샷과 다릅니다. 초기 적재 중단')
            verify(session, rows)
        target_hash = hashlib.sha256(engine.url.render_as_string(hide_password=True).encode()).hexdigest()
        checkpoint = dict(SNAPSHOT, scope_sha256=scope_hash, target_sha256=target_hash,
                          initialization='CSV_DB_VERIFIED')
        guard.check()
        save(checkpoint_path, checkpoint)
        log.info('CSV 초기 적재 및 체크포인트 연결 완료: %d행, 정상 점검일 %s', len(rows),
                 checkpoint['last_successful_check_date'])
        return checkpoint


# 재시도 대상의 범위를 검증하고 국가·주기·기간별 상태/폴더에 사용할 키를 만듭니다.
def dataset_key(dataset, cfg, today):
    """저장된 재시도 대상도 현재 범위 및 경로에 안전한 형식으로 검증합니다."""
    import re
    freq=dataset['freqCode'];period=str(dataset['period']);reporter=str(dataset['reporterCode'])
    require(freq in ('M','A'), '잘못된 재시도 주기')
    require(re.fullmatch(r'[0-9]{6}' if freq=='M' else r'[0-9]{4}',period), '잘못된 재시도 기간')
    start=date(int(period[:4]),int(period[4:]) if freq=='M' else 1,1)
    require(2022<=start.year and start<=today, '재시도 기간 범위 오류')
    require(reporter in cfg['reporters'][freq], '재시도 국가 범위 오류')
    require(dataset['classificationCode'] in VER and dataset['isOriginalClassification'] is True,
            '재시도 HS 판본 오류')
    return f'{freq}_{period}_{reporter}'


# 발표 시각 전체와 HS 판본을 함께 비교합니다. 같은 날짜의 새 발표도 별도 버전입니다.
def release_version(dataset):
    released=dataset.get('lastReleased')
    require(isinstance(released,str) and bool(released), '발표 버전 시각 누락')
    datetime.fromisoformat(released.replace('Z','+00:00'))
    return [dataset['classificationCode'],released]


# 문자열 표기가 달라도 같은 시각이면 같은 버전으로 비교합니다. 무오프셋 시각은 UTC로 취급합니다.
def version_time(version):
    stamp=datetime.fromisoformat(version[1].replace('Z','+00:00'))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


# 데이터셋 하나를 수집→변환→검증→교체합니다. 같은 범위의 품목은 함께 성공하거나 롤백합니다.
def process_dataset(engine, client, cfg, dataset, folder, guard):
    """국가·주기·기간 하나만 수집/검증/교체합니다. 품목 간 부분 적재는 하지 않습니다."""
    raw=collect(client,cfg,[dataset],folder)
    if raw:
        rawhead=list(raw[0]);require(all(list(r)==rawhead for r in raw),'RAW 컬럼 구성 변경')
        write_csv(folder/'raw.csv',rawhead,[[r[h] for h in rawhead] for r in raw])
    else:save(folder/'raw_empty.json',[])
    missing=[]
    values,lineage,adjustments,problems=transform(raw,cfg,missing=missing)
    save(folder/'missing_amounts.json',missing)
    save(folder/'rejected.json',problems)
    require(not problems,'원본 금액/평가 기준 이상: 적재하지 않음')
    write_csv(folder/'un_data.csv',HEAD,values)
    # CSV is an audit artifact; replace_scopes validates these in-memory rows before writing.
    rows=to_rows(values)
    freq=dataset['freqCode'];period=str(dataset['period']);reporter=str(dataset['reporterCode'])
    country=cfg['country_map'][reporter]
    scope=(freq,date(int(period[:4]),int(period[4:]) if freq=='M' else 1,1),country)
    products={country:{hs for r,hs in cfg['target_pairs'] if r==reporter}}
    save(folder/'lineage.json',lineage);save(folder/'valuation_adjustments.json',adjustments)
    result=replace_scopes(engine,rows,{scope},products,guard=guard)
    return {'raw_rows':len(raw),'un_data_rows':result},missing


# 발표 목록과 미해결 대상을 합쳐 범위별로 처리하고, 모두 해결된 경우에만 정상 점검일을 전진합니다.
def run(engine,client,scope_path=DEFAULT_SCOPE,state_dir=DEFAULT_STATE,today=None,collection_factory=None):
    today=today or datetime.now(KST).date()
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    with FileLock(str(state_dir/'refresh.lock'),timeout=0),db_lock(engine) as guard:
        preflight(engine)
        cfg=json.loads(Path(scope_path).read_text(encoding='utf-8'));validate_config(cfg)
        scope_hash=hashlib.sha256(Path(scope_path).read_bytes()).hexdigest()
        target_hash=hashlib.sha256(engine.url.render_as_string(hide_password=True).encode()).hexdigest()
        checkpoint_path=state_dir/'checkpoint.json'
        checkpoint=json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else None
        require(checkpoint is not None, '초기 체크포인트가 없습니다. --initialize-csv를 먼저 실행하세요')
        require(checkpoint['scope_sha256']==scope_hash,'대상 범위 변경: 별도 초기 재수집 필요')
        require(checkpoint['target_sha256']==target_hash,'다른 DB의 체크포인트 사용 금지')
        start=date.fromisoformat(checkpoint['last_successful_check_date'])
        require(start<=today,'실행일이 마지막 정상 점검일보다 이전입니다')
        pending_path=state_dir/'pending_datasets.json'
        pending={'version':1,'scope_sha256':scope_hash,'target_sha256':target_hash,
                 'published_from':str(start),'datasets':{},'completed_versions':{}}
        if pending_path.exists():
            pending=json.loads(pending_path.read_text(encoding='utf-8'))
            require(pending['version']==1,'지원하지 않는 재시도 상태 버전')
            require(pending['scope_sha256']==scope_hash and pending['target_sha256']==target_hash,
                    '다른 DB/범위의 재시도 상태 사용 금지')
            require(isinstance(pending['datasets'],dict),'잘못된 재시도 상태')
            if pending['datasets'] or pending.get('catalog_errors'):
                require(pending['published_from']==str(start),'점검일과 재시도 기준일 불일치: 상태 복구 필요')
            for key,entry in pending['datasets'].items():
                require(key==dataset_key(entry['dataset'],cfg,today),'재시도 대상 키 불일치')
        # 기존 상태 파일에는 성공 버전이 없으므로 첫 실행은 안전하게 재처리합니다.
        completed=pending.setdefault('completed_versions',{})
        require(isinstance(completed,dict),'잘못된 성공 발표 버전 상태')
        for version in completed.values():
            require(isinstance(version,list) and len(version)==2 and version[0] in VER,
                    '잘못된 성공 발표 버전')
            release_version({'classificationCode':version[0],'lastReleased':version[1]})
        # 목록에서 사라진 오류도 정상 항목으로 교정되기 전까지 보류 상태를 유지합니다.
        catalog_errors=pending.setdefault('catalog_errors',{})
        require(isinstance(catalog_errors,dict),'잘못된 발표 목록 오류 상태')
        for key,record in catalog_errors.items():
            require(key==discovery_scope_key(record['scope'],cfg,today),'발표 목록 오류 키 불일치')
        # 성공/실패와 별개로 확인한 최신 시각을 보존해 오류 보류 후에도 과거로 후퇴하지 않습니다.
        known=pending.setdefault('known_versions',{})
        require(isinstance(known,dict),'잘못된 최신 발표 버전 상태')
        for version in known.values():
            require(isinstance(version,list) and len(version)==2 and version[0] in VER,
                    '잘못된 최신 발표 버전')
            release_version({'classificationCode':version[0],'lastReleased':version[1]})
        previous=list(completed.items())+[(key,release_version(entry['dataset']))
                                         for key,entry in pending['datasets'].items()]
        for key,version in previous:
            if key not in known or version_time(version)>version_time(known[key]):known[key]=version
        pending['published_from']=str(start)
        run_dir=state_dir/'runs'/(datetime.now(KST).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
        run_dir.mkdir(parents=True)
        manifest={'status':'RUNNING','published_from':str(start),'published_to':str(today),
                  'scope_sha256':scope_hash,'succeeded':0,'skipped':0,'skipped_stale':0,'failed':0,'raw_rows':0,'un_data_rows':0}
        save(run_dir/'manifest.json',manifest)
        try:
            client=guard.watch(client)
            discovered_errors=[]
            datasets=discover(client,cfg,start,today,run_dir,errors=discovered_errors)
            for record in discovered_errors:
                key=discovery_scope_key(record['scope'],cfg,today)
                catalog_errors[key]=record
                # 기존 대기 항목으로 우회 적재하거나 과거 성공 버전으로 오류를 덮지 않습니다.
                pending['datasets'].pop(key,None);completed.pop(key,None)
                log.warning('UN 발표 목록 범위 보류 (%s): %s',key,record['errors'])
            # 재시도 대상이 이번 발표 조회에 없어도 유지. 새 발표가 있으면 최신 판본으로 교체.
            for dataset in datasets:
                key=dataset_key(dataset,cfg,today)
                incoming=release_version(dataset)
                previous=known.get(key)
                if previous is not None:
                    if version_time(incoming)<version_time(previous):
                        manifest['skipped_stale']+=1
                        log.warning('UN 오래된 발표 무시 (%s): %s < %s',key,incoming[1],previous[1])
                        continue
                    if version_time(incoming)==version_time(previous) and incoming[0]!=previous[0]:
                        catalog_errors[key]={'scope':{field:dataset[field] for field in ('freqCode','period','reporterCode')},
                                             'errors':[{'error_type':'ValueError','error':'동일 발표 시각의 HS 판본 충돌'}]}
                        log.warning('UN 발표 목록 범위 보류 (%s): 동일 발표 시각의 HS 판본 충돌',key)
                        continue
                known[key]=incoming
                catalog_errors.pop(key,None)
                pending['datasets'][key]={'dataset':dataset,'status':'PENDING'}
            for key in catalog_errors:
                pending['datasets'].pop(key,None);completed.pop(key,None)
            # 재조회된 발표와 기존 미처리 대상 중 이미 commit된 같은 버전만 제외합니다.
            for key in list(pending['datasets']):
                success=completed.get(key);incoming=release_version(pending['datasets'][key]['dataset'])
                if success is not None and success[0]==incoming[0] and version_time(success)==version_time(incoming):
                    del pending['datasets'][key]
                    manifest['skipped']+=1
            save(pending_path,pending)  # 어떤 DB 변경보다 먼저 재시도 의도를 영속화
            save(run_dir/'datasets.json',[entry['dataset'] for entry in pending['datasets'].values()])
            save(run_dir/'catalog_errors.json',catalog_errors)
            manifest['catalog_failed']=len(catalog_errors)
            first_error=ValueError(f'미해결 발표 목록 오류 {len(catalog_errors)}개: 정상 점검일 유지') if catalog_errors else None
            all_missing=[]
            manifest['datasets']=len(pending['datasets'])
            if collection_factory is not None:
                client=collection_factory(client,cfg,[entry['dataset'] for entry in pending['datasets'].values()])
            for key in sorted(list(pending['datasets'])):
                guard.check()
                entry=pending['datasets'][key]
                folder=run_dir/'datasets'/key;folder.mkdir(parents=True)
                record={'dataset':entry['dataset'],'status':'RUNNING'}
                # 다른 버전을 쓰기 시작하면 이전 성공 표시는 폐기합니다.
                # 이후 파일 저장 실패 시 이전 버전을 근거로 잘못 건너뛰지 않게 합니다.
                completed.pop(key,None)
                entry.update(status='RUNNING',run_dir=run_dir.name)
                save(pending_path,pending);save(folder/'manifest.json',record)
                try:
                    counts,missing=process_dataset(engine,client,cfg,entry['dataset'],folder,guard)
                except Exception as error:
                    # 공통 API 장애는 메시지 내용이 아닌 예외 유형으로 판별합니다.
                    # 검증된 응답의 범위별 데이터 오류는 아래 실패 기록 후 다음 범위를 처리합니다.
                    guard.check()
                    if isinstance(error,(OSError,ApiRequestError)):
                        raise
                    first_error=first_error or error
                    record.update(status='FAILED',error_type=type(error).__name__,error=str(error))
                    entry.update(status='FAILED',error_type=type(error).__name__,error=str(error))
                    manifest['failed']+=1
                    save_error_record(folder/'manifest.json',record,error,manifest)
                    save_error_record(pending_path,pending,error,manifest)
                    log.warning('UN 범위 갱신 실패 (%s): %s',key,error)
                    continue
                # commit 뒤 기록 저장 실패 시 대상은 pending에 남아 재실행됩니다.
                record.update(status='DB_VERIFIED',**counts)
                save(folder/'manifest.json',record)
                guard.check()
                # commit·검증·실행 기록 완료 후 성공 버전과 대기 목록 제거를 한 파일에 함께 저장합니다.
                completed[key]=release_version(entry['dataset'])
                del pending['datasets'][key]
                save(pending_path,pending)
                manifest['succeeded']+=1
                manifest['raw_rows']+=counts['raw_rows'];manifest['un_data_rows']+=counts['un_data_rows']
                all_missing.extend(missing)
            if first_error is None:
                save(run_dir/'missing_amounts.json',all_missing)
            else:
                save_error_record(run_dir/'missing_amounts.json',all_missing,first_error,manifest)
            manifest.update(status='PARTIAL_FAILED' if first_error else 'DB_VERIFIED',
                            pending=len(pending['datasets']),request_count=client.request_count)
            if first_error is not None:
                save_error_record(run_dir/'manifest.json',manifest,first_error,manifest)
                raise first_error
            save(run_dir/'manifest.json',manifest)
            require(not pending['datasets'] and not catalog_errors,'미해결 재시도/발표 오류 존재: 점검일 전진 금지')
            guard.check()
            save(checkpoint_path,{'last_successful_check_date':str(today),'run_dir':run_dir.name,
                                 'scope_sha256':scope_hash,'target_sha256':target_hash})
            log.info('UN 갱신 완료: 범위 %d, RAW %d행, un_data %d행',manifest['succeeded'],manifest['raw_rows'],manifest['un_data_rows'])
            return manifest
        except Exception as error:
            manifest.update(error_type=type(error).__name__,error=str(error))
            if isinstance(error,ApiRequestError):
                manifest.update(api_error_kind=error.kind,http_status=error.status_code,
                                api_attempts=error.attempts,api_retry_info=error.retry_info,pending=len(pending['datasets']),
                                request_count=client.request_count)
            if manifest['status']!='PARTIAL_FAILED':manifest['status']='FAILED_OR_CHECKPOINT_PENDING'
            try:save(run_dir/'manifest.json',manifest)
            except Exception:log.exception('실패 기록 저장 오류 (원래 오류 유지)')
            raise


# 수동 실행과 스케줄러가 공유하는 진입점으로, 설정을 읽고 종료 시 API·DB 자원을 정리합니다.
def execute_once(scope_path=DEFAULT_SCOPE, state_dir=DEFAULT_STATE, api_env=None, *, resume_now=False):
    """수동 실행과 통합 스케줄러가 공유하는 1회 실행 경로."""
    values={**dotenv_values(api_env or ROOT/'.env', encoding='utf-8'), **os.environ}
    key=values.get('UN_COMTRADE_API_KEY') or values.get('UN')
    engine=make_engine()
    client=Client(key)
    try:
        from backend.app.scheduler.jobs.un_data_resume import run_resumable
        return run_resumable(engine, client, scope_path, state_dir, resume_now=resume_now)
    finally:
        try:client.session.close()
        finally:engine.dispose()


# CLI 옵션에 따라 최초 CSV 적재 또는 API 1회 갱신을 실행합니다. 정기 실행은 통합 스케줄러를 사용합니다.
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',type=Path,default=DEFAULT_SCOPE)
    parser.add_argument('--state-dir',type=Path,default=DEFAULT_STATE)
    parser.add_argument('--api-env',type=Path,help='UN_COMTRADE_API_KEY 또는 UN 값을 읽을 별도 설정 파일')
    parser.add_argument('--resume-now',action='store_true',help='운영자가 확인한 뒤 즉시 재개하고 API 재개 횟수 초기화')
    parser.add_argument('--schedule',action='store_true',help='폐기된 옵션: backend.app.scheduler.main 사용')
    parser.add_argument('--initialize-csv',action='store_true',help='전달 CSV 적재·검증 후 초기 체크포인트 연결 (API 호출 없음)')
    parser.add_argument('--csv',type=Path,default=DEFAULT_CSV,help='초기 적재할 전달 CSV')
    args=parser.parse_args();logging.basicConfig(level=logging.INFO)
    if args.schedule:
        parser.error('--schedule은 통합되었습니다. python -m backend.app.scheduler.main --un-state-dir <같은 상태 경로> --un-api-env <설정 파일>을 사용하세요.')
    if args.initialize_csv:
        engine=make_engine()
        try:initialize_csv(engine,args.csv,args.scope,args.state_dir)
        finally:engine.dispose()
    else:
        result=execute_once(args.scope,args.state_dir,args.api_env,resume_now=args.resume_now)
        log.info("UN 실행 결과: %s",result)
        if result.get("status") in ("FAILED","RETRY_EXHAUSTED"):
            parser.exit(1,"UN 실행 중단: 재개 상태와 오류 기록을 확인하세요.\n")

if __name__=='__main__':main()
