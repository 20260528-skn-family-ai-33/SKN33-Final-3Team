-- 관리자 ERD 마이그레이션 이후 수동 적용용; 크롤러가 DDL을 실행하지 않습니다.
CREATE OR REPLACE VIEW v_article_day AS SELECT published_date,COUNT(DISTINCT article_id) AS article_count FROM article WHERE source_code='KOTRA_MARKET' AND visibility_status='VISIBLE' GROUP BY published_date;
CREATE OR REPLACE VIEW v_article_country_day AS SELECT a.published_date,t.country_code,COUNT(DISTINCT a.article_id) AS article_count FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE a.source_code='KOTRA_MARKET' AND a.visibility_status='VISIBLE' AND t.country_code IS NOT NULL GROUP BY a.published_date,t.country_code;
-- 기존 v_article_day/v_article_country_day는 KOTRA_MARKET 전용입니다.
-- 일일 신문은 출처별 조회에만 포함하며 통합 시장·단신 알림 지표에 자동 편입하지 않습니다.
CREATE OR REPLACE VIEW v_article_source_day AS SELECT published_date,source_code,COUNT(DISTINCT article_id) AS article_count FROM article GROUP BY published_date,source_code;
CREATE OR REPLACE VIEW v_article_source_country_day AS SELECT a.published_date,a.source_code,t.country_code,COUNT(DISTINCT a.article_id) AS article_count FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE t.country_code IS NOT NULL GROUP BY a.published_date,a.source_code,t.country_code;
CREATE OR REPLACE VIEW v_article_integrated_day AS SELECT published_date,COUNT(DISTINCT article_id) AS article_count FROM article WHERE source_code IN ('KOTRA_MARKET','KOTRA_SHORT_NEWS') GROUP BY published_date;
CREATE OR REPLACE VIEW v_article_integrated_country_day AS SELECT a.published_date,t.country_code,COUNT(DISTINCT a.article_id) AS article_count FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE a.source_code IN ('KOTRA_MARKET','KOTRA_SHORT_NEWS') AND t.country_code IS NOT NULL GROUP BY a.published_date,t.country_code;
