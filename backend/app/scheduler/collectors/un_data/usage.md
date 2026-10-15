# UN 데이터 사용법

전달 CSV를 처음 한 번 적재한 뒤, UN API의 발표 변경분으로 MySQL `un_data`를 갱신합니다.
SKN 공통 DB 모듈을 사용합니다. RAW·실행기록·체크포인트는 파일로만 보관하고 국가·HS 마스터는 조회만 합니다.

## 준비

저장소 루트에서 실행합니다. Python 3.10 이상(검증: 3.12).

```sh
python -m pip install -r backend/requirements.txt
python -m pip install 'filelock>=3,<4'
python -m backend.app.scheduler.jobs.un_data_load --validate-only
```

`.env.un.sample`의 필요한 설정을 기존 루트 `.env`에 병합합니다.
`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `UN_COMTRADE_API_KEY`를 설정합니다.
실제 키·비밀번호는 커밋하지 않습니다. 환경변수가 `.env`보다 우선합니다.

DB 담당자가 `country`, `hs_code`, `un_data` 스키마와 `data/un_data/required_country.csv`·`required_hs.csv`의 부모 키를 확인해야 합니다.
`db/mysql/DB_스키마_읽기전용_조회.sql`은 대상 DB를 선택한 전용 연결에서 실행합니다.
`db/mysql/init/03_un_data.sql`은 기존 스키마 및 FK 문자셋·collation과 비교한 뒤 필요한 경우에만 적용합니다.
코드는 테이블이나 부모 마스터를 자동 생성하지 않습니다.

## 1. CSV 최초 적재 및 체크포인트 연결

먼저 테스트 DB로 실행합니다. 아래 `runtime` 대신 `runtime_test` 경로를 사용하세요.
테스트 완료 후 실제 대상 DB에서는 별도 상태 경로로 동일 순서를 실행합니다.

```sh
python -m backend.app.scheduler.jobs.un_data_refresh --initialize-csv --state-dir data/un_data/runtime
```

전달 CSV **76,191행**을 검증·적재·전수 대조한 뒤 체크포인트를 생성합니다. API는 호출하지 않습니다.
기준일 **2026-10-15**는 원본 수집 성공 기록과 CSV·수집 범위 해시로 확인했습니다.
다른 CSV·범위, 기존 체크포인트, 스냅샷과 다른 DB 내용은 초기화를 차단합니다.
DB commit 후 체크포인트 저장만 실패했다면 같은 명령으로 복구할 수 있습니다.
`un_data_load`의 일반 적재 명령은 체크포인트를 만들지 않으므로 위 명령을 사용하세요.

## 2. API 갱신 확인

초기 적재와 같은 DB 및 상태 경로를 사용합니다.

```sh
python -m backend.app.scheduler.jobs.un_data_refresh --api-env .env --state-dir data/un_data/runtime
```

마지막 정상 점검일 당일~실행일 발표 범위를 M/A 각각 조회합니다.
대상 국가·2022년 이후 통계기간의 변경 데이터셋을 재수집하고 RAW → 9컬럼 `un_data`로 변환·검증하여 교체합니다.
한국산 비중은 DB 생성 컬럼이 계산합니다. 정상 완료 시에만 점검일을 전진합니다.
확인: 터미널 `UN 갱신 완료`, 실행 폴더 `manifest.json`의 `DB_VERIFIED`, `checkpoint.json`의 `last_successful_check_date`.

## 3. 매월 운영

```sh
python -m backend.app.scheduler.jobs.un_data_refresh --api-env .env --state-dir data/un_data/runtime --schedule
```

시작 시 1회 점검 후 **매월 1일 03:00 한국시간** 실행합니다. 서버에서 프로세스를 계속 실행해야 합니다.
기존 `backend.app.scheduler.main`과 별개인 UN 전용 프로세스입니다. 중복 등록하지 마세요.
체크포인트를 다른 DB에 복사하지 말고 상태 폴더를 영속 보관하세요.
API·DB 오류 시 점검일을 유지하며 요청별 이어받기는 없습니다. 기존 데이터셋 전체가 빈 응답이면 자동 삭제를 막습니다.
상태 유실·수집 범위 변경 시 임의 점검일을 넣지 말고 백업과 DB를 대조해 복구해야 합니다.

## 검증 및 범위

```sh
python -m unittest discover backend/tests -v
```

운영 DB 접속정보가 없어 실제 스키마·마스터 호환성은 미검증입니다.
로컬 MySQL 검사에는 임시 DB와 테스트 부모 키를 사용했으며 후속 API 연결 검증은 모의 응답을 포함합니다.
원본 데이터와 SKN 공통 DB 코드는 변경하지 않았습니다.
