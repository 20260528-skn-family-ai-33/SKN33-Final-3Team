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
API_ML_MODEL_PATHS=
API_ML_GROWTH_MODEL_PATH=
```

- 기본값은 `local`: 이관 전 실행 동작을 보존한다. 잘못된 모드·누락된 버킷 설정은 실패한다.
- `ARTIFACT_S3_PREFIX`는 격리 검증용 전체 키 앞부분이다. 운영은 기존 `raw/`, `ml-inputs/`, `reports/`, `models/` 권한과 맞추어 비운다.
- `API_REPORT_DIRECTORY`는 local 모드 및 구형 리포트 이관 원본 위치다. S3 리포트 생성에는 쓰지 않는다.
- `API_ML_MODEL_DIRECTORY` 등 기존 경로는 작업 폴더다. S3 모드의 API는 S3 월별 레지스트리를 선택하며 로컬 모델 파일 직접 고정 설정은 거부한다.
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
- 모델은 파일·READY 레지스트리를 S3에 확정한 뒤 DB에 등록한다. DB만 실패하면 같은 입력의 기존 버전을 읽어 재등록한다. API의 모델 캐시는 파일 목록의 해시별 디렉터리여서 갱신 도중 버전을 섞지 않는다.
- UN은 매 원자적 상태 저장 시 원본·pending·continuation·checkpoint를 함께 확정한다. 작업 전 fence가 S3에 있어야 진행하고, 마지막 release까지 확정돼야 복구 차단이 풀린다. 강제 종료의 `RECOVERY_REQUIRED`를 시간이 지났다는 이유로 해제하지 않는다. 기존 DB UUID·범위 해시 검사를 유지한다.
- 기사 수집의 SQLite는 S3 `database/_current.json`이 원본이다. 커밋마다 256KiB 단위로 바뀐 부분만 업로드한 뒤 전체 스냅샷 목록을 조건부 갱신한다. 예산 카운터 변경마다 DB 전체를 다시 업로드하지 않는다. 예산 예약 저장이 실패하면 HTTP 요청을 허용하지 않으며 해당 연결의 후속 동작도 차단한다. 일일 파일 manifest는 이 DB와 journal을 덮어쓰지 않는다.

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

기사 일일 작업은 기존 `NEWS_CRAWL_DATA_DIR`를 지정한다. 예산 DB도 별도로 검증·확정된다.

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
- multipart를 쓰는 역할: 해당 prefix의 AbortMultipartUpload 권한. 이 구현은 객체 삭제 권한을 요구하지 않는다.
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

코드 롤백 때도 S3 계약을 읽을 수 있는 버전을 사용한다. `ARTIFACT_STORAGE=local`만 바꾸면 새 S3 리포트 메타데이터와 현재 수집 상태를 읽을 수 없다. 완전한 구버전 복귀는 모든 writer 정지, S3 최신 파일 export, 리포트 메타데이터 변환·해시 검증이 필요하며 자동 수행하지 않는다. 파일 snapshot만 과거로 되돌려도 MySQL 데이터는 되돌아가지 않는다.

## 검증

```bash
python -m unittest backend.tests.test_s3_storage backend.tests.test_s3_reports backend.tests.test_s3_models
python -m unittest discover backend/tests
python -m pytest backend/tests/trade_news/test_pipeline.py backend/tests/trade_news/test_traffic.py backend/tests/trade_news/test_daily_scheduler.py backend/tests/test_shortnews_isolated_contract.py
```

테스트는 메모리 S3·SQLite·fake Redis를 사용한다. AWS/RDS/LLM/Chroma 연결은 필요하지 않다. 기존 PDF 테스트에는 Playwright Chromium·한글 글꼴이 필요하다. Windows에서는 `PYTHONUTF8=1`을 사용한다. POSIX 실제 SIGINT 전달 및 `kill(pid, 0)` 생존 확인 시험은 Linux에서 실행한다.

실 AWS 통합 검증은 별도 격리 환경에서 수행해야 한다. S3 403/타임아웃·업로드 후 DB 실패·프로세스 강제 종료·캐시 전체 유실·이중 실행을 확인하고, 배포 서버의 S3 요청 비용과 대형 원본/SQLite snapshot 처리 시간을 측정한다.
