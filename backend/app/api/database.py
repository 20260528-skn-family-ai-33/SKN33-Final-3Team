"""기존 RDS 테이블을 그대로 반영한다. 시작/요청 시 DDL·적재 없음."""
from threading import RLock
from sqlalchemy import MetaData, Table, select, func
from sqlalchemy.exc import NoSuchTableError
from backend.app.api.common import unavailable, ApiError, clean


class Database:
    def __init__(self, engine):
        self.engine = engine
        self.metadata = MetaData()
        self.lock = RLock()

    def table(self, name, connection=None):
        with self.lock:
            if name not in self.metadata.tables:
                try:
                    Table(name, self.metadata, autoload_with=connection if connection is not None else self.engine, resolve_fks=False)
                except NoSuchTableError:
                    unavailable(f'{name} 저장 구조가 준비되지 않았습니다.', 'FEATURE_NOT_READY')
            return self.metadata.tables[name]

    def require_columns(self, name, *columns):
        table = self.table(name)
        if any(c not in table.c for c in columns):
            unavailable(f'{name} 추가 컬럼 적용이 필요합니다.', 'FEATURE_NOT_READY')
        return table


class Store:
    def __init__(self, database, connection):
        self.db, self.c = database, connection
        self.queries = []

    def t(self, name):
        return self.db.table(name, self.c)

    def rows(self, stmt):
        return [dict(r) for r in self.c.execute(stmt).mappings()]

    def one(self, stmt):
        row = self.c.execute(stmt).mappings().first()
        return dict(row) if row else None

    def owned(self, name, id_col, identity, user_id, lock=False):
        t = self.t(name)
        stmt = select(t).where(t.c[id_col] == identity, t.c.user_id == user_id)
        row = self.one(stmt.with_for_update() if lock else stmt)
        if row is None:
            raise ApiError(404, 'NOT_FOUND', '대상을 찾을 수 없습니다.')
        return row

    def insert(self, table_name, **values):
        return self.c.execute(self.t(table_name).insert().values(**values)).inserted_primary_key[0]

    def has_data(self, name):
        return self.c.execute(select(self.t(name)).limit(1)).first() is not None

    def require_data(self, name):
        if not self.has_data(name):
            unavailable(f'{name} 데이터 적재를 기다리고 있습니다.')

    def count(self, stmt):
        return self.c.scalar(select(func.count()).select_from(stmt.subquery()))
