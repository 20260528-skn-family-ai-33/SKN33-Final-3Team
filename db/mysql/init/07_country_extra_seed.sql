-- 외부 데이터(BACI·Comtrade·WDI)에만 있던 국가 코드를 country에 추가한다. 다시 실행해도 안전하다.
-- KR(한국 자신)과 JG(채널제도 = GG+JE 합계)는 SPECIAL이라 수집·ML 피처 대상(COUNTRY)에서 빠진다.
-- 03_market_region_seed.sql·04_country_seed.sql 다음에 실행한다.
INSERT INTO country (country_code,name_ko,country_role,region_id,currency_code) VALUES
('KR','대한민국','SPECIAL',30,NULL),
('KP','북한','COUNTRY',30,NULL),
('XK','코소보','COUNTRY',39,NULL),
('JG','채널제도','SPECIAL',154,NULL)
AS incoming ON DUPLICATE KEY UPDATE
name_ko=incoming.name_ko,
country_role=incoming.country_role,
region_id=COALESCE(incoming.region_id,country.region_id),
currency_code=COALESCE(incoming.currency_code,country.currency_code);
