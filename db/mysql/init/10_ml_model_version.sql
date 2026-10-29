-- A(유망도)·B(시장 유형) 모델별 버전 이력. 기존 DB에도 한 번 적용한다.
-- 기존 국가 군집용 model_run 테이블은 변경하지 않는다.
CREATE TABLE IF NOT EXISTS ml_model_version (
  version_id VARCHAR(64) NOT NULL COMMENT 'KST 생성 날짜_시간_마이크로초와 충돌 방지 ID',
  model_key VARCHAR(16) NOT NULL COMMENT 'model_a 또는 model_b',
  model_name VARCHAR(160) NOT NULL COMMENT '모델명과 생성 날짜_시간을 포함한 버전 이름',
  created_at DATETIME(6) NOT NULL COMMENT '모델 버전 생성 시각, Asia/Seoul(KST)',
  status VARCHAR(16) NOT NULL COMMENT 'READY: 학습 파일 생성, SKIPPED: 미학습·미채택',
  artifact_path TEXT NULL COMMENT '출력 루트 기준 버전 joblib 상대 경로. SKIPPED는 NULL',
  artifact_sha256 VARCHAR(64) NULL,
  parent_version_id VARCHAR(64) NULL COMMENT '직전 계열 버전. PARTIAL_FIT이면 기존 학습 상태를 이어받은 부모',
  training_mode VARCHAR(24) NOT NULL COMMENT 'FULL_RETRAIN / INITIAL_FIT / PARTIAL_FIT / UNCHANGED',
  train_start DATE NULL COMMENT '실제 학습 행의 최초 기준월',
  train_end DATE NULL COMMENT '실제 학습 행의 마지막 기준월',
  train_rows BIGINT NOT NULL COMMENT '실제 학습 행 수. SKIPPED는 0',
  data_info JSON NOT NULL COMMENT '입력 파일 해시·기간·행 수·추가 기준월·변경 파일·내용 변경 기준월',
  evaluation JSON NOT NULL COMMENT '평가 지표와 모델 채택 근거',
  skip_reason TEXT NULL,
  PRIMARY KEY (version_id, model_key),
  KEY ix_ml_version_created (model_key, created_at),
  CONSTRAINT ck_ml_version_model CHECK (model_key IN ('model_a','model_b')),
  CONSTRAINT ck_ml_version_status CHECK (status IN ('READY','SKIPPED')),
  CONSTRAINT ck_ml_version_rows CHECK (train_rows >= 0),
  CONSTRAINT ck_ml_version_artifact CHECK (
    (status='READY' AND artifact_path IS NOT NULL AND artifact_sha256 IS NOT NULL)
    OR (status='SKIPPED' AND artifact_path IS NULL AND artifact_sha256 IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
