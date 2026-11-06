# S3 영구 저장소 전환

`ARTIFACT_STORAGE=s3`에서는 파일의 확정본과 실행 상태를 S3에 저장한다. 기존 `data/`·수집기 출력 폴더는 라이브러리에서 요구하는 작업 경로이며, 실행 전에 S3에서 복원한다. S3 오류 때 로컬 저장으로 전환하지 않는다. MySQL의 업무 데이터, Redis의 채팅 상태, Chroma 색인 계약은 유지한다.

코드 반영만으로 운영 전환이 끝나지 않는다. 아래 순서로 **실행 중인 수집의 종료 확인 → 기존 파일 이관 → IAM·설정 반영 → 검증 → 기존 동기화 타이머 해제**가 필요하다. 현재 배포 서버나 IAM을 이 코드가 자동 변경하지 않는다.

## 설정

API·worker·scheduler·ML이 같은 루트 `.env` 또는 프로세스 환경을 읽는다. 프로세스 환경이 우선한다.

```dotenv
ARTIFACT_STORAGE=s3
ARTIFACT_S3_BUCKET=your-private-bucket
AWS_REGION=ap-northeast-2
ARTIFACT_S3_PREFIX=
ARTIFACT_CACHE_DIRECTORY=runtime/s3-cache
ARTIFACT_MODEL_CHECK_SECONDS=30
API_ML_MODEL_PATHS=
API_ML_GROWTH_MODEL_PATH=
```

- 기본값은 `local`: 이관 전 실행 동작을 보존한다. 잘못된 모드·누락된 버킷 설정은 실패한다.
- `ARTIFACT_S3_PREFIX`는 격리 검증용 전체 키 앞부분이다. 운영은 기존 `raw/`, `ml-inputs/`, `reports/`, `models/` 권한과 맞추어 비운다.
- `API_REPORT_DIRECTORY`는 local 모드 및 구형 리포트 이관 원본 위치다. S3 리포트 생성에는 쓰지 않는다.
- `API_ML_MODEL_DIRECTORY` 등 기존 경로는 작업 폴더다. S3 모드의 API는 S3 월별 레지스트리를 선택하며 로컬 모델 파일 직접 고정 설정은 거부한다.
- 모델 목록은 기본 30초마다 ETag 조건부 조회한다(`ARTIFACT_MODEL_CHECK_SECONDS`, 0~3600초). 확인 주기 안에서는 S3를 다시 호출하지 않는다. 변경 없는 파일은 크기·수정 시각·파일 식별자를 확인하며 전체 해시를 반복 계산하지 않는다. 다운로드·변경 감지 시에는 해시를 검증한다.
- AWS 키를 코드·설정 예시에 넣지 않는다. EC2 IAM Role 등 boto3 기본 자격증명 체계를 사용한다.
- `boto3`, `filelock`을 각 실행 환경에 설치한다. ML 별도 환경도 `backend/requirements.txt`의 S3 의존성이 필요하다.

## 저장 범위와 진입점

