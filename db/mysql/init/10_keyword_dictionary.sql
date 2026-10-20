-- 키워드 사전(분류 틀·주제어) 테이블. 원본은 이 테이블과 backend/app/services/news_keywords/의 csv 둘 다이고,
-- keyword_dict.py sync가 한쪽 변경을 다른 쪽에 반영한다. 행은 sync가 채우므로 여기서는 테이블만 만든다.
-- 새 DB는 01_schema.sql이 같은 정의로 만든다. 이 파일은 01을 이미 실행한 기존 DB용이다 (이미 있으면 아무것도 하지 않음).
CREATE TABLE IF NOT EXISTS keyword_category (
  category_id INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '분류 틀 한 쌍(대분류 > 중분류)의 번호다.',
  major_name VARCHAR(40) NOT NULL COMMENT '대분류 이름이다. 예: 무역·통상.',
  minor_name VARCHAR(60) NOT NULL COMMENT '중분류 이름이다. 예: 관세·무역규제.',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '행을 마지막으로 바꾼 시각이다.',
  synced_at DATETIME(6) NULL COMMENT 'csv와 마지막으로 맞춘 시각이다. NULL이거나 updated_at보다 이르면 csv에 아직 반영하지 않은 DB 변경이다.',
  PRIMARY KEY (category_id),
  UNIQUE KEY uq_keyword_category (major_name, minor_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS keyword_topic (
  keyword_text VARCHAR(120) COLLATE utf8mb4_bin NOT NULL COMMENT '정제·지역 분리를 마친 주제어다. article_tag.keyword_text와 같은 표기다.',
  canonical_text VARCHAR(120) COLLATE utf8mb4_bin NULL COMMENT '동의어(SYNONYM)면 대표어, 아니면 NULL이다.',
  category_id INT UNSIGNED NULL COMMENT '분야(CLASSIFIED)면 keyword_category 번호, 아니면 NULL이다.',
  status ENUM('PENDING','CLASSIFIED','UNCLASSIFIED','SYNONYM') NOT NULL COMMENT 'PENDING 검수 대기(관리자 분류 대상), CLASSIFIED 분야 있음, UNCLASSIFIED 분야 없음으로 확정, SYNONYM 다른 말의 동의어다.',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '행을 마지막으로 바꾼 시각이다.',
  synced_at DATETIME(6) NULL COMMENT 'csv와 마지막으로 맞춘 시각이다. NULL이거나 updated_at보다 이르면 csv에 아직 반영하지 않은 DB 변경이다.',
  PRIMARY KEY (keyword_text),
  KEY ix_keyword_topic_status (status),
  FOREIGN KEY (category_id) REFERENCES keyword_category (category_id),
  CHECK ((status='SYNONYM') = (canonical_text IS NOT NULL)),
  CHECK ((status='CLASSIFIED') = (category_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
