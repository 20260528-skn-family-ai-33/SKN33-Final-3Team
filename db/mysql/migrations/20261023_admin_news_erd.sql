-- 기사·배치 관리 ERD 적용 (기존배치유지_20261022 기준).
-- 범위: article, batch_run, article_classification, FK 의존 async_job, 기사 집계 뷰 2개.
-- 회원/채팅/리포트 등 나머지 관리자 ERD는 이 마이그레이션의 범위가 아닙니다.
-- 기존 기사 시각·분류 근거는 추정하지 않고 NULL로 보존합니다.
-- DDL은 자동 커밋됩니다. apply_admin_news_erd.py가 백업·단계별 재실행을 처리합니다.
-- 이 파일은 @step 단위로 실행합니다. 트리거 본문을 세미콜론으로 분할하지 마세요.

-- @step table async_job -
CREATE TABLE async_job (
  job_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  job_type VARCHAR(60) NOT NULL,
  target_key VARCHAR(160) NOT NULL,
  requested_by BIGINT UNSIGNED NULL,
  subject_user_id BIGINT UNSIGNED NULL,
  status ENUM('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED') NOT NULL DEFAULT 'QUEUED',
  payload_json JSON NOT NULL,
  result_json JSON NULL,
  request_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  lock_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  active_lock_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (CASE WHEN status IN ('QUEUED','RUNNING') THEN lock_key ELSE NULL END) STORED,
  attempt_no INT UNSIGNED NOT NULL DEFAULT 0,
  claim_token BIGINT UNSIGNED NOT NULL DEFAULT 0,
  lease_expires_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  available_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (job_id),
  UNIQUE KEY uq_job_request (request_key),
  UNIQUE KEY uq_job_active (active_lock_key),
  KEY ix_job_ready (status,available_at,job_id),
  KEY ix_job_lease (status,lease_expires_at),
  KEY ix_job_subject (subject_user_id,status),
  KEY ix_job_requester (requested_by),
  CONSTRAINT fk_job_requester FOREIGN KEY (requested_by) REFERENCES users (user_id) ON DELETE SET NULL,
  CONSTRAINT fk_job_subject FOREIGN KEY (subject_user_id) REFERENCES users (user_id) ON DELETE SET NULL,
  CONSTRAINT ck_job_running CHECK (status<>'RUNNING' OR (started_at IS NOT NULL AND lease_expires_at IS NOT NULL)),
  CONSTRAINT ck_job_finished CHECK (status NOT IN ('SUCCEEDED','FAILED','CANCELLED') OR finished_at IS NOT NULL),
  CONSTRAINT ck_job_payload CHECK (JSON_TYPE(payload_json)='OBJECT'),
  CONSTRAINT ck_job_result CHECK (result_json IS NULL OR JSON_TYPE(result_json)='OBJECT')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @step column article visibility_status
ALTER TABLE article ADD COLUMN visibility_status ENUM('VISIBLE','HIDDEN') NOT NULL DEFAULT 'VISIBLE';

-- @step column article first_ingested_at
ALTER TABLE article ADD COLUMN first_ingested_at DATETIME NULL;

-- @step column article admin_meta_json
ALTER TABLE article ADD COLUMN admin_meta_json JSON NULL;

-- @step data article admin_meta_json
UPDATE article SET admin_meta_json='{"schema_version":1,"visibility":{"reason":null,"changed_by":null,"changed_at":null},"country_tags_manual":false,"hs_tags_manual":false,"classification_input":null,"classification_state":{"status":"PENDING","revision":0}}' WHERE admin_meta_json IS NULL;

-- @step required article admin_meta_json
ALTER TABLE article MODIFY COLUMN admin_meta_json JSON NOT NULL;

-- @step index article ix_article_visible_date
ALTER TABLE article ADD KEY ix_article_visible_date (visibility_status,published_date,article_id);

-- @step index article ix_article_first_ingested
ALTER TABLE article ADD KEY ix_article_first_ingested (first_ingested_at,article_id);

-- @step constraint article ck_article_admin_meta_json
ALTER TABLE article ADD CONSTRAINT ck_article_admin_meta_json CHECK (JSON_SCHEMA_VALID('{"type":"object","properties":{"schema_version":{"type":"integer","enum":[1]},"visibility":{"type":"object","properties":{"reason":{"type":["string","null"]},"changed_by":{"type":["string","null"]},"changed_at":{"type":["string","null"]}},"required":["reason","changed_by","changed_at"],"additionalProperties":true},"country_tags_manual":{"type":"boolean"},"hs_tags_manual":{"type":"boolean"},"classification_input":{"anyOf":[{"type":"null"},{"type":"object","properties":{"snapshot_ref":{"type":"string","minLength":1},"sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"}},"required":["snapshot_ref","sha256"],"additionalProperties":true}]},"classification_state":{"type":"object","properties":{"status":{"type":"string","enum":["PENDING","READY"]},"revision":{"type":"integer","minimum":0}},"required":["status","revision"],"additionalProperties":true}},"required":["schema_version","visibility","country_tags_manual","hs_tags_manual","classification_input","classification_state"],"additionalProperties":true}',admin_meta_json) IS TRUE);

-- @step table article_classification -
CREATE TABLE article_classification (
  classification_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id BIGINT UNSIGNED NOT NULL,
  category_id INT UNSIGNED NOT NULL,
  source_kind ENUM('TOPIC','BASE') NOT NULL,
  keyword_text VARCHAR(120) COLLATE utf8mb4_bin NULL,
  basis_revision INT UNSIGNED NOT NULL,
  representative_term VARCHAR(120) COLLATE utf8mb4_bin NULL,
  basis_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  applied_at DATETIME NOT NULL,
  job_id BIGINT UNSIGNED NULL,
  PRIMARY KEY (classification_id),
  UNIQUE KEY uq_classification_basis (article_id,basis_key),
  KEY ix_classification_category (category_id,article_id),
  KEY ix_classification_topic (keyword_text,basis_revision,article_id),
  KEY ix_classification_job (job_id),
  CONSTRAINT fk_classification_article FOREIGN KEY (article_id) REFERENCES article (article_id) ON DELETE CASCADE,
  CONSTRAINT fk_classification_category FOREIGN KEY (category_id) REFERENCES keyword_category (category_id),
  CONSTRAINT fk_classification_topic FOREIGN KEY (keyword_text) REFERENCES keyword_topic (keyword_text),
  CONSTRAINT fk_classification_job FOREIGN KEY (job_id) REFERENCES async_job (job_id) ON DELETE SET NULL,
  CONSTRAINT ck_classification_revision CHECK (basis_revision>=1),
  CONSTRAINT ck_classification_basis CHECK ((source_kind='TOPIC' AND keyword_text IS NOT NULL AND representative_term IS NULL) OR (source_kind='BASE' AND keyword_text IS NULL AND representative_term IS NOT NULL)),
  CONSTRAINT ck_classification_term CHECK (representative_term IS NULL OR CHAR_LENGTH(TRIM(representative_term))>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- @step column batch_run job_id
ALTER TABLE batch_run ADD COLUMN job_id BIGINT UNSIGNED NULL;

-- @step column batch_run retry_of_run_id
ALTER TABLE batch_run ADD COLUMN retry_of_run_id BIGINT UNSIGNED NULL;

-- @step column batch_run detail_json
ALTER TABLE batch_run ADD COLUMN detail_json JSON NULL;

-- @step data batch_run detail_json
UPDATE batch_run SET detail_json='{"schema_version":1,"scope":{},"source_ref":null,"quality":null}' WHERE detail_json IS NULL;

-- @step required batch_run detail_json
ALTER TABLE batch_run MODIFY COLUMN detail_json JSON NOT NULL;

-- @step enum batch_run hs_code_correlation
ALTER TABLE batch_run MODIFY COLUMN target_table ENUM('hs_code','export_monthly','import_monthly','export_summary','import_summary','fx_daily','article','article_tag','news_product_daily','news_keyword_daily','model_run','report','hs_code_correlation') NOT NULL COMMENT '이 실행이 처리하는 실제 테이블 이름이다. 예: export_monthly.';

-- @step index batch_run ix_batch_job
ALTER TABLE batch_run ADD KEY ix_batch_job (job_id,started_at);

-- @step index batch_run ix_batch_retry
ALTER TABLE batch_run ADD KEY ix_batch_retry (retry_of_run_id);

-- @step constraint batch_run fk_batch_job
ALTER TABLE batch_run ADD CONSTRAINT fk_batch_job FOREIGN KEY (job_id) REFERENCES async_job (job_id) ON DELETE SET NULL;

-- @step constraint batch_run fk_batch_retry
ALTER TABLE batch_run ADD CONSTRAINT fk_batch_retry FOREIGN KEY (retry_of_run_id) REFERENCES batch_run (run_id) ON DELETE SET NULL;

-- @step constraint batch_run ck_batch_run_detail_json
ALTER TABLE batch_run ADD CONSTRAINT ck_batch_run_detail_json CHECK (JSON_SCHEMA_VALID('{"type":"object","properties":{"schema_version":{"type":"integer","enum":[1]}},"required":["schema_version"],"additionalProperties":true}',detail_json) IS TRUE);

-- @step trigger article trg_article_insert_v3
CREATE TRIGGER trg_article_insert_v3 BEFORE INSERT ON article FOR EACH ROW
BEGIN
  IF (NEW.first_ingested_at IS NULL) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='New articles require first_ingested_at'; END IF;
  IF (JSON_TYPE(JSON_EXTRACT(NEW.admin_meta_json,'$.classification_input'))<>'OBJECT' OR JSON_EXTRACT(NEW.admin_meta_json,'$.classification_input') IS NULL) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='New articles require a classification snapshot'; END IF;
END;

-- @step trigger batch_run trg_batch_run_insert_v3
CREATE TRIGGER trg_batch_run_insert_v3 BEFORE INSERT ON batch_run FOR EACH ROW
BEGIN
  IF (NEW.retry_of_run_id IS NOT NULL AND NEW.retry_of_run_id=NEW.run_id) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Self retry reference is not allowed'; END IF;
END;

-- @step trigger batch_run trg_batch_run_update_v3
CREATE TRIGGER trg_batch_run_update_v3 BEFORE UPDATE ON batch_run FOR EACH ROW
BEGIN
  IF (NEW.retry_of_run_id IS NOT NULL AND NEW.retry_of_run_id=NEW.run_id) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Self retry reference is not allowed'; END IF;
END;

-- @step view v_article_country_day -
CREATE OR REPLACE VIEW v_article_country_day AS SELECT a.published_date,t.country_code,COUNT(DISTINCT a.article_id) AS article_count FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE a.source_code IN ('KOTRA_MARKET','HANKYUNG','FNNEWS','KOTRA_SHORT_NEWS') AND a.visibility_status='VISIBLE' AND t.country_code IS NOT NULL GROUP BY a.published_date,t.country_code;

-- @step view v_article_day -
CREATE OR REPLACE VIEW v_article_day AS SELECT published_date,COUNT(DISTINCT article_id) AS article_count FROM article WHERE source_code IN ('KOTRA_MARKET','HANKYUNG','FNNEWS','KOTRA_SHORT_NEWS') AND visibility_status='VISIBLE' GROUP BY published_date;
