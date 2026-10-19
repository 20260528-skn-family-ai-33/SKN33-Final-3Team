-- 수출입 작업의 기존 DB 호환용 변경. 원래 ENUM 값은 유지하고 누락된 기록 대상만 추가한다.
-- 01_schema.sql 또는 기존 batch_run 테이블이 준비된 DB에서 실행한다.
SET @batch_target_type = (SELECT COLUMN_TYPE FROM information_schema.columns
  WHERE table_schema=DATABASE() AND table_name='batch_run' AND column_name='target_table');
SET @batch_target_sql = IF(LOCATE("'hs_code_correlation'", @batch_target_type)>0, 'SELECT 1',
  CONCAT('ALTER TABLE batch_run MODIFY target_table ', LEFT(@batch_target_type,CHAR_LENGTH(@batch_target_type)-1),
  ",'hs_code_correlation') NOT NULL COMMENT '이 실행이 처리하는 실제 테이블 이름이다. 예: export_monthly.'"));
PREPARE batch_target_statement FROM @batch_target_sql;
EXECUTE batch_target_statement;
DEALLOCATE PREPARE batch_target_statement;