| 이관 이름 | S3 prefix | 쓰기/읽기 연결 |
|---|---|---|
| `un-runtime` | `raw/un-data/runtime` | UN 초기화·월별 갱신·분당 재개·제외 승인, 체크포인트와 복구 차단 표식 |
| `un-inputs` | `raw/un-data/inputs` | 검증된 초기 CSV·수집 범위·기준 파일. UN 초기화/갱신은 S3에서 읽음 |
| `customs-monthly` | `raw/customs/monthly` | 월별 수집·누락 월 복구·단계 상태, ML 입력 |
| `customs-history` | `raw/customs/history` | 관세청 일괄 수집 CLI, ML의 과거 CSV |
| `shortnews-daily` | `raw/shortnews/daily` | 단신속보 일일/복구 작업의 원본·전처리·상태 |
| `shortnews-history` | `raw/shortnews/history` | 단신 수집 CLI와 ML의 기존 뉴스 입력 |
| `shortnews-processed` | `ml-inputs/shortnews-processed` | 단독 단신 분석 파이프라인 출력. 일일 작업에서는 일일 namespace에 함께 확정 |
| `news`, `news-history` | `raw/news`, `raw/news-history` | 해외시장뉴스 수집 CLI 및 기존 통합 CSV 적재 입력 |
| `external` | `ml-inputs/external` | WDI/BACI/Comtrade 수집 CLI·월별 갱신, availability와 ML 읽기 |
| `hs-output`, `hs-reference` | `ml-inputs/hscode`, `ml-inputs/hs-reference` | HS 연간 갱신의 출력·원본, ML 읽기 |
| `keywords` | `ml-inputs/keywords` | 기존 키워드 사전/전처리 파일의 ML·기사 적재 입력 |
| `trade-news` | `raw/trade-news` | 기사 일일 파이프라인의 원본·실행 기록. 요청 예산 DB는 하위 `database/`에서 커밋별 확정 |
| `models` | `models/monthly` | 모델 A/B 월별 학습·DB 등록 재시도·API 읽기 |
| `model-candidates` | `models/candidates` | 후보 모델 및 two_stage train 출력 |
| `model-versions` | `models/versions` | `artifacts.save_version`, 버전 파일 읽기·레지스트리 재동기화 |
| `reports` | `reports/` | API 리포트 PDF·DOCX·XLSX 저장/다운로드 |

MySQL에서 직접 조회하는 업무 데이터·환율은 S3 파일로 바꾸지 않는다. 재생성 가능한 ML 피처 캐시·전처리 중간 파일·색인은 영구 원본에 포함하지 않는다. 연구용 노트북의 임의 파일 쓰기까지 가로채는 구조가 아니다. 새 생산자/수동 파일 수정은 아래 저장 경계를 연결해야 운영 입력으로 반영된다. 특히 키워드 사전의 수동 편집은 로컬 캐시 변경만으로 배포되지 않는다.

## 저장과 오류 처리

각 데이터셋은 `_blobs/<SHA256>`에 내용별 불변 파일을 저장한다. 모두 저장한 뒤 `_manifest.json`을 `If-Match`/`If-None-Match`로 교체한다. `_snapshots/`에 확정 목록 이력도 보존한다. 여러 파일이 반쯤 올라간 목록을 공개하지 않는다. 원본 파일 이름은 manifest의 상대 경로로 유지된다. 버킷에 빈 폴더만 만들어 놓는 것으로는 manifest나 파일이 생기지 않는다.

