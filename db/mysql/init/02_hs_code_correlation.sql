-- HS 판 간 6단위 대응표. HS 판이 바뀔 때(HS2017→HS2022, HS2022→HS2028 …) 판을 넘어 같은 품목을 이어 보기 위해 씁니다.
-- 원천: UN 통계국(UNSD) HS Conversion and Correlation Tables (WCO 대응표 Table I·II 기반, WCO 원본은 PDF만 제공)
-- 적재: python -m backend.app.scheduler.jobs.hs_correlation_load --new HS2022 --old HS2017

CREATE TABLE hs_code_correlation (
  new_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '새 HS 판이다. 예: HS2022.',
  new_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '새 판의 HS 6단위 코드다.',
  old_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '이전 HS 판이다. 예: HS2017.',
  old_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '이전 판의 HS 6단위 코드다.',
  relation ENUM('1:1','1:n','n:1','n:n') NOT NULL COMMENT '새 판:이전 판 대응 관계다. n:1은 이전 코드 하나가 새 코드 여러 개로 나뉜 경우다.',
  group_code VARCHAR(6) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '대응으로 서로 이어진 코드 묶음(비교 그룹)의 대표 코드다. 묶음 안 가장 작은 new_code이며 판을 넘는 집계 키로 쓴다. 이전 코드 100개 넘게와 이어진 신설 코드(예: 8549 전자 폐기물)는 묶지 않고 자기 코드다.',
  source_edition VARCHAR(80) NOT NULL COMMENT '대응표 원천 파일이다.',
  PRIMARY KEY (new_ver, new_code, old_ver, old_code),
  KEY ix_corr_old (old_ver, old_code, new_ver),
  KEY ix_corr_group (new_ver, old_ver, group_code),
  CHECK (new_code REGEXP '^[0-9]{6}$' AND old_code REGEXP '^[0-9]{6}$' AND group_code REGEXP '^[0-9]{6}$'),
  CHECK (new_ver REGEXP '^[A-Z0-9_]+$' AND old_ver REGEXP '^[A-Z0-9_]+$' AND new_ver <> old_ver)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='HS 판 간 6단위 대응표';

-- batch_run.target_table에 대응표 테이블을 추가합니다 (적재 실행 기록용). 기존 값 순서는 그대로 둡니다.
ALTER TABLE batch_run MODIFY target_table ENUM('hs_code','export_monthly','import_monthly','export_summary','import_summary',
  'fx_daily','article','article_tag','news_product_daily','news_keyword_daily','model_run','report','hs_code_correlation')
  NOT NULL COMMENT '이 실행이 처리하는 실제 테이블 이름이다. 예: export_monthly.';
