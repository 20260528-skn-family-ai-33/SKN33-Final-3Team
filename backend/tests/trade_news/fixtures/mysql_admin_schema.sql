-- MySQL 8.4 관리자 ERD 회귀 시험 전용. 운영 초기화·마이그레이션에서 실행하지 않습니다.
-- 출처: 관리자_ERD_SQL_기존배치유지_20261022/01_create_full_schema.sql
-- 기존 로컬 MySQL 시험에서 사용한 12개 의존 테이블·3개 트리거 정의를 그대로 발췌했습니다.
-- 합성 데이터는 Python fixture가 삽입합니다. 기사 원문·접속 정보는 포함하지 않습니다.

CREATE TABLE service_option (
  option_code VARCHAR(40) NOT NULL COMMENT '설정 하나의 고유 코드다. 예를 들어 MONTHLY이며, 이 표의 PK다.',
  option_type ENUM('SOURCE','REPORT_PERIOD','REPORT_TYPE','RULE','NOTIFICATION_TERMS') NOT NULL COMMENT '출처·구독 주기·리포트 종류·판정 규칙·약관 중 어떤 선택지 그룹인지 구분한다.',
  label_ko VARCHAR(120) NOT NULL COMMENT '사람이 읽는 한글 이름이다. 예를 들어 월간 리포트다.',
  value_json JSON NOT NULL COMMENT '설정의 구체 내용을 묶은 객체다. 주기는 발행 조건, 출처는 URL·한도, 규칙은 임계값을 담는다.',
  sort_order SMALLINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '같은 그룹 안에서 화면에 표시할 순서다. 작은 수를 먼저 표시한다.',
  enabled BOOLEAN NOT NULL DEFAULT TRUE COMMENT '서비스가 현재 이 설정을 선택·사용하도록 허용하는지 저장한다.',
  PRIMARY KEY (option_code),
  KEY ix_option_selection (option_type, enabled, sort_order, option_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE article (
  article_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '우리 DB 안에서 기사 한 건을 구분하는 자동 번호다.',
  source_code VARCHAR(40) NOT NULL COMMENT '기사 제공 기관·언론사를 구분하는 출처 코드다. service_option의 SOURCE를 참조한다.',
  external_id VARCHAR(100) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '기사 제공 API 또는 언론사가 부여한 기사 ID다. 내부 article_id와 다른 값이다.',
  title VARCHAR(600) NOT NULL COMMENT '원천 기사의 제목이다.',
  published_date DATE NOT NULL COMMENT '원천에 표시된 기사 발행일이다. 수집 실행일이 아니다.',
  source_url VARCHAR(2048) NOT NULL COMMENT '원문 기사 페이지로 이동할 링크다.',
  body_masked LONGTEXT NOT NULL COMMENT '원문을 정제하고 연락처 등 개인정보를 가린 본문이다.',
  category VARCHAR(120) NULL COMMENT '원천이 제공한 기사 분류다. 제공되지 않으면 NULL이다.',
  visibility_status ENUM('VISIBLE','HIDDEN') NOT NULL DEFAULT 'VISIBLE',
  first_ingested_at DATETIME NULL,
  admin_meta_json JSON NOT NULL,
  KEY ix_article_visible_date (visibility_status,published_date,article_id),
  KEY ix_article_first_ingested (first_ingested_at,article_id),
  CONSTRAINT ck_article_admin_meta_json CHECK (JSON_SCHEMA_VALID('{"type":"object","properties":{"schema_version":{"type":"integer","enum":[1]},"visibility":{"type":"object","properties":{"reason":{"type":["string","null"]},"changed_by":{"type":["string","null"]},"changed_at":{"type":["string","null"]}},"required":["reason","changed_by","changed_at"],"additionalProperties":true},"country_tags_manual":{"type":"boolean"},"hs_tags_manual":{"type":"boolean"},"classification_input":{"anyOf":[{"type":"null"},{"type":"object","properties":{"snapshot_ref":{"type":"string","minLength":1},"sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"}},"required":["snapshot_ref","sha256"],"additionalProperties":true}]},"classification_state":{"type":"object","properties":{"status":{"type":"string","enum":["PENDING","READY"]},"revision":{"type":"integer","minimum":0}},"required":["status","revision"],"additionalProperties":true}},"required":["schema_version","visibility","country_tags_manual","hs_tags_manual","classification_input","classification_state"],"additionalProperties":true}',admin_meta_json) IS TRUE),
  PRIMARY KEY (article_id),
  UNIQUE KEY uq_article_source_id (source_code, external_id),
  FOREIGN KEY (source_code) REFERENCES service_option (option_code),
  KEY ix_article_date (source_code, published_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE market_region (
  region_id SMALLINT UNSIGNED NOT NULL COMMENT '대륙 또는 하위 지역 하나를 식별하는 내부 번호다.',
  parent_id SMALLINT UNSIGNED NULL COMMENT '하위 지역이 속한 대륙의 region_id다. 대륙 행에는 상위가 없어 NULL이다.',
  region_level TINYINT UNSIGNED NOT NULL COMMENT '1은 대륙, 2는 대륙 안의 하위 지역이다. 국가 단계는 이 표에 넣지 않는다.',
  name_ko VARCHAR(80) NOT NULL COMMENT '아시아·동남아시아처럼 화면에 표시하는 지역 이름이다.',
  PRIMARY KEY (region_id),
  UNIQUE KEY uq_region_name (name_ko),
  FOREIGN KEY (parent_id) REFERENCES market_region (region_id),
  CHECK ((region_level=1 AND parent_id IS NULL) OR (region_level=2 AND parent_id IS NOT NULL AND parent_id<>region_id))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE currency (
  currency_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'USD·JPY처럼 통화 하나를 식별하는 코드다.',
  name_ko VARCHAR(80) NOT NULL COMMENT '미국 달러·일본 엔처럼 사람이 읽는 통화 이름이다.',
  ecos_item_code VARCHAR(20) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT 'ECOS 환율 API에서 이 통화를 요청할 때 사용하는 항목 코드다. 미제공이면 NULL이다.',
  quote_unit SMALLINT UNSIGNED NOT NULL COMMENT '환율의 기준 외화 수량이다. 예를 들어 달러는 1, 엔은 100처럼 저장한다.',
  PRIMARY KEY (currency_code),
  UNIQUE KEY uq_currency_item (ecos_item_code),
  CHECK (quote_unit>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE country (
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '관세청 기준 국가 식별 코드다. 수집·뉴스·관심에서 같은 기준으로 연결한다.',
  name_ko VARCHAR(120) NOT NULL COMMENT '국가 선택과 결과에 표시할 한국어 이름이다.',
  country_role ENUM('COUNTRY','SPECIAL','TOTAL') NOT NULL DEFAULT 'COUNTRY' COMMENT 'COUNTRY 일반 국가, SPECIAL 특수 코드, TOTAL 총계 행의 역할 구분이다.',
  region_id SMALLINT UNSIGNED NULL COMMENT '해당 국가가 속한 하위 지역의 번호다. 미매핑이면 NULL이다.',
  currency_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '환율 카드에 연결할 대표 통화 코드다. 확인되지 않으면 NULL이다.',
  PRIMARY KEY (country_code),
  FOREIGN KEY (region_id) REFERENCES market_region (region_id),
  FOREIGN KEY (currency_code) REFERENCES currency (currency_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE hs_code (
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '품목을 구분하는 HS 문자열이다. 2·4·6·10자리 코드를 함께 담는다.',
  depth TINYINT UNSIGNED GENERATED ALWAYS AS (CHAR_LENGTH(hs_code)) STORED COMMENT 'HS 코드의 자릿수다. 2·4·6·10 중 하나이며 DB가 코드 길이로 자동 계산한다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '코드의 공통 사전 판본 식별값. 예: HS2017, HS2022.',
  name_ko VARCHAR(500) NOT NULL COMMENT '화면에 표시하고 검색할 한국어 품목명이다. HS6만 HS4·HS6 품목명을 합친 요약명을 저장하고 HS2·HS4·HS10은 해당 분류의 원래 품목명을 저장한다.',
  original_name_ko TEXT NULL COMMENT '공식 원천의 HS6 한국어 품목명 원문이다. depth=6에만 저장하고 HS4·HS2·HS10은 NULL이다.',
  source_edition VARCHAR(80) NOT NULL COMMENT '품목명을 확인한 공식 사전의 판본·기준 정보를 저장한다.',
  PRIMARY KEY (hs_code, hs_ver),
  CHECK (CHAR_LENGTH(hs_code) IN (2,4,6,10) AND hs_code REGEXP '^[0-9]+$'),
  CHECK ((CHAR_LENGTH(hs_code)=6 AND original_name_ko IS NOT NULL AND CHAR_LENGTH(TRIM(original_name_ko))>0) OR (CHAR_LENGTH(hs_code)<>6 AND original_name_ko IS NULL)),
  KEY ix_hs_depth (depth, hs_ver, hs_code),
  CHECK (hs_ver REGEXP '^[A-Z0-9_]+$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE article_tag (
  tag_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '기사와 대상 하나의 연결을 식별하는 번호다.',
  article_id BIGINT UNSIGNED NOT NULL COMMENT '이 국가·품목·키워드가 연결된 기사 번호다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '기사와 연결된 국가 코드다. 다른 종류의 태그 행이면 NULL이다.',
  hs_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '기사 내용과 연결한 검증된 HS 품목 코드다. 다른 태그 종류면 NULL이다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT '연결한 hs_code의 사전 판본이다. 코드와 함께 한 쌍으로 저장한다.',
  keyword_text VARCHAR(120) COLLATE utf8mb4_bin NULL COMMENT '원천에서 제공하거나 단신속보 본문에서 추출·정규화한 키워드 하나다. 다른 태그 종류이면 NULL이다.',
  PRIMARY KEY (tag_id),
  UNIQUE KEY uq_tag_country (article_id, country_code),
  UNIQUE KEY uq_tag_hs (article_id, hs_code, hs_ver),
  UNIQUE KEY uq_tag_keyword (article_id, keyword_text),
  FOREIGN KEY (article_id) REFERENCES article (article_id) ON DELETE CASCADE,
  FOREIGN KEY (country_code) REFERENCES country (country_code),
  FOREIGN KEY (hs_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  CHECK ((country_code IS NOT NULL)+(hs_code IS NOT NULL)+(keyword_text IS NOT NULL)=1),
  CHECK (keyword_text IS NULL OR CHAR_LENGTH(TRIM(keyword_text))>0),
  CHECK ((hs_code IS NULL AND hs_ver IS NULL) OR (hs_code IS NOT NULL AND hs_ver IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE users (
  user_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '회원 한 명을 구분하는 내부 번호다. 자동으로 부여한다.',
  email VARCHAR(254) NOT NULL COMMENT '로그인에 사용하는 이메일 주소다. 같은 주소를 중복 등록하지 않는다.',
  password_hash VARCHAR(255) NOT NULL COMMENT 'bcrypt로 계산한 비밀번호 해시 문자열이다. 평문 비밀번호는 저장하지 않는다.',
  landing_page ENUM('GLOBAL','INTERESTED') NOT NULL DEFAULT 'INTERESTED' COMMENT '로그인 후 첫 화면을 GLOBAL 전체 시장 또는 INTERESTED 관심 시장으로 선택한다.',
  account_status ENUM('ACTIVE','WITHDRAWN','RESTRICTED') NOT NULL DEFAULT 'ACTIVE',
  admin_role ENUM('MEMBER','CONTENT_ADMIN','SUPER_ADMIN') NOT NULL DEFAULT 'MEMBER',
  withdrawn_at DATETIME NULL,
  restricted_at DATETIME NULL,
  restriction_reason VARCHAR(1000) NULL,
  KEY ix_users_status_withdrawn (account_status,withdrawn_at,user_id),
  CONSTRAINT ck_users_withdrawn CHECK (account_status<>'WITHDRAWN' OR withdrawn_at IS NOT NULL),
  CONSTRAINT ck_users_restricted CHECK ((account_status<>'RESTRICTED' OR (restricted_at IS NOT NULL AND CHAR_LENGTH(TRIM(restriction_reason))>0)) IS TRUE),
  PRIMARY KEY (user_id),
  UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

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

CREATE TABLE batch_run (
  run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '테이블·기간별 데이터 작업 실행 한 건의 번호다.',
  source_code VARCHAR(40) NOT NULL COMMENT '이 작업의 대상 출처 코드다. service_option의 SOURCE 행을 참조한다.',
  job_name VARCHAR(60) NOT NULL COMMENT '수행한 작업 이름이다. 예를 들어 NEWS_COLLECTION 또는 HS6_AGGREGATION이다.',
  target_table ENUM('hs_code','export_monthly','import_monthly','export_summary','import_summary','fx_daily','article','article_tag','news_product_daily','news_keyword_daily','model_run','report','hs_code_correlation') NOT NULL COMMENT '이 실행이 처리하는 실제 테이블 이름이다. 예: export_monthly.',
  period_start DATE NOT NULL COMMENT '이 작업이 처리하려는 기간의 시작일이다.',
  period_end DATE NOT NULL COMMENT '처리 대상 기간의 마지막 날이다. 시작일을 포함한 양끝 포함 범위다.',
  status ENUM('RUNNING','SUCCESS','FAILED') NOT NULL DEFAULT 'RUNNING' COMMENT 'RUNNING 실행 중, SUCCESS 정상 완료, FAILED 실패를 저장한다.',
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '이번 실행이 시작된 날짜와 시각을 초까지 저장한다.',
  finished_at DATETIME NULL COMMENT '이번 실행이 종료된 시각이다. 아직 실행 중이면 NULL이다.',
  request_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '이번 실행에서 실제 API를 호출한 횟수다. 재시도 호출도 센다.',
  received_count BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '원천에서 받은 원본 데이터 행 수다. 내부 집계면 읽은 입력 행 수로 정의한다.',
  job_id BIGINT UNSIGNED NULL,
  retry_of_run_id BIGINT UNSIGNED NULL,
  detail_json JSON NOT NULL,
  KEY ix_batch_job (job_id,started_at),
  KEY ix_batch_retry (retry_of_run_id),
  CONSTRAINT fk_batch_job FOREIGN KEY (job_id) REFERENCES async_job (job_id) ON DELETE SET NULL,
  CONSTRAINT fk_batch_retry FOREIGN KEY (retry_of_run_id) REFERENCES batch_run (run_id) ON DELETE SET NULL,
  CONSTRAINT ck_batch_run_detail_json CHECK (JSON_SCHEMA_VALID('{"type":"object","properties":{"schema_version":{"type":"integer","enum":[1]}},"required":["schema_version"],"additionalProperties":true}',detail_json) IS TRUE),
  PRIMARY KEY (run_id),
  FOREIGN KEY (source_code) REFERENCES service_option (option_code),
  KEY ix_batch_source (source_code, started_at),
  CHECK (period_end>=period_start),
  KEY ix_run_target_period (target_table, period_start, period_end, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE keyword_category (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  major_name VARCHAR(40) NOT NULL,
  minor_name VARCHAR(60) NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  synced_at DATETIME(6) NULL,
  origin ENUM('BASE','CUSTOM') NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  representative_terms_json JSON NOT NULL,
  revision INT UNSIGNED NOT NULL DEFAULT 1,
  created_by BIGINT UNSIGNED NULL,
  created_at DATETIME NULL,
  active_minor_name VARCHAR(60) GENERATED ALWAYS AS (CASE WHEN enabled THEN minor_name ELSE NULL END) STORED,
  UNIQUE KEY uq_category_active_name (major_name,active_minor_name),
  KEY ix_category_active (enabled,major_name,category_id),
  KEY ix_category_creator (created_by),
  CONSTRAINT fk_category_creator FOREIGN KEY (created_by) REFERENCES users (user_id) ON DELETE SET NULL,
  CONSTRAINT ck_category_revision CHECK (revision>=1),
  CONSTRAINT ck_category_name CHECK (CHAR_LENGTH(TRIM(major_name))>0 AND CHAR_LENGTH(TRIM(minor_name))>0),
  CONSTRAINT ck_category_enabled CHECK (enabled IN (0,1)),
  CONSTRAINT ck_keyword_category_representative_te CHECK (JSON_SCHEMA_VALID('{"type":"object","properties":{"schema_version":{"type":"integer","enum":[1]},"terms":{"type":"array","items":{"type":"string","minLength":1},"uniqueItems":true}},"required":["schema_version","terms"],"additionalProperties":true}',representative_terms_json) IS TRUE),
  CONSTRAINT ck_category_custom_terms CHECK (origin<>'CUSTOM' OR JSON_LENGTH(representative_terms_json,'$.terms')=0),
  PRIMARY KEY (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE keyword_topic (
  keyword_text VARCHAR(120) COLLATE utf8mb4_bin NOT NULL,
  canonical_text VARCHAR(120) COLLATE utf8mb4_bin NULL,
  category_id INT UNSIGNED NULL,
  status ENUM('PENDING','CLASSIFIED','UNCLASSIFIED','SYNONYM','HELD') NOT NULL,
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  synced_at DATETIME(6) NULL,
  revision INT UNSIGNED NOT NULL DEFAULT 1,
  policy_json JSON NOT NULL,
  updated_by BIGINT UNSIGNED NULL,
  KEY ix_topic_editor (updated_by),
  CONSTRAINT fk_topic_editor FOREIGN KEY (updated_by) REFERENCES users (user_id) ON DELETE SET NULL,
  CONSTRAINT ck_topic_revision CHECK (revision>=1),
  CONSTRAINT ck_topic_text CHECK (CHAR_LENGTH(TRIM(keyword_text))>0),
  CONSTRAINT ck_keyword_topic_policy_json CHECK (JSON_SCHEMA_VALID('{"type":"object","properties":{"schema_version":{"type":"integer","enum":[1]}},"required":["schema_version"],"additionalProperties":true}',policy_json) IS TRUE),
  PRIMARY KEY (keyword_text),
  KEY ix_keyword_topic_status (status),
  KEY category_id (category_id),
  CONSTRAINT keyword_topic_ibfk_1 FOREIGN KEY (category_id) REFERENCES keyword_category (category_id),
  CONSTRAINT keyword_topic_chk_1 CHECK ((status='SYNONYM') = (canonical_text IS NOT NULL)),
  CONSTRAINT keyword_topic_chk_2 CHECK ((status='CLASSIFIED') = (category_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DELIMITER $$
CREATE TRIGGER trg_batch_run_insert_v3 BEFORE INSERT ON batch_run FOR EACH ROW
BEGIN
  IF (NEW.retry_of_run_id IS NOT NULL AND NEW.retry_of_run_id=NEW.run_id) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Self retry reference is not allowed'; END IF;
END$$

CREATE TRIGGER trg_batch_run_update_v3 BEFORE UPDATE ON batch_run FOR EACH ROW
BEGIN
  IF (NEW.retry_of_run_id IS NOT NULL AND NEW.retry_of_run_id=NEW.run_id) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Self retry reference is not allowed'; END IF;
END$$

CREATE TRIGGER trg_article_insert_v3 BEFORE INSERT ON article FOR EACH ROW
BEGIN
  IF (NEW.first_ingested_at IS NULL) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='New articles require first_ingested_at'; END IF;
  IF (JSON_TYPE(JSON_EXTRACT(NEW.admin_meta_json,'$.classification_input'))<>'OBJECT' OR JSON_EXTRACT(NEW.admin_meta_json,'$.classification_input') IS NULL) IS TRUE THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='New articles require a classification snapshot'; END IF;
END$$
DELIMITER ;
