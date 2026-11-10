# feat/52-agent-impl 서버 반영 절차

기준: 서버에는 지금 `main`이 올라가 있다. 이 문서는 `feat/52-agent-impl`(PR #61)을 반영할 때 서버에서 추가로 해야 할 일만 적는다. 작성 2026-11-05.

> 이 브랜치에는 아직 커밋하지 않은 변경(모델 B 월별 학습·재시도, ML 시간 제한, 패키지 버전 고정 등)이 있다. 반영 전에 커밋·푸시하고 아래 절차를 따른다.

## 한눈에 보기

| 순서 | 할 일 | 필수 여부 |
|---|---|---|
| 1 | 코드 반영 | 필수 |
| 2 | API·워커 Python 3.11 환경과 패키지 설치 | 필수 |
| 3 | 모델 파일 업로드 (git에 없음) | 필수(유망시장), B는 선택 |
| 4 | `.env` 변경 | 필수 |
| 5 | DB 마이그레이션·테이블 확인 | 필수 |
| 6 | Redis 모듈 확인 | 필수 |
| 7 | 프론트 빌드·배포 | 필수 |
| 8 | 재시작 | 필수 |
| 9 | 동작 확인 | 필수 |
| 10 | AWS ML 실행일 변경·다음 실행 확인 | 수집 일정 배포 후 |

---

## 1. 코드 반영

```bash
git fetch origin
git checkout main
git merge --no-ff origin/feat/52-agent-impl   # 또는 PR #61 병합 뒤 git pull
```

## 2. API·워커 실행 환경 (Python 3.11)

이번 변경으로 API·워커가 유망시장 모델을 직접 읽는다. 패키지 버전을 배포 모델의 runtime에 맞췄기 때문에 **Python 3.11**이어야 한다(scipy 1.10.1은 Python 3.12를 지원하지 않는다).

| 패키지 | 버전 | 비고 |
|---|---|---|
| numpy | 1.26.4 | 모델 runtime은 1.24.3. `redisvl`(langgraph-checkpoint-redis)이 1.26 이상을 요구해서 1.26.4. 같은 1.x라 모델 파일을 그대로 읽는다 |
| pandas | 2.2.3 | 모델 runtime과 같음 |
| scipy | 1.10.1 | 같음 |
| scikit-learn | 1.9.1 | 같음 |
| joblib | 1.6.0 | 같음 |
| lightgbm | 4.7.0 | 같음 |
| threadpoolctl | 3.7.0 | |
| langgraph / langgraph-checkpoint-redis / sqlglot | 1.2.19 / 0.5.2 / 30.27.0 | 채팅 에이전트 |

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends libgomp1     # LightGBM이 쓰는 OpenMP 런타임
python3.11 -m venv /opt/borderless/venv-api
/opt/borderless/venv-api/bin/pip install -r backend/requirements-api.txt
/opt/borderless/venv-api/bin/python -c "import lightgbm, pandas, numpy; print(lightgbm.__version__, pandas.__version__, numpy.__version__)"
```

- 2026-11-05에 Python 3.11에서 `pip install --dry-run -r backend/requirements-api.txt`로 의존성이 모두 풀리는 것을 확인했다(실제 서버 설치는 아직 안 함).
- 가상환경 경로는 예시다. 기존 API 환경이 Python 3.11이 아니면 새로 만든다.

## 3. 모델 파일 업로드

`data/`는 `.gitignore`(`/data/*`)라 **git으로 올라가지 않는다.** 아래 파일을 서버의 저장소 루트 기준 `data/ml/monthly_models/`에 직접 복사한다.

| 파일 | 크기 | SHA-256 | 필수 |
|---|---|---|---|
| `model_a_two_stage_202609.joblib` | 8,990,041 B | `32d693ee46ca47af3ddfceb230f571ba1b9587f97b1efe133edf479c4c14b327` | 필수 (유망시장 순위) |
| `model_a_two_stage_202609.registry.json` | 26,632 B | `96eee0b4ac79779c73fca987f52a951d606ed4f12dd7d0ea5504ef975caee4ad` | 필수 (워커가 이 READY 기록으로 모델 A를 찾는다) |
| `model_b_growth_202609.joblib` | 14,151,812 B | `234f5a0d243d7593f325d30459b715430d60d91228b935adb8c4656365d2efed` | 선택 (증가·감소 예상). 없으면 방향 없이 답한다 |

```bash
# 로컬(저장소 루트)에서
scp data/ml/monthly_models/model_a_two_stage_202609.joblib \
    data/ml/monthly_models/model_a_two_stage_202609.registry.json \
    data/ml/monthly_models/model_b_growth_202609.joblib \
    <사용자>@<서버>:<저장소 경로>/data/ml/monthly_models/

# 서버에서
cd <저장소 경로>
sha256sum data/ml/monthly_models/*.joblib data/ml/monthly_models/*.registry.json
```

- 모델 A의 체크섬은 레지스트리의 `artifact_sha256`과 같아야 한다(위 값과 동일).
- 레지스트리의 `data_info.artifact_root`에 로컬 Windows 경로가 적혀 있지만 정보용이다. 워커는 `API_ML_MODEL_DIRECTORY` 기준 `artifact_path`(파일 이름)로 찾는다.
- 모델 B 파일은 직접 만든 파일이라 레지스트리가 없다. 워커는 "모델 A와 같은 기준월(2026-09)의 파일"로 찾는다. 기준월이 다른 B 파일은 쓰지 않는다.
- 파일을 바꾸면 워커가 파일 크기·수정 시각을 보고 새로 읽는다(재시작 불필요).
- 워커는 시작할 때 모델 A·B를 미리 올린다(모델 A 약 25초). 기다리지 않고 바로 작업을 받으므로, 재시작 뒤 약 25초 안에 들어온 유망시장 질문만 "예측 모델을 불러오는 중입니다. 잠시 후 다시 질문해 주세요." 안내를 받는다. 로그 `agent_ml_warm_failed`가 보이면 모델 파일·경로를 확인한다.

## 4. `.env` 변경

### API·워커 (`backend/.env.api.example` 기준)

| 키 | 값 | 변경 |
|---|---|---|
| `API_CHAT_AGENT_ENABLED` | `true` | 추가. 새 질문을 LangGraph 워커로 접수한다. 기존 미완료 작업은 원래 실행기로 마친다 |
| `API_COMPOSE_MODEL` | `gpt-4.1-mini` | 추가 |
| `API_COMPOSE_TIMEOUT_SECONDS` | `20` | 추가 |
| `API_SQL_MODEL` | `gpt-4.1` | 추가 (C-30 미정, 임시값) |
| `API_SQL_TIMEOUT_SECONDS` | `20` | 추가 |
| `API_SQL_EXECUTION_SECONDS` | `30` | 추가 |
| `API_CHAT_MAX_RECOVERIES` | `2` | 추가 |
| `API_ML_MODEL_DIRECTORY` | `data/ml/monthly_models` | 추가 |
| `API_ML_MODEL_PATHS` | (비움) | 값 변경. 예전 예시값(`data/ml/candidates/...`)을 지운다. 비우면 위 폴더의 READY 레지스트리로 찾는다 |
| `API_ML_GROWTH_MODEL_PATH` | (비움) | 추가. 비우면 위 폴더에서 모델 A와 같은 기준월 B 파일을 찾는다 |
| `API_ML_SNAPSHOT_PATH` | - | **삭제** (더 이상 읽지 않음) |

- 기존 `OPENAI_API_KEY`, `API_REDIS_URL`, `API_REDIS_PREFIX`, `API_INTENT_MODEL` 등은 그대로 쓴다.
- 자유 SQL(LLM SQL)은 지금 `.env`의 DB 계정으로 `READ ONLY` 트랜잭션에서 돈다. 배포 전에 읽기 전용 DB 계정으로 바꾸는 것이 설계 결정이다(`TODO(D-41)`).

### 스케줄러 (`.env.sample` 기준, 10번을 할 때만)

| 키 | 값 |
|---|---|
| `CUSTOMS_ML_ENTRYPOINT` | AWS 운영은 빈 값 유지. 기존 월 파이프라인 수동 실행에만 사용 |
| `CUSTOMS_ML_GROWTH_RETRIES` | `3` (모델 B가 같은 달에 실패했을 때 자동 재학습 최대 횟수) |

## 5. DB

| 대상 | 할 일 |
|---|---|
| `db/mysql/migrations/20261102_report_service_option.sql` | **적용**. 요청형 리포트 옵션(`RPT_ON_DEMAND`)을 켠다. 채팅에서 리포트를 만들 때 필요하다 |
| `hs_code_correlation` | 있는지 확인(`db/mysql/init/02_hs_code_correlation.sql`). 판 개정으로 묶인 HS6(예: 150930)의 대표 코드를 찾는다. 없으면 요청 코드로만 조회한다 |
| `ml_model_version` | 10번(월별 학습)을 켤 때만 확인(`db/mysql/init/10_ml_model_version.sql`) |

```bash
mysql -h <RDS> -u <user> -p <db> < db/mysql/migrations/20261102_report_service_option.sql
mysql -h <RDS> -u <user> -p <db> -e "SHOW TABLES LIKE 'hs_code_correlation'; SHOW TABLES LIKE 'ml_model_version';"
```

## 6. Redis

- 워커는 LangGraph 체크포인트를 Redis에 저장하므로 **RedisJSON·RediSearch 모듈**이 필요하다. 시작할 때 모듈이 없으면 멈춘다(`agents/worker.require_modules`).
- `deploy/redis/docker-compose.yml`(`redis:8.2`)을 쓰고 있으면 두 모듈이 들어 있다. 다른 Redis를 쓰면 확인한다.

```bash
redis-cli -a <비밀번호> MODULE LIST     # search, ReJSON이 보여야 한다
```

## 7. 프론트

API 응답이 바뀌었다(작업 진행 상태, 되묻기, 하위 질문별 결과, 차트 선택, 통합 비교, 답변 스트림 API-133, 유망시장 표의 "증감 예상" 열). 프론트를 다시 빌드해 배포한다.

```bash
cd frontend
npm ci
npm run build
# dist/를 기존 정적 배포 위치에 올린다
```

## 8. 재시작

1. API: `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers`
2. 워커: `python -m backend.app.api.worker`
   - 처음 시작할 때 Playwright Chromium이 없으면 설치한다(리포트 PDF용).
3. (10번을 켤 때) 스케줄러: `python -m backend.app.scheduler.main`

API와 워커는 2번에서 만든 Python 3.11 환경으로 실행한다.

## 9. 동작 확인

```bash
# 워커 환경에서 모델 로드 확인
python -c "from backend.app.api.config import Settings; from backend.app.agents.market import loaded, growth; b,_,_=loaded(Settings()); print(b['anchor']); print(growth(Settings(), '2026-09')[1:])"
```

- 첫 줄에 `2026-09-01`, 둘째 줄에 `(<체크섬>, None)`이 나오면 A·B를 모두 읽은 것이다. 둘째 값이 `'none'`이면 B 파일이 없다는 뜻이다.
- 채팅 확인 질문:
  - "2025년 미국 수출 추이" (통계·차트)
  - "베트남 유망 품목 알려줘" (유망시장. 표에 "증감 예상" 열)
  - "수출 리포트 만들어줘" (리포트 확인 안내 → 생성)
- 워커 로그에서 확인할 것:
  - `agent_ml_timeout`: 유망시장 예측 시간 초과(30초) 경고
  - `agent_ml_stuck`: 예측이 5분 넘게 끝나지 않음 → **워커를 다시 시작한다**

## 10. AWS 월별 학습 운영

월별 수출입 작업은 매월 18일 03:00 KST에 수집·계산을 실행하고 ML은 AWS에서만 예약한다. 통합 스케줄러에는 ML 예약·복구 작업이 없으며 `CUSTOMS_ML_ENTRYPOINT`가 남아 있어도 수집·시작 복구·재시도에서는 학습하지 않는다. 운영 설정은 빈 값을 유지한다. **배포 담당자는 머지·수집 일정 배포 후 AWS ML 실행일을 11일에서 19일로 변경하고 기존 실행 시각·시간대 및 변경 후 다음 실행 일시를 확인한다. AWS 설정은 이번 코드 수정에서 변경하지 않았다.** 모델 B가 실패해도 모델 A는 저장되며, 재시도는 AWS 운영 작업 또는 기존 수동 명령으로 처리한다.

- **월별 학습은 API·워커와 다른 서버에서 돌린다(2026-11-06 결정, C-23).** 같은 서버면 학습 시간대에 메모리가 모자라 채팅 응답이 느려진다.
- **스케줄러는 API·워커와 다른 환경이어야 한다.** 월별 피처 캐시를 만든 runtime(Python 3.11.9, numpy **1.24.3**, pandas 2.2.3, scipy 1.10.1)과 버전이 정확히 같아야 읽힌다. API 환경(numpy 1.26.4)으로 돌리면 캐시 읽기를 거부한다. 버전은 `backend/requirements-ml-runtime-20261022.txt`·모델 레지스트리의 `data_info.runtime`을 따른다.
- 필요한 자료: 관세청 수집 원본, `data/ml/monthly_feature_cache`, `data/external`(외부 피처), DB `fx_daily`.
- 시간·메모리 (2026-11-05, 4코어, 8GB PC에서 측정, 스왑이 섞임):

  | 단계 | 시간 |
  |---|---|
  | 모델 A | 약 11분 |
  | 모델 B 피처 표 | 435초 |
  | 모델 B 학습(검증 + 전체) | 약 12분 |
  | 합계 | 약 31분 |

  8GB에서는 여유 메모리가 600MB까지 떨어졌다. 그래서 학습은 별도 서버로 분리하기로 했다(C-23).
- 직접 만든 B 파일(`model_b_growth_202609.joblib`)이 있는 달은 월별 학습이 덮어쓰지 않는다. 월별 학습 결과(레지스트리 포함)로 바꾸려면 그 파일을 치운다.
- 모델 B만 수동으로 다시 돌리기(DB `ml_model_version`에 기록함):

  ```bash
  python -c "from backend.app.ml.candidate_models import run_growth; print(run_growth('2026-09-01'))"
  ```

## 롤백

1. 코드를 이전 `main`으로 되돌리고 API·워커를 재시작한다.
2. `API_CHAT_AGENT_ENABLED=false`로 두면 새 질문은 기존 실행기로 돌아간다. 이미 접수된 에이전트 작업은 워커가 마친다.
3. 모델 파일·DB 마이그레이션(`RPT_ON_DEMAND` 활성화)은 그대로 두어도 기존 코드에 영향이 없다.

## 반영 후 남은 확인 사항

- 자유 SQL DB 계정을 읽기 전용 계정으로 바꾸기 (`TODO(D-41)`)
- EC2 메모리 배분 (C-23): 월별 학습은 별도 서버로 분리(2026-11-06 결정). API·워커 서버에 Redis 1280MB + API + 워커(유망시장 모델 + 기사 색인 2~4GB, D-104) + Chromium이 들어가는지 확인
- 알려진 낮은 빈도 문제 (빈도 1% 미만):
  - 과거 연도를 붙인 유망시장 질문에서 최신 예측에 동의해도 조건 기간이 그대로 남는다
  - 워커 재시작 직후 HS4 유망시장 질문에서 모델 로드 대기가 조건 확인 예약 시간을 넘길 수 있다
