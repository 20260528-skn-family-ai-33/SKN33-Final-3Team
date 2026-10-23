# 모델 A 실험: 제외 피처 복구 · 여러 모델 비교 · 상세 지표 리포트

작성: 2026-10-23 · 관련: [ML_PLAN.md](ML_PLAN.md), [ML_USAGE.md](../backend/app/ml/ML_USAGE.md)

운영 학습(`market_model train`)과 별개인 실험 도구다. 운영 모델·`data/ml/metadata.json`·DB를 바꾸지 않고, 결과는 `data/ml/experiments/<실행ID>/`에만 남긴다.
**실행은 사용자가 노트북에서 직접 한다.** 노트북은 모든 스위치가 꺼진 상태로 저장돼 있어 열기만 해서는 아무것도 학습하지 않는다.

## 1. 파일

| 파일 | 역할 |
|---|---|
| `notebooks/ml_model_a_experiments.ipynb` | 실행 노트북. 데이터 로드 → 피처 복구 → 설정 → Dev 비교 → 리포트 → Holdout |
| `backend/app/ml/experiments.py` | 시간 분할, 모델 생성(8종 + 기준선 3종), 실행 기록, leaderboard, Holdout 설정 복사 |
| `backend/app/ml/experiment_metrics.py` | 순위·증가 여부·회귀 지표 계산과 지표 정의 |
| `backend/app/ml/experiment_report.py` | 저장된 결과로 HTML·Markdown·CSV·PDF 리포트 생성 (학습 안 함) |
| `backend/app/ml/feature_recovery.py` | 제외 피처 복구(외부 통계·환율)와 관측률 점검 |
| `backend/app/ml/reference/experiments/` | 복구용 CSV 템플릿과 추정 공개일표(`availability_release_estimate.csv`) |
| `backend/requirements-ml-experiments*.txt` | 실험 환경 패키지 (LightGBM·XGBoost는 선택) |
| `backend/tests/test_ml_experiments.py` | 작은 가상 데이터로 실험 코드가 끝까지 도는지 확인하는 테스트 |

## 2. 환경 준비

저장된 피처 스냅샷(`data/ml/features_snapshot.pkl`)은 Python 3.12 · pandas 3 · NumPy 2로 만들어져, 이 PC의 기본 Python 3.11로는 읽지 못한다.

```powershell
py -3.12 -m venv .venv-ml-experiments
.\.venv-ml-experiments\Scripts\python.exe -m pip install -r backend/requirements-ml-experiments.txt -c backend/requirements-ml-runtime-20261022.txt
.\.venv-ml-experiments\Scripts\python.exe -m pip install -r backend/requirements-ml-experiments-optional.txt -c backend/requirements-ml-runtime-20261022.txt  # LightGBM·XGBoost 쓸 때만
.\.venv-ml-experiments\Scripts\python.exe -m jupyterlab
```

코드가 맞는지만 빠르게 볼 때 (가상 데이터, 실데이터 학습 없음):

```powershell
.\.venv-ml-experiments\Scripts\python.exe -m unittest backend.tests.test_ml_experiments
```

## 3. 제외된 11개 피처를 되살리는 방법

저장 모델은 45개 피처 중 34개를 썼다. 빠진 11개는 이유가 셋이다.

| 피처 | 빠진 이유 | 되살리는 방법 (권장 순) |
|---|---|---|
| 환율 2개 `fx_change_12m`, `fx_vol_12m` | DB `fx_daily`가 0건 | ① ECOS 과거 적재 후 원본부터 피처 재생성 ② 근거 있는 월평균 CSV로 `recover_fx` |
| WDI 4개 `log_gdp`, `gdp_growth`, `log_population`, `log_gdp_pc` | `data/external/availability.csv`가 전부 2026-10-22(파일을 받은 날)이라 모든 기준월(~2026-09)보다 늦음 | ① WDI 과거 판(Database Archives)과 공개일 근거로 `wdi_history` ② 민감도 실험 |
| 시장 5개 `mkt_log_import`, `mkt_growth`, `kor_share`, `kor_share_change`, `mkt_hs4_growth` | 위와 같음. BACI를 2022년부터 받아 2022년 성장률도 만들 수 없음 | ① 과거 BACI 판과 공개일 근거로 `market_history` ② BACI 수집 연도를 2019~2021까지 넓힘 ③ 민감도 실험 |

