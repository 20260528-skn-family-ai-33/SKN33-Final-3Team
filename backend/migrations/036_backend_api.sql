-- SUPERSEDED by 038_integration.sql and backend.scripts.migrate_integration.
-- Historical proposal ONLY: do not apply this file to local or RDS databases.
-- API application storage only; MySQL 8. Review before applying. No seed/ingestion.
-- Existing users.user_id must be BIGINT UNSIGNED. Timestamps are UTC.

CREATE TABLE api_audit_event (
	audit_id VARCHAR(64) NOT NULL,
	actor_id BIGINT UNSIGNED NOT NULL,
	action VARCHAR(80) NOT NULL,
	target_type VARCHAR(40) NOT NULL,
	target_id VARCHAR(120) NOT NULL,
	reason TEXT NOT NULL,
	before_json JSON NOT NULL,
	after_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (audit_id)
)

;

CREATE TABLE api_rate_limit (
	bucket_key VARCHAR(64) NOT NULL,
	window_start DATETIME NOT NULL,
	request_count INTEGER NOT NULL,
	PRIMARY KEY (bucket_key)
)

;

CREATE TABLE api_report_file (
	report_id BIGINT UNSIGNED NOT NULL,
	format VARCHAR(8) NOT NULL,
	storage_path VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (report_id, format)
)

;

CREATE TABLE api_auth_session (
	token_hash VARCHAR(64) NOT NULL,
	user_id BIGINT UNSIGNED NOT NULL,
	csrf_hash VARCHAR(64) NOT NULL,
	expires_at DATETIME NOT NULL,
	PRIMARY KEY (token_hash),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
)

;
CREATE INDEX ix_api_auth_session_user_id ON api_auth_session (user_id);

CREATE TABLE api_keyword_block (
	user_id BIGINT UNSIGNED NOT NULL,
	kw_topic VARCHAR(120) NOT NULL,
	selected_terms JSON NOT NULL,
	PRIMARY KEY (user_id, kw_topic),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
)

;

CREATE TABLE api_snapshot (
	snapshot_id VARCHAR(64) NOT NULL,
	user_id BIGINT UNSIGNED NOT NULL,
	kind VARCHAR(20) NOT NULL,
	payload_json JSON NOT NULL,
	created_at DATETIME NOT NULL,
	expires_at DATETIME,
	PRIMARY KEY (snapshot_id),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
)

;
CREATE INDEX ix_api_snapshot_user_id ON api_snapshot (user_id);

CREATE TABLE api_user_profile (
	user_id BIGINT UNSIGNED NOT NULL,
	name VARCHAR(120) NOT NULL,
	`role` VARCHAR(20) NOT NULL,
	account_status VARCHAR(20) NOT NULL,
	created_at DATETIME NOT NULL,
	last_login_at DATETIME,
	password_change_advised BOOL NOT NULL,
	withdrawn_at DATETIME,
	scheduled_delete_at DATETIME,
	PRIMARY KEY (user_id),
	FOREIGN KEY(user_id) REFERENCES users (user_id) ON DELETE CASCADE
)

;
