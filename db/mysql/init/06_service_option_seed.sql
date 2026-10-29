-- service_option 공통 기준정보 14건. 01_schema.sql 적용 후 대상 DB를 선택하고 실행합니다.
-- UTF-8 파일이며 클라이언트 연결 문자셋은 utf8mb4를 사용합니다.
-- 같은 option_code가 있으면 기존 JSON·표시명·정렬·활성 상태를 모두 보존합니다.
-- 기존 설정 변경은 별도 UPDATE SQL을 작성하여 PR에서 검토합니다.
-- null은 미확정이며 무제한 또는 이용조건 확인 완료를 뜻하지 않습니다.
-- SOURCE 코드·URL·제공 기관·파일 형식 등 근거가 확인된 정보는 유지합니다.
-- jobs의 일정·호출 관계·처리 범위는 기존 코드에서 확인한 값이며 가이드의 예시 운영값은 쓰지 않습니다.
-- service_option은 실행 설정이고 batch_run은 실제 실행 이력입니다.
-- 이 seed는 작업 실행·스케줄러 등록·batch_run 기록·수출입 재계산을 하지 않습니다.
-- 미확정 발행 일정·무역/기사 임계값·약관·추가 출처는 기본 등록하지 않습니다.
-- SOURCE.jobs 검증은 해당 설정을 읽는 실행기 연동 시 구현합니다.
-- 현재 실행기는 jobs를 읽지 않습니다. 아래 JSON을 바꿔도 실제 실행 일정은 바뀌지 않습니다.
-- [합의 필요 초안] 아래 jobs 확장 형식은 팀에서 합의된 실행 계약이 아닙니다.
-- MONTHLY(day/time/timezone), YEARLY(month/day/time/timezone),
-- WITH_JOB(job_name: 같은 호출·트랜잭션에 포함), ON_HS_REFRESH(출처/작업/상태 조건)는 제안 표현입니다.
-- 추가한 collection_scope.mode·세부 필드와 on_startup·retry의 형식도 합의가 필요합니다.
-- 값의 근거 확인과 JSON 형식 합의는 별개입니다. 서버 담당자와 허용값·필수 키·동작을 확정한 뒤
-- 소비 코드의 검증·실행을 연동해야 하며, 이 seed 등록만으로 실행기 연동이 완료되지는 않습니다.

INSERT IGNORE INTO service_option
    (option_code, option_type, label_ko, value_json, sort_order, enabled)
