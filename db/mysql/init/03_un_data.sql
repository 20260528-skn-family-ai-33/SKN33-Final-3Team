-- MySQL 8.4; 설계 원문의 테이블 정의. 부모 테이블은 별도 준비.
SET NAMES utf8mb4;

CREATE TABLE un_data (
  freq_code CHAR(1) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'M 월별, A 연간. 기간이 같아도 두 주기를 구분한다.',
  period_start DATE NOT NULL COMMENT '월별은 해당 월 1일, 연간은 해당 연도 1월 1일. 연간을 1월 실적으로 해석하지 않는다.',
  country_code VARCHAR(12) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '대상 수입국. UN reporterCode를 대상목록 iso2로 매핑한 뒤 기존 country PK 존재 여부를 검증한다.',
  hs6_code VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '대상 품목의 6자리 문자열. 참조 대상 자료형을 따르며 CHECK로 6자리를 제한한다.',
  hs_ver VARCHAR(10) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '원본 HS 판본. H3=HS2007,H4=HS2012,H5=HS2017,H6=HS2022. 임의 판본 변환 금지.',
  total_import_usd DECIMAL(24,3) NULL COMMENT '동일 대상국·품목·기간·판본의 partnerCode=0, flowCode=M인 primaryValue. USD. 원본 누락은 NULL.',
  korea_import_usd DECIMAL(24,3) NULL COMMENT 'partnerCode=410, flowCode=M의 금액을 전체 수입액과 같은 CIF 또는 FOB 기준으로 선택. USD. 미관측은 NULL.',
  korea_share_pct DECIMAL(12,6) GENERATED ALWAYS AS (
    CASE WHEN total_import_usd > 0 AND korea_import_usd IS NOT NULL
      THEN ROUND(CAST(korea_import_usd AS DECIMAL(36,12)) * 100 / total_import_usd, 6)
      ELSE NULL END
  ) STORED COMMENT '한국산 비중. 26.826927은 26.826927%다. 금액 변경 시 자동 재계산하며 직접 INSERT하지 않는다.',
  valuation_basis ENUM('CIF','FOB') NOT NULL COMMENT '두 금액에 공통 적용한 평가 기준. 원본 전체 primaryValue와 일치하는 CIF 우선, 아니면 FOB. 기간 비교도 동일 기준만 허용.',
  PRIMARY KEY (freq_code, period_start, country_code, hs6_code, hs_ver),
  CONSTRAINT fk_un_data_country FOREIGN KEY (country_code) REFERENCES country (country_code),
  CONSTRAINT fk_un_data_hs FOREIGN KEY (hs6_code, hs_ver) REFERENCES hs_code (hs_code, hs_ver),
  KEY ix_un_data_country (country_code, hs6_code, hs_ver, freq_code, period_start),
  KEY ix_un_data_hs (hs6_code, hs_ver, freq_code, period_start, country_code),
  CONSTRAINT ck_un_data_frequency CHECK (freq_code IN ('M','A')),
  CONSTRAINT ck_un_data_period CHECK (DAYOFMONTH(period_start)=1 AND (freq_code='M' OR MONTH(period_start)=1)),
  CONSTRAINT ck_un_data_hs6 CHECK (CHAR_LENGTH(hs6_code)=6 AND hs6_code REGEXP '^[0-9]{6}$'),
  CONSTRAINT ck_un_data_hs_ver CHECK (hs_ver REGEXP '^HS[0-9]{4}$'),
  CONSTRAINT ck_un_data_has_value CHECK (total_import_usd IS NOT NULL OR korea_import_usd IS NOT NULL),
  CONSTRAINT ck_un_data_world_nonnegative CHECK (total_import_usd IS NULL OR total_import_usd>=0),
  CONSTRAINT ck_un_data_korea_nonnegative CHECK (korea_import_usd IS NULL OR korea_import_usd>=0),
  CONSTRAINT ck_un_data_subset CHECK (total_import_usd IS NULL OR korea_import_usd IS NULL OR korea_import_usd<=total_import_usd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 9열 통합 RAW에서 1~7번째 열과 9번째 valuation_basis만 명시하여 INSERT한다.
-- 8번째 비중 열은 DB가 재계산한다. valuation_basis는 PK 구성원이 아니다.
-- 실제 source 값 예시. 예시는 검토용이며 실행되는 INSERT는 제공하지 않는다.
-- ('M','2025-01-01','US','850710','HS2022',243839065.000,65414529.000,'CIF')
-- -> korea_share_pct = 26.826927
-- PK: 위의 첫 5개 값. FK: US -> country.country_code;
-- ('850710','HS2022') -> hs_code(hs_code,hs_ver).