- S3는 연결 5초·읽기 30초, SDK standard 방식 최대 4회 시도한다. 일시적 장애·충돌은 재시도 가능, 권한 오류·무결성 오류·설정 오류는 조치가 필요한 오류다.
- CSV·ZIP·joblib은 스트림으로 전송/검증한다. 큰 파일은 multipart를 사용하며, 전송 내용의 해시 확인 후 `If-None-Match` 조건으로 완료해 기존 blob을 덮어쓰지 않는다. 다운로드는 해시를 확인한 임시 파일만 원자적으로 교체한다. `.tmp`, `.part`, 잠금 파일과 심볼릭 링크는 영구 파일로 취급하지 않는다.
- manifest의 300초 lease를 30초마다 갱신한다. 같은 namespace의 실행을 직렬화하며, 만료된 실행은 후속 실행의 ETag를 덮어쓸 수 없다. 갱신 실패 시 완료 확정을 차단한다. 서버 시계를 NTP로 동기화한다. 기존 DB 잠금·트랜잭션은 유지한다.
- S3 실패를 하위 코드가 잡더라도 해당 작업 공간은 실패 상태를 유지한다. 마지막에 `SUCCESS`로 바뀌지 않는다. 미완료 blob은 참조되지 않으므로 읽기에 사용하지 않는다.
- 리포트는 필요한 모든 형식의 업로드 성공 뒤 DB를 `READY`로 바꾼다. S3 성공 후 DB 실패 시 기존 worker 재시도로 복구하며, 미참조 파일은 사용자에게 노출되지 않는다. 다운로드의 소유권·READY 검사를 먼저 수행한다.
- 모델은 파일·READY 레지스트리를 S3에 확정한 뒤 DB에 등록한다. DB만 실패하면 같은 입력의 기존 버전을 읽어 재등록한다. API의 모델 캐시는 해시별 blob을 공유하고 현재·직전 목록의 디렉터리만 유지한다. 파일 선택부터 모델 로드까지 프로세스 간 잠금을 유지해 로드 중인 캐시를 삭제하지 않는다. 하드링크가 불가능한 파일시스템에서는 복사한다. 구형 `runtime/s3-cache/models/`는 새 캐시와 별개이며, 구버전 프로세스를 종료한 뒤 수동 정리한다.
- UN은 매 원자적 상태 저장 시 원본·pending·continuation·checkpoint를 함께 확정한다. 작업 전 fence가 S3에 있어야 진행하고, 마지막 release까지 확정돼야 복구 차단이 풀린다. 강제 종료의 `RECOVERY_REQUIRED`를 시간이 지났다는 이유로 해제하지 않는다. 기존 DB UUID·범위 해시 검사를 유지한다.
- UN의 프로세스 잠금 뒤 예외 처리 안에서 S3 작업 공간을 연다. 로컬·원격 잠금 충돌은 `ALREADY_RUNNING`으로 반환한다. 매분 재개 기한 확인은 읽기 전용이며, 실제 재개할 때만 쓰기 lease를 획득하고 최신 상태를 재확인한다.
- 기사 수집의 SQLite는 S3 `database/_current.json`이 원본이다. schema 2는 예산·대기열 DB, 기사 DB, 검수 DB를 별도 snapshot으로 저장하고 **하나의 포인터로 함께 확정**한다. 예산만 바뀌면 작은 상태 DB만 복사·해시하며 기사 본문을 직렬화하지 않는다. 각 snapshot은 256KiB 조각을 공유한다. 기존 CLI 호환을 위해 로컬 `articles.sqlite3`는 두 영역을 합친 작업 사본이다.
- 검수 결과가 저장된 뒤 기사 완료 기록을 확정할 때 검수 DB도 같은 포인터에 포함된다. 부모 checkpoint 전 중단·캐시 유실에도 기사와 검수 결과를 함께 복원한다. 예산 예약 저장 실패는 HTTP 요청을 막고 연결을 실패 상태로 고정한다. 부모 파일 manifest는 기사·검수 DB와 journal을 덮어쓰지 않는다. 복원 시 이전 WAL·rollback journal도 제거해 미확정 내용의 재적용을 막는다.
- schema 1은 읽기 호환을 유지하며 다음 쓰기 때 schema 2로 전환한다. 기존 기사에 검수 결과가 없거나 불일치하면 수집 시작 및 적재 전에 저장된 원문으로 복구한다. 현재 사전·정책으로 재생성한 결과가 기록된 판정·버전·본문과 일치해야 한다. 일치하지 않으면 `RECOVERY_REQUIRED`로 남겨 적재에서 제외하고 필요한 원래 입력을 확인한다. HTTP 재수집은 하지 않는다.
- 기사 일일 작업은 부모 잠금과 자식의 `crawler.lock`을 확보한 후에만 S3 파일·DB를 복원한다. 부모만 종료되고 자식이 살아 있으면 `already_running`으로 반환하며 작업 파일을 건드리지 않는다. 새 실행 ID를 확정한 뒤 자식 잠금을 해제하므로 늦게 시작한 이전 자식은 실행 ID 검사에서 중단된다.
- 검수 JSON이 깨졌거나 객체가 아닌 경우에도 원문의 검수 근거로 재생성을 시도한다. 근거가 없거나 재현되지 않으면 복구 필요로 남기며, 개별 검수 데이터의 형식 오류 때문에 전체 수집이 중단되지 않는다. 저장소 초기화 실패 시 SQLite 연결을 닫는다.
- `news_load` 진단 로그는 입력 작업 공간 밖 `runtime/logs/news_load`에 기록한다. 구형 `data/news/logs`도 입력 복원 시 삭제하지 않는다. 로그의 장기 보존·전송은 운영 로그 수집 정책을 따른다.
- 리포트 S3 클라이언트는 프로세스·버킷·prefix·리전별로 재사용한다. IAM Role의 갱신 가능한 자격증명은 SDK가 관리한다.

## 기존 파일 이관

