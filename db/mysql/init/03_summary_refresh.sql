-- 집계 테이블(export_summary·import_summary·news_product_daily·news_keyword_daily) 갱신 프로시저와 매일 03:00 스케줄입니다.
-- 원본 적재 작업이 batch_run에 SUCCESS로 남긴 기간만 골라 다시 집계합니다. 원본 적재 작업은 반드시 batch_run을 남겨야 합니다.
--   수출입 집계 ← export_monthly / import_monthly 실행 기록
--   기사 집계   ← article / article_tag 실행 기록 (시장뉴스·단신속보 모두 포함)
-- 적용: mysql -u root -p <DB이름> < db/mysql/init/03_summary_refresh.sql   (다시 실행해도 됩니다. 데이터는 건드리지 않습니다)
-- 수동 실행: CALL sp_refresh_trade_summary('export_summary', '2025-01-01', '2025-12-01');
--            CALL sp_refresh_news_daily('news_keyword_daily', '2026-10-01', '2026-10-14');
--   수동 실행도 성공 기록이 남으므로, 그 실행 시작 전에 끝난 원본 적재는 스케줄이 다시 집계하지 않습니다. 기간을 넉넉히 주세요.
-- 스케줄 확인: SELECT @@event_scheduler;  (ON이어야 실행됩니다. MySQL 8.4 기본값은 ON)
--             SHOW EVENTS;  실행 기록은 SELECT * FROM batch_run WHERE source_code = 'INTERNAL_AGG' ORDER BY run_id DESC;

-- 집계 실행 기록용 출처. batch_run.source_code가 service_option을 참조하므로 먼저 있어야 합니다.
INSERT INTO service_option (option_code, option_type, label_ko, value_json)
VALUES ('INTERNAL_AGG', 'SOURCE', '내부 집계 (DB 프로시저)', JSON_OBJECT('provider', 'DB', 'format', 'procedure'))
ON DUPLICATE KEY UPDATE option_code = option_code;

DROP EVENT IF EXISTS ev_refresh_summaries;
DROP PROCEDURE IF EXISTS sp_refresh_pending_summaries;
DROP PROCEDURE IF EXISTS sp_refresh_pending;
DROP PROCEDURE IF EXISTS sp_refresh_news_daily;
DROP PROCEDURE IF EXISTS sp_refresh_trade_summary;

DELIMITER $$

-- 수출입 HS6 집계. 기간 안의 월을 한 달씩(월마다 한 트랜잭션) 지우고 원본 합계로 다시 넣습니다.
-- p_target: 'export_summary' 또는 'import_summary'. 기간은 양끝 포함이며 월 중간 날짜를 줘도 그 달 전체를 집계합니다.
CREATE PROCEDURE sp_refresh_trade_summary(IN p_target VARCHAR(40), IN p_from DATE, IN p_to DATE)
BEGIN
  DECLARE v_run BIGINT UNSIGNED;
  DECLARE v_month DATE DEFAULT DATE_FORMAT(p_from, '%Y-%m-01');
  DECLARE v_read BIGINT UNSIGNED DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;  -- 실패한 달만 되돌립니다. 앞서 끝난 달은 이미 반영돼 있고, 재실행하면 같은 결과로 덮어씁니다.
    UPDATE batch_run SET status = 'FAILED', finished_at = NOW(), received_count = v_read WHERE run_id = v_run;
    RESIGNAL;
  END;

  IF p_target NOT IN ('export_summary', 'import_summary') OR p_from IS NULL OR p_to IS NULL OR p_from > p_to THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'p_target은 export_summary/import_summary, 기간은 p_from <= p_to여야 합니다';
  END IF;

  INSERT INTO batch_run (source_code, job_name, target_table, period_start, period_end)
  VALUES ('INTERNAL_AGG', 'TRADE_SUMMARY_REFRESH', p_target, v_month, LAST_DAY(p_to));
  SET v_run = LAST_INSERT_ID();

  WHILE v_month <= p_to DO
    START TRANSACTION;
    IF p_target = 'export_summary' THEN
      DELETE FROM export_summary WHERE trade_month = v_month;
      INSERT INTO export_summary (trade_month, country_code, hs6_code, hs_ver, amount_usd)
      SELECT trade_month, country_code, hs6_code, hs_ver, SUM(amount_usd)
      FROM export_monthly WHERE trade_month = v_month
      GROUP BY trade_month, country_code, hs6_code, hs_ver;
      SET v_read = v_read + (SELECT COUNT(*) FROM export_monthly WHERE trade_month = v_month);
    ELSE
      DELETE FROM import_summary WHERE trade_month = v_month;
      INSERT INTO import_summary (trade_month, country_code, hs6_code, hs_ver, amount_usd)
      SELECT trade_month, country_code, hs6_code, hs_ver, SUM(amount_usd)
      FROM import_monthly WHERE trade_month = v_month
      GROUP BY trade_month, country_code, hs6_code, hs_ver;
      SET v_read = v_read + (SELECT COUNT(*) FROM import_monthly WHERE trade_month = v_month);
    END IF;
    COMMIT;
    SET v_month = v_month + INTERVAL 1 MONTH;
  END WHILE;

  UPDATE batch_run SET status = 'SUCCESS', finished_at = NOW(), received_count = v_read WHERE run_id = v_run;
