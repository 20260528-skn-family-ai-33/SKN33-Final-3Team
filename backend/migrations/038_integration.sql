-- Approved integration structure. Prefer python -m backend.scripts.migrate_integration --apply for checked, resumable application.

ALTER TABLE `users` ADD COLUMN name VARCHAR(120);

ALTER TABLE `users` ADD COLUMN `role` VARCHAR(20) NOT NULL DEFAULT 'USER';

ALTER TABLE `users` ADD COLUMN account_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE `users` ADD COLUMN created_at DATETIME;

ALTER TABLE `users` ADD COLUMN last_login_at DATETIME;

ALTER TABLE `users` ADD COLUMN withdrawn_at DATETIME;

ALTER TABLE `users` ADD COLUMN auth_version INTEGER NOT NULL DEFAULT '0';

ALTER TABLE `notification_setting` ADD COLUMN keyword_blocks_json JSON;

ALTER TABLE `report` ADD COLUMN files_json JSON;


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