추가로 `anomaly_ratio`는 사용 피처로 잡혀 있지만 R1 보정을 적용하지 않아 항상 0이다. 실험의 `drop_constant=True`에서는 상수 열로 빠지고, R1 임계값이 정해져야 의미가 생긴다.

### 3.1 환율 (가장 쉽고 깨끗함)

환율은 사후 수정이 거의 없고 월말에 확정되므로, 과거 값을 채우기만 하면 시점 문제 없이 쓸 수 있다.
12개월 변화율에 13개월이 필요하므로 첫 학습 기준월 2023-12를 채우려면 2022-12부터, 여유 있게 2022-01부터 받는다.

```powershell
python -m backend.app.scheduler.jobs.fx_daily_load --start 2022-01-01 --end 2026-09-30
```

`.env`에 ECOS 키와 DB 접속 정보가 있어야 한다. 적재 뒤 노트북에서 `DATA_SOURCE="raw"`로 피처를 다시 만들면 `sources.fx_from_db`가 읽는다.
국가 시드에 통화 코드가 없는 나라는 계속 비어 있다(2026-10-22 EDA 기준 249개국 중 129개국).

### 3.2 WDI·시장: 엄격 복구

그 기준월 말에 실제로 공개돼 있던 판의 값만 붙인다. Holdout·운영에 쓸 수 있는 유일한 방법이다.
`reference/experiments/wdi_history_template.csv`, `market_history_template.csv`에 맞춰 판별 값과 `available_from`, `source`, `evidence`를 채운 뒤 `recover_external(..., market_history=..., wdi_history=...)`로 연결한다.
현재 외부 파일만으로 엄격 복구를 돌리면 복구 값 0개가 정상이다. `availability.csv`를 과거 날짜로 고쳐 쓰지 않는다.

### 3.3 WDI·시장: 민감도 실험 (가치 가늠용)

`reference/experiments/availability_release_estimate.csv`는 공개 주기로 추정한 날짜다. 모든 행에 "추정(확인 필요)" 근거를 적었다.

| 자료 | 추정 공개일 (보수적으로 잡음) |
|---|---|
| BACI 2022 · 2023 · 2024 | 2024-02-29 · 2025-02-28 · 2026-02-28 (매년 초 새 판에 Y-2년까지) |
| Comtrade 2024 · 2025 | 2025-09-30 · 2026-09-30 (다음 해 중반까지 신고) |
| WDI 2022 ~ 2025 | 다음 해 9월 30일 (매년 7월 전후 갱신) |

`recover_external(df, availability=<이 파일>)`은 **지금 파일의 값(나중에 수정된 값)**을 추정 공개일에 붙인다. 수정 반영분만큼 낙관적이라 결과가 좋아도 바로 채택하면 안 된다.
"과거 판을 구해 올 가치가 있나"를 판단하는 데만 쓴다. 이 시나리오는 Holdout 실행에서 코드가 거부한다.

예상되는 모습 (실제 관측률은 노트북의 복구 점검표로 확인):
- WDI는 거의 모든 기준월에 값이 붙는다.
- 시장은 2022년 값이 2024-02 이후 기준월부터 붙는다. 하지만 성장률 3개(`mkt_growth`, `kor_share_change`, `mkt_hs4_growth`)는 2023년 값부터라 2025-02 이후 기준월에만 생긴다.
- 그래서 12개월 학습 구간(~2024-09)에서는 시장 성장률이 비어 학습에서 다시 빠질 수 있다. 성장률까지 살리려면 BACI 2019~2021년 수집이 필요하다.

## 4. 모델

| kind | 성격 | 전처리 (모두 각 분할의 train에서만 학습) |
|---|---|---|
| `hgb` | 운영 모델과 같은 HistGradientBoosting. 내부 early stopping을 끄고 반복 수 고정 | 결측 그대로, 범주는 정수 코드(native categorical) |
| `lightgbm`, `xgboost` | 다른 구현의 부스팅 (선택 설치, 미설치면 skipped) | 결측 그대로, 범주는 정수 코드. 원핫·밀집 행렬을 쓰지 않아 메모리를 적게 씀 |
| `random_forest`, `extra_trees` | 배깅 트리 | 중앙값 대체 + 결측 표시 + 원핫(희소) |
| `ridge`, `elasticnet`, `sgd` | 선형 (비선형을 못 잡는 하한 비교) | 중앙값 대체 + 결측 표시 + 표준화 + 원핫 |
| 기준선 3종 (자동 포함) | 작년 성장률(6개월이면 단순 지속), train 평균, 0 | 없음 |

