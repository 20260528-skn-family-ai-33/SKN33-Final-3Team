# 수출 유망 시장 모델 사용 안내

미진출 기능은 [추가 개발 계획](../../../docs/ML_NEW_MARKETS_PLAN.md)을 참고합니다. 이 코드의 후보는 기존 수출 쌍입니다.

설계와 근거는 [docs/ML_PLAN.md](../../../docs/ML_PLAN.md)에 있습니다. 여기에는 실행 방법만 적습니다.

| 파일 | 하는 일 |
|---|---|
| `preprocess.py` | 외부 CSV(BACI·Comtrade·WDI)를 연 단위 시장·국가 피처 표로 전처리 |
| `features.py` | DB 실적(수출·**수입**)·기준정보, `data/external/` CSV, 단신속보 수집 결과 파일을 읽어 (국가, HS6, 기준월) 학습 테이블을 만듦 |
| `market_model.py` | 모델 A(유망도)·B(시장 유형) 평가·학습, 최신 기준월 예측 저장, 조회 함수 |

## 준비

1. 패키지: `python -m pip install -r backend/requirements.txt` (scikit-learn 포함)
2. 관세청 수출 실적: `export_monthly`에 **최소 48개월**이 있어야 평가까지 됩니다 (과거 24개월 + 미래 12개월 + 평가 간격 12개월).
   24개월 미만이면 `학습 행이 없음`, 48개월 미만이면 `학습 기간 부족` 오류로 멈춥니다. 이는 현재 단일 평가월 절차의 최소 길이이며, Dev와 별도 최종 Holdout을 마련하기에 충분하다는 뜻은 아닙니다.
   입력에 등장한 국가·HS6 쌍마다 입력 최소~최대 월의 금액이 관측돼야 합니다. 중간 월·쌍별 월 누락, 금액 결측·비유한 값, 월별 중복 키는 오류로 중단하며 위치를 알립니다. 수집 완료와 무거래를 확인한 경우에만 명시적인 0 행을 제공하세요. 원본에 없는 행을 자동으로 0으로 만들거나 해당 후보를 조용히 제외하지 않습니다.
   이 검사는 입력 범위 내부의 누락을 찾습니다. 전체 국가·품목 누락, 잘려 나간 시작·종료 기간, 값이 있는 월의 부분 수집까지 증명하지는 못하므로 별도의 수집 완료 기록이 필요합니다. 아직 도래하지 않은 미래 구간은 기존처럼 정답 결측으로 남깁니다.
3. 외부 데이터: `python -m backend.app.scheduler.jobs.external_refresh` ([EXTERNAL_COLLECT_USAGE.md](../scheduler/collectors/external/EXTERNAL_COLLECT_USAGE.md)).
   파일이 있으면 해당 값·버전의 사용 가능일 메타정보가 필요합니다. 파일 자체가 없으면 외부 피처·유형 라벨이 비며, 유형 임계값도 구하지 못하면 미판정합니다.
4. 관련 품목·뉴스 피처 입력 (없으면 해당 피처만 결측이 되고 학습은 진행됩니다)
   - 관세청 **수입**: `import_monthly`. 관세청 적재 때 수출과 함께 들어갑니다. 수입이 수집되지 않은 달이 12개월 창에 있으면 수입 피처는 결측입니다.
   - 단신속보: `backend/app/scheduler/collectors/shortnews/shortnews_outputs/`의 최신 `*/shortnews_raw.csv`, `short_news_analysis.jsonl`(HS 태깅), `short_news_topics.jsonl`(토픽). 토픽 대분류는 DB `keyword_topic`·`keyword_category`로 연결합니다. 국가명 표기가 `country.name_ko`와 다르면 `features.NEWS_COUNTRY_ALIASES`에 추가합니다.
   - 학습 행에서 전부 결측인 피처 열(예: 환율)은 학습에서 빠지며, 실제로 쓴 열은 `metadata.json`의 `features_used`에 남습니다.

## 외부 CSV 전처리만 따로 보기

관세청 실적 없이도 지금 있는 외부 CSV만으로 실행됩니다. `train`도 내부에서 같은 함수를 쓰므로, 이 명령은 결과를 눈으로 확인할 때 씁니다.

```powershell
python -m backend.app.ml.preprocess          # CSV만 (국가 = 시드 SQL의 COUNTRY, HS 묶음 통일 없음)
python -m backend.app.ml.preprocess --db     # DB의 country·hs_code_correlation 사용 (train과 같은 결과)
```

| 결과 | 키 | 컬럼 |
|---|---|---|
| `data/ml/market_yearly.csv` | 연도·국가·HS6 | `import_world_usd`, `import_from_kor_usd`, `source`(BACI·COMTRADE), `kor_share`, `mkt_log_import`, `mkt_growth`, `kor_share_change` |
| `data/ml/wdi_yearly.csv` | 연도·국가 | `log_gdp`, `gdp_growth`, `log_population`, `log_gdp_pc`, `filled` |

전처리 규칙:

- 국가: `country_role='COUNTRY'`만 남김 (KR·JG 같은 SPECIAL 제외)
- 시장: BACI가 있는 해는 BACI, 그 뒤 해는 Comtrade. 전세계 수입액 0 이하 행 제외, HS 묶음 통일 뒤 같은 키는 합침
- 성장률 `log1p(올해) − log1p(전해)`, 점유율 변화는 **같은 출처의 바로 전해 값이 있을 때만** 계산 (2022년과 새로 생긴 쌍은 결측)
  - BACI는 FOB, Comtrade 수입은 CIF라 섞지 않습니다. BACI 다음 해(2025)는 연결 연도 Comtrade 2024와 비교합니다.
  - 연결 연도 Comtrade가 없는 나라는 그 해 성장률이 결측입니다.
