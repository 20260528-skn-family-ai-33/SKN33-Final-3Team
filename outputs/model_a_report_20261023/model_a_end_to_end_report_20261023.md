# 모델 A 데이터와 학습 과정 상세 보고서

작성일 2026년 10월 23일  |  대상 feat/20-collect-external-data  |  HEAD 159316c

이 보고서는 현재 저장된 모델 A가 어떤 자료로 만들어졌고, 무엇을 정답으로 학습했으며, 어떤 범위에서 사용할 수 있는지 설명한다. 원본 CSV, 피처 스냅샷, 저장 모델, 예측 CSV, 학습 이력과 현재 코드를 대조했다. 모델을 새로 학습하거나 기존 파일을 수정하지 않았다.

현재 모델 A는 한국의 기존 수출 국가와 품목 조합에 대해 다음 12개월 수출의 로그 성장값을 예측하는 HistGradientBoostingRegressor다. 예측 점수가 높은 순으로 국가별 유망 품목과 품목별 유망 국가를 추천한다. 성공 확률이나 수출액 자체를 정답으로 학습한 모델은 아니다.

| 질문 | 확인된 답 |
| --- | --- |
| 입력 한 행 | 국가 × 비교용 HS6 × 기준월 |
| 실제 입력 | 관세청 수출입·중량, 단신속보 집계, 산업 관계표, 국가 지역·HS2 |
| 정답 label | ln(1 + 미래 12개월 수출액) - ln(1 + 최근 12개월 수출액) |
| 모델 | 히스토그램 기반 그래디언트 부스팅 회귀, 300회 학습 |
| 학습 규모 | 정답이 있는 1,122,200행, 기준월 2023-12~2025-09 |
| 현재 예측 | 2026-09 기준 51,153쌍, 217개국·3,802개 비교용 품목 |
| 추천 대상 조건 | 수출 기록이 존재하고 최근 12개월 수출액 100,000달러 이상 |
| 실제 피처 | 45개 후보 중 34개 유지. 그중 anomaly_ratio는 전부 0 |
| 주요 한계 | 외부 시장·WDI·환율 미사용, 독립 최종 Holdout 미확보 |

### 먼저 알아둘 세 가지

첫째, BACI·Comtrade·WDI를 수집했어도 현재 모델에 그 값이 들어갔다는 뜻은 아니다. 이번 학습에서는 11개 관련 피처가 모두 결측이라 제거됐다. 둘째, NDCG@5 0.7106은 정확도 71%가 아니다. 실제 상위 5개와 정확히 겹치는 비율인 국가별 Precision@5는 14.55%다. 셋째, 기존 시장의 성장 추천과 미진출 시장 발굴은 다른 문제이며 현재 A는 전자다.

읽는 순서: 2~4장 데이터 준비 → 5~8장 피처 → 9장 정답 → 10~13장 모델과 검증 → 14~16장 사용과 갱신 → 17~19장 확인 결과·개선 과제·근거. [S1~S12]

## 1 예측 문제와 데이터 한 행의 의미

### 무엇을 예측하는가

예를 들어 베트남에 대한 한국산 HS6 750620의 수출 실적을 기준월까지 관찰한다. 과거 수출 흐름, 관련 품목 수출입, 기사량 등을 입력해 앞으로 12개월 수출 규모가 직전 12개월에 비해 얼마나 변할지 예측한다. 국가별 모델을 따로 만드는 구조가 아니라 모든 국가·품목 행을 합쳐 하나의 회귀 모델을 학습한다.

| 개념 | 현재 구현의 뜻 |
| --- | --- |
| 국가 country_code | 한국의 수출 상대국. ISO2 코드이며 VN은 베트남 |
| 품목 hs6 | 원본 HS10의 앞 6자리에서 시작하되 HS 판 변경을 대응 묶음으로 통일한 코드 |
| 기준월 anchor | 입력 정보를 계산하는 월. 2025-09-01은 9월 월간 실적을 포함한 기준월 표기 |
| 피처 X | 기준월까지의 과거 정보에서 만든 숫자 또는 범주 |
| label y_growth | 기준월 뒤 12개월의 실제 수출과 기준월까지 12개월 수출의 로그 차이 |
| score | 모델이 예측한 y_growth. 클수록 성장 순위가 높음 |
| rank | 같은 국가 또는 같은 품목 안에서 score를 내림차순 정렬한 순위 |

### 어디까지 추천하는가

후보는 관세청 수출 입력에 한 번 이상 등장한 국가·품목 조합에서 시작한다. 각 기준월의 최근 12개월 수출 합계가 10만 달러 이상인 조합만 학습·예측 테이블에 남긴다. 세계에 존재하는 모든 국가와 품목의 조합을 만들어 신규 시장을 찾는 방식은 아니다. 예측이 없는 쌍을 낮은 유망도로 해석하면 안 된다.

품목 점수는 한국 전체의 국가·품목 수출 패턴에 대한 값이다. 개별 회사의 가격, 생산 능력, 인증, 유통망, 마진, 고객 계약이나 기업별 수출 이력은 입력에 없다. 따라서 기업의 진출 성공 확률이나 수익률로 사용할 수 없다.

### 학습에 직접 넣지 않는 열

country_code, hs6, anchor는 행 식별·집계·분할·정렬에 사용한다. 세 열 자체는 모델 피처가 아니다. 국가 정보는 region_id, 품목 정보는 hs2와 실적 파생값 등으로 반영한다. 미래 정보인 y_growth, future_cv, future_mkt_growth, future_share는 A의 입력 FEATURES 목록에 포함되지 않는다. [S1, S2]

## 2 원본 데이터와 실제 사용 여부

| 자료 | 원본과 역할 | 이번 A에 사용 |
| --- | --- | --- |
| 관세청 수출입 | 2022·2023·2024·2025·2026년 1~9월 CSV 5개. 월, 국가, HS10, 수출액·중량·수입액 | 사용 |
| 단신속보 | shortnews_raw.csv와 HS 태깅·토픽 JSONL. 게시월별 기사 수 계산 | 사용 |
| 관련 산업표 | related_hs4.csv. 14개 산업, 100행의 curated 관계 | 사용 |
| 국가 시드 | COUNTRY 국가만 선택. 지역과 국가명 연결 | 사용 |
| HS 기준정보 | HS2017 잔존 코드와 HS2022↔HS2017 대응표 | 사용 |
| 키워드 사전 | 기사 토픽을 무역·통상, 경제·시장 등 대분류로 연결 | 사용 |
| BACI·Comtrade | 세계 수입·한국발 수입, 시장 성장과 점유율. 연결 연도 Comtrade 포함 | 파일은 읽음 / 피처 제외 |
| WDI | GDP·성장률·인구·1인당 GDP | 파일은 읽음 / 피처 제외 |
| 환율 | fx_daily 월평균과 국가 통화 연결을 DB에서 조회 | 0행 / 피처 제외 |

