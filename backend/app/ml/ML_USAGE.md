# 수출 유망 시장 모델 사용 안내

설계와 근거는 [docs/ML_PLAN.md](../../../docs/ML_PLAN.md)에 있습니다. 여기에는 실행 방법만 적습니다.

| 파일 | 하는 일 |
|---|---|
| `features.py` | DB 실적·기준정보와 `data/external/` CSV를 읽어 (국가, HS6, 기준월) 학습 테이블을 만듦 |
| `market_model.py` | 모델 A(유망도)·B(시장 유형) 평가·학습, 최신 기준월 예측 저장, 조회 함수 |

## 준비

1. 패키지: `python -m pip install -r backend/requirements.txt` (scikit-learn 포함)
2. 관세청 수출 실적: `export_monthly`에 **최소 48개월**이 있어야 평가까지 됩니다 (과거 24개월 + 미래 12개월 + 평가 간격 12개월).
   24개월 미만이면 `학습 행이 없음`, 48개월 미만이면 `학습 기간 부족` 오류로 멈춥니다.
3. 외부 데이터: `python -m backend.app.scheduler.jobs.external_refresh` ([EXTERNAL_COLLECT_USAGE.md](../scheduler/collectors/external/EXTERNAL_COLLECT_USAGE.md)).
   없어도 학습은 되지만 시장 피처가 비고, 모델 B는 라벨을 못 만들어 규칙 기준선으로 대신합니다.

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
