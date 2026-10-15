"""매월 발표일 변경분 수집 → RAW 검증/변환 → un_data만 교체 → 정상 점검일 저장."""
import argparse
import csv
import hashlib
import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from filelock import FileLock
from sqlalchemy import text, select, func
from dotenv import dotenv_values
from backend.app.db.session import make_engine, session_scope
from backend.app.db.crud import CRUD
from backend.app.models.un_data import UnData
from backend.app.scheduler.collectors.un_data.api import Client,discover,collect,validate_config
from backend.app.scheduler.collectors.un_data.transform import transform
from backend.app.scheduler.collectors.un_data.pipeline import HEAD, require
from backend.app.scheduler.jobs.un_data_load import read_rows,replace_scopes,preflight,parents,verify,DEFAULT_CSV

ROOT=Path(__file__).resolve().parents[4]
DEFAULT_SCOPE=ROOT/'data/un_data/scope.json'
DEFAULT_STATE=ROOT/'data/un_data/runtime'
KST=ZoneInfo('Asia/Seoul')
log=logging.getLogger('un_data_refresh')


def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            json.dump(obj,f,ensure_ascii=False,indent=2,default=str);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def write_csv(path,head,rows):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(head);w.writerows(rows)


@contextmanager
def db_lock(engine):
    if engine.dialect.name!='mysql':
        yield;return
    # 데이터 테이블 기록 없이 연결 단위 advisory lock으로 다른 호스트의 중복 실행도 차단.
    with engine.connect() as connection:
        acquired=connection.scalar(text("SELECT GET_LOCK('un_data_monthly_refresh',0)"))
        if acquired!=1:raise RuntimeError('다른 UN 갱신이 실행 중입니다')
        try:yield
        finally:connection.execute(text("SELECT RELEASE_LOCK('un_data_monthly_refresh')"))


# Original successful collection initial_20261015: checkpoint + SUCCESS summary,
# matching delivered CSV and scope SHA256. Never derive this date from CSV periods.
SNAPSHOT = {
    'last_successful_check_date': '2026-10-15',
    'source_run': 'initial_20261015',
    'csv_sha256': '485985a82ca11825140bcbf12bad7a7a55f5fc69c21d894211384105b4ea621a',
    'scope_sha256': '71e1c9f769a3effa7d29d603eaecc48e78f2da1d26aa7b05e30c9d041213517a',
    'rows': 76191,
}


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
    validate_config(json.loads(Path(scope_path).read_text()))
    rows = read_rows(csv_path)
    require(len(rows) == SNAPSHOT['rows'], '스냅샷 행 수 불일치')
    state_dir = Path(state_dir); state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(state_dir/'refresh.lock'), timeout=0), db_lock(engine):
        checkpoint_path = state_dir/'checkpoint.json'
        require(not checkpoint_path.exists(), '체크포인트가 이미 존재합니다. 초기 적재 대신 갱신을 실행하세요')
        preflight(engine)
        with session_scope(engine) as session:
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
        save(checkpoint_path, checkpoint)
        log.info('CSV 초기 적재 및 체크포인트 연결 완료: %d행, 정상 점검일 %s', len(rows),
                 checkpoint['last_successful_check_date'])
        return checkpoint