ML의 기본 입력은 수집 CSV·xlsx·JSONL·SQL 시드 파일이다. 관세청 export_summary를 DB에서 읽어서 학습하는 구조가 아니다. 환율만 DB 조회 예외이며 DB 접속 실패 시 빈 표로 계속 진행한다. 모델 이력을 DB에 쓰는 기능은 입력 조회와 별개다.

### 실제 자료 기간과 추적 가능성

학습 이력의 원본 수출 기간은 2022-01~2026-09, 수집 완료 기록은 57개월이다. 이번 점검에서 학습 당시 입력 목록의 27개 파일이 모두 존재하며 SHA-256 해시가 기록과 일치했다. 이는 해당 파일이 바뀌지 않았다는 근거다. 각 원천 통계가 당시 완전 수집됐거나 수정 전 최초 발표본이라는 사실까지 해시가 보증하지는 않는다.

단신속보는 정렬상 마지막 수집 폴더의 원본을 선택하며, 이번 기록은 20261015_161111_065302 폴더다. 기사 분석·토픽 파일은 공통 경로에서 읽는다. 전체 원본 경로는 편집용 보고서의 부록과 audit_evidence.json에 기록했다. [S3, S4, S6]

## 3 관세청 자료를 학습용 월별 표로 바꾸는 과정

### 파일 선택과 중복 기간 처리

일괄 수집 폴더와 정기 수집 폴더를 읽는다. 정기 수집은 state.json의 collection_dir가 가리키는 파일만 사용하고 collect 완료 여부를 검사한다. 같은 달이 여러 파일에 있으면 정기 수집 폴더와 뒤쪽 파일이 우선한다. 같은 달을 합산해서 두 배로 만들지 않는다.

### 형식과 금액 검사

year가 YYYY.MM이고 hsCd가 숫자 10자리인 행만 남겨 총계·형식 오류 행을 제외한다. expDlr·impDlr는 숫자로 바꾸고 결측·무한대가 있으면 중단한다. COUNTRY 국가만 남긴다. 나미비아 코드 NA를 결측으로 바꾸지 않도록 CSV를 읽는다.

### HS 코드의 시간 비교 가능성 확보

HS10 앞 6자리로 집계한다. HS2017 잔존 HS10 목록에 있는 코드는 HS2017, 그 외는 HS2022로 표시한다. UNSD 대응표를 이용해 분할·통합된 HS6를 비교용 HS2022 대표 코드로 모은다. 따라서 결과 코드가 여러 원래 품목을 묶은 대표 코드일 수 있다.

### 월별 집계

월·국가·HS6·판 단위로 수출액과 중량을 합산한 뒤 대응 코드 변환 후 다시 월·국가·HS6로 합산한다. 수입도 동일한 비교용 코드로 합친다. 금액은 USD다. 중량 결측이 포함된 집계의 중량은 결측을 보존한다.

### 없는 행과 실제 0 구분

수출 쌍 × 전체 월 행렬을 만들 때 기본값은 NaN이다. 수집 완료 기록이 true이고 실패 국가·누락 월·미확인 무거래 국가가 없다고 기록된 달에 한해 행이 없는 쌍을 0으로 채운다. 실제 존재하는 행의 금액 결측은 0으로 바꾸지 않는다.

### 정상 관측 여부와 후보 제한

전체 입력 기간의 수출 금액이 유한한지 확인한다. 중간 누락·중복 키가 있으면 중단한다. 최소 24개월 과거 자료가 생긴 기준월부터 계산하고 최근 12개월 10만 달러 이상 후보만 남긴다.

현재 CSV 경로에서는 R1 일회성 거래 보정 기준이 미정이므로 보정하지 않는다. anomaly_amount=0이며 실제 anomaly_ratio도 모든 행에서 0이었다. 과거 문서의 보정 구상과 현재 실행을 구분해야 한다. [S1, S3]

## 4 피처와 정답 테이블의 전체 흐름

원본 파일 → 월별 표준화 → 과거 피처 X와 미래 정답 y → 시간 평가 → 최종 학습 → 점수와 순위 저장

전체 과거를 매 행에 그대로 넣지 않고, 24개월 월별 실적에서 여러 요약 숫자를 만든다. 입력 X와 미래 정답 y는 같은 행에 함께 저장하지만 학습기에는 지정된 FEATURES 열만 전달한다.

| 단계 | 현재 저장 스냅샷 |
| --- | --- |
| 원본 월 | 2022-01~2026-09, 57개월 |
| 피처 생성 가능 기준월 | 2023-12~2026-09, 34개월 |
| 피처 전체 | 1,737,598행 × 54열. 식별자·정답·보조 열 포함 |
| 정답까지 확보된 기준월 | 2023-12~2025-09, 22개월 |
| 학습 가능한 행 | 1,122,200행 |
| 아직 12개월 정답이 없는 행 | 615,398행. 학습 정답을 0으로 만들지 않음 |
| 최신 예측 행 | 2026-09의 51,153행 |
| 키 중복 | 국가·HS6·기준월 중복 0건 |

### 이후 표에서 쓰는 계산 기호

E12는 기준월 t를 포함한 최근 12개월 수출 합계, P12는 그 앞 12개월 수출 합계다. E3·P3는 최근 3개월과 그 직전 3개월이다. G(a,b)=ln(1+a)-ln(1+b)는 로그 성장값이다. 3개월 피처는 전년 동기 대비가 아니라 직전 3개월 대비이므로 계절성도 섞인다.

country는 한국에서 해당 국가로, world는 한국에서 전체 대상 국가로의 수출 또는 한국의 전체 대상 국가로부터 수입을 뜻한다. 관세청 기반 world 피처는 세계 모든 수출국의 교역량이 아니다. 상대국의 전 세계 수입은 별도 mkt 계열이며 이번 모델에서는 제외됐다.

### 전체 결측 열과 일부 결측 열

학습 구간에서 값이 한 개도 없는 열만 DropEmptyColumns가 제거한다. 일부 행에만 값이 없는 피처는 남기고 HGB가 결측 분기를 처리한다. 전체 45개 중 34개가 남았지만, 값이 남았다는 사실만으로 실제 예측 기여가 확인된 것은 아니다. [S1, S2, S5, S6]

## 5 기본 수출 피처 12개

| 피처 이름 | 정의와 의미 |
| --- | --- |
| log_exp_12m | ln(1+E12). 최근 수출 규모 |
| growth_12m | G(E12,P12). 최근 12개월과 그 앞 12개월 비교 |
| growth_3m | G(E3,P3). 최근 3개월과 직전 3개월 비교 |
| active_months | 최근 12개월 중 수출액이 양수인 월 수 |
| cv_12m | 최근 12개월 수출액 표준편차 / 평균. NumPy 기본 ddof=0 |
| anomaly_ratio | 이상 거래 금액 / E12. 현재 CSV 경로에서는 전부 0 |
| unit_trend | ln((최근 6개월 금액/중량) / (그 앞 6개월 금액/중량)) |
| share_in_country | 해당 쌍 E12 / 한국의 해당 국가 전체 품목 E12 |
| share_in_product | 해당 쌍 E12 / 한국의 해당 HS6 전체 국가 E12 |
| country_growth | 한국의 해당 국가 전체 수출 합계에 대한 12개월 G |
| product_growth | 한국의 해당 HS6 전체 국가 수출 합계에 대한 12개월 G |
| product_countries | 최근 12개월 해당 HS6 수출이 양수인 대상 국가 수 |