VALUES
    -- CUSTOMS: backend/app/services/customs_reference.py의 CUSTOMS_SOURCE 코드·URL 유지.
    -- 일정 근거: jobs/customs_monthly.py:register_jobs의 매월 10일 03:00(KST) 기본값.
    -- CUSTOMS_MONTHLY_DAY/HOUR/MINUTE 환경변수로 실제 일정이 달라질 수 있습니다.
    -- 시작 20초 후 및 월 실행 시 누락·미완료 월을 점검합니다. 실패/미공개는 재시도 가능할 때만
    -- 같은 대상 월로 1일 후 재시도하며 최대 30회입니다. SOURCE의 갱신 주기는 제공기관 기준이므로 null 유지.
    -- CSV 적재와 HS6 집계는 services/customs_load.py:publish_month의 같은 트랜잭션입니다.
    -- hs_code는 유효한 미등록 HS10이 있을 때 보완하는 조건부 저장 대상입니다.
    ('CUSTOMS', 'SOURCE', '관세청',
     '{
       "url":"https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList",
       "license":null,
       "daily_limit":null,
       "data_period":"MONTHLY",
       "source_update_frequency":null,
       "jobs":[
         {
           "job_name":"CUSTOMS_CSV_LOAD",
           "schedule":{"type":"MONTHLY","day":10,"time":"03:00","timezone":"Asia/Seoul"},
           "on_startup":{"delay_seconds":20},
           "retry":{"interval_days":1,"max_attempts":30,"retryable_only":true,"statuses":["FAILED","WAITING_FOR_PUBLICATION"]},
           "collects":["국가·HS10·판본별 월별 수출 금액(USD)과 중량(kg)","국가·HS10·판본별 월별 수입 금액(USD)과 중량(kg)","실적에서 확인한 유효한 미등록 HS10 품목명·판본 보완"],
           "collection_scope":{
             "mode":"PENDING_MONTHS",
             "end_anchor":"PREVIOUS_MONTH",
             "start_month_env":"CUSTOMS_START_MONTH",
             "start_month_fallback":"EARLIEST_LOCAL_MONTH_OR_PREVIOUS_MONTH",
             "country_roles":["COUNTRY","SPECIAL"],
             "hs_depth":10
           },
           "target_tables":["export_monthly","import_monthly","hs_code"]
         },
         {
           "job_name":"HS6_AGGREGATION",
           "schedule":{"type":"WITH_JOB","job_name":"CUSTOMS_CSV_LOAD"},
           "collects":["국가·HS6·판본별 월별 수출입 금액 합계"],
           "collection_scope":{"mode":"SAME_AS_JOB","job_name":"CUSTOMS_CSV_LOAD","hs_depth":6},
           "target_tables":["export_summary","import_summary"]
         },
         {
           "job_name":"CUSTOMS_METRICS",
           "schedule":{"type":"AFTER_JOB","after_job_name":"HS6_AGGREGATION"},
           "collects":["방향·국가·HS10·판본별 직전 12개월 실제 관측 이력 수와 금액 평균","과거 유효 중량의 단가 중앙값 및 일회성 가능성 판정"],
           "collection_scope":{
             "mode":"RELATIVE_TO_TARGET_YEAR",
             "write_start":{"year_offset":-1,"month":12},
             "write_end":{"year_offset":1,"month":12},
             "read_start":{"year_offset":-2,"month":12},
             "read_end":{"year_offset":2,"month":1},
             "lookback_months":12,
             "country_roles":["COUNTRY","SPECIAL"],
             "hs_depth":10
           },
           "target_tables":["export_monthly","import_monthly"]
         }
       ]
     }',
     10, TRUE),

    -- KCS_HS_NAMES: backend/app/scheduler/jobs/hs_code_load.py의 SOURCE 유지.
    -- 관세청 HS부호 단위별 품목명 파일(data.go.kr 15130660). 표시 순서는 적재 명세 기준.
    -- 일정 근거: scheduler/main.py의 HS_REFRESH_TRIGGER·RETRY_DAYS 및 시작 점검(10초 후).
    -- hs_code_refresh는 올해 판을 확인하며 이전·미래 연도 파일이면 적재하지 않습니다.
    -- HS_CODE_LOAD는 CSV 수동 적재이며 연간 갱신 안에서는 별도 실행 이력을 만들지 않습니다.
    ('KCS_HS_NAMES', 'SOURCE', '관세청 HS부호 단위별 품목명',
     '{
       "provider":"관세청",
       "url":"https://www.data.go.kr/data/15130660/fileData.do",
       "dataset_id":"15130660",
       "format":"xlsx",
       "license":null,
       "daily_limit":null,
       "data_period":null,
       "source_update_frequency":null,
       "jobs":[
         {
           "job_name":"HS_CODE_REFRESH",
           "schedule":{"type":"YEARLY","month":1,"day":1,"time":"00:15","timezone":"Asia/Seoul"},
           "on_startup":{"delay_seconds":10},
           "retry":{"interval_days":1,"time":"00:15","timezone":"Asia/Seoul","max_attempts":90,"statuses":["NOT_READY","FAILED"]},
           "collects":["올해 관세청 HS부호 단위별 품목명 xlsx","HS 2·4·6·10단위 코드·품목명·원문명·판본·출처 판","신규 HS6 품목명의 LLM 요약"],
           "collection_scope":{"mode":"CURRENT_YEAR_EDITION","timezone":"Asia/Seoul","hs_depths":[2,4,6,10]},
           "target_tables":["hs_code"]
         },
         {
           "job_name":"HS_CODE_LOAD",
           "schedule":{"type":"MANUAL"},
           "collects":["검증된 HS 마스터 CSV의 코드·품목명·원문명·판본·출처 판"],
           "collection_scope":{"mode":"INPUT_FILE_HS_EDITION","hs_depths":[2,4,6,10]},
           "target_tables":["hs_code"]
         }
       ]
     }',
     20, TRUE),

    -- UNSD_HS_CORR: backend/app/scheduler/jobs/hs_correlation_load.py의 SOURCE 유지.
    -- UN 통계국의 WCO HS correlation tables 기반 판 대응표. 표시 순서는 적재 명세 기준.
    -- 실제 다운로드 URL 템플릿: jobs/hs_correlation_load.py:URL.
    -- 자동 호출 근거: hs_code_refresh.py:run_refresh/ensure_correlation.
    -- 타 SOURCE의 작업이므로 가이드의 같은 출처용 AFTER_JOB와 구분하여 ON_HS_REFRESH로 기술합니다.
    -- 올해 판과 직전 판이 다르고 대응 쌍이 누락·부분 적재·변경된 경우 적재합니다. 수동 판본 지정도 지원합니다.
    ('UNSD_HS_CORR', 'SOURCE', 'UN 통계국 HS 판 간 대응표',
     '{
       "provider":"UN Statistics Division",
       "url":"https://unstats.un.org/unsd/classifications/Econ",
       "download_url_template":"https://unstats.un.org/unsd/classifications/Econ/tables/{new}to{old}ConversionAndCorrelationTables.xlsx",
       "format":"xlsx",
       "basis":"WCO HS correlation tables",
       "license":null,
       "daily_limit":null,
       "data_period":null,
       "source_update_frequency":null,
       "jobs":[
         {
           "job_name":"HS_CORRELATION_LOAD",
           "schedule":{
             "type":"ON_HS_REFRESH",
             "source_code":"KCS_HS_NAMES",
             "job_name":"HS_CODE_REFRESH",
             "statuses":["DONE","ALREADY_LOADED"],
             "condition":"DIFFERENT_VERSIONS_AND_PAIRS_MISSING_PARTIAL_OR_CHANGED",
             "manual_allowed":true
           },
           "collects":["신·구 HS 판의 6단위 대응 코드 쌍·관계·비교 그룹·출처 판"],
           "collection_scope":{"mode":"HS_VERSION_PAIR","new_version_from":"EXPLICIT_OR_CURRENT_HS_VERSION","old_version_from":"EXPLICIT_OR_PREVIOUS_HS_VERSION","hs_depth":6},
           "target_tables":["hs_code_correlation"]
         }
       ]
     }',
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


-- 기사 제공 언론사. 기존 SOURCE 행의 설정은 덮어쓰지 않습니다.
INSERT IGNORE INTO service_option
    (option_code, option_type, label_ko, value_json, sort_order, enabled)
VALUES
    ('HANKYUNG', 'SOURCE', '한국경제',
     '{"url":"https://www.hankyung.com/","crawler_source":"hankyung"}', 140, 1),
    ('FNNEWS', 'SOURCE', '파이낸셜뉴스',
     '{"url":"https://www.fnnews.com/","crawler_source":"fnnews"}', 150, 1);
