-- #63 관리자 검수 이력 조회 최적화. 운영 DB 자동 적용 금지.
-- 권장: python -m backend.scripts.migrate_integration 으로 계획을 확인하고 --apply로 적용.
-- 아래 SQL은 기존 038 스키마에 대한 검토용이며 직접 실행 시 1회만 적용한다.
ALTER TABLE api_audit_event
    MODIFY COLUMN target_id VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL;
CREATE INDEX ix_api_audit_target_time
    ON api_audit_event (target_type, target_id, created_at, audit_id);