share_in_country는 해당 수입국의 전체 수입시장 점유율이 아니다. 한국이 그 국가에 수출한 품목들 중에서 차지하는 비율이다. share_in_product도 세계 경쟁국 대비 점유율이 아니라 한국의 해당 품목 수출 목적지 중 차지하는 비율이다.

unit_trend는 금액/중량으로 계산한 집계 단가 변화다. 품질·규격·상품 구성·거래 조건 변화가 섞일 수 있으므로 순수한 가격 변화로 단정하지 않는다. 중량이 0이거나 결측이면 계산되지 않는다. 단가 로그에서 생긴 무한대도 NaN으로 바꾼다.

전체 스냅샷에서 unit_trend 결측률은 7.49%다. anomaly_ratio는 남은 34개 열 중 하나지만 모든 값이 0이라 현재 모델에 거래 이상 신호를 제공하지 않는다. 별도의 표준화나 전체 피처 분위수 절단을 적용한 것은 아니다. [S1, S5, S6]

## 6 같은 품목군과 수입 피처 9개

| 피처 이름 | 계산 범위 |
| --- | --- |
| grp4_c_growth_12m | 해당 국가, 같은 HS4의 다른 HS6 수출 합계에 대한 12개월 G. 자기 HS6 제외 |
| grp4_c_growth_3m | 동일 범위의 최근 3개월 vs 직전 3개월 G |
| grp4_world_growth_12m | 한국의 같은 HS4 전체 국가 수출에서 자기 HS6의 전체 국가 수출을 뺀 12개월 G |
| grp2_c_growth_12m | 해당 국가, 같은 HS2 전체 수출에서 자기 HS6를 뺀 12개월 G |
| hs4_unit_trend | 동일 HS4의 전체 국가 6개월 집계 단가 변화. 양쪽 기간 중량이 유효한 쌍만 사용 |
| imp_hs6_world_growth | 한국의 해당 HS6 전체 대상 국가 수입액 12개월 G |
| imp_hs4_world_growth | 한국의 해당 HS4 전체 대상 국가 수입액 12개월 G |
| imp_hs4_from_c_growth | 한국이 해당 상대국에서 수입한 HS4 금액의 12개월 G |
| imp_exp_ratio_hs4 | ln(1+한국의 HS4 수입 합계) - ln(1+한국의 HS4 수출 합계) |

관련 품목 피처는 해당 품목의 최근 흐름만 보는 것보다 산업·공급 측면의 신호를 넓히기 위한 입력이다. 자기 품목을 빼는 일부 집계는 자신의 수출 실적을 같은 의미로 중복 입력하는 일을 줄인다. 다만 통계적 연관을 넣은 것이며 인과관계를 증명하지 않는다.

수입 피처에서 from_c는 상대국이 한국에서 들여간 수입이 아니라 한국이 상대국으로부터 들여온 수입이다. 상대국 시장 수요와 혼동하지 않아야 한다.

수입 행렬은 관측된 달에 없는 키를 0으로 처리하지만, 한 달 전체에 수입 행이 없으면 미관측으로 둔다. 현재 코드는 과거 24개월 창의 수입 관측 여부를 확인하며 창에 미관측이 있으면 해당 수입 파생 피처를 결측으로 둔다. 수출 완료 기록과는 다른 관측 판단이므로 국가·품목별 수입 수집 완전성은 별도로 검토할 여지가 있다. [S1]

## 7 산업 관계 피처 4개와 뉴스 피처 7개

| 피처 이름 | 정의 |
| --- | --- |
| rel_exp_c_growth_12m | 해당 국가에 대한 산업 묶음 product+related 수출의 12개월 G. 자기 HS4 전체 제외 |
| rel_exp_world_growth_12m | 같은 산업 묶음 전체 국가 수출의 12개월 G. 자기 HS4 전체 제외 |
| rel_input_imp_growth_12m | 산업 묶음 input 품목의 한국 수입액 최근 12개월 vs 직전 12개월 G |
| rel_input_imp_growth_3m | 같은 input 수입액 최근 3개월 vs 직전 3개월 G |
| news_3m | 해당 국가·해당 HS4 최근 3개월 고유 기사 수 |
| news_all_3m | 전체 국가·해당 HS4 최근 3개월 고유 기사 수 |
| news_change | 국가·HS4 기사 수의 최근 3개월 vs 직전 3개월 G |
| news_country_3m | 해당 국가의 최근 3개월 전체 기사 수 |
| news_trade_3m | 해당 국가 무역·통상 대분류 기사 수 |
| news_market_3m | 해당 국가 경제·시장 대분류 기사 수 |
| news_invest_3m | 해당 국가 기업·투자·진출 대분류 기사 수 |

산업표에서 product 역할을 가진 HS4에만 rel 계열 피처를 만든다. 현재 curated 14개 산업·100행이며 데이터 탐색 후보표는 적용하지 않았다. 전체 행 중 이 4개 피처의 값이 있는 비율은 19.68%다. 묶음에 없는 품목의 값은 0이 아니라 결측이다.

뉴스는 본문 텍스트나 임베딩을 모델 A에 직접 넣지 않는다. 기사 ID 중복을 제거하고 게시일을 월로 바꾼 뒤 HS 태그는 앞 4자리로 모아 기사 수를 센다. 한 기사가 여러 토픽이면 여러 대분류에 포함될 수 있어 대분류 합계가 전체 기사 수를 넘을 수 있다.

기사 수집 시작 월보다 앞선 달이 6개월 창에 포함되면 뉴스 피처를 결측으로 둔다. 이후 없는 키·달은 0으로 채우지만, 누락된 수집과 실제 무기사의 구분을 별도 완료 이력으로 증명하는 구조는 아니다. 최신 태깅 결과를 과거 게시일에 연결하므로 엄격한 당시 정보 재현에는 태깅 버전 관리도 필요하다. [S1, S7]

## 8 범주형과 이번에 제외된 피처 13개

| 피처 | 정의 | 현재 상태 |
| --- | --- | --- |
| region_id | 국가 시드의 지역 ID | 범주형 사용 |
| hs2 | 비교용 HS6 앞 2자리 | 범주형 사용 |
| fx_change_12m | 국가 통화 월평균 환율 / 12개월 전 환율 - 1 | 전체 결측 제외 |
| fx_vol_12m | 최근 12개월 월 환율 변화율 표준편차. 최소 6개 관측 | 전체 결측 제외 |
| log_gdp | GDP 자연로그 | 전체 결측 제외 |
| gdp_growth | GDP 성장률 원자료의 % 값 | 전체 결측 제외 |
| log_population | 인구 자연로그 | 전체 결측 제외 |
| log_gdp_pc | 1인당 GDP 자연로그 | 전체 결측 제외 |
| mkt_log_import | ln(1+상대국 세계 수입액) | 전체 결측 제외 |
| mkt_growth | 같은 출처의 전년 대비 세계 수입액 G | 전체 결측 제외 |
| kor_share | 한국발 수입액 / 세계 수입액, 0~1 제한 | 전체 결측 제외 |
| kor_share_change | 같은 출처 전년 대비 한국 점유율 차이 | 전체 결측 제외 |
| mkt_hs4_growth | 상대국 같은 HS4 세계 수입액의 전년 대비 G | 전체 결측 제외 |

