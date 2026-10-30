"""추가 앱 저장소 제안. 운영 자동 생성 금지; SQL 검토·적용 후 활성화."""
from sqlalchemy import (MetaData, Table, Column, Integer, BigInteger, String, Text,
                        DateTime, JSON, Boolean, ForeignKey, UniqueConstraint)
from sqlalchemy.dialects.mysql import BIGINT

metadata = MetaData()
identifier = BigInteger().with_variant(BIGINT(unsigned=True), 'mysql').with_variant(Integer, 'sqlite')

# 기존 users 참조용 정의. 이 모듈은 기존 테이블 DDL을 생성하지 않는다.
users_ref = Table('users', metadata, Column('user_id', identifier, primary_key=True))

snapshot = Table('api_snapshot', metadata,
    Column('snapshot_id', String(64), primary_key=True),
    Column('user_id', identifier, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False, index=True),
    Column('kind', String(20), nullable=False), Column('payload_json', JSON, nullable=False),
    Column('created_at', DateTime, nullable=False), Column('expires_at', DateTime))

audit = Table('api_audit_event', metadata,
    Column('audit_id', String(64), primary_key=True), Column('actor_id', identifier, nullable=False),
    Column('action', String(80), nullable=False), Column('target_type', String(40), nullable=False),
    Column('target_id', String(120), nullable=False), Column('reason', Text, nullable=False),
    Column('before_json', JSON, nullable=False), Column('after_json', JSON, nullable=False),
    Column('created_at', DateTime, nullable=False))

RUNTIME_TABLES = [t for t in metadata.sorted_tables if t.name.startswith('api_')]

# Additive columns on the team's existing tables. Unknown historical names/dates stay NULL.
EXTENSIONS = {
    'users': [Column('name', String(120)), Column('role', String(20), nullable=False, server_default='USER'),
        Column('account_status', String(20), nullable=False, server_default='ACTIVE'),
        Column('created_at', DateTime), Column('last_login_at', DateTime),
        Column('withdrawn_at', DateTime),
        Column('auth_version', Integer, nullable=False, server_default='0')],
    'notification_setting': [Column('keyword_blocks_json', JSON)],
    'report': [Column('files_json', JSON)],
}

def check_schema(database):
    for table in RUNTIME_TABLES: database.table(table.name)
    for name, columns in EXTENSIONS.items(): database.require_columns(name, *(c.name for c in columns))
