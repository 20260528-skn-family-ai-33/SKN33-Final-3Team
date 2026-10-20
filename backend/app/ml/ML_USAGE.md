# 수출 유망 시장 모델 사용 안내

설계와 근거는 [docs/ML_PLAN.md](../../../docs/ML_PLAN.md)에 있습니다. 여기에는 실행 방법만 적습니다.

| 파일 | 하는 일 |
|---|---|
| `preprocess.py` | 외부 CSV(BACI·Comtrade·WDI)를 연 단위 시장·국가 피처 표로 전처리 |
| `features.py` | DB 실적·기준정보와 `data/external/` CSV를 읽어 (국가, HS6, 기준월) 학습 테이블을 만듦 |
| `market_model.py` | 모델 A(유망도)·B(시장 유형) 평가·학습, 최신 기준월 예측 저장, 조회 함수 |

## 준비

1. 패키지: `python -m pip install -r backend/requirements.txt` (scikit-learn 포함)
2. 관세청 수출 실적: `export_monthly`에 **최소 48개월**이 있어야 평가까지 됩니다 (과거 24개월 + 미래 12개월 + 평가 간격 12개월).
   24개월 미만이면 `학습 행이 없음`, 48개월 미만이면 `학습 기간 부족` 오류로 멈춥니다.
3. 외부 데이터: `python -m backend.app.scheduler.jobs.external_refresh` ([EXTERNAL_COLLECT_USAGE.md](../scheduler/collectors/external/EXTERNAL_COLLECT_USAGE.md)).
   없어도 학습은 되지만 시장 피처가 비고, 모델 B는 라벨을 못 만들어 규칙 기준선으로 대신합니다.

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
- 시장: 같은 연도는 BACI 우선, BACI 이후 연도만 Comtrade. 전세계 수입액 0 이하 행 제외, HS 묶음 통일 뒤 같은 키는 합침
- 성장률 `log1p(올해) − log1p(전해)`, 점유율 변화는 **바로 전해 값이 있을 때만** 계산 (2022년과 새로 생긴 쌍은 결측)
- 증가율·점유율 변화·GDP 성장률은 1%·99%에서 자름, 점유율은 0~1로 제한
- WDI: GDP처럼 늦게 발표되는 지표는 직전 해 값을 1년까지 이어 쓰고 `filled=True`로 표시

2026-10-20 실행 결과 (`--db`): 시장 3,005,509행(2022~2025, 225개국), WDI 860행(215개국, GDP 결측 5.2%)

## 실행

```powershell
# 학습·평가 후 data/ml/에 저장 (평가 결과를 화면에 출력)
python -m backend.app.ml.market_model train

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

- `model_a` vs `baseline_a`: 국가별 Precision@5·NDCG@5. 기준선은 "작년 성장률 상위 5개"입니다. **모델이 기준선보다 낮으면 모델을 쓰지 않습니다.**
- `model_b` vs `baseline_b`: macro-F1. 기준선은 같은 규칙을 지난 12개월에 적용한 결과입니다.

## 주의

- 예측 대상은 최근 12개월 수출이 $100k 이상인 쌍뿐입니다. 그보다 작은 쌍은 `query`에서 "예측 대상 아님"으로 나옵니다.
- HS2017로 적재된 코드는 `hs_code_correlation`의 대응 묶음 대표 코드(HS2022)로 합쳐 계산합니다. 그래서 결과의 `hs6`가 묶음 대표 코드일 수 있습니다.

## 테스트

```powershell
python -m unittest backend/tests/test_ml_market.py
```