### 수집했는데 왜 학습에 들어가지 않았는가

외부 파일에는 과거 버전의 공개 이력이 없어 현재 CSV를 확보했다고 확인한 2026-10-22를 보수적 사용 가능일로 기록했다. 모든 학습·예측 기준월은 2026-09 이하이므로 기준월 말 이전에 사용 가능했던 외부 값이 없다. 과거 통계 연도라는 이유만으로 당시 공개·수정 상태를 알 수 있다고 가정하지 않았다. 환율은 당시 조회 결과가 0행이다.

외부 전처리 자체는 구현돼 있다. BACI가 있는 연도는 BACI를 선택하고 그 이후는 Comtrade를 사용한다. 성장률은 FOB·CIF 차이를 섞지 않도록 같은 출처의 전년과 비교하며 연결 연도 Comtrade를 보존한다. WDI는 지표별 전년값을 최대 1년 이어 쓴다. 이 절차를 지원한다는 것과 현재 A가 그 값을 사용했다는 것은 구분해야 한다. [S4, S5, S6]

## 9 정답 label은 어떻게 만드는가

### 현재 모델이 배운 정확한 식

```text
P = sum(export[t-11 : t])       # 기준월 포함 최근 12개월
F = sum(export[t+1 : t+12])     # 기준월 다음부터 미래 12개월
y_growth = ln(1 + F) - ln(1 + P)
```

표기의 구간 끝은 모두 포함한다. ln은 자연로그다. 1을 더하므로 미래 수출이 0이 되어도 로그를 계산할 수 있다. y=0이면 두 기간 수출액이 같고 양수면 증가, 음수면 감소다. 금액이 충분히 크면 exp(y)-1이 일반적인 증가율에 가깝다. label은 사람이 붙인 등급이 아니라 미래에 관측된 수출 금액으로 만든 연속 숫자다.

### 원본 CSV로 다시 계산한 실제 사례

| 항목 | VN × HS6 750620 × 기준월 2025-09 |
| --- | --- |
| 최근 12개월 | 2024-10~2025-09 / 263,486 USD |
| 미래 12개월 | 2025-10~2026-09 / 112,607 USD |
| 정답 계산 | ln(112,608) - ln(263,487) = -0.8500912698 |
| 일반 증가율 | 112,607 / 263,486 - 1 = -57.26% |
| 검사 결과 | 원본 HS10 행을 월별 합산한 값으로 재계산한 label이 저장 스냅샷과 일치 |

이 행은 2025-09 시점의 과거 평가 사례다. 뒤에서 제시하는 2026-09 예측 score 0.67256과 기준월이 다르다. 서로 다른 시점의 실제 감소와 미래 예측을 같은 사례의 정답·오답으로 직접 비교하지 않는다.

### 학습 때만 극단 label을 자른다

fit_a는 해당 학습 행의 label 1%·99% 분위수를 구해 그 밖의 값을 경계로 자른 후 학습한다. 최종 학습의 경계는 -13.6194968906, 1.9836360402다. 평가용 모델은 평가용 학습 구간에서 경계를 별도로 추정한다. 평가 정답 자체는 원래 y_growth를 사용한다. 이 clipping은 모든 입력 피처에 일괄 적용되는 처리가 아니다.

최근 기준월의 미래 12개월이 아직 끝나지 않았다면 label은 NaN으로 남아 학습에서 제외된다. 현재 코드의 6개월 정답은 평가 옵션이며 저장된 주 모델 A는 12개월 모델이다. 현재 저장 피처 스냅샷에는 새 6개월 정답 열이 없다. [S1, S2, S6]

## 10 어떤 모델을 어떻게 학습하는가

```text
DropEmptyColumns
  -> TrainingClipper
  -> HistGradientBoostingRegressor
```

HGB는 숫자·범주형 표 데이터에서 여러 결정트리를 차례로 더하는 회귀 모델이다. 각 단계가 앞 단계의 예측 오차를 줄이도록 학습한다. 숫자를 구간으로 나눠 분할 후보를 평가한다. 현재 모델은 텍스트 생성 모델, 딥러닝 시계열 모델, 분류기 또는 직접 순위 손실을 최적화하는 랭커가 아니다. 회귀 결과를 나중에 정렬한다. [S2, E1]

| 설정 | 저장 모델에서 직접 확인한 값 |
| --- | --- |
| loss | squared_error. 절단된 로그 성장 정답의 제곱 오차 최소화 |
| max_iter / 실제 n_iter_ | 300 / 300 |
| learning_rate | 0.05 |
| max_leaf_nodes / max_depth | 31 / None |
| min_samples_leaf / max_bins | 20 / 255 |
| l2_regularization / max_features | 0.0 / 1.0 |
| categorical_features | region_id, hs2 |
| random_state / warm_start | 0 / False |
| early_stopping | auto. 저장 모델에서 실제 활성화 True |
| validation_fraction | 0.1. 학습 데이터 내부에서 검증용으로 분리 |
| n_iter_no_change / tol | 10 / 1e-7 |

TrainingClipper의 대상 열은 mkt_growth·mkt_hs4_growth·kor_share_change·gdp_growth다. 이번에는 모두 제거돼 저장된 bounds_가 빈 사전이다. 따라서 이번 A의 입력에 실제 적용된 분위수 clipping 경계는 없다. 남아 있는 결측은 HGB가 처리하고, 별도의 평균 대체·표준화·원핫 인코딩 파이프라인은 없다.

주의할 구현 세부사항은 내부 early stopping이다. 별도 시간 검증 데이터를 fit에 전달하지 않아 scikit-learn 내부에서는 train_test_split으로 10%를 무작위 분리한다. 외부 성능 평가는 시간 순서로 나눴지만 내부 검증까지 시간 순서인 것은 아니다. 최종 fit에 넘긴 1,122,200행 중 기본 분리상 약 90%인 1,009,980행이 트리 학습용, 112,220행이 내부 검증용이다. 이번에는 300회까지 진행됐다. [S2, S5, S6, E1]

## 11 평가용 모델과 최종 모델의 시간 구분

평가: 학습 기준월 2023-12~2024-09 → 12개월 간격 → 평가 기준월 2025-09 → 정답 2025-10~2026-09

최종: 학습 기준월 2023-12~2025-09 → 예측 기준월 2026-09 → 대상 미래 2026-10~2027-09

