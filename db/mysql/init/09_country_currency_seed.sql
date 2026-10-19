-- 국가 ↔ 대표 통화 연결(country.currency_code). 08_currency_seed.sql 다음에 실행합니다. 재실행 가능.
-- 기준: CLDR(유니코드 공통 로케일 데이터) 2026-10-19 기준 국가별 법정통화의 첫 번째(대표) 통화.
-- 대표 통화가 ECOS 731Y001 제공 통화(currency)에 있을 때만 연결합니다. 없으면 다른 통화로 대체하지 않고 NULL로 둡니다.
-- CLDR 미반영 변경: 불가리아(BG)는 2026-01-01 유로 도입으로 EUR (EU 집행위원회 공지).
-- 여기 없는 국가의 기존 값은 바꾸지 않습니다 (04_country_seed.sql은 그대로 둠).
-- USD: 아메리칸 사모아, 보네르 신트외스타티위스, 에쿠아도르, 마이크로네시아, 괌, 영령 인도양, 마샬군도, 북마리아나 군도, 푸에르토리코, 팔라우, 엘살바도르, 투르크 & 카이코스 군도, 티모르, 마이너 아우틀링 합중국 군도, 미국, 영령 버진군도, 미령 버진군도, 짐바브웨
UPDATE country SET currency_code='USD' WHERE country_code IN ('AS','BQ','EC','FM','GU','IO','MH','MP','PR','PW','SV','TC','TL','UM','US','VG','VI','ZW');
-- JPY: 일본
UPDATE country SET currency_code='JPY' WHERE country_code IN ('JP');
-- EUR: 안도라, 오스트리아, 벨기에, 불가리아, 세인트 바르탤르미, 사이프러스, 독일, 에스토니아, 스페인, 핀란드, 프랑스, 불령 가이아나, 과델로프, 그리스, 크로아티아, 아일랜드, 이탈리아, 리투아니아, 룩셈부르그, 라트비아, 모나코, 몬테네그로, 세인트 마틴, 마티니크, 몰타, 네덜란드, 포루투갈, 불령 리유니온 코모도 제도, 슬로베니아, 슬로바키아, 산마리노, 불령 남부지역, 교황청, 메요트
UPDATE country SET currency_code='EUR' WHERE country_code IN ('AD','AT','BE','BG','BL','CY','DE','EE','ES','FI','FR','GF','GP','GR','HR','IE','IT','LT','LU','LV','MC','ME','MF','MQ','MT','NL','PT','RE','SI','SK','SM','TF','VA','YT');
-- CNY: 중국
UPDATE country SET currency_code='CNY' WHERE country_code IN ('CN');
-- GBP: 영국, 건지, 남조지아 & 남샌드위치 군도, 맨섬, 저어지
UPDATE country SET currency_code='GBP' WHERE country_code IN ('GB','GG','GS','IM','JE');
-- CAD: 캐나다
UPDATE country SET currency_code='CAD' WHERE country_code IN ('CA');
-- CHF: 스위스, 리히텐슈타인
UPDATE country SET currency_code='CHF' WHERE country_code IN ('CH','LI');
-- HKD: 홍콩
UPDATE country SET currency_code='HKD' WHERE country_code IN ('HK');
-- SEK: 스웨덴
UPDATE country SET currency_code='SEK' WHERE country_code IN ('SE');
-- AUD: 호주, 코스 군도, 크리스마스 아일랜드, 허드 앤 맥도날드 군도, 키리바티, 노폴크 아일랜드, 나우루, 투발루
UPDATE country SET currency_code='AUD' WHERE country_code IN ('AU','CC','CX','HM','KI','NF','NR','TV');
-- DKK: 덴마크, 파로에 군도, 그린랜드
UPDATE country SET currency_code='DKK' WHERE country_code IN ('DK','FO','GL');
-- NOK: 노르웨이, 스발비드 군도
UPDATE country SET currency_code='NOK' WHERE country_code IN ('NO','SJ');
-- SAR: 사우디아라비아
UPDATE country SET currency_code='SAR' WHERE country_code IN ('SA');
-- KWD: 쿠웨이트
UPDATE country SET currency_code='KWD' WHERE country_code IN ('KW');
-- BHD: 바레인
UPDATE country SET currency_code='BHD' WHERE country_code IN ('BH');
-- AED: 아랍에미리트 연합
UPDATE country SET currency_code='AED' WHERE country_code IN ('AE');
-- SGD: 싱가포르
UPDATE country SET currency_code='SGD' WHERE country_code IN ('SG');
-- MYR: 말레이시아
UPDATE country SET currency_code='MYR' WHERE country_code IN ('MY');
-- NZD: 쿡 아일랜드, 니우에, 뉴질랜드, 피트카이른, 토켈라우
UPDATE country SET currency_code='NZD' WHERE country_code IN ('CK','NU','NZ','PN','TK');
-- THB: 태국
UPDATE country SET currency_code='THB' WHERE country_code IN ('TH');
-- IDR: 인도네시아
UPDATE country SET currency_code='IDR' WHERE country_code IN ('ID');
-- TWD: 대만
UPDATE country SET currency_code='TWD' WHERE country_code IN ('TW');
-- MNT: 몽골
UPDATE country SET currency_code='MNT' WHERE country_code IN ('MN');
-- KZT: 카자흐스탄
UPDATE country SET currency_code='KZT' WHERE country_code IN ('KZ');
-- PHP: 필리핀
UPDATE country SET currency_code='PHP' WHERE country_code IN ('PH');
-- VND: 베트남
UPDATE country SET currency_code='VND' WHERE country_code IN ('VN');
-- BND: 브루나이
UPDATE country SET currency_code='BND' WHERE country_code IN ('BN');
-- INR: 부탄, 인도
UPDATE country SET currency_code='INR' WHERE country_code IN ('BT','IN');
-- PKR: 파키스탄
UPDATE country SET currency_code='PKR' WHERE country_code IN ('PK');
-- BDT: 방글라데시
UPDATE country SET currency_code='BDT' WHERE country_code IN ('BD');
-- MXN: 멕시코
UPDATE country SET currency_code='MXN' WHERE country_code IN ('MX');
-- BRL: 브라질
UPDATE country SET currency_code='BRL' WHERE country_code IN ('BR');
-- ARS: 아르헨티나
UPDATE country SET currency_code='ARS' WHERE country_code IN ('AR');
-- RUB: 러시아 연방
UPDATE country SET currency_code='RUB' WHERE country_code IN ('RU');
-- HUF: 헝가리
UPDATE country SET currency_code='HUF' WHERE country_code IN ('HU');
-- PLN: 폴란드
UPDATE country SET currency_code='PLN' WHERE country_code IN ('PL');
-- CZK: 체코공화국
UPDATE country SET currency_code='CZK' WHERE country_code IN ('CZ');
-- QAR: 카타르
UPDATE country SET currency_code='QAR' WHERE country_code IN ('QA');
-- ILS: 이스라엘, 팔레스타인 해방기구
UPDATE country SET currency_code='ILS' WHERE country_code IN ('IL','PS');
-- JOD: 요르단
UPDATE country SET currency_code='JOD' WHERE country_code IN ('JO');
-- TRY: 튀르키예
UPDATE country SET currency_code='TRY' WHERE country_code IN ('TR');
-- ZAR: 레소토, 나미비아, 남아프리카공화국
UPDATE country SET currency_code='ZAR' WHERE country_code IN ('LS','NA','ZA');
-- EGP: 이집트
UPDATE country SET currency_code='EGP' WHERE country_code IN ('EG');
