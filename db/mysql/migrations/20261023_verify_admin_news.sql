-- 읽기 전용 확인. 트리거 개수는 3이어야 관리자 기사·배치 ERD 적용이 완료된 것입니다.
SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA=DATABASE()
  AND ((TABLE_NAME='article' AND COLUMN_NAME IN ('visibility_status','first_ingested_at','admin_meta_json'))
    OR (TABLE_NAME='batch_run' AND COLUMN_NAME IN ('job_id','retry_of_run_id','detail_json')));

SELECT TRIGGER_NAME,EVENT_MANIPULATION,EVENT_OBJECT_TABLE,ACTION_TIMING
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA=DATABASE()
  AND TRIGGER_NAME IN ('trg_article_insert_v3','trg_batch_run_insert_v3','trg_batch_run_update_v3');

SELECT source_code,COUNT(*) AS articles FROM article GROUP BY source_code;
SELECT * FROM v_article_day ORDER BY published_date DESC;
SELECT * FROM v_article_country_day ORDER BY published_date DESC,country_code;

-- 0행이어야 합니다: 출처+원천 ID 중복 / 태그 중복 / 일별 키워드 집계 중복.
SELECT source_code,external_id,COUNT(*) AS duplicates
FROM article GROUP BY source_code,external_id HAVING COUNT(*)>1;
SELECT article_id,country_code,hs_code,hs_ver,keyword_text,COUNT(*) AS duplicates
FROM article_tag GROUP BY article_id,country_code,hs_code,hs_ver,keyword_text HAVING COUNT(*)>1;
SELECT metric_date,keyword_text,country_key,COUNT(*) AS duplicates
FROM news_keyword_daily GROUP BY metric_date,keyword_text,country_key HAVING COUNT(*)>1;

SELECT run_id,job_name,status,period_start,period_end,received_count,
       JSON_EXTRACT(detail_json,'$.schema_version') AS detail_version
FROM batch_run WHERE source_code='KOTRA_SHORT_NEWS' ORDER BY run_id DESC LIMIT 10;