| 단계 | 기준월 | 행 수 |
| --- | --- | --- |
| 평가 모델 학습 | 2023-12~2024-09 | 511,413 |
| 주 평가 | 2025-09 한 기준월 | 51,406 |
| 주 평가 정답 관측 | 2025-10~2026-09 | 미래 12개월 |
| 채택 후 최종 fit | 2023-12~2025-09 | 1,122,200 |
| 최종 모델로 예측 | 2026-09 | 51,153 |
| 현재 예측의 미래 대상 | 2026-10~2027-09 | 아직 정답 미확정 |

평가 기준월은 label이 존재하는 마지막 기준월이다. 학습은 그보다 12개월 이상 앞선 기준월만 사용한다. 예를 들어 2024-09 학습 행의 label은 2024-10~2025-09 실적이고, 2025-09 평가 행의 label은 2025-10~2026-09 실적이다. 두 label 기간을 분리하기 위해 학습 기준월을 뒤로 제한한다.

2024-10~2025-08 행은 주 평가 모델 학습에서 제외된다. 그렇다고 해당 월의 원본 실적 자체를 모든 피처에서 버리는 것은 아니다. 평가 기준월까지 알 수 있는 과거 실적은 평가 X에 반영된다.

주 평가로 관련 피처 유지 여부와 기준선 통과를 결정한 후에는 label이 있는 모든 기준월을 최종 fit에 다시 넣는다. 따라서 저장된 최종 모델은 평가 전용 모델과 다르다. 저장된 최종 모델로 2025-09 행을 다시 예측해 주 평가 성능이라고 제시하면, 이미 그 행을 학습 후보에 포함했으므로 평가가 오염된다.

한 쌍은 여러 기준월에 반복 등장하고 인접 기준월의 과거·미래 창은 대부분 겹친다. 112만 행이 112만 개의 독립 사건을 의미하지 않는다. 시간상의 경험은 최종 22개 label 기준월, 주 평가 학습은 10개 기준월이라는 점을 함께 봐야 한다. [S1, S2, S6]

## 12 평가 지표와 모델 선택 규칙

### 비교 기준선

12개월 모델 A의 기준선은 growth_12m, 즉 최근 12개월 수출이 그 앞 12개월보다 많이 늘어난 순서다. 복잡한 모델이 단순히 최근 잘된 품목을 추천하는 방법보다 나은지 확인한다. 모델과 기준선은 같은 평가 행·같은 국가·같은 품목 집합을 사용한다.

| 지표 | 구현과 해석 |
| --- | --- |
| 국가별 Precision@5 | 국가별 예측 상위 5개와 실제 y_growth 상위 5개의 교집합 수 / 5를 평균 |
| 품목별 Precision@5 | 품목별 예측 상위 5개 국가와 실제 상위 5개 국가의 교집합 / 5를 평균 |
| NDCG@5 | 실제 y_growth를 그룹 내 백분위 순위로 바꾼 relevance를 사용. 예측 상위 5개를 순위 가중 평가하고 이상적 순위로 정규화 |
| 평가 대상 최소 후보 | 국가 또는 품목별 후보가 10개 이상인 그룹만 계산 |
| 평균 방식 | 그룹별 동일 가중 평균. 수출액 또는 후보 수로 가중하지 않음 |
| 주 채택 지표 | country_ndcg_at_5. 국가별 품목 추천을 우선 |

NDCG는 실제 상위권을 위에 배치하는 정도를 본다. 이 구현은 정답의 백분위 순위를 relevance로 쓰므로 실제 1~5위를 정확히 맞히지 못해도 비교적 높은 값이 나올 수 있다. 0.7106은 71.06% 정확도나 성공 확률이 아니다. Precision@5와 함께 해석해야 한다. [S2, E2]

### 관련 피처 비교와 저장 조건

관련 피처를 포함한 모델과 RELATED_FEATURES를 뺀 모델을 같은 시간 분할로 학습·평가한다. 지정한 주 지표가 더 높은 구성을 선택하고 동률이면 전체 피처를 유지한다. 선택 모델의 주 지표가 기준선보다 낮거나 계산 불가능하면 새 모델 저장을 거부한다. 기준선과 동률이면 허용한다.

이 절차는 현재 운영 중인 이전 모델의 점수보다 좋아야만 배포하는 규칙은 아니다. 저장 기준은 성장률 기준선이며, 기존 모델 버전과의 동일 조건 재비교는 추가할 수 있다. 또한 같은 평가월로 피처를 선택했으므로 그 점수는 완전히 독립된 최종 테스트 결과가 아니다. [S2]

## 13 실제 평가 결과와 의미

| 구성 | 국가 P@5 | 국가 NDCG@5 | 품목 P@5 | 품목 NDCG@5 |
| --- | --- | --- | --- | --- |
| 성장률 기준선 | 0.1023 | 0.3459 | 0.2259 | 0.4507 |
| 관련 피처 포함 | 0.1455 | 0.7106 | 0.3342 | 0.6795 |
| 관련 피처 제외 | 0.1477 | 0.7087 | 0.3350 | 0.6819 |

평가 그룹은 국가 176개, 품목 1,560개다. 관련 피처 포함 모델이 주 지표에서 0.00194 높아 선택됐다. 다른 세 지표는 제외 모델이 약간 높다. 따라서 관련 피처가 모든 측면에서 우월하다고 결론내릴 수 없고, 작은 차이의 유의성도 검증하지 않았다.

국가 Precision@5 0.14545는 추천 5개 중 실제 성장 상위 5개와 평균 약 0.73개가 겹친다는 뜻이다. 기준선은 약 0.51개다. 품목별 추천은 평균 약 1.67개가 겹친다. 높은 NDCG만 보고 정확한 Top 5 적중을 과장하면 안 된다.

### 시간 순서 보조 검증 3회

| 평가월 | 학습 종료 | 학습 행 | 국가 NDCG | 기준선 |
| --- | --- | --- | --- | --- |
| 2025-02 | 2024-02 | 153,663 | 0.7299 | 0.3240 |
| 2025-05 | 2024-05 | 306,977 | 0.7096 | 0.3268 |
| 2025-09 | 2024-09 | 511,413 | 0.7106 | 0.3459 |
| 평균 | 동일 가중 | 3개 fold | 0.7167 | 0.3322 |

보조 검증의 국가 NDCG 표준편차는 0.01145다. 각 fold에서 변환기와 모델을 다시 학습했지만, 선택된 피처 구성을 고정한 후향적 보조 평가이고 미래 12개월 정답 구간이 fold 사이에 겹친다. 이를 독립 최종 Holdout이나 통계적 신뢰구간으로 해석하지 않는다.

### 품목군별 결과

반도체 HS4 8541·8542는 평가 390행·17개국이며 국가 NDCG 0.6594, 기준선 0.6077이다. 그 외 51,016행은 0.7092 대 0.3459다. 전체 평균 개선이 모든 산업에 동일하게 적용되는 것은 아니다.

