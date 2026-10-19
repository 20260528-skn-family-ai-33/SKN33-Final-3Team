"""공통 ORM 테스트의 FK 부모 fixture. 운영 테이블은 팀 DDL로 준비합니다."""
from sqlalchemy import Column, String, Table

from backend.app.models.base import Base


# UN/HS 테스트의 import 순서와 무관하게 SQLite create_all의 country 참조를 해소합니다.
# 메타데이터만 등록하며 실제 DB 연결·테이블 생성·마스터 적재는 수행하지 않습니다.
if 'country' not in Base.metadata.tables:
    Table('country', Base.metadata,
          Column('country_code', String(12), primary_key=True))
