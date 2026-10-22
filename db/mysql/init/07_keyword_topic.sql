-- 해외시장뉴스 키워드 분류 (분류 틀 · 주제어 상태). 01_schema.sql 적용 후 대상 DB를 선택하고 실행합니다.
-- 적재: python -m backend.app.scheduler.jobs.keyword_topic_load  (사용 안내: backend/app/services/NEWS_DB_USAGE.md)
-- keyword_topic.keyword_text는 article_tag.keyword_text와 같은 표기(최종 주제어 = 상위어 적용 후)입니다.
-- updated_at은 행을 바꾼 시각, synced_at은 csv(services/news_keywords의 사전·분류표)와 맞춘 시각입니다.
-- synced_at이 NULL이거나 updated_at보다 이르면 관리자가 DB에서 바꾼 뒤 csv에 아직 반영하지 않은 행이며, 적재가 덮어쓰지 않습니다.
SET NAMES utf8mb4;

CREATE TABLE keyword_category (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '분류 틀 한 쌍(대분류 > 중분류)의 번호다.',
  major_name VARCHAR(40) NOT NULL COMMENT '대분류 이름이다. 예: 무역·통상.',
  minor_name VARCHAR(60) NOT NULL COMMENT '중분류 이름이다. 예: 관세·무역규제.',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '행을 마지막으로 바꾼 시각이다.',
  synced_at DATETIME(6) NULL COMMENT 'csv와 마지막으로 맞춘 시각이다. NULL이거나 updated_at보다 이르면 csv에 아직 반영하지 않은 DB 변경이다.',
  PRIMARY KEY (category_id),
  UNIQUE KEY uq_keyword_category (major_name, minor_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE keyword_topic (
  keyword_text VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '정제·지역 분리를 마친 주제어다. article_tag.keyword_text와 같은 표기다.',
  canonical_text VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL COMMENT '동의어(SYNONYM)면 대표어, 아니면 NULL이다.',
  category_id INT UNSIGNED NULL COMMENT '분야(CLASSIFIED)면 keyword_category 번호, 아니면 NULL이다.',
  status ENUM('PENDING','CLASSIFIED','UNCLASSIFIED','SYNONYM') NOT NULL COMMENT 'PENDING 검수 대기(관리자 분류 대상), CLASSIFIED 분야 있음, UNCLASSIFIED 분야 없음으로 확정, SYNONYM 다른 말의 동의어다.',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '행을 마지막으로 바꾼 시각이다.',
  synced_at DATETIME(6) NULL COMMENT 'csv와 마지막으로 맞춘 시각이다. NULL이거나 updated_at보다 이르면 csv에 아직 반영하지 않은 DB 변경이다.',
  PRIMARY KEY (keyword_text),
  KEY ix_keyword_topic_status (status),
  KEY category_id (category_id),
  CONSTRAINT keyword_topic_ibfk_1 FOREIGN KEY (category_id) REFERENCES keyword_category (category_id),
  CONSTRAINT keyword_topic_chk_1 CHECK ((status = 'SYNONYM') = (canonical_text IS NOT NULL)),
  CONSTRAINT keyword_topic_chk_2 CHECK ((status = 'CLASSIFIED') = (category_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