위 평가 수치는 저장된 metadata와 기존 검증 JSON을 대조한 값이다. 이번 보고서 작업에서 모델을 재학습해 성능을 새로 측정한 것은 아니다. 현재 코드에는 MAE·RMSE·R²·방향 분류·상위 20% 등 추가 지표 계산 기능이 있지만, 현재 버전 metadata에는 detail 결과가 없어 수치를 만들어 기재하지 않았다. [S6, S8]

## 14 예측 점수와 실제 추천 결과 읽는 법

최신 기준월의 각 후보 X를 최종 모델에 넣어 score를 계산한다. 같은 국가 안에서 점수가 높은 순서가 rank_in_country, 같은 품목 안에서 점수가 높은 순서가 rank_in_product다. 동점은 입력 순서 기준 method=first로 순위를 나눈다. 별도의 산업 다양성·위험·금액 가중 재정렬은 없다.

| 베트남 순위 | 비교용 HS6 | score | exp(score)-1 참고 |
| --- | --- | --- | --- |
| 1 | 750620 | 0.672562 | 95.9% |
| 2 | 950639 | 0.417922 | 51.9% |
| 3 | 190120 | 0.362542 | 43.7% |
| 4 | 843890 | 0.352581 | 42.3% |
| 5 | 901920 | 0.329230 | 39.0% |

위 값은 2026-09를 기준으로 2026-10~2027-09의 성장을 예측한 결과다. exp(score)-1은 로그 점수를 이해하기 위한 근사 환산이다. score 0.67256은 67.26%의 성공 확률이 아니다. 약 95.9%의 성장에 대응하는 로그 값이지만, 이를 보장 성장률이나 검증된 수출액 전망으로 제시하면 안 된다.

정확한 역변환 관계는 F_hat = exp(score) × (1+P) - 1이다. 그러나 모델은 절단된 로그 정답의 제곱 오차를 학습했으므로 역변환 값이 달러 단위 조건부 평균에 대한 불편 추정량인 것은 아니다. 현재 서비스 목적은 성장 순위이며 금액 예측으로 쓰려면 별도의 보정·오차 평가가 필요하다.

### 조회는 재학습이나 실시간 추론이 아니다

top_products와 top_countries는 저장된 predictions.csv를 읽어 미리 계산된 순위를 반환한다. 새 기사나 수출 CSV를 추가해도 기존 CSV가 자동 갱신되지 않는다. 데이터 갱신 후 피처 생성·학습·예측 저장을 실행해야 바뀐다.

현재 예측 score 중앙값은 -0.14976이고 75% 분위수도 -0.00828이다. 추천 순위는 상대적인 순위이므로 모든 후보의 score가 음수인 그룹에서도 Top 5가 나올 수 있다. Top 5가 반드시 성장 전망 양수를 뜻하지 않는다.

이번 점검에서 저장 모델로 최신 51,153개 점수를 다시 계산했다. CSV와의 최대 절대 차이는 1.78e-15로 부동소수점 오차 수준이다. 이는 저장·조회 일관성 확인이며 미래 성능 검증은 아니다. [S2, S6]

## 15 모델 파일과 학습 이력의 구성

현재 버전 ID는 20261022_144737_145087_e8ba400c다. 학습 완료 기록 시각은 2026-10-22 14:47:32 KST, 버전 생성은 14:47:37 KST다. 학습 방식은 FULL_RETRAIN이며 모델 A 상태는 READY다.

| 파일 또는 정보 | 역할 |
| --- | --- |
| model_a_<버전>.joblib | 열 선택기·피처 clipping 변환기·HGB를 함께 저장한 A 파이프라인 |
| models.joblib | A와 B를 묶은 사전. 현재 B는 학습 분류기가 없음 |
| predictions.csv | 국가·HS6별 score, 국가 내 순위, 품목 내 순위, 시장 유형 |
| metadata.json | 피처·채택 지표·평가 결과·label 절단 경계·버전 정보 |
| registry.json | DB에 등록할 모델별 이력과 입력 데이터 계보 |
| registry_sync.json | 등록 상태. 현재 PENDING / ProgrammingError |
| features_snapshot.pkl | 기존 보고서용 피처 스냅샷. 1,737,598행, 약 722 MB |
| versions/legacy_<ID> | 날짜 버전 도입 전 모델의 보관 사본 |

data/ml 루트의 날짜 없는 파일은 최신 호환 사본이다. 로딩 함수는 metadata의 버전 폴더를 찾아 일관된 산출물을 읽는다. 버전 폴더 아래 개별 A 파일의 SHA-256은 이력에 기록된 값과 이번 점검에서 일치했다.

### DB 등록 상태

파일 READY와 DB 등록 완료는 다른 상태다. 현재 registry_sync.json은 ProgrammingError로 PENDING이다. 오류 종류만으로 원인을 테이블 미생성이라고 단정할 수 없다. 실제 DB를 조회해 원인을 진단하거나 등록을 재시도하지는 않았다. DB가 준비되면 model_registry sync로 파일을 다시 학습하지 않고 이력을 재전송할 수 있다.

### 모델 B 및 SGD와의 관계

A는 B의 분류 결과를 입력으로 쓰지 않는다. B가 라벨 부족으로 미판정이어도 A의 점수와 순위는 생성된다. 별도 incremental.py의 SGD A·B는 partial_fit용 비교 계열이며, 지금 설명한 저장 HGB 모델을 자동으로 대체하지 않는다. HGB의 학습을 다시 실행하면 전체 재학습이다. [S2, S6, S9, S10]

## 16 조회와 다음 학습을 진행하는 순서

### 현재 결과만 확인

```text
python -m backend.app.ml.market_model query --country VN
python -m backend.app.ml.market_model query --hs6 750620
```

두 명령은 저장 예측에서 각각 국가별 품목 순위, 품목별 국가 순위를 읽는다. --country와 --hs6를 동시에 주는 현재 CLI는 A의 단일 쌍 점수가 아니라 B의 시장 유형을 조회한다. A의 단일 쌍 score는 load_predictions() 결과에서 두 열로 필터링한다.

### 새 모델을 만들기 전 확인

먼저 원본 수집 범위와 완료 기록, HS 대응표, 뉴스 수집 범위와 태깅 버전을 확인한다. 외부 값의 availability를 실제 근거 없이 과거로 당기지 않는다. 현재 버전의 실행 환경 메타정보 불일치를 해결한 뒤 재현 환경을 확정하고, 평가 기간과 채택 지표를 고정한다. 모델 A의 목적이 정확한 Top 5 적중인지 성장 상위권 추천인지 먼저 합의하는 것이 좋다.

```text
python -m backend.app.ml.runtime check
python -m backend.app.ml.related_groups validate
python -m backend.app.ml.market_model train --promotion-metric country_ndcg_at_5
```

train은 원본을 읽고 X·y 생성 → 시간 분할 평가 → 관련 피처 비교 → 기준선 통과 확인 → 최종 fit → 최신 예측·날짜 버전 저장 → DB 이력 등록을 수행한다. 현재 runtime check는 아래 17장의 기록 불일치로 실패할 수 있으므로 무조건 통과한다고 가정하지 않는다.