- 외부 성장률·점유율 변화·GDP 성장률은 원값을 보존하고 모델 학습 행에서만 1%·99% 경계를 추정해 적용합니다. 변환기는 모델과 함께 저장합니다. 점유율은 기존대로 0~1로 제한합니다.
- WDI: GDP처럼 늦게 발표되는 지표는 직전 해 값을 1년까지 이어 쓰고 `filled=True`로 표시

2026-10-21 검토한 CSV 기준: 시장 3,181,577행(2022~2025, 224개 국가 코드), WDI 860행(215개 코드). 국가 시드만 적용했으며 DB의 HS 대응 묶음은 미적용입니다. 과거 `--db` 실행 기록을 현재 팀 DB의 적재 완료 근거로 사용하지 않습니다. 팀원 보유 자료의 적재 상태와 범위는 별도로 확인해야 합니다.

2026-10-21 연결 연도(Comtrade 2024, 147개국) 적용 후 `--db` 실행: 시장 3,005,509행(225개국), 실행 약 2분. 2025년 성장률 결측 6.8%이며, Comtrade 2024 신고가 없는 5개국(부탄·트리니다드토바고·카보베르데·코모로·남수단)은 2025년 성장률이 모두 결측입니다.

## 공개·버전 시점 메타정보

`data/external/availability.csv`의 필수 열은 `dataset,source,year,available_from`입니다. `dataset`은 `market` 또는 `wdi`, `source`는 `BACI`, `COMTRADE`, `WDI`입니다. 출처·연도당 하나의 행을 사용합니다. 날짜는 현재 CSV 값·버전이 실제 사용 가능했던 날짜여야 합니다. 모든 값이 그날 공개됐다는 보수적인 연도별 근거가 필요하며, 국가·지표별 시점을 세밀하게 사용하려면 스키마를 추가 확장해야 합니다.

실제 공개일을 임의로 만들지 마세요. 수정된 최신 파일에 과거 최초 발표일을 붙여 과거 피처로 쓰면 안 됩니다. 기준월 이전에 공개되지 않은 값은 연결하지 않습니다. 전년값으로 만든 성장률·보충값은 전년 버전 시점도 반영합니다. 검토한 파일 묶음에는 이 파일이 없었습니다. 팀원 보유본의 값·버전에 대응하는 메타정보를 확인해 준비해야 합니다.

## 실행

아래 `country_ndcg_at_5`는 명령 형식 예시이며 팀의 채택 지표 결정이 아닙니다. 선택지는 국가별 또는 품목별 `precision_at_5`, `ndcg_at_5`의 네 조합입니다. 지표를 선택한 뒤 실행하세요.

```powershell
# 학습·평가 후 data/ml/에 저장 (평가 결과를 화면에 출력)
python -m backend.app.ml.market_model train --promotion-metric country_ndcg_at_5

# 기능 ① 국가 → 유망 품목 Top 5
python -m backend.app.ml.market_model query --country VN
# 기능 ② 품목 → 유망 국가 Top 5
python -m backend.app.ml.market_model query --hs6 850760
# 기능 ③ 국가 + 품목 → 시장 유형
python -m backend.app.ml.market_model query --country VN --hs6 850760
```

코드에서 쓸 때는 `market_model.top_products("VN")`, `top_countries("850760")`, `market_type_of("VN", "850760")`를 부릅니다.

## 결과

| 파일 | 내용 |
|---|---|
| `data/ml/predictions.csv` | 최신 기준월의 (국가, HS6)별 점수·국가 내 순위·품목 내 순위·시장 유형 |
| `data/ml/models.joblib` | 모델 A·B |
| `data/ml/metadata.json` | 학습 시각, 피처 목록, 유형 기준값, **평가 결과** |

`metadata.json`의 `evaluation`에서 모델과 기준선을 비교합니다.

- `model_a` vs `baseline_a`: 국가별 Precision@5·NDCG@5. 기준선은 "작년 성장률 상위 5개"입니다. **명시한 채택 지표가 기준선보다 낮거나 미평가이면 저장을 중단하며 기존 파일은 유지됩니다.** 동률은 기존 문구의 ‘낮으면 거부’에 맞춰 허용합니다. 반대 방향 평가도 별도로 확인해야 합니다.
- `model_a_product` vs `baseline_a_product`: 품목별 국가 추천 평가입니다.
- `model_b` vs `baseline_b`: 공통 행 및 동일 5개 유형의 macro-F1. `model_b_all`은 모델 전체 행 점수와 혼동행렬입니다. 비교 불가·미달 시 B 분류기를 채택하지 않습니다.

## 주의

- 예측 대상은 최근 12개월 수출이 $100k 이상인 쌍뿐입니다. 그보다 작은 쌍은 `query`에서 "예측 대상 아님"으로 나옵니다.
- HS2017로 적재된 코드는 `hs_code_correlation`의 대응 묶음 대표 코드(HS2022)로 합쳐 계산합니다. 그래서 결과의 `hs6`가 묶음 대표 코드일 수 있습니다.

## 테스트

```powershell
python -m unittest backend/tests/test_ml_market.py backend/tests/test_external_data.py backend/tests/test_ml_temporal.py
```

