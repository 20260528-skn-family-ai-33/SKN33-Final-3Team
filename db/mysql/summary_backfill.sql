-- 이미 적재된 원본으로 기사 집계 테이블 2개를 처음부터 다시 채웁니다. 한 번만 실행하면 됩니다 (다시 실행해도 같은 결과로 덮어씁니다).
-- 순서: db/mysql/init/07_summary_refresh.sql 적용(INTERNAL_AGG 출처 등록) → 이 파일 실행
-- 실행: mysql -u root -p <DB이름> < db/mysql/summary_backfill.sql
-- 테이블마다 한 트랜잭션으로 전체를 지우고 다시 넣습니다. 중간에 실패하면 그 테이블은 이전 상태로 돌아갑니다.
-- 끝나면 batch_run에 SUCCESS를 남겨, 매일 스케줄이 이미 집계한 원본 기간을 다시 집계하지 않게 합니다.
-- 수출입 집계(1·2)는 관세 적재(customs_load.merge_summary)가 이미 채우므로 주석 처리했습니다. 끝의 확인 쿼리로 차이만 봅니다.
--   어긋나 있으면 CALL sp_refresh_trade_summary('export_summary', '<첫 월>', '<마지막 월>'); 로 월 단위로 다시 채우세요.

-- 1. 수출 HS6 집계 ← export_monthly
-- SET @started = NOW();
-- START TRANSACTION;
-- DELETE FROM export_summary;
-- INSERT INTO export_summary (trade_month, country_code, hs6_code, hs_ver, amount_usd)
-- SELECT trade_month, country_code, hs6_code, hs_ver, SUM(amount_usd)
-- FROM export_monthly
-- GROUP BY trade_month, country_code, hs6_code, hs_ver;
-- COMMIT;
-- INSERT INTO batch_run (source_code, job_name, target_table, period_start, period_end, status, started_at, finished_at, received_count)
-- SELECT 'INTERNAL_AGG', 'SUMMARY_BACKFILL', 'export_summary', MIN(trade_month), LAST_DAY(MAX(trade_month)), 'SUCCESS', @started, NOW(), COUNT(*)
-- FROM export_monthly HAVING COUNT(*) > 0;

-- 2. 수입 HS6 집계 ← import_monthly
-- SET @started = NOW();
-- START TRANSACTION;
-- DELETE FROM import_summary;
-- INSERT INTO import_summary (trade_month, country_code, hs6_code, hs_ver, amount_usd)
-- SELECT trade_month, country_code, hs6_code, hs_ver, SUM(amount_usd)
-- FROM import_monthly
-- GROUP BY trade_month, country_code, hs6_code, hs_ver;
-- COMMIT;
-- INSERT INTO batch_run (source_code, job_name, target_table, period_start, period_end, status, started_at, finished_at, received_count)
-- SELECT 'INTERNAL_AGG', 'SUMMARY_BACKFILL', 'import_summary', MIN(trade_month), LAST_DAY(MAX(trade_month)), 'SUCCESS', @started, NOW(), COUNT(*)
-- FROM import_monthly HAVING COUNT(*) > 0;

-- 3. 품목·국가별 일별 기사 수 ← article + article_tag (HS 태그)
--    전체 국가 행(country_code NULL)과, 같은 기사에 국가 태그도 있으면 국가별 행을 만듭니다. 값은 고유 기사 수입니다.
SET @started = NOW();
START TRANSACTION;
DELETE FROM news_product_daily;
INSERT INTO news_product_daily (metric_date, hs_code, hs_ver, country_code, day_count)
SELECT a.published_date, h.hs_code, h.hs_ver, NULL, COUNT(DISTINCT a.article_id)
FROM article a
JOIN article_tag h ON h.article_id = a.article_id AND h.hs_code IS NOT NULL
GROUP BY a.published_date, h.hs_code, h.hs_ver
UNION ALL
SELECT a.published_date, h.hs_code, h.hs_ver, c.country_code, COUNT(DISTINCT a.article_id)
FROM article a
JOIN article_tag h ON h.article_id = a.article_id AND h.hs_code IS NOT NULL
JOIN article_tag c ON c.article_id = a.article_id AND c.country_code IS NOT NULL
GROUP BY a.published_date, h.hs_code, h.hs_ver, c.country_code;
COMMIT;
INSERT INTO batch_run (source_code, job_name, target_table, period_start, period_end, status, started_at, finished_at, received_count)
SELECT 'INTERNAL_AGG', 'SUMMARY_BACKFILL', 'news_product_daily', MIN(published_date), MAX(published_date), 'SUCCESS', @started, NOW(), COUNT(*)
FROM article HAVING COUNT(*) > 0;

-- 4. 키워드·국가별 일별 기사 수 ← article + article_tag (키워드 태그)
SET @started = NOW();
START TRANSACTION;
DELETE FROM news_keyword_daily;
INSERT INTO news_keyword_daily (metric_date, keyword_text, country_code, day_count)
SELECT a.published_date, k.keyword_text, NULL, COUNT(DISTINCT a.article_id)
FROM article a
JOIN article_tag k ON k.article_id = a.article_id AND k.keyword_text IS NOT NULL
GROUP BY a.published_date, k.keyword_text
UNION ALL
SELECT a.published_date, k.keyword_text, c.country_code, COUNT(DISTINCT a.article_id)
FROM article a
JOIN article_tag k ON k.article_id = a.article_id AND k.keyword_text IS NOT NULL
JOIN article_tag c ON c.article_id = a.article_id AND c.country_code IS NOT NULL
GROUP BY a.published_date, k.keyword_text, c.country_code;
COMMIT;
INSERT INTO batch_run (source_code, job_name, target_table, period_start, period_end, status, started_at, finished_at, received_count)
SELECT 'INTERNAL_AGG', 'SUMMARY_BACKFILL', 'news_keyword_daily', MIN(published_date), MAX(published_date), 'SUCCESS', @started, NOW(), COUNT(*)
FROM article HAVING COUNT(*) > 0;

-- 확인: 원본 합계와 집계 합계가 같아야 합니다 (diff_usd = 0).
SELECT 'export' AS direction, (SELECT COALESCE(SUM(amount_usd), 0) FROM export_monthly) - (SELECT COALESCE(SUM(amount_usd), 0) FROM export_summary) AS diff_usd
UNION ALL
SELECT 'import', (SELECT COALESCE(SUM(amount_usd), 0) FROM import_monthly) - (SELECT COALESCE(SUM(amount_usd), 0) FROM import_summary);
SELECT 'news_product_daily' AS target, COUNT(*) AS row_count FROM news_product_daily
UNION ALL
SELECT 'news_keyword_daily', COUNT(*) FROM news_keyword_daily;