### 캐시와 보고서 갱신

```text
python -m backend.app.ml.market_model train --promotion-metric country_ndcg_at_5 --feature-cache-dir data/ml/feature_cache
python -m backend.app.ml.model_registry sync
```

월별 캐시는 원본 파일 파싱·해시 확인을 매번 수행하되 변하지 않은 기준월 피처 계산을 재사용한다. 새 월이 추가되면 미래 label 확정까지 고려해 보통 최근 13개 기준월을 다시 계산한다. 이는 HGB의 부분 학습이 아니며 모델 fit은 전체 정답 행 대상으로 다시 수행한다.

기존 report_workflow는 테스트·산출물 검사·보조 시간 검증·PDF 생성을 실행하며 --train을 붙이면 원본부터 재학습한다. 현재 저장 스냅샷과 소스 해시 불일치가 있으므로 기존 보고서가 지금도 그대로 통과한다고 보장할 수 없다. 이번 작성에서는 원본·모델·메타정보를 보존하고 읽기 검증과 신규 보고서 생성만 수행했다. [S2, S9~S12]

## 17 이번 점검에서 확인한 일치와 불일치

| 항목 | 확인 결과 | 의미 |
| --- | --- | --- |
| 원본 파일 27개 | 모두 존재, 기록 해시와 일치 | 입력 파일 변조·변경 여부 확인 |
| 개별 모델 A 파일 | 기록 해시와 일치 | 기록된 모델 파일임 |
| 재계산 예측 51,153행 | 최대 차이 1.78e-15 | 저장 모델·스냅샷·CSV 일관 |
| 실제 label 사례 | 원본 합계와 y_growth 일치 | label 산식 표본 검증 |
| features.py | 기록 해시와 현재 파일 불일치 | 과거 013bc747 버전과 기록 해시 일치 |
| market_model.py | 기록 해시와 현재 파일 불일치 | 현재 Git 파일 변경 이력에서 동일 해시 미발견 |
| 실행 환경 | 버전 이력과 기존 실행 기록 상충 | 공식 로더의 엄격한 버전 검사에 영향 |
| DB 등록 | PENDING / ProgrammingError | DB 등록 성공은 미확인 |

### 소스 코드 차이의 해석

features.py의 기록은 커밋 013bc747의 내용과 일치했다. 현재 코드와의 차이는 6개월 정답·기준선, 선택 기준월 계산 기능 추가이며 기존 12개월 피처·정답 산식의 변경은 해당 diff에서 발견하지 않았다. market_model.py의 기록 해시와 동일한 파일은 현재 Git에서 해당 파일의 변경 커밋을 조사한 범위에서는 찾지 못했다. 중간 작업본일 가능성은 있지만 원인은 확정하지 않는다.

### 실행 환경 기록의 구체적인 차이

현재 버전의 data_info.runtime은 Python 3.11.9·pandas 2.2.3·NumPy 1.24.3으로 적혀 있다. 반면 training_run.json·기존 검증 문서·고정 의존성 파일은 Python 3.12.14·pandas 3.0.1·NumPy 2.5.4를 기록한다. scikit-learn 1.9.1·joblib 1.6.0은 같다. 어느 기록이 실제 생성 환경인지 이 사실만으로 단정할 수 없다.

이번 점검은 Python 3.12.14·pandas 3.0.1·NumPy 2.5.4·scikit-learn 1.9.1·joblib 1.6.0에서 파일 자체를 직접 읽어 수행했고 점수가 일치했다. 공식 load_models는 버전 이력을 우선 검사하므로 그 경로가 통과했다고 보고하지 않는다. 동작한다는 이유로 메타정보를 임의 수정하지 않았다. 정확한 생성 환경 확인과 이력 정정이 우선이다. [S6, S9, S11, S12]

## 18 현재 결과의 한계와 모델 A의 다음 작업

### 우선순위 1 재현 가능한 기준 모델 확정

현재 모델 파일과 입력을 보관하고 생성 당시 소스·실행 환경·보고서 실행 ID를 하나의 버전에 맞춘다. 런타임 메타정보 불일치, 학습 코드 해시 추적, DB 등록 오류를 각각 처리한다. 피처·label 산식을 유지한 재현을 먼저 확인하고 그 다음 기능을 바꾸면 차이의 원인을 설명하기 쉽다.

### 우선순위 2 추천 목적과 평가 기준 명확화

정확히 실제 성장 Top 5를 맞히려는지, 안정적인 상위권 후보를 제안하려는지, 수출액 증가 기여를 높이려는지 합의한다. 현재는 절단 로그 성장의 제곱 오차로 학습하고 국가별 NDCG로 선택하므로 업무 목표와 손실이 완전히 같은 것은 아니다. 최소 거래 규모, 양수 성장 조건, 산업별 분포와 추천 다양성도 의도적으로 정할 필요가 있다.

### 우선순위 3 시간 검증 강화

피처 선택·설정 조정용 Dev와 손대지 않을 최종 Holdout을 시간 순서로 구분한다. 내부 early stopping도 시간 검증 데이터 또는 명시적 반복 수 정책으로 맞추는 방안을 검토한다. 동일 쌍의 겹치는 창과 산업·국가 편차를 고려해 월별 변동과 산업별 성능을 함께 본다. 현재 3-fold 결과만으로 통계적 유의성을 주장하지 않는다.

### 우선순위 4 실제 이용 가능 시점과 데이터 품질

관세청 월간 실적의 실제 공표 지연·수정본 이력은 현재 기준월 계산에 별도 반영되지 않는다. 월말 관측값이 즉시 사용 가능하다는 가정인지, 다음 달 수집 완료 후 예측하는 운영인지 정해야 한다. 뉴스의 수집 누락과 무기사, 현재 태깅을 과거에 적용한 영향, 수입 관측 범위, HS 대표 묶음의 정확성을 확인한다. 외부 자료는 과거 버전 근거가 확보된 후 추가한다.

### 우선순위 5 피처 기여와 실사용 효과

34개 열이 유지됐다고 모두 성능에 기여한 것은 아니다. anomaly_ratio는 상수다. 뉴스·수입·산업표를 각각 뺀 비교와 시점별 안정성 확인이 필요하다. 현재 관련 피처 비교만으로 뉴스 중요도나 특정 산업 관계의 인과 효과를 주장할 수 없다. feature importance·SHAP 결과는 이번에 계산하지 않았다.

### 확장 범위

SGD 추가학습의 성능·시간 비교, 모델 B, 미진출 후보 발굴은 이후 단계로 분리한다. 새 통계나 모델을 넣기 전에 기존 A의 정의와 재현성을 먼저 고정하는 것을 권한다. 이 장은 확인된 구현에서 도출한 개선 제안이며 이미 완료된 작업을 뜻하지 않는다.

## 19 근거 파일과 읽는 위치

