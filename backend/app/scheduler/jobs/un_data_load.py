"""SKN 공통 세션/CRUD 사용. un_data만 변경하고 부모/배치/RAW 테이블은 쓰지 않습니다."""
import argparse
from datetime import date
from contextlib import nullcontext
from pathlib import Path
from sqlalchemy import inspect, select, Table, MetaData, delete, and_, or_, tuple_
from backend.app.db.session import make_engine
from backend.app.db.crud import CRUD
from backend.app.scheduler.collectors.un_data.locking import db_lock
from backend.app.models.un_data import UnData
from backend.app.scheduler.collectors.un_data.pipeline import HEAD, validate_csv, validate_records, require

DEFAULT_CSV = Path(__file__).resolve().parents[4] / 'data/un_data/un_data.csv'


# 9컬럼 변환 결과를 ORM 적재용 딕셔너리로 바꾸고 기간을 date 형식으로 변환합니다.
def to_rows(values):
    result=[]
    for value in values:
        require(len(value)==len(HEAD), '9개 컬럼 개수 불일치')
        row=dict(zip(HEAD,value));row['period_start']=date.fromisoformat(row['period_start']);result.append(row)
    return result


# CSV를 검증한 뒤 적재용 행으로 변환합니다. 최초 CSV 적재에서 사용합니다.
def read_rows(path):
    return to_rows(validate_csv(path))


# 메모리의 적재 행에도 CSV와 같은 공통 검증 규칙을 적용합니다.
def validate(rows, allow_empty=False):
    records=[{k: '' if r[k] is None else str(r[k]) for k in HEAD} for r in rows]
    return to_rows(validate_records(records,allow_empty=allow_empty))


# 필수 테이블·컬럼·생성 비중·복합 PK를 확인합니다. 테이블을 생성하거나 수정하지 않습니다.
def preflight(engine):
    inspector=inspect(engine)
    for name in ('un_data','country','hs_code'):
        if not inspector.has_table(name):raise ValueError(f'{name} 테이블이 없습니다. 공통 DB를 먼저 준비하세요.')
    cols={c['name']:c for c in inspector.get_columns('un_data')}
    if set(cols)!=set(HEAD) or not cols['korea_share_pct'].get('computed'):raise ValueError('un_data 컬럼/생성 비중 정의 불일치')
    if inspector.get_pk_constraint('un_data')['constrained_columns']!=HEAD[:5]:raise ValueError('un_data 복합 PK 불일치')


# 입력과 DB의 대상 행을 9컬럼 전수 비교하여 적재 누락·값 변경을 탐지합니다.
def verify(session, rows, conditions=None):
    expected={tuple(r[k] for k in HEAD[:5]):tuple(r[k] for k in HEAD) for r in rows}
    found={}
    # ORM 객체를 만들지 않고 전수 대조에 필요한 9개 컬럼만 조회합니다.
    columns=[getattr(UnData,k) for k in HEAD]
    if conditions is None:
        # 수동/초기 적재는 입력 복합 키만 조회합니다. 바인드 수를 제한해 나누어 처리합니다.
        keys=list(expected)
        statements=(select(*columns).where(tuple_(*columns[:5]).in_(keys[i:i+100]))
                    for i in range(0,len(keys),100))
    else:
        # 범위 교체는 입력 키 밖의 잔여 행도 검사해야 하므로 범위 조건 전체를 유지합니다.
        statements=(select(*columns).where(or_(*conditions[i:i+100]))
                    for i in range(0,len(conditions),100))
    for stmt in statements:
        for row in session.execute(stmt.execution_options(yield_per=500)):
            values=tuple(row);found[values[:5]]=values
    if found!=expected:raise ValueError('적재 후 대상 범위 9열 전수 대조 불일치')


# 국가·HS 마스터를 읽어 필요한 부모 키가 모두 있는지 확인합니다. 누락 키를 자동 생성하지 않습니다.
def parents(session, engine, rows):
    metadata=MetaData()
    country=Table('country',metadata,autoload_with=engine)
    hs=Table('hs_code',metadata,autoload_with=engine)
    # 전체 마스터 대신 이번 입력의 국가 키와 (HS 코드, 판본) 조합만 읽습니다.
    required_c=sorted({r['country_code'] for r in rows})
    required_h=sorted({(r['hs6_code'],r['hs_ver']) for r in rows})
    countries=set();pairs=set()
    for i in range(0,len(required_c),100):
        countries.update(session.scalars(select(country.c.country_code).where(
            country.c.country_code.in_(required_c[i:i+100]))))
    for i in range(0,len(required_h),100):
        pairs.update(session.execute(select(hs.c.hs_code,hs.c.hs_ver).where(
            tuple_(hs.c.hs_code,hs.c.hs_ver).in_(required_h[i:i+100]))).tuples())
    missing_c=sorted(set(required_c)-countries)
    missing_h=sorted(set(required_h)-pairs)
    if missing_c or missing_h:raise ValueError(f'부모 마스터 누락 country={missing_c}, hs_code={missing_h}')


# 잠금을 확보한 뒤 입력 행을 upsert하고 검증합니다. 입력에 없는 기존 행은 보존합니다.
def load(engine, rows):
    """수동 CSV 초기 적재. 기존 키 갱신, 입력에 없는 행은 보존."""
    rows=validate(rows);preflight(engine)
    with db_lock(engine) as guard, guard.transaction() as session:
        parents(session,engine,rows)
        CRUD(UnData).upsert_many(session,rows,batch=500)
        verify(session,rows)
        return CRUD(UnData).count(session)


# 지정된 국가·주기·기간의 대상 품목만 한 트랜잭션으로 교체하고 결과를 대조합니다.
def replace_scopes(engine, rows, scopes, products_by_country, guard=None):
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
    with (nullcontext(guard) if guard is not None else db_lock(engine)) as active_guard, active_guard.transaction() as session:
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