END$$

-- 기사 일별 집계. 기간 안의 날짜를 한 트랜잭션으로 지우고 다시 넣습니다.
-- p_target: 'news_product_daily' 또는 'news_keyword_daily'. 날짜는 기사 published_date 기준입니다.
-- 행마다 고유 기사 수(COUNT DISTINCT article_id)이며, 전체 국가 행(country_code NULL)과 국가 태그가 붙은 기사의 국가별 행을 함께 만듭니다.
-- 기사가 없는 날은 0 행을 만들지 않습니다(미수집과 0건을 구분하지 않기 위해).
CREATE PROCEDURE sp_refresh_news_daily(IN p_target VARCHAR(40), IN p_from DATE, IN p_to DATE)
BEGIN
  DECLARE v_run BIGINT UNSIGNED;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    UPDATE batch_run SET status = 'FAILED', finished_at = NOW() WHERE run_id = v_run;
    RESIGNAL;
  END;

  IF p_target NOT IN ('news_product_daily', 'news_keyword_daily') OR p_from IS NULL OR p_to IS NULL OR p_from > p_to THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'p_target은 news_product_daily/news_keyword_daily, 기간은 p_from <= p_to여야 합니다';
  END IF;

  INSERT INTO batch_run (source_code, job_name, target_table, period_start, period_end)
  VALUES ('INTERNAL_AGG', 'NEWS_DAILY_REFRESH', p_target, p_from, p_to);
  SET v_run = LAST_INSERT_ID();

  START TRANSACTION;
  IF p_target = 'news_product_daily' THEN
    DELETE FROM news_product_daily WHERE metric_date BETWEEN p_from AND p_to;
    INSERT INTO news_product_daily (metric_date, hs_code, hs_ver, country_code, day_count)
    SELECT a.published_date, h.hs_code, h.hs_ver, NULL, COUNT(DISTINCT a.article_id)
    FROM article a
    JOIN article_tag h ON h.article_id = a.article_id AND h.hs_code IS NOT NULL
    WHERE a.published_date BETWEEN p_from AND p_to
    GROUP BY a.published_date, h.hs_code, h.hs_ver
    UNION ALL
    SELECT a.published_date, h.hs_code, h.hs_ver, c.country_code, COUNT(DISTINCT a.article_id)
    FROM article a
    JOIN article_tag h ON h.article_id = a.article_id AND h.hs_code IS NOT NULL
    JOIN article_tag c ON c.article_id = a.article_id AND c.country_code IS NOT NULL
    WHERE a.published_date BETWEEN p_from AND p_to
    GROUP BY a.published_date, h.hs_code, h.hs_ver, c.country_code;
  ELSE
    DELETE FROM news_keyword_daily WHERE metric_date BETWEEN p_from AND p_to;
    INSERT INTO news_keyword_daily (metric_date, keyword_text, country_code, day_count)
    SELECT a.published_date, k.keyword_text, NULL, COUNT(DISTINCT a.article_id)
    FROM article a
    JOIN article_tag k ON k.article_id = a.article_id AND k.keyword_text IS NOT NULL
    WHERE a.published_date BETWEEN p_from AND p_to
    GROUP BY a.published_date, k.keyword_text
    UNION ALL
    SELECT a.published_date, k.keyword_text, c.country_code, COUNT(DISTINCT a.article_id)
    FROM article a
    JOIN article_tag k ON k.article_id = a.article_id AND k.keyword_text IS NOT NULL
    JOIN article_tag c ON c.article_id = a.article_id AND c.country_code IS NOT NULL
    WHERE a.published_date BETWEEN p_from AND p_to
    GROUP BY a.published_date, k.keyword_text, c.country_code;
  END IF;
  COMMIT;

  UPDATE batch_run SET status = 'SUCCESS', finished_at = NOW(),
    received_count = (SELECT COUNT(*) FROM article WHERE published_date BETWEEN p_from AND p_to)
  WHERE run_id = v_run;