| 번호 | 저장소 내 위치 | 확인 대상 |
| --- | --- | --- |
| S1 | backend/app/ml/features.py | FEATURES, build_dataset, _related_features, _add_news |
| S2 | backend/app/ml/market_model.py | regressor, fit_a, split, rank_metrics, train, top_products |
| S3 | backend/app/ml/sources.py | raw_customs, customs, fx_from_db, hs_correlation |
| S4 | backend/app/ml/preprocess.py / temporal.py | market_yearly, wdi_yearly, dated_external, join_known |
| S5 | backend/app/ml/transforms.py | DropEmptyColumns, TrainingClipper |
| S6 | data/ml/metadata.json / versions/<ID> / features_snapshot.pkl | 현재 모델 이력·저장 파이프라인·피처·예측 직접 확인 |
| S7 | backend/app/ml/related_groups.py / reference/related_hs4.csv | 산업 묶음과 탐색 규칙 |
| S8 | output/pdf/ml_report_validation.json / docs/ML_VALIDATION_20261022.md | 기존 주 평가·3-fold 결과 |
| S9 | backend/app/ml/artifacts.py / model_registry.py | 버전 파일과 계보·DB 이력 저장 |
| S10 | backend/app/ml/incremental.py / feature_cache.py | 별도 SGD 계열과 월별 피처 캐시 |
| S11 | backend/app/ml/runtime.py / backend/requirements-ml-runtime-20261022.txt | 런타임 검증과 기존 의존성 기록 |
| S12 | data/ml/training_run.json / report_training_attempt.json / backend/app/ml/report_workflow.py | 이전 실행 기록과 보고서 흐름 |

모든 상대 경로의 기준은 C:/SKN_AI/SKN33-Final-3Team이다. 버전 <ID>는 20261022_144737_145087_e8ba400c다. 현재 파일과 학습 당시 파일의 차이는 17장에 명시했다. 소스가 현재와 다를 때 저장 모델의 파라미터·선택 피처, 원본 해시, 캐시·예측 일치를 우선 대조했다.

E1 scikit-learn HistGradientBoostingRegressor 공식 문서: https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html

E2 scikit-learn ndcg_score 공식 문서: https://scikit-learn.org/stable/modules/generated/sklearn.metrics.ndcg_score.html

별도 증거 파일 outputs/model_a_report_20261023/audit_evidence.json에는 파일 해시 검사, 저장 모델 파라미터, 피처별 결측·고유값, label 원본 검산, 예측 재계산 결과가 담겨 있다. 전체 재학습·전체 저장소 테스트·실제 DB 등록 검증은 이번 작업에서 실행하지 않았다.

## 부록 학습 이력에 기록된 입력 파일 27개

- [backend/app/ml/reference/related_hs4.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/ml/reference/related_hs4.csv)  |  5476 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2022/customs_collection_2022.json](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2022/customs_collection_2022.json)  |  703 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2022/customs_raw_2022.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2022/customs_raw_2022.csv)  |  129728972 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2023/customs_collection_2023.json](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2023/customs_collection_2023.json)  |  703 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2023/customs_raw_2023.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2023/customs_raw_2023.csv)  |  130711360 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2024/customs_collection_2024.json](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2024/customs_collection_2024.json)  |  703 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2024/customs_raw_2024.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2024/customs_raw_2024.csv)  |  133898883 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2025/customs_collection_2025.json](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2025/customs_collection_2025.json)  |  703 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/2025/customs_raw_2025.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/2025/customs_raw_2025.csv)  |  135735355 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/202601_202609/customs_collection_202601_202609.json](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/202601_202609/customs_collection_202601_202609.json)  |  655 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/customs/customs_outputs/202601_202609/customs_raw_202601_202609.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/customs/customs_outputs/202601_202609/customs_raw_202601_202609.csv)  |  101977048 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/hscode/hscode_outputs/correlation/HS2022toHS2017.xlsx](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/hscode/hscode_outputs/correlation/HS2022toHS2017.xlsx)  |  5519 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/hscode/hscode_outputs/HS2017/hs_code_hs2017.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/hscode/hscode_outputs/HS2017/hs_code_hs2017.csv)  |  55662 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/hscode/reference/hs_names_20260101.xlsx](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/hscode/reference/hs_names_20260101.xlsx)  |  801696 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/shortnews/shortnews_outputs/20261015_161111_065302/shortnews_raw.csv](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/shortnews/shortnews_outputs/20261015_161111_065302/shortnews_raw.csv)  |  109347523 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/shortnews/shortnews_outputs/short_news_analysis.jsonl](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/shortnews/shortnews_outputs/short_news_analysis.jsonl)  |  11350089 bytes  |  SHA-256 일치

- [backend/app/scheduler/collectors/shortnews/shortnews_outputs/short_news_topics.jsonl](C:/SKN_AI/SKN33-Final-3Team/backend/app/scheduler/collectors/shortnews/shortnews_outputs/short_news_topics.jsonl)  |  1059643 bytes  |  SHA-256 일치

- [data/external/availability.csv](C:/SKN_AI/SKN33-Final-3Team/data/external/availability.csv)  |  289 bytes  |  SHA-256 일치

- [data/external/availability_snapshot.json](C:/SKN_AI/SKN33-Final-3Team/data/external/availability_snapshot.json)  |  1285 bytes  |  SHA-256 일치

- [data/external/baci/baci_imports_hs22.csv](C:/SKN_AI/SKN33-Final-3Team/data/external/baci/baci_imports_hs22.csv)  |  79829530 bytes  |  SHA-256 일치

- [data/external/comtrade/comtrade_imports_2024.csv](C:/SKN_AI/SKN33-Final-3Team/data/external/comtrade/comtrade_imports_2024.csv)  |  19112705 bytes  |  SHA-256 일치

- [data/external/comtrade/comtrade_imports_2025.csv](C:/SKN_AI/SKN33-Final-3Team/data/external/comtrade/comtrade_imports_2025.csv)  |  16828077 bytes  |  SHA-256 일치

- [data/external/wdi/wdi_2022_2025.csv](C:/SKN_AI/SKN33-Final-3Team/data/external/wdi/wdi_2022_2025.csv)  |  57246 bytes  |  SHA-256 일치

- [data/news_keywords/keyword_dictionary_view.csv](C:/SKN_AI/SKN33-Final-3Team/data/news_keywords/keyword_dictionary_view.csv)  |  94299 bytes  |  SHA-256 일치

- [db/mysql/init/04_country_seed.sql](C:/SKN_AI/SKN33-Final-3Team/db/mysql/init/04_country_seed.sql)  |  11097 bytes  |  SHA-256 일치

- [db/mysql/init/07_country_extra_seed.sql](C:/SKN_AI/SKN33-Final-3Team/db/mysql/init/07_country_extra_seed.sql)  |  809 bytes  |  SHA-256 일치

- [db/mysql/init/09_country_currency_seed.sql](C:/SKN_AI/SKN33-Final-3Team/db/mysql/init/09_country_currency_seed.sql)  |  6375 bytes  |  SHA-256 일치
