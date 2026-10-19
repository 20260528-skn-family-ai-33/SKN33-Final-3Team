-- ECOS 731Y001(주요국 통화의 대원화환율) 일별 항목 코드 기준 통화 사전. 재실행 가능한 기준정보 초기값.
-- 대상 DB를 선택한 뒤 실행합니다. 기존 환율(fx_daily)은 삭제하지 않습니다.
-- quote_unit은 기존 환율 값의 의미(1단위·100단위)를 바꾸므로 재실행 때 갱신하지 않습니다.
INSERT INTO currency (currency_code,name_ko,ecos_item_code,quote_unit) VALUES
('USD','미국 달러','0000001',1),
('JPY','일본 엔','0000002',100),
('EUR','유로','0000003',1),
('CNY','중국 위안','0000053',1)
AS incoming ON DUPLICATE KEY UPDATE
name_ko=incoming.name_ko,
ecos_item_code=incoming.ecos_item_code;