스케줄러·worker·수동 수집·학습이 모두 멈춘 구간에서 수행한다. 진행 중인 UN 수집은 정상 종료/대기 및 복구 표식을 확인한 후 이관한다. 전달받은 다른 DB용 checkpoint로 서버의 현재 checkpoint를 덮어쓰지 않는다.

```bash
# 먼저 파일 목록·크기·SHA256만 검토: S3 쓰기 없음
python -m backend.app.storage.migrate --dataset un-runtime
python -m backend.app.storage.migrate --dataset external
python -m backend.app.storage.migrate --dataset models
python -m backend.app.storage.migrate --dataset reports --source /opt/trade/app/runtime/reports

# 확인한 동일 원본을 업로드하고 S3에서 다시 읽어 SHA256 검증
python -m backend.app.storage.migrate --dataset un-inputs --apply
python -m backend.app.storage.migrate --dataset un-runtime --apply
python -m backend.app.storage.migrate --dataset external --apply
python -m backend.app.storage.migrate --dataset models --apply
python -m backend.app.storage.migrate --dataset reports --source /opt/trade/app/runtime/reports --apply
```

표의 사용 데이터셋마다 실행한다. ML이 읽는 `customs-monthly`, `customs-history`, `shortnews-history`, `external`, `hs-output`, `hs-reference`, `keywords`는 **빈 데이터셋도 manifest를 먼저 생성**한다. 의도한 빈 데이터셋에만 `--apply --allow-empty`를 사용한다. 빈/잘못된 원본 폴더를 실수로 확정하지 않도록 기본 업로드는 빈 입력을 거부한다. S3 모드에서 manifest가 없으면 기존 로컬 파일을 자동 승격하지 않는다. 명시적 `--source`로 다른 서버의 원본 폴더를 지정할 수 있다. `model-versions`는 `data/ml` 전체를 복사하지 않고 `versions/`, `models.joblib`, `predictions.csv`, `metadata.json`만 관리한다.

기사 일일 작업은 기존 `NEWS_CRAWL_DATA_DIR`를 지정한다. 사전 목록에는 별도 포인터로 관리하는 기사·검수 DB도 포함되며, SQLite에서 WAL의 확정 내용까지 읽어 크기와 SHA256을 계산한다. DB만 있는 원본도 정상적인 이관 대상으로 인정한다. 재이관은 기사뿐 아니라 검수 DB의 일치 여부도 확인하며, 업로드 도중 원본 DB가 바뀌면 포인터를 공개하지 않고 중단한다.

```bash
python -m backend.app.storage.migrate --dataset trade-news --source /absolute/news-data --apply
```

같은 이관을 재실행할 수 있다. 이미 확정된 S3 내용과 다른 로컬 파일을 발견하면 덮어쓰지 않고 중단한다. 원본을 지우지 않으며 검증 영수증은 `<prefix>/_migrations/`에 남긴다. 운영 중인 namespace에 초기 이관 명령을 재사용하지 않는다.

구형 리포트는 기존 `files_json.storage=local` 메타데이터를 그대로 두어도 S3의 `reports/<기존 key>`를 SHA256으로 확인하여 읽는다. 따라서 구형 리포트 경로를 보존해 이관해야 한다. 구형 XLSX는 저장된 리포트 내용으로 처음 다운로드할 때 생성·S3 저장하고 메타데이터를 남긴다. 이후 다운로드는 저장본을 사용한다.

## AWS 권한과 운영 전환

버킷은 비공개로 유지하고 SSE-S3 또는 운영 KMS 설정을 사용한다. 역할별 최소 권한:

- API/worker: `reports/*` GetObject·PutObject, `models/*` GetObject.
- 수집 scheduler: 사용하는 `raw/*`, `ml-inputs/*` GetObject·PutObject.
- ML: 사용하는 `raw/*`, `ml-inputs/*` GetObject, `models/*` GetObject·PutObject.
- multipart를 쓰는 역할: 해당 prefix의 AbortMultipartUpload 권한. 일반 저장·조회 경로는 객체 삭제 권한을 요구하지 않는다.
- 별도 조각 정리 역할만 `raw/trade-news/database/_chunks/*` DeleteObject와 해당 prefix ListBucket을 추가한다. 일반 수집·API 역할에는 삭제 권한을 추가하지 않는다.
- 웹 서버에서 `CUSTOMS_ML_ENTRYPOINT`를 켜 직접 학습한다면 그 역할에도 `models/*` PutObject가 필요하다. 기존 모델 읽기 권한만으로는 학습 결과 저장이 되지 않는다.

