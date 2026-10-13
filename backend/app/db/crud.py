"""모든 테이블에 공통으로 쓰는 CRUD. 테이블마다 클래스를 만들지 않고 CRUD(모델)로 씁니다.

    hs = CRUD(HsCodeMst)
    with session_scope(engine) as s:
        hs.get(s, "010121")
        hs.list(s, depth=6, limit=50)
        hs.upsert_many(s, rows)
"""
from sqlalchemy import func, select
from sqlalchemy.dialects import mysql, sqlite


class CRUD:
    def __init__(self, model):
        self.model = model
        self.table = model.__table__
        self.pk = [c.name for c in self.table.primary_key.columns]
        # 기본키와 DB가 계산하는 컬럼(Computed)은 값을 넣거나 바꾸지 않습니다.
        self.writable = [c.name for c in self.table.columns if c.computed is None]

    def get(self, session, pk):
        return session.get(self.model, pk)

    def list(self, session, limit=100, offset=0, order_by=None, **filters):
        """filters는 컬럼=값 (같음 조건). 예: list(s, depth=6, hs_ver="HS2022")"""
        stmt = select(self.model).filter_by(**filters)
        stmt = stmt.order_by(*(order_by or [getattr(self.model, k) for k in self.pk]))
        return session.scalars(stmt.limit(limit).offset(offset)).all()

    def count(self, session, **filters):
        return session.scalar(select(func.count()).select_from(self.model).filter_by(**filters))

    def create(self, session, **values):
        obj = self.model(**values)
        session.add(obj)
        session.flush()
        return obj

    def update(self, session, pk, **values):
        obj = self.get(session, pk)
        if obj is None:
            return None
        for key, value in values.items():
            if key not in self.writable or key in self.pk:
                raise ValueError(f"{self.table.name}.{key}는 수정할 수 없는 컬럼입니다.")
            setattr(obj, key, value)
        session.flush()
        return obj

    def delete(self, session, pk):
        obj = self.get(session, pk)
        if obj is not None:
            session.delete(obj)
            session.flush()
        return obj is not None

    def upsert_many(self, session, rows, batch=1000):
        """rows(dict 목록)를 기본키 기준으로 넣고, 이미 있으면 그 행에 있는 컬럼만 갱신합니다. MySQL·SQLite 지원.

        행마다 컬럼 구성이 다를 수 있어, 같은 컬럼 구성끼리 묶어 따로 실행합니다.
        (한 INSERT에 섞으면 빠진 컬럼이 NULL로 덮이거나 CompileError가 납니다.)
        """
        rows = [{k: r[k] for k in self.writable if k in r} for r in rows]
        groups = {}
        for r in rows:
            groups.setdefault(tuple(r), []).append(r)
        dialect = session.get_bind().dialect.name
        for cols, group in groups.items():
            update = [k for k in cols if k not in self.pk]
            for i in range(0, len(group), batch):
                chunk = group[i:i + batch]
                if dialect == "mysql":
                    stmt = mysql.insert(self.table).values(chunk)
                    stmt = (stmt.on_duplicate_key_update({k: stmt.inserted[k] for k in update}) if update
                            else stmt.prefix_with("IGNORE"))
                elif dialect == "sqlite":
                    stmt = sqlite.insert(self.table).values(chunk)
                    stmt = (stmt.on_conflict_do_update(index_elements=self.pk,
                                                       set_={k: stmt.excluded[k] for k in update}) if update
                            else stmt.on_conflict_do_nothing(index_elements=self.pk))
                else:
                    raise NotImplementedError(f"upsert 미지원 DB: {dialect}")
                session.execute(stmt)
        return len(rows)
