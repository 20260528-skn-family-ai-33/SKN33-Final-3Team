-- SKN / un_data 호환성 확인용 읽기 전용 추출 SQL (MySQL 8.x)
-- 실행 전에 실제 SKN 데이터베이스를 선택하세요. 이 파일에는 USE를 지정하지 않습니다.
-- 권장: SELECT 권한만 있는 계정, 전용 새 연결에서 전체 파일을 한 번에 실행.
-- 변경 SQL/임시 테이블/프로시저/파일 쓰기/배치 기록 없음.
-- SET/PREPARE/DEALLOCATE는 현재 연결의 설정·변수·준비문만 사용합니다.
-- READ ONLY 트랜잭션 종료 시 ROLLBACK. 원본 테이블은 변경하지 않습니다.
-- 조회 대상: country, hs_code, un_data.
-- 누락 테이블/필수 컬럼은 상태 메시지로 출력하고 다음 항목을 계속 조회합니다.
-- 테이블이 보이지 않으면 실제 누락 또는 계정의 조회 권한 부족일 수 있습니다.
-- DDL은 트랜잭션 스냅샷으로 고정되지 않으므로 스키마 변경 작업과 겹치지 않게 실행하세요.

-- DB 미선택은 서버 오류로 중단합니다. mysql CLI에서 --force를 사용하지 마세요.
SET @skn_export_guard = IF(DATABASE() IS NULL,
  'SELECT * FROM information_schema.`ERROR_SELECT_SKN_DATABASE_FIRST`',
  'SELECT DATABASE() AS selected_database');
PREPARE skn_database_guard FROM @skn_export_guard;
EXECUTE skn_database_guard;
DEALLOCATE PREPARE skn_database_guard;
SET @skn_export_guard = NULL;

SET NAMES utf8mb4;
START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY;

SELECT '00_CONTEXT' AS section,
       DATABASE() AS selected_database,
       IF(DATABASE() IS NULL, 'ERROR: 실제 SKN DB를 먼저 선택하세요', 'DATABASE_SELECTED') AS status,
       VERSION() AS mysql_version,
       CURRENT_TIMESTAMP AS extracted_at_server_time,
       @@session.time_zone AS session_time_zone,
       @@session.sql_mode AS sql_mode,
       @@character_set_database AS database_charset,
       @@collation_database AS database_collation;

SELECT '01_TABLE_STATUS' AS section, expected.table_name,
       IF(t.TABLE_NAME IS NULL, 'MISSING_OR_NOT_VISIBLE', 'VISIBLE') AS status,
       t.TABLE_TYPE, t.ENGINE, t.TABLE_COLLATION
FROM (
    SELECT 'country' AS table_name UNION ALL SELECT 'hs_code'
    UNION ALL SELECT 'un_data'
) AS expected
LEFT JOIN information_schema.TABLES AS t
  ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = expected.table_name
ORDER BY expected.table_name;

SELECT '02_COLUMNS' AS section, TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME,
       COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, CHARACTER_SET_NAME, COLLATION_NAME,
       COLUMN_KEY, EXTRA, GENERATION_EXPRESSION, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('country','hs_code','un_data')
ORDER BY TABLE_NAME, ORDINAL_POSITION;

SELECT '03_INDEXES' AS section, TABLE_NAME, INDEX_NAME, NON_UNIQUE,
       SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, INDEX_TYPE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('country','hs_code','un_data')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;

SELECT '04_FOREIGN_KEYS' AS section, TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION,
       COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
  AND TABLE_NAME IN ('country','hs_code','un_data')
ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION;

-- CHECK, FK 동작, ENUM, 생성식 등 정확한 전체 DDL
SELECT '05_DDL_country' AS section;
SET @skn_export_sql = IF(
  EXISTS(SELECT 1 FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'country'),
  'SHOW CREATE TABLE `country`',
  'SELECT ''country'' AS table_name, ''MISSING_OR_NOT_VISIBLE'' AS status'
);
PREPARE skn_readonly_export FROM @skn_export_sql;
EXECUTE skn_readonly_export;
DEALLOCATE PREPARE skn_readonly_export;

-- CHECK, FK 동작, ENUM, 생성식 등 정확한 전체 DDL
SELECT '06_DDL_hs_code' AS section;
SET @skn_export_sql = IF(
  EXISTS(SELECT 1 FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'hs_code'),
  'SHOW CREATE TABLE `hs_code`',
  'SELECT ''hs_code'' AS table_name, ''MISSING_OR_NOT_VISIBLE'' AS status'
);
PREPARE skn_readonly_export FROM @skn_export_sql;
EXECUTE skn_readonly_export;
DEALLOCATE PREPARE skn_readonly_export;

-- CHECK, FK 동작, ENUM, 생성식 등 정확한 전체 DDL
SELECT '07_DDL_un_data' AS section;
SET @skn_export_sql = IF(
  EXISTS(SELECT 1 FROM information_schema.TABLES
         WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'un_data'),
  'SHOW CREATE TABLE `un_data`',
  'SELECT ''un_data'' AS table_name, ''MISSING_OR_NOT_VISIBLE'' AS status'
);
PREPARE skn_readonly_export FROM @skn_export_sql;
EXECUTE skn_readonly_export;
DEALLOCATE PREPARE skn_readonly_export;

-- 실제 마스터 전체 키 목록. 데이터값 변경·필터링·판본 변환 없음.
SET @skn_export_sql = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'country'
     AND COLUMN_NAME IN ('country_code')) = 1,
  'SELECT ''10_COUNTRY_MASTER'' AS section, `country_code` FROM `country` ORDER BY `country_code`',
  'SELECT ''10_COUNTRY_MASTER'' AS section, ''TABLE_OR_REQUIRED_COLUMNS_MISSING_OR_NOT_VISIBLE'' AS status'
);
PREPARE skn_readonly_export FROM @skn_export_sql;
EXECUTE skn_readonly_export;
DEALLOCATE PREPARE skn_readonly_export;

-- 실제 마스터 전체 키 목록. 데이터값 변경·필터링·판본 변환 없음.
SET @skn_export_sql = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'hs_code'
     AND COLUMN_NAME IN ('hs_code','hs_ver')) = 2,
  'SELECT ''11_HS_MASTER'' AS section, `hs_code`, `hs_ver` FROM `hs_code` ORDER BY `hs_code`, `hs_ver`',
  'SELECT ''11_HS_MASTER'' AS section, ''TABLE_OR_REQUIRED_COLUMNS_MISSING_OR_NOT_VISIBLE'' AS status'
);
PREPARE skn_readonly_export FROM @skn_export_sql;
EXECUTE skn_readonly_export;
DEALLOCATE PREPARE skn_readonly_export;

ROLLBACK;
SET @skn_export_sql = NULL;
SELECT '99_EXPORT_COMPLETE' AS section, 'READ_ONLY_EXPORT_FINISHED' AS status;