IAM 외에 KMS 키 정책/SCP/버킷 정책의 제한도 확인한다. 버킷 버전 관리와 미완료 multipart 정리 lifecycle을 권장한다. `_blobs`, `_snapshots`, 현재 상태에 일괄 만료 규칙을 적용하지 않는다. 참조되지 않는 blob 정리는 보존 정책을 확정한 별도 작업이다.

1. 격리된 버킷/prefix에서 이관·복구·권한 오류를 검증한다. 테스트 prefix에는 별도 IAM 허용이 필요하다.
2. 운영 실행과 기존 동기화 타이머를 일시 정지하고 모든 사용 파일·UN 상태·구형 리포트를 이관한다. 검증 결과 및 원본을 보존한다.
3. S3 의존성과 역할 권한을 적용하고 모든 실행 주체에 같은 S3 설정을 배포한다.
4. 새 비어 있는 작업 폴더에서 UN 상태 조회, 리포트 생성·재다운로드, 월별 모델 로드, 기존 수집본을 이용한 재실행을 확인한다. 수집 API 호출 예산은 기존 규칙을 따른다.
5. **API/worker/scheduler 검증 뒤** 기존 `/opt/trade/synchronize.sh`의 `trade-sync.timer`를 해제한다. 예전 모델 `s3 sync` 다운로드와 새 모델 캐시를 함께 운영하지 않는다.

## 복구와 롤백

캐시 복원은 S3를 변경하지 않는다. 비어 있는 별도 경로에서 검사한다.

```bash
python -m backend.app.storage.migrate --dataset un-runtime --restore --destination /tmp/un-state-check
python -m backend.app.storage.migrate --dataset models --restore --destination /tmp/model-check
```

UN의 `RECOVERY_REQUIRED`는 상태·DB 대조 후 기존 `--resume-now` 절차로 해제한다. S3 장애 중 로컬 상태로 계속 수집하거나 초기화하지 않는다. lease를 수동 삭제하는 대신 실행 종료와 만료를 확인한다.

기사 DB의 미참조 조각은 아래 명령으로 별도 정리한다. 기본은 조회뿐이며, 삭제는 **수집·적재·수동 CLI 등 모든 writer 종료 확인 후** 수행한다. 현재 포인터가 참조하는 조각과 최근 조각은 보존한다. 정리 중에는 유지보수 표식을 조건부 기록해 새 실행을 차단하고 기존 writer의 확정을 무효화한다. 강제 종료로 표식이 남으면 writer가 모두 정지했는지 다시 확인한 뒤 같은 정리 명령을 재실행한다. 삭제한 조각을 참조하는 과거 포인터만 단독으로 롤백할 수 없다.

```bash
python -m backend.app.storage.crawler_gc
python -m backend.app.storage.crawler_gc --apply --writers-stopped --min-age-days 7
```

코드 롤백 때도 S3 계약을 읽을 수 있는 버전을 사용한다. `ARTIFACT_STORAGE=local`만 바꾸면 새 S3 리포트 메타데이터와 현재 수집 상태를 읽을 수 없다. 완전한 구버전 복귀는 모든 writer 정지, S3 최신 파일 export, 리포트 메타데이터 변환·해시 검증이 필요하며 자동 수행하지 않는다. 파일 snapshot만 과거로 되돌려도 MySQL 데이터는 되돌아가지 않는다.

## 검증

```bash
python -m unittest backend.tests.test_s3_storage backend.tests.test_s3_review backend.tests.test_s3_additional_review backend.tests.test_s3_reports backend.tests.test_s3_models
python -m unittest discover backend/tests
python -m pytest backend/tests/trade_news/test_pipeline.py backend/tests/trade_news/test_traffic.py backend/tests/trade_news/test_daily_scheduler.py backend/tests/test_shortnews_isolated_contract.py
```

