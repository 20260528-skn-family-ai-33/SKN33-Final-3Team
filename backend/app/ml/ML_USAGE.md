# 수출 유망 시장 모델 사용 안내

미진출 기능은 [추가 개발 계획](../../../docs/ML_NEW_MARKETS_PLAN.md)을 참고합니다. 이 코드의 후보는 기존 수출 쌍입니다.

2026-10-28 기준 최종 선정 구성은 **2단계 LightGBM C 방식(K=100)을 v1 · v3 피처로 각각 학습한 두 모델의 앙상블**입니다. 실행은 아래 최종 모델 안내를 따릅니다. [초기 설계](../../../docs/ML_PLAN.md)와 뒤쪽 HGB·SGD 안내는 이전 모델·실험을 재현할 때 참고합니다.

## Linux 배포 준비

LightGBM은 Linux에서 OpenMP 런타임을 사용합니다. 런타임이 없으면 패키지 설치가 끝났어도 `import lightgbm`에서 `OSError: libgomp.so.1`이 발생할 수 있습니다([공식 설치 안내](https://github.com/lightgbm-org/LightGBM/blob/v4.6.0/python-package/README.rst#install-from-pypi)). Debian·Ubuntu 서버에서는 다음과 같이 준비합니다.

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends libgomp1
python -m pip install -r backend/requirements.txt
python -c "import lightgbm; print(lightgbm.__version__)"
```

컨테이너를 쓰면 실제 실행 이미지에도 `libgomp1`이 필요합니다. `backend/requirements.txt`에는 LightGBM이 포함돼 있으므로 이 목록을 설치하는 환경에 적용합니다. API·worker 전용 `backend/requirements-api.txt`에는 LightGBM이 포함돼 있지 않습니다.

`.env.sample`의 `CUSTOMS_ML_ENTRYPOINT`는 기본적으로 비워 두었습니다. 월별 재학습을 활성화하기 전에 위 import 확인과 입력 데이터·DB 준비, 서버 자원(기본 학습 스레드 4개)을 확인한 뒤 아래 월별 재학습 설정을 적용합니다. 활성화 후에는 정기 일정뿐 아니라 **스케줄러 시작 20초 뒤 누락 월 복구**에서도 학습이 실행될 수 있습니다. 이미 `WAITING_FOR_ML` 상태인 월은 아래 안내대로 수동 재개해야 합니다.

## 최종 모델 생성·조회

1단계 `LGBMRegressor`가 다음 12개월의 로그 수출 성장값을 예측하고, 2단계 `LGBMRanker`가 국가별 1단계 상위 100개 품목을 다시 정렬합니다. 후보 밖 품목은 1단계 순서로 그 아래에 둡니다. 이 구성을 외부 피처 없는 v1(1단계 33개)과 외부 피처를 넣은 v3(42개)로 각각 학습하고, 국가 안 순서는 두 모델의 순위 평균, 성장값은 평균으로 합칩니다. 같은 시드끼리 v3 단독과 비교하면 국가별 NDCG@5가 평가 기준월 3개 × 시드 3개 9번 모두 같거나 높았습니다(5차 테스트). 6차 테스트에서 품목 → 국가 순위용 **품목 방향 순위 모델**(같은 기준월 · 같은 품목 안 국가 순서, 후보를 거르지 않음)을 더했습니다. 앙상블 품목 NDCG@5가 Dev 0.6801 → 0.6910, 2025-09 0.6840 → 0.6905로 올랐고(같은 시드 9번 모두 상승) 국가 지표는 그대로입니다.

| 조회 | 사용하는 결과 |
|---|---|
| 국가 → 유망 품목 | 국가 방향 순서 `order` |
| 품목 → 유망 국가 | 품목 방향 순서 `product` (품목 방향 모델이 없는 예전 파일은 1단계 `growth`) |
| 예측 증감률 | 1단계 성장값 `growth` |
| 국가 + 품목 | 예측 증감률·최근 수출액·양방향 순위. 기존 B 모델의 시장 유형 분류는 제공하지 않음 |

명령은 저장소 루트에서 실행합니다. LightGBM은 `backend/requirements.txt`에 포함돼 있습니다(월별 재학습을 돌리는 서버에도 필요). 저장 모델을 조회할 때는 모델의 `runtime`에 기록된 환경을 맞춥니다. 아래 확인된 파일은 Python 3.11.9, NumPy 1.24.3, pandas 2.2.3, SciPy 1.10.1, scikit-learn 1.9.1, joblib 1.6.0, LightGBM 4.7.0에서 생성됐습니다. 뒤쪽의 Python 3.12 HGB 보고서 환경은 별도입니다.

```powershell
# 새 학습 환경에 백엔드 의존성(LightGBM 포함) 설치
python -m pip install -r backend/requirements.txt

# 준비된 피처 스냅샷으로 최종 구성의 두 모델 생성 (v1 · v3)
python -m backend.app.ml.two_stage train --method C --k 100 --external none --version v2 --n-jobs 4
python -m backend.app.ml.two_stage train --method C --k 100 --external first_release --version v2 --n-jobs 4

# 국가 → 품목 / 품목 → 국가 / 특정 조합 조회
# --model에 두 파일을 함께 주면 앙상블로 조회
python -m backend.app.ml.two_stage query --model data/ml/candidates/two_stage_c100_base_v2.joblib data/ml/candidates/two_stage_c100_ext_v2.joblib --country VN --top 5
python -m backend.app.ml.two_stage query --model data/ml/candidates/two_stage_c100_base_v2.joblib data/ml/candidates/two_stage_c100_ext_v2.joblib --hs6 850760 --top 5
python -m backend.app.ml.two_stage query --model data/ml/candidates/two_stage_c100_base_v2.joblib data/ml/candidates/two_stage_c100_ext_v2.joblib --country VN --hs6 850760
```

- 학습 기본 입력은 `data/ml/features_snapshot_py311.pkl`입니다. 다른 스냅샷은 `--snapshot <경로>`로 지정합니다. 이 명령은 수출입 원본을 새로 수집하거나 피처 스냅샷을 생성하지 않습니다.
- 최종 구성은 **`--external none`(v1)과 `--external first_release`(v3)를 각각 한 번씩** 생성합니다. `none`은 외부 시장·WDI 피처를 제외합니다. 스냅샷의 해당 외부 피처가 전부 비어 있으면 현재 외부 원본과 `data/external/availability.csv`로 복구합니다.
- 파일명의 `base`는 v1 구성, `ext`는 외부 피처를 포함하는 v3 구성, 끝의 `v1`은 저장 버전명입니다. 이미 같은 파일이 있으면 덮어쓰지 않으므로 다시 생성할 때는 `--version v2`처럼 새 버전을 지정합니다.
- 각 저장 파일에는 1·2단계 학습 파이프라인·피처 목록·학습 정보와 최신 기준월 예측 `latest`가 함께 들어갑니다. 조회에 파일을 하나만 주면 그 모델 단독 결과입니다. 조회는 저장된 예측을 읽으므로 새로운 수집 자료를 자동 반영하지 않습니다. 국가·품목명은 저장소의 기준 파일에서 읽습니다.
- 정답은 `log1p(미래 12개월 수출액) - log1p(최근 12개월 수출액)`입니다. 출력하는 `expm1(growth) × 100`은 근사 증감률이며 성공 확률이 아닙니다.

### 생성 완료 확인 기록

2026-10-28 로컬 파일 점검 결과입니다. 다른 PC나 서버의 생성·배포 완료를 뜻하지 않습니다.

| 항목 | 확인값 |
|---|---|
| 파일 | `data/ml/candidates/two_stage_c100_base_v1.joblib` · `two_stage_c100_ext_v1.joblib` |
| 생성 시각 | 2026-10-27 16:49:32 · 16:55:20 KST |
| 구성 | C 방식, K=100. v1은 1단계 33개 피처, v3는 외부 피처 포함 42개 |
| 학습 | 기준월 2023-12~2025-09, 1,122,200행; 2단계 264,342행 |
| 저장 예측 | 두 파일 모두 2026-09 기준, 51,153쌍, 217개국·3,802개 HS6 (같은 대상이라 앙상블 가능) |
| 파일 점검 | 정상 로드, 예측 `growth`·`order`에 결측·무한값 없음, 기준월·국가·품목 중복 키 없음 |

### 시간 순서 K-fold 검증 (최종 모델 리포트)

```powershell
python -m backend.app.ml.two_stage kfold --folds 8 --seeds 0 1 2 --n-jobs 4
```

- 검증 기준월 하나가 fold 하나입니다. 학습은 그 기준월보다 12개월 이상 앞선 기준월만 쓰는 확장창이라(`validation.temporal_folds`) 검증 기간 정답이 학습에 들어가지 않습니다. 피처 선택·정답 절단도 fold 학습 행에서만 정합니다.
- 현재 정답 있는 기준월(2023-12~2025-09)로는 2025-02~2025-09의 8개월을 검증할 수 있습니다. 최종 구성(`MEMBERS` 앙상블, 국가·품목 방향 순위 모델)을 시드별로 학습해 국가 지표는 국가 방향 순서, 품목 지표는 품목 방향 순서로 잽니다.
- 결과는 `data/ml/reports/model_a_two_stage_kfold_<날짜_시각>.md` · `.json`입니다. 지표별 fold 평균·fold 간 표준편차·최소·최대, 작년 성장률 기준선, 기준선보다 높은 fold 수와 fold별 표를 담습니다. fold 사이 미래 정답 창이 겹치므로 평균·표준편차는 기술 통계이며 신뢰구간이 아닙니다.

`data/`의 모델·스냅샷은 Git에서 제외합니다. 코드 푸시만으로 다른 환경에 모델 파일이 전달되지는 않습니다.

## 최종 모델 월별 재학습

연결 경로는 `scheduler.main → customs_monthly → candidate_models.run → two_stage.monthly`입니다. 위 Linux 배포 준비와 서버 자원을 확인한 뒤 재학습을 활성화하려면 `.env`에 다음과 같이 설정합니다. 환경변수에 같은 키가 있으면 `.env`보다 우선합니다.

```dotenv
CUSTOMS_ML_ENTRYPOINT=backend.app.ml.candidate_models:run
CUSTOMS_MONTHLY_DAY=10
CUSTOMS_MONTHLY_HOUR=3
CUSTOMS_MONTHLY_MINUTE=0
# 누락 월 복구 시작점. 실제 운영 범위에 맞게 지정합니다.
CUSTOMS_START_MONTH=202601
```

- 일정 기본값은 **매월 10일 03:00, Asia/Seoul**입니다. 전월 수출입 수집·검증·적재·집계·계산이 끝난 뒤 ML 단계가 실행되므로 모델 학습 시작·완료 시각은 고정되지 않습니다.
- 2026-10-28 기준 다음 정기 실행은 **2026-11-10 03:00 KST**이며, 전월인 2026-10까지 누락·미완료 월을 오래된 순서로 처리합니다. 스케줄러 시작 20초 뒤에도 같은 복구 작업이 실행됩니다.
- 2026-10-29 로컬 실측(Python 3.11, `n_jobs=4`, 학습 1,122,200행, 품목 방향 순위 모델 포함): ML 단계 약 **11.3분**(전체 학습 451초 + Holdout 점검 187초, 피처 생성 제외)으로 30분 이내라 매월 전체 재학습합니다. 정답 있는 기준월이 매월 하나(약 4.5%)씩 늘어 학습 시간도 비슷하게 늘어납니다. 서버 사양에 따라 달라지므로 첫 월별 실행의 `fit_seconds`로 확인합니다. 스케줄러가 부르는 월별 단계는 기본 4코어(`candidate_models.run(n_jobs=4)`)로 학습합니다. (2026-10-28 품목 방향 모델 추가 전 측정은 `n_jobs=2`로 약 10.5분이었습니다.)
- 외부 데이터 갱신은 별도 작업으로 **매월 1일 04:00 KST**입니다. 모델 재학습 일정과 구분합니다.
- `CUSTOMS_ML_ENTRYPOINT`가 비어 있으면 `WAITING_FOR_ML`로 남습니다. 이 상태의 월은 자동 복구 대상에서 빠지므로 설정을 채운 뒤 해당 월 파이프라인을 수동 재개합니다.

```powershell
# 통합 스케줄러 상시 실행: 수출입·외부 데이터 외에 다른 등록 작업도 함께 실행
python -m backend.app.scheduler.main

# 특정 월 파이프라인 실행 또는 완료되지 않은 단계부터 재개
python -m backend.app.scheduler.jobs.customs_monthly --month 202610

# 해당 월까지 원본이 준비된 경우 ML 단계만 실행·재시도
python -m backend.app.ml.candidate_models --monthly 2026-10-01 --n-jobs 2

# 월별 생성 파일 조회 (생성 완료 후)
python -m backend.app.ml.two_stage query --model data/ml/monthly_models/model_a_two_stage_202610.joblib --country VN
```

통합 스케줄러는 운영 환경에서 계속 실행해야 합니다. 2026-10-28 로컬 점검에서는 실행 프로세스와 월별 모델 산출물이 확인되지 않았습니다. 코드에 예약이 등록된 것과 서버에서 자동 실행 중인 것은 별도이며, 서버 실행 상태는 미확인입니다.

### 월별 입력·산출물·재시도

- 월별 경로는 고정 스냅샷 대신 **수집 원본에서 피처를 생성**합니다. 대상 월까지 원본이 있어야 하며 `data/ml/monthly_feature_cache/`를 사용합니다. 최종 구성은 `two_stage.MEMBERS = {"base": "none", "ext": "first_release"}`(v1 · v3 앙상블)이고, 정답이 있는 전체 행으로 매번 **`FULL_RETRAIN`**합니다. 기존 모델에 `partial_fit`하지 않습니다.
- 외부 자료(BACI·Comtrade·WDI)는 연도별로 늦게 들어와 일부가 빌 수 있어 학습을 멈추지 않습니다. v3 모델에서 비어 빠진 외부 피처는 `.registry.json`의 `data_info.external_dropped`에 남기고, 9개가 모두 빠지면 v3가 v1과 같은 피처로 학습돼 앙상블 효과가 없으므로 경고 로그를 남깁니다.
- `data/ml/monthly_models/model_a_two_stage_<YYYYMM>.joblib` 한 파일에 두 모델과 앙상블한 최신 예측을, 같은 이름의 `.registry.json`에 입력 계보·피처·평가·DB 등록 정보를 저장합니다. DB `ml_model_version`에는 `model_key=model_a`, `model_family=two_stage_full`, `status=READY`로 기록합니다.
- `.env`의 `MYSQL_*` 접속 설정과 `ml_model_version` 테이블이 필요합니다. 초기 테이블 정의는 [`10_ml_model_version.sql`](../../../db/mysql/init/10_ml_model_version.sql)을 참고합니다. 신규 학습 전 DB에서 부모 버전을 확인하며, 파일 생성 뒤 DB 기록이 실패하면 파일과 등록 JSON을 보존합니다.
- 같은 달·같은 입력의 재실행은 저장 파일의 해시를 확인하고 DB 등록을 재시도합니다. 원본이 정정되거나 학습 코드(`candidate_models.MODEL_SOURCES`의 파일: K·앙상블 구성·피처 계산 포함)나 패키지 버전이 바뀌면 `_r2`, `_r3` 등 새 버전을 만듭니다. 주석만 고쳐도 같은 달 재실행은 새로 학습합니다.
- 누락 월 자동 복구로 지난달을 나중에 학습하면, 원본에 그 뒤 달이 있어도 기준월은 그 달까지, 정답은 미래 12개월이 그 달 안에 끝나는 기준월까지만 씁니다(`input_snapshot.as_of_month`). 그 뒤 달 수집 폴더가 추가된 것은 재실행 비교에서 정정으로 보지 않습니다. 피처 값은 현재 보유 원본 기준이라, 그 뒤에 들어온 정정분까지 그 시점 상태로 되돌리지는 않습니다. 월별 등록 재시도는 위 `candidate_models --monthly` 명령을 사용합니다.
- 평가용 모델은 마지막 정답 기준월보다 12개월 이상 앞선 기준월로 학습합니다. `.registry.json`의 `evaluation.model_product`는 품목 방향 순서 평가(품목별 국가 추천 지표는 여기서 봅니다), `evaluation.model`은 2단계 순서 평가, `model_stage1`은 1단계 성장값 평가, `baseline_growth_12m`은 기준선입니다. 국가별 품목 추천은 `model`, 품목별 국가 추천은 `model_stage1`을 확인합니다. `model`은 0~1 순위 점수(`order`)로 계산하므로 순서만 쓰는 지표(NDCG·Precision@K·Hit@K·top20 F1·`spearman`·`roc_auc`·`average_precision`)만 유효합니다. 같은 블록의 값 크기를 쓰는 지표(`mae`·`rmse`·`median_ae`·`r2`·`explained_variance`·`pearson`)와 0 기준 증가 판정 지표(`accuracy`·`precision`·`recall`·`f1`·`mcc`·`balanced_accuracy`·`predicted_growth_rate`·`tn`/`fp`/`fn`/`tp`)는 해석하지 않고, 증감률 오차·증가 판정은 `model_stage1`에서 봅니다.
- 현재 월별 경로는 평가 결과를 기록하지만 기준선 미달을 이유로 저장·등록을 차단하지 않습니다. `READY`는 성능 승격 심사를 통과했다는 뜻이 아닙니다. 뒤쪽 HGB·SGD의 기준선 통과 규칙을 이 경로에 적용해 해석하지 않습니다.
- 파이프라인 완료 여부는 `data/customs_load_outputs/monthly/<YYYYMM>/state.json`의 `status=SUCCESS`, `completed`의 `ml`, `ml` 결과와 실제 모델·DB 기록을 함께 확인합니다. ML만 직접 실행하면 파이프라인 체크포인트는 갱신하지 않습니다.

## 공통 입력과 이전 모델 안내

| 파일 | 하는 일 |
|---|---|
| `sources.py` | 수집 CSV·xlsx와 기준 파일을 읽는다. **환율만 DB 조회 예외** (아래 "입력 파일") |
| `preprocess.py` | 외부 CSV(BACI·Comtrade·WDI)를 연 단위 시장·국가 피처 표로 전처리 |
| `features.py` | 관세청 수출·수입, 외부 표, 단신속보, 관련 품목 묶음표로 (국가, HS6, 기준월) 학습 테이블을 만듦 |
| `related_groups.py` | 관련 품목 묶음표 검증과 데이터 기반 후보 찾기 |
| `two_stage.py` | 최종 2단계 모델 학습·조회, 월별 재학습 구성 |
| `candidate_models.py` | 월별 최종 모델 생성·DB 등록, 단일 후보 모델 생성 |
| `market_model.py` | 기존 HGB 모델 A(유망도)·B(시장 유형) 평가·학습·조회 |
| `artifacts.py` / `model_registry.py` | 공통 학습 입력·계보·DB 등록과 기존 A·B 날짜 버전 관리 |

## 준비

1. 패키지: `python -m pip install -r backend/requirements.txt` (scikit-learn 포함)
2. 관세청 수출입 실적: 수집 원본 CSV(연도별 일괄 + 매월 정기, 아래 "입력 파일")가 **최소 48개월** 있어야 평가까지 됩니다 (과거 24개월 + 미래 12개월 + 평가 간격 12개월). DB 적재는 필요 없습니다.
   24개월 미만이면 `학습 행이 없음`, 48개월 미만이면 `학습 기간 부족` 오류로 멈춥니다. 이는 현재 단일 평가월 절차의 최소 길이이며, Dev와 별도 최종 Holdout을 마련하기에 충분하다는 뜻은 아닙니다.
   입력에 등장한 국가·HS6 쌍마다 입력 최소~최대 월의 금액이 관측돼야 합니다. 중간 월·쌍별 월 누락, 금액 결측·비유한 값, 월별 중복 키는 오류로 중단하며 위치를 알립니다. 수집 완료와 무거래를 확인한 경우에만 명시적인 0 행을 제공하세요. 원본에 없는 행을 자동으로 0으로 만들거나 해당 후보를 조용히 제외하지 않습니다.
   이 검사는 입력 범위 내부의 누락을 찾습니다. 전체 국가·품목 누락, 잘려 나간 시작·종료 기간, 값이 있는 월의 부분 수집까지 증명하지는 못하므로 별도의 수집 완료 기록이 필요합니다. 아직 도래하지 않은 미래 구간은 기존처럼 정답 결측으로 남깁니다.
   **수집 완료 기록**: CSV와 같은 폴더의 완료 기록(`customs_collection_*.json` 또는 정기 수집의 `collection.json`)이 `collection_complete: true`이고 실패 국가·빠진 달·거래 없음 미확인 국가가 없으면, 그 파일의 달에 행이 없는 (국가, HS6)는 확인된 무역 없음(0)으로 채웁니다. 완료 기록이 없는 달의 누락은 지금처럼 오류로 멈춥니다.
   실제 행의 금액 결측·무한대는 완료 기록이 있어도 오류입니다. 중량 결측도 0으로 채우지 않습니다.
   R1(일회성 거래) 보정은 팀 임계값이 정해지지 않아 CSV 경로에서는 적용하지 않고 원금액을 씁니다.
3. 외부 데이터: `python -m backend.app.scheduler.jobs.external_refresh` ([EXTERNAL_COLLECT_USAGE.md](../scheduler/collectors/external/EXTERNAL_COLLECT_USAGE.md)).
   파일이 있으면 해당 값·버전의 사용 가능일 메타정보가 필요합니다. 파일 자체가 없으면 외부 피처·유형 라벨이 비며, 유형 임계값도 구하지 못하면 미판정합니다.
4. 관련 품목·뉴스 피처 입력 (없으면 해당 피처만 결측이 되고 학습은 진행됩니다)
   - 관세청 **수입**: 수출과 같은 원본 CSV의 수입 열. 수입이 수집되지 않은 달이 12개월 창에 있으면 수입 피처는 결측입니다.
   - 단신속보: `backend/app/scheduler/collectors/shortnews/shortnews_outputs/`의 최신 `*/shortnews_raw.csv`, `short_news_analysis.jsonl`(HS 태깅), `short_news_topics.jsonl`(토픽). 토픽 대분류는 `data/news_keywords/keyword_dictionary_view.csv`로 연결합니다. 국가명 표기가 국가 시드의 `name_ko`와 다르면 `features.NEWS_COUNTRY_ALIASES`에 추가합니다.
   - 관련 품목 묶음표: `backend/app/ml/reference/related_hs4.csv` (아래 "관련 품목 묶음표").
   - 환율: **예외로 DB에서 읽습니다**(`fx_daily` 월평균, `country.currency_code`). ECOS 수집기가 CSV를 남기지 않기 때문입니다. DB에 접속하지 못하면 환율 피처만 비고 학습은 계속됩니다. 2026-10-22 현재 `fx_daily`는 0건, 통화가 연결된 국가는 120개입니다.
   - 학습 행에서 전부 결측인 피처 열(예: 환율)은 학습에서 빠집니다. 실제 피처는 최종 모델 joblib의 `features`와 월별 `.registry.json`의 `data_info.features_used`, 기존 HGB의 `metadata.json.features_used`에서 확인합니다.

## 입력 파일

| 입력 | 파일 |
|---|---|
| 관세청 수출·수입 | 연도별 일괄 수집 `backend/app/scheduler/collectors/customs/customs_outputs/*/customs_raw_*.csv` + 정기 수집 `data/customs_load_outputs/monthly/<YYYYMM>/<collection_dir>/customs_raw_<YYYYMM>.csv`. 정기 수집은 해당 월의 `state.json`이 지정하고 `collect`를 완료한 수집본만 사용하며 CSV 옆의 `collection.json`을 확인합니다. 같은 달은 정기 수집본 우선. 이전 형식인 월 폴더 바로 아래 CSV도 지원합니다 |
| HS2017 잔존 코드 | `.../collectors/hscode/hscode_outputs/HS2017/hs_code_hs2017.csv` |
| HS 판 대응표 | `.../collectors/hscode/hscode_outputs/correlation/HS2022toHS2017.xlsx` (UNSD 원본, `hs_correlation_load.fetch_pairs`가 받음) |
| HS 품목명 | `.../collectors/hscode/reference/hs_names_*.xlsx` |
| 국가·지역·통화 | `db/mysql/init/*country*seed*.sql` |
| 외부 시장·WDI | `data/external/` ([EXTERNAL_COLLECT_USAGE.md](../scheduler/collectors/external/EXTERNAL_COLLECT_USAGE.md)) |
| 단신속보·키워드 사전 | 위 4번 |
| 환율 (예외) | DB `fx_daily`, `country.currency_code` |

## 관련 품목 묶음표

HS 체계상 다른 장(chapter)에 있는 장비·소재·연관 품목을 산업 단위로 묶은 표입니다. 같은 HS4·HS2 피처만으로는 반도체(85장)와 반도체 장비(84장)·웨이퍼(38장)가 연결되지 않아 둡니다.

- 파일: `backend/app/ml/reference/related_hs4.csv` — `group, hs4, role, source, note`
  - `role`: `product`(그 산업의 수출 제품) · `input`(한국이 들여오는 장비·소재) · `related`(함께 움직이는 연관·수요 품목)
  - `source`: `curated`(사람이 정함) · `data`(데이터 후보에서 추가)
- 피처: 품목의 HS4가 어떤 묶음의 `product`이면 `rel_exp_c_growth_12m`(묶음 수출, 자기 HS4 제외, 그 국가), `rel_exp_world_growth_12m`(전 세계), `rel_input_imp_growth_12m`·`rel_input_imp_growth_3m`(투입 품목의 한국 수입). 묶음에 없는 품목은 결측입니다.

```powershell
# 형식·중복·HS 품목명 원본에 있는지 검사 (묶음표를 고친 뒤 항상 실행)
python -m backend.app.ml.related_groups validate
# 관세청 실적으로 관련 품목 후보 찾기 (학습 기간 끝까지의 실적만 사용) → reference/related_hs4_candidates.csv
python -m backend.app.ml.related_groups suggest --until 2024-09
# 엄격한 기준(상관 0.8 이상, 3개월 이상 선행)을 넘은 후보만 묶음표에 data 출처로 추가
python -m backend.app.ml.related_groups suggest --until 2024-09 --apply
```

- 후보 찾기: 산업 제품 수출의 전년 대비 증가율과 각 HS4 수출·수입 증가율(0·3·6개월 앞선 값)의 상관. 두 증가율 모두 한국 전체 수출(수입) 증가율을 빼서 경기 영향을 덜어냅니다. 연평균 $1억 미만 HS4는 제외합니다.
- **`--apply` 결과는 반드시 사람이 확인합니다.** 2026-10-22 실제 실행(2022-01~2025-09)에서 기준을 넘은 14개 중 구리박·필름·유리·양극재 계열은 그럴듯했지만 버터(0405)·파티클보드(4410)처럼 우연한 상관도 섞였습니다. 33개월 안팎의 증가율은 서로 매끄럽게 이어져 우연한 상관이 높게 나오기 쉽습니다.
- 현재 보관된 후보표는 위 2025-09까지의 탐색 결과이며 묶음표에 자동 적용하지 않았습니다. 평가 기준월 2025-09의 학습 종료월은 2024-09이므로, 평가에 사용할 데이터 기반 관계는 반드시 2024-09까지만 탐색해야 합니다.
- 갱신 시점: HS 판 개정(다음은 HS2028) 때 `validate`가 사라진 코드를 알려 주면 대응표로 고칩니다. 재학습 때(분기 1회) `suggest`로 후보를 다시 보고, 새 산업·공급망 변화는 1년에 한 번 사람이 검토합니다.

## 외부 CSV 전처리만 따로 보기

관세청 실적 없이도 지금 있는 외부 CSV만으로 실행됩니다. `train`도 내부에서 같은 함수를 쓰므로, 이 명령은 결과를 눈으로 확인할 때 씁니다.

```powershell
python -m backend.app.ml.preprocess          # 국가 = 국가 시드의 COUNTRY, HS 묶음 통일 = UNSD 대응표 원본 (train과 같은 결과)
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
- WDI: 일부 지표만 비어 있으면 직전 해 값을 1년까지 이어 쓰고 `filled=True`로 표시합니다. 원본 지표가 전부 빈 국가·연도는 보충해 만들지 않으며, 공개일이 확인된 가장 최근 이전 연도 자료를 연결합니다. 공개된 과거 자료도 없으면 피처를 결측으로 둡니다. 새 연도 값이 실제로 도착하기 전에는 그 연도 공개일을 미리 기록하지 않습니다.

2026-10-21 검토한 CSV 기준: 시장 3,181,577행(2022~2025, 224개 국가 코드), WDI 860행(215개 코드). 국가 시드만 적용했으며 DB의 HS 대응 묶음은 미적용입니다. 과거 `--db` 실행 기록을 현재 팀 DB의 적재 완료 근거로 사용하지 않습니다. 팀원 보유 자료의 적재 상태와 범위는 별도로 확인해야 합니다.

2026-10-21 연결 연도(Comtrade 2024, 147개국) 적용 후 `--db` 실행: 시장 3,005,509행(225개국), 실행 약 2분. 2025년 성장률 결측 6.8%이며, Comtrade 2024 신고가 없는 5개국(부탄·트리니다드토바고·카보베르데·코모로·남수단)은 2025년 성장률이 모두 결측입니다.

## 공개·버전 시점 메타정보

`data/external/availability.csv`의 필수 열은 `dataset,source,year,available_from`입니다. `dataset`은 `market` 또는 `wdi`, `source`는 `BACI`, `COMTRADE`, `WDI`입니다. 출처·연도당 하나의 행을 사용합니다. 날짜는 현재 CSV 값·버전이 실제 사용 가능했던 날짜여야 합니다. 모든 값이 그날 공개됐다는 보수적인 연도별 근거가 필요하며, 국가·지표별 시점을 세밀하게 사용하려면 스키마를 추가 확장해야 합니다.

실제 공개일을 임의로 만들지 마세요. 수정된 최신 파일에 과거 최초 발표일을 붙여 과거 피처로 쓰면 안 됩니다. 기준월 이전에 공개되지 않은 값은 연결하지 않습니다. 전년값으로 만든 성장률·보충값은 전년 버전 시점도 반영합니다.

2026-10-22 초기 HGB 검증에서는 **로컬 스냅샷 확보 확인일 2026-10-22**를 보수적 사용 가능일로 기록했습니다. 당시 학습·예측 기준월(마지막 2026-09)에서는 외부 시장·WDI 피처가 모두 결측이었습니다. `data/external/availability_snapshot.json`은 그 시점 원본의 기록이며 현재 최종 모델의 외부 피처 사용 여부를 대신하지 않습니다.

2026-10-28 확인한 로컬 `availability.csv`는 최초 공개일 근거를 사용하는 설정으로 변경돼 있고, 최종 앙상블의 v3 모델에는 외부 피처가 포함돼 있습니다. 다만 파일의 `evidence`에도 명시된 대로 현재 보유 값에 과거 최초 공개일을 붙인 자료와 국가별 신고 시점 차이가 있어, 과거 수정값·늦게 신고된 값이 이르게 반영될 수 있습니다. `first_release`라는 이름만으로 당시 실제 이용 가능했던 데이터가 재현됐다고 보지 않습니다. 재학습 시 실제 날짜표·원본 버전·피처 목록을 함께 확인하고, 이 설정의 평가를 엄격한 과거 시점 재현 성능으로 해석하지 않습니다.

## 기존 HGB 모델 실행

이 절부터 나오는 `market_model` 명령은 기존 HGB A·B 모델용입니다. 최종 2단계 모델 생성·조회는 문서 앞부분의 `two_stage` 명령을 사용합니다.

2026-10-22 채택 지표는 **국가별 품목 추천 NDCG@5 (`country_ndcg_at_5`)**로 결정했습니다. 다른 지표는 국가별 또는 품목별 `precision_at_5`, `ndcg_at_5`의 네 조합 중 지정할 수 있습니다.

첫 실데이터 실행은 모델 A 저장·조회까지 완료했습니다. 국가별 NDCG@5는 0.7106(기준선 0.3459), 2026-09 예측은 51,153쌍입니다. 상세 피처 유무 비교·품목군별 점수·제한은 [실데이터 검증 기록](../../../docs/ML_VALIDATION_20261022.md)을 참고하세요.

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

## 기존 HGB 모델 결과

| 파일 | 내용 |
|---|---|
| `data/ml/predictions.csv` | 최신 기준월의 (국가, HS6)별 점수·국가 내 순위·품목 내 순위·시장 유형 |
| `data/ml/models.joblib` | 모델 A·B |
| `data/ml/metadata.json` | 학습 시각, 피처 목록, 유형 기준값, **평가 결과** |

`metadata.json`의 `evaluation`에서 모델과 기준선을 비교합니다.

- `model_a` vs `baseline_a`: 국가별 Precision@5·NDCG@5. 기준선은 "작년 성장률 상위 5개"입니다. **명시한 채택 지표가 기준선보다 낮거나 미평가이면 저장을 중단하며 기존 파일은 유지됩니다.** 동률은 기존 문구의 ‘낮으면 거부’에 맞춰 허용합니다. 반대 방향 평가도 별도로 확인해야 합니다.
- `model_a_product` vs `baseline_a_product`: 품목별 국가 추천 평가입니다.
- `without_related`: 같은 시간 분할에서 관련 품목 피처를 뺀 모델의 점수입니다. 채택 지표가 더 높은 쪽으로 최종 학습하며 동률이면 전체 피처를 유지합니다. `model_a_variant`와 `features_used`에 선택 결과를 남깁니다. 피처를 뺀 모델을 선택하면 전체 피처 모델의 점수는 `with_related`에 보존합니다.
- `segments`: 반도체(HS4 8541·8542)와 나머지 품목군의 점수·행 수입니다. 국가·품목별 후보가 10개 미만이면 점수를 만들지 않고 평가 그룹 수 0과 결측 점수를 남깁니다. `missing_training_features`는 학습 행에서 전부 결측인 피처입니다.
- `model_b` vs `baseline_b`: 공통 행 및 동일 5개 유형의 macro-F1. `model_b_all`은 모델 전체 행 점수와 혼동행렬입니다. 비교 불가·미달 시 B 분류기를 채택하지 않습니다.

## 기존 HGB 모델 A·B 날짜 버전과 DB 이력

다음 학습부터 A·B를 각각 `data/ml/versions/<버전 ID>/model_a_<버전 ID>.joblib`, `model_b_<버전 ID>.joblib`로 저장합니다. 버전 ID는 **KST 생성 날짜_시간_마이크로초_충돌방지 ID**입니다. 예: `20261022_143012_123456_a1b2c3d4`. 각 파일은 해당 모델의 피처 변환기를 포함하는 파이프라인입니다.

모델 B가 라벨·비교 평가 부족 또는 기준선 미달로 생성되지 않으면 B 파일은 만들지 않고, 같은 형식의 DB 이력에 **SKIPPED·실제 학습 0행·사유**를 기록합니다. A는 READY로 기록합니다. 두 기록은 한 DB 트랜잭션으로 저장합니다.

기존 MySQL에는 [`db/mysql/init/10_ml_model_version.sql`](../../../db/mysql/init/10_ml_model_version.sql)을 한 번 적용해 `ml_model_version` 테이블을 준비합니다. 접속은 기존 `.env`의 `MYSQL_*` 설정을 사용합니다. 국가 군집용 `model_run` 테이블은 별도입니다. 코드 추가 시 실제 DB 변경·학습·기존 모델 재저장은 실행하지 않았습니다.

DB에는 다음 항목을 모델별로 기록합니다.

- 모델명·버전·생성 시각(KST)·상태·파일 경로와 SHA-256·직전 로컬 버전 ID.
- 실제 학습 기준월의 시작·종료·행 수·국가 및 품목 수, 모델 B의 학습 가능 행 수.
- 입력 CSV·완료 기록·체크포인트·기준 파일의 경로와 해시, 원본 기간·완료 월, 환율/국가 매핑 스냅샷 해시, 코드·라이브러리 버전.
- 이전 버전 대비 **추가 기준월과 해당 행 수**, 내용이 변경된 기존 기준월, 추가·변경·제외 파일, 전체 학습 행 수 차이.

기존 HistGradientBoosting 계열은 **전체 재학습(`FULL_RETRAIN`)**입니다. 아래 SGD 계열은 최초 `INITIAL_FIT` 이후 `PARTIAL_FIT`을 사용합니다. 신규 기준월 행 수는 기존 월에서 새로 생긴 국가·품목 쌍의 수를 포함하지 않으며, 순증 행 수를 신규 행 수로 간주하지 않습니다. 최초 버전이나 이전 입력 기록이 없는 경우 비교 정보는 미확인으로 남깁니다. `train(ds, data_info=...)`를 직접 호출할 때 원본 정보를 생략하면 피처 테이블 기준 정보만 기록됩니다. 일반 학습 CLI와 `report_workflow --train`은 원본 계보를 자동 수집합니다.

버전 폴더에는 모델 묶음·예측·메타정보·DB 등록용 `registry.json`도 보존합니다. 날짜 없는 기존 파일은 최신 호환 복사본이고, `metadata.json`이 현재 버전 폴더를 가리킵니다. 첫 전환에서는 기존 날짜 없는 모델도 `versions/legacy_<ID>/`로 보관합니다. 조회는 일관된 버전 폴더를 사용하는 다음 함수를 권장합니다.

```python
from backend.app.ml.market_model import load_models, load_predictions
models = load_models()  # 최신 버전; 기존 날짜 없는 모델도 지원
models = load_models(version_id="20261022_143012_123456_a1b2c3d4")
predictions = load_predictions(version_id="20261022_143012_123456_a1b2c3d4")
```

DB 장애·테이블 미준비 시 모델 파일은 보존하고 해당 버전의 `registry_sync.json`에 **PENDING**을 남깁니다. DB를 준비한 뒤 아래 명령으로 학습 없이 재등록합니다. 같은 기록을 여러 번 보내도 행이 중복되지 않고 기존 이력의 다른 내용으로 덮어쓰지도 않습니다.

```powershell
python -m backend.app.ml.model_registry sync
```

등록 성공은 SYNCED로 표시합니다. ML 입력 전처리는 계속 수집 파일을 사용하며, 환율 조회와 모델 메타정보 기록은 별도 DB 용도입니다. S3 전환은 보류 상태입니다.

## 실험용 월별 캐시와 SGD 추가학습 A·B

기존 HGB와 독립된 실험 계열을 `incremental.py`에 추가했습니다. **기존 HGB 가중치를 SGD로 변환하는 기능은 아닙니다.** 최초 한 번 SGD 모델을 생성한 뒤 저장된 계수·학습 카운터·전처리 통계·학습 완료 행 기록을 불러와 새 정답 행에만 `partial_fit`을 적용합니다. A는 `SGDRegressor(loss="huber")`, B는 `SGDClassifier(loss="log_loss")`이며, 두 모델 모두 날짜 버전과 DB 이력을 남깁니다.

실데이터 명령은 아직 실행하지 않았습니다. 아래 명령은 실행하면 전처리·학습을 시작합니다.

```powershell
# 최초 한 번: 별도 SGD 계열 생성 (원본 CSV -> 피처 캐시 -> 초기 학습·평가·저장)
python -m backend.app.ml.incremental bootstrap

# 월별 수집 완료 후: 영향 구간만 피처 갱신 -> 기존 SGD에 새 정답 행만 추가학습
python -m backend.app.ml.incremental update

# 전체 재학습 모델도 전처리 캐시를 사용할 수 있음
python -m backend.app.ml.market_model train --promotion-metric country_ndcg_at_5 --feature-cache-dir data/ml/monthly_feature_cache
```

- 원본 CSV 파싱·정규화·해시 확인은 매번 수행합니다. 시간이 큰 기준월별 피처 계산을 캐시로 줄이며, 전체 원본 읽기를 없앤 구현은 아닙니다. 캐시가 없으면 최초 전처리가 필요합니다. 기존 보고서용 `features_snapshot.pkl`은 월별 입력 해시가 없어 자동 전환하지 않습니다.
- 월이 하나 추가되면 보통 **최근 13개 기준월**을 계산합니다. 예를 들어 2026-10이 추가되면 2025-10의 12개월 미래 정답이 새로 확정되고 2026-10 피처가 새로 생깁니다. 나머지 과거 기준월은 재사용합니다. 과거 데이터 수정은 해당 월의 12개월 전부터, 외부 기준정보·코드 변경은 전체 피처를 다시 계산합니다.
- SGD의 결측 대체값·표준화·범주 인코딩·A 정답 클립·B 유형 임계값을 초기 학습 이후 고정합니다. 표준화를 매월 다시 맞춰 기존 계수의 의미가 달라지는 것을 막습니다. 새 범주는 현재 인코딩에서 무시되므로 범주/분포 변화가 크면 새 계열 초기화와 재비교가 필요합니다.
- 이미 학습한 `(기준월, 국가, HS6)`의 피처·정답 수정 또는 삭제는 중단합니다. 수정한 행을 다시 추가해서 기존 학습을 취소할 수 없기 때문입니다. **새 `--data-dir` 폴더에 bootstrap**하여 이력을 보존합니다. 처음 보는 키는 같은 기준월에 추가돼도 새 행으로 학습할 수 있습니다. 같은 입력을 다시 실행하면 중복 학습·중복 버전을 만들지 않습니다.
- 평가는 운영 모델과 별도로 보존한 SGD 체크포인트를 사용합니다. 평가 기준월보다 **12개월 이상 앞선 기준월**까지만 학습하고, 전처리도 그 초기 학습 구간에서만 추정합니다. 운영 모델은 더 최근 정답을 이미 배웠기 때문에 그대로 과거 평가에 쓰지 않습니다. 이는 단일 시간 분할이며 독립 최종 Holdout/시간 K-fold를 대체하지 않습니다.
- A는 국가별 NDCG@5가 성장률 기준선 이상이어야 새 버전을 저장합니다. B는 공개 시점과 2개 이상 학습 클래스·평가 라벨이 확보되고 macro-F1 비교를 통과할 때만 생성/갱신합니다. B가 미평가·미달이면 기존 B는 그대로 보존하고 `UNCHANGED`, 기존 B가 없으면 `SKIPPED`입니다. A의 월별 갱신에 새로운 BACI 수집본이 필수인 것은 아닙니다.

산출물은 `data/ml/incremental/versions/<ID>/`의 A·B joblib, 예측 CSV, 평가를 담은 `metadata.json`, 재개용 `incremental_state.joblib`입니다. DB의 `data_info.model_family=sgd_incremental`과 `training_mode`로 계열·학습 방식을 구분합니다. `train_rows`는 누적 학습 행 수, `data_info.updated_rows`는 이번 실제 추가학습 행 수입니다. `PARTIAL_FIT`의 `parent_version_id`는 실제 학습 상태를 이어받은 직전 버전입니다. 새 버전이 저장되어도 HGB 최신 모델 포인터는 바뀌지 않습니다.

```python
from backend.app.ml.market_model import load_models, load_predictions
models = load_models(out_dir="data/ml/incremental")
predictions = load_predictions(out_dir="data/ml/incremental")
```

SGD의 DB 기록 재시도는 `python -m backend.app.ml.model_registry sync --data-dir data/ml/incremental`입니다. **SGD 실험 경로는 자동 월별 스케줄에 연결하지 않았습니다.** 최종 2단계 모델은 앞부분에 설명한 별도 월별 경로를 사용합니다. 기존 PDF 노트북은 HGB 검증용입니다. SGD 결과는 해당 버전의 평가 JSON을 확인합니다. 실제 시간·추천 성능은 미측정이고 기존 HGB보다 좋다고 주장하지 않습니다.

### 초기 HGB·SGD 선택 근거와 추가학습 대안

HGB는 표 데이터의 비선형 상호작용, 결측·범주형 입력을 처리하면서 기존 scikit-learn 환경에서 사용할 수 있어 첫 기준 모델로 선택했습니다. 현재 실데이터 검증은 성장률 기준선과 관련 피처 유무 비교이며 **다른 부스팅을 모두 비교한 최적 모델 선정은 아닙니다.** SGD 선택은 월별 작은 배치에 대한 명시적 `partial_fit`, 별도 의존성 없이 A·B에 같은 재개 구조를 적용할 수 있다는 구현상 판단입니다. 선형 회귀·분류여서 비선형 상호작용 표현력이 제한되고, 순서·학습률·분포 변화에 민감합니다.

| 후보 | 월별 갱신 방식 | 이 작업의 판단 |
|---|---|---|
| HistGradientBoosting | 전체 재학습. `warm_start`는 같은 데이터에서 부스팅 반복을 늘리는 용도 | 초기 비교 기준 모델. 피처 캐시로 전처리 비용을 줄일 수 있음 |
| SGD 회귀·분류 | 저장된 계수와 학습 상태에 `partial_fit` | 가벼운 추가학습 비교 모델. 추천 성능 우위는 미확인 |
| LightGBM | 추가학습 대안은 `init_model`; 최종 2단계 경로는 전체 재학습 | 추가 트리 누적 방식은 별도 비교 대상이며 현재 월별 실행에는 사용하지 않음 |
| XGBoost | `xgb_model`로 기존 부스터에서 학습 계속 | 동일한 비교 후보. 새 월만 사용할지 과거 표본을 섞을지 평가 설계 필요 |

성능 비교 시 입력/정답/공개 시점/12개월 gap/평가 국가·품목을 맞추고 국가별 NDCG@5를 주 지표로 사용합니다. 기존 HGB 점수와 다른 평가월의 SGD 점수를 단순 비교하지 않습니다. 학습·전처리 시간, Precision@5, 품목군별 성능, 반복 업데이트 후 과거 성능 유지도 함께 기록해야 합니다. 이 절의 SGD는 추가학습 실험용이며, 현재 최종 선정 구성은 문서 앞부분의 2단계 C(K=100) v1 · v3 앙상블입니다.

공식 근거: [HGB warm_start 제약](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html), [SGDRegressor partial_fit](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.SGDRegressor.html), [SGDClassifier partial_fit](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.SGDClassifier.html), [LightGBM init_model](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.train.html), [XGBoost 학습 재개 예제](https://xgboost.readthedocs.io/en/stable/python/examples/continuation.html).

## 주의

- 예측 대상은 최근 12개월 수출이 $100k 이상인 쌍뿐입니다. 그보다 작은 쌍은 `query`에서 "예측 대상 아님"으로 나옵니다.
- HS2017 잔존 코드는 UNSD 대응표의 묶음 대표 코드(HS2022)로 합쳐 계산합니다. 그래서 결과의 `hs6`가 묶음 대표 코드일 수 있습니다.

## 테스트

```powershell
# 최종 모델·월별 저장/DB 등록 경로: 가상 데이터와 SQLite 사용
python -m unittest backend.tests.test_ml_two_stage backend.tests.test_ml_candidate_models

# 기존 모델 및 공통 입력 처리
python -m unittest backend/tests/test_ml_market.py backend/tests/test_ml_sources.py backend/tests/test_external_data.py backend/tests/test_ml_temporal.py
```

## 기존 HGB 검증 노트북과 PDF 자동 보고서

아래 `report_workflow`는 기존 HGB 산출물을 검증합니다. 최종 2단계 모델의 검증·평가 보고서로 대신 사용하지 않습니다.

### 저장 환경과 실행 환경 일치

현재 제공된 `features_snapshot.pkl`·`models.joblib`은 Python 3.12.14, NumPy 2.5.4, pandas 3.0.1, scikit-learn 1.9.1, joblib 1.6.0 환경에서 생성했습니다. SciPy 1.18.1을 포함한 재현 버전은 `backend/requirements-ml-runtime-20261022.txt`로 고정합니다. 기존 환경을 변경하기보다 Python 3.12의 별도 환경에서 보고서를 실행하세요.

```powershell
# Python 3.12가 설치되어 있어야 합니다. 저장소 루트에서 실행합니다.
py -3.12 -m venv .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install -r backend/requirements-ml-report.txt -c backend/requirements-ml-runtime-20261022.txt
.\.venv-ml\Scripts\python.exe -m backend.app.ml.runtime check
```

Jupyter에서 직접 실행하면 설치 후 커널을 재시작하고 같은 `.venv-ml` Python을 선택합니다. CLI는 실행한 Python으로 커널을 생성합니다. `runtime check`는 버전만 확인하며 피처 생성·모델 로드·학습을 하지 않습니다. 이후 모델은 버전별 `metadata.json`과 `runtime-requirements.txt`에 실제 저장 환경을 남깁니다.

위 날짜 고정 버전은 최초 제공 모델을 위한 것입니다. 다른 환경에서 새로 학습한 버전은 그 버전의 `runtime-requirements.txt`를 `-c`에 지정합니다. 최근 학습 시도 기록이 `running`이면 완료 여부를 아직 확인할 수 없으므로, 파일을 삭제하거나 성공으로 바꿔 검사를 통과시키지 않습니다.

`No module named 'numpy._core'`는 저장된 캐시와 현재 NumPy 환경이 맞지 않을 때 발생할 수 있습니다. 이 경우 PDF가 생성되어도 산출물 검사가 실행되지 않아 전체 검증은 실패합니다. `pdf_check.passed=true`는 PDF 구조만 정상이라는 뜻입니다. 버전을 맞추는 문제를 재학습으로 해결하거나 import 이름을 임의로 바꿔 우회하지 않습니다.

### 실행

저장소 루트에서 다음 명령을 실행합니다. 보고서용 의존성은 기존 환경에 추가로 설치합니다.

```powershell
python -m pip install -r backend/requirements-ml-report.txt

# 저장된 학습 결과 검증 → ML 관련 단위 테스트 → 필요 시 시간 K-fold → PDF 자동 생성
python -m backend.app.ml.report_workflow

# 수집 원본부터 피처 생성·학습한 뒤 위 검증과 보고서까지 한 번에 실행
python -m backend.app.ml.report_workflow --train

# 이미 생성된 검증 JSON으로 PDF만 다시 생성
python -m backend.app.ml.reporting
```

실제 테스트와 보고서 생성 절차는 [`notebooks/ml_validation_report.ipynb`](../../../notebooks/ml_validation_report.ipynb)에 있습니다. Jupyter에서 셀 전체 실행도 가능합니다. 자동 실행은 현재 Python의 임시 커널을 사용하며 전역 커널 설정을 바꾸지 않습니다. 기존 `market_model train`·`unittest` 단독 실행에는 PDF 생성이 연결되지 않으므로, 자동 보고서가 필요하면 위 `report_workflow` 명령을 사용합니다.

| 생성 파일 | 내용 |
|---|---|
| `output/pdf/ml_market_report.pdf` | 전체 흐름, 입력 형식·실제 기간, 전처리·정답, 출력 스키마, 평가 방법·점수, 테스트·한계 |
| `output/pdf/ml_report_validation.json` | 단위 테스트, 산출물 검사, 시간 K-fold, 모델·CSV·피처 캐시 SHA-256 |
| `output/pdf/ml_validation_report.executed.ipynb` | 실제 셀 실행 로그와 결과 |

기존 결과 검증에는 `data/ml/features_snapshot.pkl`이 필요합니다. 없으면 `--train`으로 생성합니다. pickle과 joblib은 직접 생성한 신뢰할 수 있는 파일만 사용합니다. 경로 변경은 `--data-dir`, `--feature-cache`, `--output-dir`로 지정합니다. PDF·실행 노트북은 Git에서 제외하며 원본 노트북과 생성 코드는 버전 관리합니다.

`--cv auto`가 기본입니다. 단일 평가의 **학습 기준월이 12개 미만** 또는 **학습 행이 1,000개 미만**이면 시간 순서 K-fold를 실행합니다. 현재는 학습 기준월 10개여서 3-fold가 적용됩니다. `--cv on --folds 3`으로 강제할 수 있고 `--cv off`는 생략 사실을 기록합니다. 이 기준은 운영 설정이며 통계적 충분성을 보장하는 검정은 아닙니다.

한 fold의 검증은 같은 기준월의 모든 국가·품목을 포함합니다. 학습은 검증 기준월보다 12개월 이상 앞선 기준월만 사용하여 학습 정답이 검증 시점을 넘지 않도록 하고, 변환기를 매번 다시 학습합니다. 최소 학습 기준월 3개·1,000행을 확보한 검증 시점이 2개 미만이면 이력 부족으로 기록합니다. 무작위 K-fold로 바꾸지 않습니다. **저장 모델의 선택된 피처 구성에 대한 후향적 보조 검증**이며, fold 간 정답 기간이 겹칠 수 있어 평균·표준편차는 기술 통계입니다. 독립 최종 Holdout을 대체하지 않습니다.

테스트·산출물 검사 실패 시에도 실패 항목을 담은 새 PDF와 실행 노트북을 남긴 뒤 종료 코드 1을 반환합니다. `--train`의 새 모델 채택이 거부되어 기존 모델이 남아 있는 경우에도 최신 학습 시도 실패로 표시합니다. 한글 TTF가 없는 환경에서는 `ML_REPORT_FONT`와 선택적 `ML_REPORT_FONT_BOLD`에 폰트 경로를 지정합니다.

