-- 기존 DB용 수동 적용. 백업·검토 후 실행하며 크롤러는 이 SQL을 자동 실행하지 않습니다.
-- MySQL DDL은 암묵적 커밋을 발생시킵니다.
-- 기사 제공 언론사. 기존 SOURCE 행의 설정은 덮어쓰지 않습니다.
INSERT IGNORE INTO service_option
    (option_code, option_type, label_ko, value_json, sort_order, enabled)
VALUES
    ('HANKYUNG', 'SOURCE', '한국경제',
     '{"url":"https://www.hankyung.com/","crawler_source":"hankyung"}', 140, 1),
    ('FNNEWS', 'SOURCE', '파이낸셜뉴스',
     '{"url":"https://www.fnnews.com/","crawler_source":"fnnews"}', 150, 1);

CREATE OR REPLACE VIEW v_article_country_day AS SELECT a.published_date,t.country_code,COUNT(DISTINCT a.article_id) AS article_count FROM article a JOIN article_tag t ON t.article_id=a.article_id WHERE a.source_code IN ('KOTRA_MARKET','HANKYUNG','FNNEWS') AND t.country_code IS NOT NULL GROUP BY a.published_date,t.country_code;
CREATE OR REPLACE VIEW v_article_day AS SELECT published_date,COUNT(DISTINCT article_id) AS article_count FROM article WHERE source_code IN ('KOTRA_MARKET','HANKYUNG','FNNEWS') GROUP BY published_date;