테스트는 메모리 S3·SQLite·fake Redis를 사용한다. AWS/RDS/LLM/Chroma 연결은 필요하지 않다. 기존 PDF 테스트에는 Playwright Chromium·한글 글꼴이 필요하다. Windows에서는 `PYTHONUTF8=1`을 사용한다. POSIX 실제 SIGINT 전달 및 `kill(pid, 0)` 생존 확인 시험은 Linux에서 실행한다.

2026-11-06 PR 리뷰 재검증: 전체 unittest 1,690개 실행(실패 0, 제외 49), S3·시장 분석 집중 42개 통과, 기사 수집·검수·적재 pytest 123개 통과(제외 2). 최종 리뷰 전용 12개 및 UN·로그 관련 28개도 통과했다. 기사 저장 후 부모 checkpoint 전 중단을 캐시 유지/전체 유실 각각으로 재현해 요청 예산 유지, 본문 재요청 없음, 검수 결과 복원 및 합성 DB 적재를 확인했다. 검수 입력·버전 불일치는 `RECOVERY_REQUIRED`로 남는다.

같은 날 추가 검수에서 살아 있는 자식 작업의 파일 복원 충돌, 재이관의 검수 DB 비교 누락, DB 이관 목록 누락, 잘못된 검수 JSON의 전체 수집 중단을 재현·수정했다. 추가 회귀 7개는 캐시 전체 유실, WAL 데이터 이관, 업로드 중 원본 변경도 검증한다. 전체 검사에서 별도로 드러난 기존 ERD 이관의 시각 기반 백업 폴더 충돌도 UUID를 추가하고 같은 시각의 연속 실행 시험으로 수정했다.

추가 검수의 전체 unittest 초회 결과는 1,699개 중 실패/오류 4개, 제외 49개였다. 백업 폴더 충돌 수정 후 S3·ERD·UN 관련 112개를 집중 재검증해 실패 0개(제외 1개)를 확인했다. UN 파일 교체 오류 2개는 분리 재실행에서 재현되지 않았으며, 자식 종료 시험은 Windows 가상환경 실행기와 실제 Python의 PID가 다르므로 실제 인터프리터를 지정해 검증했다. 마지막 변경 후 전체 suite를 다시 실행한 결과는 아니다. 확장 기사 pytest는 134개 통과, 2개 제외, Linux 전용 프로세스 시험 22개가 Windows의 `pass_fds`·파이프 `select`·`killpg` 미지원으로 실패했다. 이 22개의 후속 Linux 검증은 아래와 같이 완료했다.

2026-11-06 Linux 후속 검증: Ubuntu WSL의 Python 3.11.18에서 기존 미검증 22개를 포함한 프로세스 시험 **33개 전부 통과**, S3·ERD·UN 관련 **112개 전부 통과**(제외 없음). 저장소 요구사항과 OpenMP 런타임을 준비하고, 현재 변경 파일과 SHA256이 같은 격리 소스에서 합성 HTTP·로컬 SQLite·메모리 S3로 실행했다. 초회에는 준비 지연 때문에 중단 지점에 도달하지 못한 시험 2개가 실패했다. 기사 커밋 후 타임아웃 시험의 제한을 3초에서 10초로, 벡터 단계 도달 대기를 15초에서 45초로 조정한 뒤 두 시험 및 33개 전체를 다시 통과했다. 실제 타임아웃·부모 강제 종료, 커밋 보존, 예산 유지, 본문 재요청 없음, 중복 적재 방지 검증은 유지하며 운영 제한시간은 변경하지 않았다.

```bash
# Linux에서 실행: Windows는 프로세스 그룹·파이프 감시 API를 지원하지 않음
python -m pytest backend/tests/trade_news/test_daily_timeout.py backend/tests/trade_news/test_daily_vector.py -q
```

실 AWS 통합 검증은 별도 격리 환경에서 수행해야 한다. S3 403/타임아웃·업로드 후 DB 실패·프로세스 강제 종료·캐시 전체 유실·이중 실행을 확인하고, 배포 서버의 S3 요청 비용과 대형 원본/SQLite snapshot 처리 시간을 측정한다.