END$$

-- 집계 대상 하나를 밀린 기간만큼 갱신합니다.
-- 마지막으로 성공한 집계가 시작된 뒤에 끝난 원본 적재(SUCCESS)들의 기간을 하나로 묶어 다시 집계합니다. 없으면 아무것도 안 합니다.
-- 집계 실패 기록은 무시하므로, 실패한 기간은 다음 실행 때 다시 시도합니다.
CREATE PROCEDURE sp_refresh_pending(IN p_sources VARCHAR(100), IN p_target VARCHAR(40))
BEGIN
  DECLARE v_from DATE;
  DECLARE v_to DATE;

  SELECT MIN(r.period_start), MAX(r.period_end) INTO v_from, v_to
  FROM batch_run r
  WHERE FIND_IN_SET(r.target_table, p_sources) AND r.status = 'SUCCESS'
    AND r.finished_at >= COALESCE((SELECT MAX(s.started_at) FROM batch_run s
                                   WHERE s.target_table = p_target AND s.status = 'SUCCESS'), '1000-01-01');

  IF v_from IS NOT NULL THEN
    IF p_target IN ('export_summary', 'import_summary') THEN
      CALL sp_refresh_trade_summary(p_target, v_from, v_to);
    ELSE
      CALL sp_refresh_news_daily(p_target, v_from, v_to);
    END IF;
  END IF;
END$$

-- 스케줄이 부르는 진입점. 이전 실행이 아직 돌고 있으면 이번 실행은 건너뜁니다.
-- 중간에 오류가 나면 나머지 대상은 이번 실행에서 빠지고 다음 실행 때 다시 시도합니다. 잠금은 세션이 끝나면 풀립니다.
CREATE PROCEDURE sp_refresh_pending_summaries()
BEGIN
  IF GET_LOCK('refresh_summaries', 0) = 1 THEN
    CALL sp_refresh_pending('export_monthly', 'export_summary');
    CALL sp_refresh_pending('import_monthly', 'import_summary');
    CALL sp_refresh_pending('article,article_tag', 'news_product_daily');
    CALL sp_refresh_pending('article,article_tag', 'news_keyword_daily');
    DO RELEASE_LOCK('refresh_summaries');
  END IF;
END$$

DELIMITER ;

-- 매일 03:00(DB 서버 시간대 기준, Asia/Seoul로 맞춰 두세요)에 밀린 집계를 갱신합니다. 첫 실행은 적용한 다음 날 03:00입니다.
CREATE EVENT ev_refresh_summaries
  ON SCHEDULE EVERY 1 DAY STARTS (CURRENT_DATE + INTERVAL 1 DAY + INTERVAL 3 HOUR)
  ON COMPLETION PRESERVE
  COMMENT '원본 적재 이후 밀린 수출입·기사 집계를 갱신합니다'
  DO CALL sp_refresh_pending_summaries();
