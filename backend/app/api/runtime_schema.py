"""추가 앱 저장소 제안. 운영 자동 생성 금지; SQL 검토·적용 후 활성화."""
from sqlalchemy import (MetaData, Table, Column, Integer, BigInteger, String, Text,
                        DateTime, JSON, Boolean, ForeignKey, UniqueConstraint)
from sqlalchemy.dialects.mysql import BIGINT

metadata = MetaData()
identifier = BigInteger().with_variant(BIGINT(unsigned=True), 'mysql').with_variant(Integer, 'sqlite')

# 기존 users 참조용 정의. 이 모듈은 기존 테이블 DDL을 생성하지 않는다.
users_ref = Table('users', metadata, Column('user_id', identifier, primary_key=True))

profile = Table('api_user_profile', metadata,
    Column('user_id', identifier, ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True),
    Column('name', String(120), nullable=False),
    Column('role', String(20), nullable=False), Column('account_status', String(20), nullable=False),
    Column('created_at', DateTime, nullable=False), Column('last_login_at', DateTime),
    Column('password_change_advised', Boolean, nullable=False, default=False),
    Column('withdrawn_at', DateTime), Column('scheduled_delete_at', DateTime))

session = Table('api_auth_session', metadata,
    Column('token_hash', String(64), primary_key=True),
    Column('user_id', identifier, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False, index=True),
    Column('csrf_hash', String(64), nullable=False), Column('expires_at', DateTime, nullable=False))

snapshot = Table('api_snapshot', metadata,
    Column('snapshot_id', String(64), primary_key=True),
    Column('user_id', identifier, ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False, index=True),
    Column('kind', String(20), nullable=False), Column('payload_json', JSON, nullable=False),
    Column('created_at', DateTime, nullable=False), Column('expires_at', DateTime))

block = Table('api_keyword_block', metadata,
    Column('user_id', identifier, ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True),
    Column('kw_topic', String(120), primary_key=True), Column('selected_terms', JSON, nullable=False))

audit = Table('api_audit_event', metadata,
    Column('audit_id', String(64), primary_key=True), Column('actor_id', identifier, nullable=False),
    Column('action', String(80), nullable=False), Column('target_type', String(40), nullable=False),
    Column('target_id', String(120), nullable=False), Column('reason', Text, nullable=False),
    Column('before_json', JSON, nullable=False), Column('after_json', JSON, nullable=False),
    Column('created_at', DateTime, nullable=False))

rate_limit = Table('api_rate_limit', metadata,
    Column('bucket_key', String(64), primary_key=True), Column('window_start', DateTime, nullable=False),
    Column('request_count', Integer, nullable=False))

report_file = Table('api_report_file', metadata,
    Column('report_id', identifier, primary_key=True), Column('format', String(8), primary_key=True),
    Column('storage_path', String(1024), nullable=False), Column('sha256', String(64), nullable=False),
    Column('created_at', DateTime, nullable=False))

RUNTIME_TABLES = [t for t in metadata.sorted_tables if t.name.startswith('api_')]
