-- service_option 공통 기준정보 12건. 01_schema.sql 적용 후 대상 DB를 선택하고 실행합니다.
-- UTF-8 파일이며 클라이언트 연결 문자셋은 utf8mb4를 사용합니다.
-- 같은 option_code가 있으면 기존 JSON·표시명·정렬·활성 상태를 모두 보존합니다.
-- 기존 설정 변경은 별도 UPDATE SQL을 작성하여 PR에서 검토합니다.
-- null은 미확정이며 무제한 또는 이용조건 확인 완료를 뜻하지 않습니다.
-- jobs=[]는 실행 설정 미구성입니다. 일정·수집 범위·호출 한도를 임의로 정하지 않습니다.
-- service_option은 실행 설정이고 batch_run은 실제 실행 이력입니다.
-- 이 seed는 작업 실행·스케줄러 등록·batch_run 기록·수출입 재계산을 하지 않습니다.
-- 미확정 발행 일정·무역/기사 임계값·약관·추가 출처는 기본 등록하지 않습니다.
-- SOURCE.jobs 검증은 해당 설정을 읽는 실행기 연동 시 구현합니다.

INSERT IGNORE INTO service_option
    (option_code, option_type, label_ko, value_json, sort_order, enabled)
VALUES
    -- CUSTOMS: backend/app/services/customs_reference.py의 CUSTOMS_SOURCE 코드·URL 유지.
    -- MONTHLY는 월별 실적의 자료 단위이며, 기존 스케줄러의 운영 일정을 복제하지 않습니다.
    ('CUSTOMS', 'SOURCE', '관세청',
     '{"url":"https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList","license":null,"daily_limit":null,"data_period":"MONTHLY","source_update_frequency":null,"jobs":[]}',
     10, TRUE),

    -- KCS_HS_NAMES: backend/app/scheduler/jobs/hs_code_load.py의 SOURCE 유지.
    -- 관세청 HS부호 단위별 품목명 파일(data.go.kr 15130660). 표시 순서는 적재 명세 기준.
    ('KCS_HS_NAMES', 'SOURCE', '관세청 HS부호 단위별 품목명',
     '{"provider":"관세청","url":"https://www.data.go.kr/data/15130660/fileData.do","dataset_id":"15130660","format":"xlsx","license":null,"daily_limit":null,"data_period":null,"source_update_frequency":null,"jobs":[]}',
     20, TRUE),

    -- UNSD_HS_CORR: backend/app/scheduler/jobs/hs_correlation_load.py의 SOURCE 유지.
    -- UN 통계국의 WCO HS correlation tables 기반 판 대응표. 표시 순서는 적재 명세 기준.
    ('UNSD_HS_CORR', 'SOURCE', 'UN 통계국 HS 판 간 대응표',
     '{"provider":"UN Statistics Division","url":"https://unstats.un.org/unsd/classifications/Econ","format":"xlsx","basis":"WCO HS correlation tables","license":null,"daily_limit":null,"data_period":null,"source_update_frequency":null,"jobs":[]}',
     30, TRUE),

    -- REPORT_PERIOD 근거: JSON 컬럼 작성 가이드 13장 / 적재 명세서 v1.2 4장.
    -- WEEKLY: 발행 요일·시각 미확정. trigger·weekday·time·timezone·news_only·report_type_code
    -- 설정을 확정한 뒤 별도 UPDATE를 검토하여 활성화합니다.
    ('WEEKLY', 'REPORT_PERIOD', '주간', '{}', 10, FALSE),
    -- MONTHLY: VALIDATED_MONTH_STATS·news_only=false·RPT_MONTHLY 참조를 확정한 뒤 활성화합니다.
    -- 실제 자료 공개 및 통계 검증 완료 판단은 소비 서비스의 책임입니다.
    ('MONTHLY', 'REPORT_PERIOD', '월간', '{}', 20, FALSE),
    -- QUARTERLY: 분기 발행 trigger의 운영 적용 미확정으로 비활성 등록합니다.
    ('QUARTERLY', 'REPORT_PERIOD', '분기', '{}', 30, FALSE),

    -- REPORT_TYPE 근거: DB설계서 service_option / 적재 명세서 v1.1 4장의 리포트 선택지.
    ('RPT_WEEKLY', 'REPORT_TYPE', '주간 리포트', '{}', 10, TRUE),
    ('RPT_MONTHLY', 'REPORT_TYPE', '월간 리포트', '{}', 20, TRUE),
    ('RPT_QUARTERLY', 'REPORT_TYPE', '분기 리포트', '{}', 30, TRUE),
    ('RPT_ON_DEMAND', 'REPORT_TYPE', '요청형 리포트', '{}', 40, TRUE),

    -- CUSTOMS_ANOMALY: 기존 적용 합의(R1 3배·복귀 ±20%, R2 88·89류, R3 5배).
    -- 참조 커밋: 638af63b158a03057189e6102dde0bf69d3d9db7.
    -- 임계값 사용 시 검증은 backend/app/services/customs_metrics.py의 validate_rules가 담당합니다.
    ('CUSTOMS_ANOMALY', 'RULE', '수출입 일회성 가능성',
     '{"r1":{"spike_ratio":3,"return_pct":20},"r2":{"hs_prefixes":["88","89"]},"r3":{"unit_ratio":5}}',
     10, TRUE),

    -- INTEREST_SCOPE: DB설계서 service_option / 적재 명세서 v1.1 4장.
    -- 최근 12개월 수출 상위 5개 연결 국가를 관심 범위에 포함합니다.
    ('INTEREST_SCOPE', 'RULE', '관심 범위 공통',
     '{"include_linked_countries":true,"top_country_count":5,"lookback_months":12}',
     20, TRUE);
