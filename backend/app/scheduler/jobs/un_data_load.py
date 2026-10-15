"""SKN 공통 세션/CRUD 사용. un_data만 변경하고 부모/배치/RAW 테이블은 쓰지 않습니다."""
import argparse
from datetime import date
from pathlib import Path
from sqlalchemy import inspect, select, Table, MetaData, delete, and_, or_
from backend.app.db.session import make_engine, session_scope
from backend.app.db.crud import CRUD
from backend.app.models.un_data import UnData
from backend.app.scheduler.collectors.un_data.pipeline import HEAD, validate_csv, validate_records

DEFAULT_CSV = Path(__file__).resolve().parents[4] / 'data/un_data/un_data.csv'


def to_rows(values):
    result=[]
    for value in values:
        row=dict(zip(HEAD,value));row['period_start']=date.fromisoformat(row['period_start']);result.append(row)
    return result


def read_rows(path):
    return to_rows(validate_csv(path))


def validate(rows, allow_empty=False):
    records=[{k: '' if r[k] is None else str(r[k]) for k in HEAD} for r in rows]
    return to_rows(validate_records(records,allow_empty=allow_empty))


def preflight(engine):
    inspector=inspect(engine)
    for name in ('un_data','country','hs_code'):
        if not inspector.has_table(name):raise ValueError(f'{name} 테이블이 없습니다. 공통 DB를 먼저 준비하세요.')
    cols={c['name']:c for c in inspector.get_columns('un_data')}
    if set(cols)!=set(HEAD) or not cols['korea_share_pct'].get('computed'):raise ValueError('un_data 컬럼/생성 비중 정의 불일치')
    if inspector.get_pk_constraint('un_data')['constrained_columns']!=HEAD[:5]:raise ValueError('un_data 복합 PK 불일치')


def verify(session, rows, conditions=None):
    expected={tuple(r[k] for k in HEAD[:5]):tuple(r[k] for k in HEAD) for r in rows}
    found={}
    statements=[select(UnData)] if conditions is None else [select(UnData).where(or_(*conditions[i:i+100])) for i in range(0,len(conditions),100)]
    for stmt in statements:
        for obj in session.scalars(stmt):
            key=tuple(getattr(obj,k) for k in HEAD[:5])
            if conditions is not None or key in expected:found[key]=tuple(getattr(obj,k) for k in HEAD)
    if found!=expected:raise ValueError('적재 후 대상 범위 9열 전수 대조 불일치')


def parents(session, engine, rows):
    metadata=MetaData()
    country=Table('country',metadata,autoload_with=engine)
    hs=Table('hs_code',metadata,autoload_with=engine)
    countries=set(session.scalars(select(country.c.country_code)))
    pairs=set(session.execute(select(hs.c.hs_code,hs.c.hs_ver)).tuples())
    missing_c=sorted({r['country_code'] for r in rows}-countries)
    missing_h=sorted({(r['hs6_code'],r['hs_ver']) for r in rows}-pairs)
    if missing_c or missing_h:raise ValueError(f'부모 마스터 누락 country={missing_c}, hs_code={missing_h}')


def load(engine, rows):
    """수동 CSV 초기 적재. 기존 키 갱신, 입력에 없는 행은 보존."""
    rows=validate(rows);preflight(engine)
    with session_scope(engine) as session:
        parents(session,engine,rows)
        CRUD(UnData).upsert_many(session,rows,batch=500)
        verify(session,rows)
        return CRUD(UnData).count(session)


def replace_scopes(engine, rows, scopes, products_by_country):
    """발표된 (주기, 통계기간, 국가)의 대상 품목만 교체. HS 판본 변경·삭제도 반영."""
    rows=validate(rows,allow_empty=True);preflight(engine)
    scopes=set(scopes)
    if not scopes:
        if rows:raise ValueError('교체 범위 없는 입력')
        return 0
    for f,p,c in scopes:
        if f not in ('M','A') or p.day!=1 or (f=='A' and p.month!=1) or p.year<2022:raise ValueError('잘못된 교체 기간')
        if not products_by_country.get(c):raise ValueError('교체 범위의 대상 품목 없음')
    for r in rows:
        key=(r['freq_code'],r['period_start'],r['country_code'])
        if key not in scopes or r['hs6_code'] not in products_by_country[key[2]]:raise ValueError('교체 범위 밖 입력')
    conditions=[and_(UnData.freq_code==f,UnData.period_start==p,UnData.country_code==c,
                     UnData.hs6_code.in_(sorted(products_by_country[c]))) for f,p,c in sorted(scopes)]
    with session_scope(engine) as session:
        parents(session,engine,rows)
        # 대상 품목 전체가 사라진 데이터셋은 오응답 가능성이 있어 자동 삭제하지 않습니다.
        populated={(r['freq_code'],r['period_start'],r['country_code']) for r in rows}
        for scope in scopes-populated:
            f,p,c=scope
            exists=session.scalar(select(UnData.freq_code).where(UnData.freq_code==f,UnData.period_start==p,
                UnData.country_code==c,UnData.hs6_code.in_(sorted(products_by_country[c]))).limit(1))
            if exists is not None:raise ValueError('기존 데이터셋 전체가 빈 응답: 수동 확인 필요')
        for i in range(0,len(conditions),100):session.execute(delete(UnData).where(or_(*conditions[i:i+100])))
        CRUD(UnData).upsert_many(session,rows,batch=500)
        verify(session,rows,conditions)
        return len(rows)


def run(csv_path=DEFAULT_CSV,db_url=None):
    engine=None
    try:
        rows=read_rows(csv_path);engine=make_engine(db_url);load(engine,rows);return 0
    except Exception:
        import logging
        logging.getLogger(__name__).exception('un_data 적재 실패')
        return 1
    finally:
        if engine is not None:engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',type=Path,default=DEFAULT_CSV);parser.add_argument('--db-url')
    parser.add_argument('--validate-only',action='store_true');args=parser.parse_args()
    if args.validate_only:print(f'CSV 검증 통과: {len(read_rows(args.csv)):,}행');return 0
    return run(args.csv,args.db_url)

if __name__=='__main__':raise SystemExit(main())