모든 모델 앞에 `TrainingClipper`(외부 성장률 1%·99% 자르기)가 들어가고, 정답도 train 분위수로 자른다(`target_clip`). 평가는 자르지 않은 원래 정답으로 한다.

## 5. 평가 설계

- **Dev**: 기준월 2025-02, 2025-05에서 비교한다. 학습은 각 기준월보다 `gap_months`(12) 이상 앞선 기준월만 쓴다.
- **Holdout**: 2025-09. Dev에서 모델·피처·시나리오·seed를 하나로 고정한 뒤 한 번만 돌린다. 다만 2025-09는 운영 모델 평가와 피처 선택에 이미 쓰였으므로 **완전히 독립된 Holdout은 아니다**.
- 6개월 정답(`horizon=6`, `gap_months=6`)은 `DATA_SOURCE="raw"`로 새 스냅샷을 만든 뒤에만 가능하고, 기준선은 자동으로 단순 지속 규칙이 된다.
- 평가 행(test)은 샘플링하지 않는다. `max_train_rows`로 학습 행만 줄일 수 있다(시간 측정용).

## 6. 리포트

`data/ml/experiments/<실행ID>/`에 생긴다.

| 파일 | 내용 |
|---|---|
| `report.html` | 비교 차트, 요약표, 지표별 비교표(지표 × 후보, 최고 후보 표시), 실행별 전체 지표, 품목군별 결과, 지표 정의, 설정·데이터 계보 |
| `report.pdf` | `make_pdf=True`일 때. 지표별 비교표는 후보 6개씩 나눠 싣는다 |
| `metric_matrix.csv` | 지표별 비교표 원본 |
| `leaderboard.csv` | 후보별 fold·seed 평균·표준편차·유효 실행 수, `complete` |
| `fold_metrics.csv` | 실행 하나하나의 전체 지표와 혼동행렬 |
| `segments.csv` | 반도체(HS4 8541·8542)와 그 외 |
| `*__groups.csv`, `*__coverage.csv`, `*__predictions.csv.gz` | 그룹별 점수, 피처 관측률, 평가 행 예측 |
| `manifest.json`, `results.json` | 설정·실행 환경·입력 해시·실패 사유·시간·파라미터 |

지표 묶음:

- **순위** (국가별 품목 / 품목별 국가, K = 5·10): Precision@K, NDCG@K, Hit@K, 상위 20% Precision·Recall·F1@K, 추천 중 실제 증가 비율, 추천의 실제 성장 평균, 그룹 내 Spearman
- **증가 여부** (실제 y>0을 점수>0으로 판정, 전체 행): Accuracy, Balanced accuracy, Precision, Recall, F1, MCC, ROC-AUC, Average precision, 혼동행렬
- **성장률 회귀** (로그 성장값 자체): MAE, RMSE, Median AE, R², Explained variance, Pearson, Spearman

점수는 확률이 아니어서 log-loss·Brier는 계산하지 않는다. 계산할 수 없는 지표는 0이 아니라 '미평가'(null)로 남긴다.

## 7. 주의

- **메모리**: 스냅샷 약 722MB. 시나리오마다 사본이 생기고 실행 중 정렬용 사본이 하나 더 생긴다. 8GB PC에서는 시나리오를 한두 개씩 나눠 돌린다.
- **시간**: Random Forest·Extra Trees는 학습 행이 수십만이면 오래 걸린다. 처음에는 `max_train_rows=100_000`으로 시간을 재 본다.
- **비교 원칙**: 같은 시나리오 안에서는 행 키·정답·기준선이 같아야 하고, 코드가 다르면 멈춘다. 일부 fold가 실패한 후보는 `complete=False`로 순위에서 빠진다.
- 실험 결과가 좋아도 운영 모델로 자동 승격하지 않는다. 채택은 운영 학습(`market_model train --promotion-metric ...`)으로 따로 한다.

## 8. 참고

- [HistGradientBoostingRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html)
- [OneHotEncoder](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.OneHotEncoder.html)
- [LightGBM LGBMRegressor](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRegressor.html)
- [XGBoost Python API](https://xgboost.readthedocs.io/en/stable/python/python_api.html)
