-- 기존 DB에서 요청형 리포트 옵션을 활성화할 때 수동 적용한다.
-- 신규 DB에는 init/06_service_option_seed.sql이 같은 옵션을 등록한다.
-- 같은 코드가 이미 있으면 이전 value_json과 표시 순서를 유지하고 활성화한다.
INSERT INTO service_option (option_code, option_type, label_ko, value_json, sort_order, enabled)
VALUES ('RPT_ON_DEMAND', 'REPORT_TYPE', '요청형 리포트', JSON_OBJECT(), 40, TRUE)
ON DUPLICATE KEY UPDATE enabled = TRUE;

SELECT option_code, option_type, label_ko, value_json, sort_order, enabled
FROM service_option WHERE option_code = 'RPT_ON_DEMAND';
