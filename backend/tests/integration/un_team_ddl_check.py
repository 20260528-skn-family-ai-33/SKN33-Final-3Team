"""명시한 로컬 테스트 소켓에서만 UUID 임시 DB를 만들고 팀 DDL + UN 초기 적재 검증.
실행: python -m backend.tests.integration.un_team_ddl_check --socket /private/tmp/un-data-test.sock
국가 마스터는 팀 seed 그대로 사용. HS 명칭은 이 임시 DB에서만 쓰는 테스트 fixture.
"""
import argparse
import csv
import json
import tempfile
import uuid
from pathlib import Path
import pymysql
from pymysql.constants import CLIENT
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from backend.app.scheduler.jobs import un_data_refresh as job


def run(socket):
    if str(Path(socket).resolve()) != '/private/tmp/un-data-test.sock':
        raise ValueError('전용 로컬 테스트 소켓만 허용합니다')
    name='un_p3_test_'+uuid.uuid4().hex
    admin=pymysql.connect(unix_socket=socket,user='root',autocommit=True,client_flag=CLIENT.MULTI_STATEMENTS)
    engine=None;created=False
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4');created=True
            admin.select_db(name)
            for path in sorted((job.ROOT/'db/mysql/init').glob('*.sql')):
                cursor.execute(path.read_text(encoding='utf-8'))
                while cursor.nextset():pass
                print('DDL OK',path.name)
            cursor.execute('SELECT country_code FROM country')
            countries={r[0] for r in cursor.fetchall()}
            with (job.DEFAULT_CSV.parent/'required_country.csv').open(encoding='utf-8-sig') as handle:
                required={r['country_code'] for r in csv.DictReader(handle)}
            cfg=json.loads(job.DEFAULT_SCOPE.read_text(encoding='utf-8'))
            missing=sorted(required-countries)
            print('TEAM COUNTRY COVERAGE',json.dumps({'initial':len(required),'missing_initial':missing,
                  'missing_future_scope':sorted(set(cfg['country_map'].values())-countries)}))
            if missing:raise ValueError('실제 팀 국가 마스터 누락: 임의 생성하지 않습니다')
            with (job.DEFAULT_CSV.parent/'required_hs.csv').open(encoding='utf-8-sig') as handle:
                hs=list(csv.DictReader(handle))
            cursor.executemany('INSERT INTO hs_code (hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES (%s,%s,%s,%s,%s)',
                [(r['hs_code'],r['hs_ver'],'검증 전용','검증 전용 원문','TEST_ONLY') for r in hs])
        url=URL.create('mysql+pymysql',username='root',database=name,query={'unix_socket':socket})
        engine=create_engine(url)
        with tempfile.TemporaryDirectory(prefix='un-p3-state-') as folder:
            cp=job.initialize_csv(engine,state_dir=Path(folder))
            assert cp['rows']==67550 and cp['database_identity']['database']==name
            with engine.connect() as conn:
                assert conn.scalar(text('SELECT COUNT(*) FROM un_data'))==67550
                assert conn.scalar(text("SELECT COUNT(*) FROM un_data WHERE hs_ver <> 'HS2022'"))==0
                assert conn.scalar(text('SELECT COUNT(*) FROM country'))==len(countries)
            print('PASS: team DDL, real country seeds, 15 test HS parents, 67550 rows fully verified, DB identity bound')
    finally:
        if engine is not None:engine.dispose()
        if created:
            with admin.cursor() as cursor:cursor.execute(f'DROP DATABASE `{name}`')
            print('Removed task temporary DB',name)
        admin.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',required=True)
    run(parser.parse_args().socket)