def run(engine,client,scope_path=DEFAULT_SCOPE,state_dir=DEFAULT_STATE,today=None):
    today=today or datetime.now(KST).date()
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    # 동일 날짜 재조회로 날짜 경계의 추가 발표를 다음 점검에서 다시 포함합니다.
    with FileLock(str(state_dir/'refresh.lock'),timeout=0),db_lock(engine):
        preflight(engine)
        cfg=json.loads(Path(scope_path).read_text());validate_config(cfg)
        scope_hash=hashlib.sha256(Path(scope_path).read_bytes()).hexdigest()
        target_hash=hashlib.sha256(engine.url.render_as_string(hide_password=True).encode()).hexdigest()
        checkpoint_path=state_dir/'checkpoint.json'
        checkpoint=json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else None
        require(checkpoint is not None, '초기 체크포인트가 없습니다. --initialize-csv를 먼저 실행하세요')
        if checkpoint:
            require(checkpoint['scope_sha256']==scope_hash,'대상 범위 변경: 별도 초기 재수집 필요')
            require(checkpoint['target_sha256']==target_hash,'다른 DB의 체크포인트 사용 금지')
        start=date.fromisoformat(checkpoint['last_successful_check_date']) if checkpoint else date(2022,1,1)
        require(start<=today,'실행일이 마지막 정상 점검일보다 이전입니다')
        run_dir=state_dir/'runs'/(datetime.now(KST).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
        run_dir.mkdir(parents=True)
        manifest={'status':'RUNNING','published_from':str(start),'published_to':str(today),'scope_sha256':scope_hash}
        save(run_dir/'manifest.json',manifest)
        try:
            datasets=discover(client,cfg,start,today,run_dir)
            save(run_dir/'datasets.json',datasets)
            raw=collect(client,cfg,datasets,run_dir)
            if raw:
                rawhead=list(raw[0]);require(all(list(r)==rawhead for r in raw),'RAW 컬럼 구성 변경')
                write_csv(run_dir/'raw.csv',rawhead,[[r[h] for h in rawhead] for r in raw])
            else:save(run_dir/'raw_empty.json',[])
            values,lineage,adjustments,problems=transform(raw,cfg)
            save(run_dir/'rejected.json',problems)
            require(not problems,'원본 금액/평가 기준 이상: 적재하지 않음')
            write_csv(run_dir/'un_data.csv',HEAD,values)
            rows=read_rows(run_dir/'un_data.csv') if values else []
            scopes={(r['freqCode'],date(int(str(r['period'])[:4]),int(str(r['period'])[4:]) if r['freqCode']=='M' else 1,1),
                     cfg['country_map'][str(r['reporterCode'])]) for r in datasets}
            products={c:set() for c in cfg['country_map'].values()}
            for reporter,hs in cfg['target_pairs']:products[cfg['country_map'][reporter]].add(hs)
            save(run_dir/'lineage.json',lineage);save(run_dir/'valuation_adjustments.json',adjustments)
            result=replace_scopes(engine,rows,scopes,products)
            manifest.update(status='DB_VERIFIED',datasets=len(datasets),raw_rows=len(raw),un_data_rows=result,
                            request_count=client.request_count)
            save(run_dir/'manifest.json',manifest)
            # DB commit 이후 실패해도 점검일을 앞당기지 않아 다음 실행이 같은 범위를 재조회/재교체.
            save(checkpoint_path,{'last_successful_check_date':str(today),'run_dir':run_dir.name,
                                 'scope_sha256':scope_hash,'target_sha256':target_hash})
            log.info('UN 갱신 완료: 데이터셋 %d, RAW %d행, un_data %d행',len(datasets),len(raw),result)
            return manifest
        except Exception:
            manifest['status']='FAILED_OR_CHECKPOINT_PENDING';save(run_dir/'manifest.json',manifest)
            raise


def build_scheduler(job):
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    scheduler=BlockingScheduler(timezone=KST)
    scheduler.add_job(job,CronTrigger(day=1,hour=3,minute=0,timezone=KST),id='un_data_monthly',
                      max_instances=1,coalesce=True,misfire_grace_time=86400)
    return scheduler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',type=Path,default=DEFAULT_SCOPE)
    parser.add_argument('--state-dir',type=Path,default=DEFAULT_STATE)
    parser.add_argument('--api-env',type=Path,help='UN_COMTRADE_API_KEY 또는 UN 값을 읽을 별도 설정 파일')
    parser.add_argument('--schedule',action='store_true',help='시작 시 1회 점검 후 매월 1일 03:00 KST 실행')
    parser.add_argument('--initialize-csv',action='store_true',help='전달 CSV 적재·검증 후 초기 체크포인트 연결 (API 호출 없음)')
    parser.add_argument('--csv',type=Path,default=DEFAULT_CSV,help='초기 적재할 전달 CSV')
    args=parser.parse_args();logging.basicConfig(level=logging.INFO)
    if args.initialize_csv and args.schedule:parser.error('--initialize-csv와 --schedule은 함께 사용할 수 없습니다')
    values={**(dotenv_values(args.api_env) if args.api_env else {}),**os.environ}
    key=values.get('UN_COMTRADE_API_KEY') or values.get('UN')
    engine=make_engine()  # SKN 공통 MYSQL_* 환경설정만 사용
    def execute():return run(engine,Client(key),args.scope,args.state_dir)
    try:
        if args.initialize_csv:
            initialize_csv(engine,args.csv,args.scope,args.state_dir)
        elif args.schedule:
            scheduler=build_scheduler(execute)
            try:execute()
            except Exception:log.exception('시작 시 UN 갱신 실패: 점검일 유지, 다음 월 재시도 또는 수동 재실행')
            scheduler.start()
        else:execute()
    finally:engine.dispose()

if __name__=='__main__':main()
