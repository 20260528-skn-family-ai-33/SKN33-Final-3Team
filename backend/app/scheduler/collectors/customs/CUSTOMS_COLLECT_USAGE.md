# 관세청 수출입실적 API 수집 코드 사용 안내

`customs_collect.py`는 관세청 품목별 국가별 수출입실적 API로 **지정한 기간(1년·한 달·12개월 이내 구간, 전체 국가)**을 수집하고
원본 CSV와 수집 통계를 저장하는 독립 실행 코드입니다.

1. `.env`의 MySQL 접속 정보로 DB 국가 사전에서 수집 대상을 읽고, `customs` 인증키를 읽습니다.
2. 국가마다 API를 호출해 지정한 기간의 HS 10단위 품목별 월별 실적을 받습니다.
3. 국가별 원본을 저장한 뒤 하나의 원본 CSV로 합치고, 수집 통계와 실행 로그를 저장합니다.

응답 필드명과 값은 바꾸지 않으며, API가 함께 보내는 `총계` 행도 그대로 보존합니다.
전처리(총계 제외, 열 이름 변경 등)와 MySQL 적재는 포함하지 않습니다.

API 안내: https://www.data.go.kr/data/15100475/openapi.do

## 준비와 인증키 설정

저장소 루트에서 필요한 패키지를 설치합니다.

```powershell
cd "C:\your_folder\SKN33-Final-3Team"
python -m pip install -r backend/requirements.txt
```

인증키는 공공데이터포털에서 받습니다.

1. [공공데이터포털](https://www.data.go.kr) 로그인 → `관세청_품목별 국가별 수출입실적(GW)` 검색 → **활용신청**
2. 마이페이지 > 데이터활용 > Open API > 활용신청 현황에서 **일반 인증키**를 복사합니다.
   Encoding·Decoding 키 어느 것이든 사용할 수 있습니다.

`.env`가 없다면 저장소 루트의 예시 파일을 복사합니다.

```powershell
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.sample" -Destination ".env"
}
```

`.env`의 예시 값을 복사한 인증키로 바꾸고 저장합니다.
기존 `.env`가 있으면 덮어쓰지 말고 다음 항목만 추가합니다. 키 이름은 대소문자를 구분하지 않습니다.

```dotenv
customs=YOUR_CUSTOMS_API_KEY
MYSQL_HOST=localhost
MYSQL_PORT=3307
MYSQL_USER=YOUR_MYSQL_USER
MYSQL_PASSWORD="YOUR_MYSQL_PASSWORD"
MYSQL_DATABASE=trade_agent
```

MySQL 값은 실제 접속할 로컬 DB에 맞춥니다. 위 포트 `3307`은 이번 테스트 환경의 예시이며, 다른 환경은 해당 서버의 포트를 사용합니다.
DB 접속에는 공통 모듈 `backend/app/db/session.py`를 사용하며, 같은 이름의 환경변수가 있으면 `.env`보다 우선합니다.

실제 인증키가 들어 있는 `.env`는 공유하지 않습니다. 팀원에게는 `.env.sample`을 전달합니다.

## 국가·지역 사전 준비

수집 전에 대상 DB와 테이블이 준비되어 있어야 합니다. 새 DB라면 `db/mysql/init/01_schema.sql`로 테이블을 생성한 뒤,
같은 DB에서 다음 파일을 순서대로 실행합니다. 기존 테이블이 있는 DB에 스키마 생성 SQL을 다시 실행할 필요는 없습니다.

1. `db/mysql/init/03_market_region_seed.sql`: 대륙·하위 지역 사전
2. `db/mysql/init/04_country_seed.sql`: 국가 코드·국가명·역할·지역 연결

PyCharm의 대상 DB 쿼리 콘솔에서 위 SQL 파일 내용을 순서대로 실행해도 됩니다.
이 두 파일은 전달받은 SQL의 데이터만 반영하며 DB 전환이나 테이블 삭제를 하지 않습니다.
재실행할 수 있고, 입력값이 NULL인 지역·통화 연결은 기존 값을 유지합니다.

수집 대상은 DB `country`의 `country_role`이 `COUNTRY` 또는 `SPECIAL`인 코드입니다. `TOTAL`은 제외합니다.
지역·통화 정보도 `market_region`·`currency`에서 읽으며, 원본 SQL에 없는 통화 연결은 추정해서 채우지 않습니다.
`--countries`로 지정한 코드도 DB 사전에 있어야 합니다. 사전에 없는 코드는 자동 추가하지 않고 수집 전에 오류로 알립니다.

## 실행 방법

일부 국가로 먼저 확인합니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --year 2025 --countries US,VN
```

2025년 DB 국가 사전의 전체 수집 대상을 수집합니다. 대상 수는 사전 내용에 따라 달라집니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --year 2025
```

다른 연도는 `--year` 값만 바꿉니다. 여러 연도는 연도별로 차례대로 실행합니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --year 2024
foreach ($y in 2022..2025) { python backend/app/scheduler/collectors/customs/customs_collect.py --year $y }
```

한 달이나 원하는 구간만 받을 수 있습니다. 기간은 `YYYYMM` 형식이며 API 제한에 따라 12개월 이내입니다.
새 달 통계가 공개되면 그 달만 받는 데 사용합니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --month 202503
python backend/app/scheduler/collectors/customs/customs_collect.py --start 202501 --end 202506
python backend/app/scheduler/collectors/customs/customs_collect.py --start 202507 --end 202606
```

| 옵션 | 조회 기간 | 결과 폴더 |
|---|---|---|
| `--year 2025` (기본) | 202501 ~ 202512 | `2025` |
| `--month 202503` | 202503 한 달 | `202503` |
| `--start 202501 --end 202506` | 202501 ~ 202506 | `202501_202506` |

`--month`나 `--start`·`--end`를 주면 `--year`보다 우선합니다.
형식이 틀리거나, 시작이 끝보다 늦거나, 12개월을 넘으면 수집 전에 오류로 멈춥니다.

인증키 파일 위치나 결과 폴더를 지정할 수 있습니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --env "C:\your_folder\.env" --countries US
python backend/app/scheduler/collectors/customs/customs_collect.py --output-dir "customs_outputs\sample_run" --countries US
```

## 저장 결과

기본 저장 위치는 코드가 있는 폴더의 `customs_outputs\기간\`입니다. 기간 이름은 위 표의 결과 폴더와 같습니다.

| 파일 | 내용 |
|---|---|
| `customs_raw_기간.csv` | 원본 필드명의 실적 표 (국가별로 받은 순서, 총계 행 포함). 한글 UTF-8 BOM |
| `customs_collection_기간.json` | 조회 기간, 요청 국가 수, 실패 국가, 전체·데이터·총계 행 수, 국가·품목 수, 월 목록, 데이터가 없는 달, 키 중복, 기간 밖 행, 수출입 합계, 수집 완료 여부 |
| `_cache\국가코드.csv` | 국가별 원본. 중단 후 다시 실행하면 받은 국가는 건너뛰고 이어받습니다 |
| `logs\customs_기간_실행시각.log` | 화면 출력과 같은 실행 로그 |

컬럼은 `year`, `statCd`, `statCdCntnKor1`, `hsCd`, `statKor`, `expDlr`, `expWgt`, `impDlr`, `impWgt`,
`balPayments`이며 추가 응답 필드가 있으면 뒤에 저장합니다.

| 필드 | 뜻 | 예시 |
|---|---|---|
| `year` | 연월 (`총계` 행은 `총계`) | 2025.01 |
| `statCd` / `statCdCntnKor1` | 국가 코드 / 국가명 | US / 미국 |
| `hsCd` / `statKor` | HS 10단위 코드 / 품목명 | 3304101000 / 립스틱 |
| `expDlr` / `expWgt` | 수출 금액(USD) / 수출 중량(kg) | 807248 / 13596 |
| `impDlr` / `impWgt` | 수입 금액(USD) / 수입 중량(kg) | 305004 / 3139 |
| `balPayments` | 무역수지(USD) | 502244 |

한 행은 `연월 × 국가 × HS10`의 한 달 합계입니다.

## 읽을 때 주의

- 1년 치 전체는 160만 행이 넘어 엑셀 최대 행 수를 넘습니다. 파이썬으로 읽습니다.
- `hsCd`는 앞자리 0을, `statCd`는 나미비아 `NA`를 보존하도록 문자열로 읽습니다.
- 일부 `statKor` 값 끝에 원본 줄바꿈이 있어 CSV 셀 안에 줄바꿈으로 저장됩니다(따옴표로 감싸져 있음).
- 분석할 때는 `year == "총계"` 행을 제외합니다.

```python
import pandas as pd
df = pd.read_csv("backend/app/scheduler/collectors/customs/customs_outputs/2025/customs_raw_2025.csv",
                 dtype=str, keep_default_na=False)
data = df[df["year"] != "총계"]
```

## 실행 참고

- 호출 실패나 오류 응답은 3번까지 다시 시도하고, 계속 실패한 국가는 통계의 `failed_countries`에 기록합니다.
- `collection_complete`는 실패 국가가 없으면 true입니다.
- 0행인 국가(거래 없음, 아직 공개되지 않은 달)는 저장하지 않아 다음 실행 때 다시 조회합니다.
- 공개 전인 달은 통계의 `missing_months`와 화면의 "데이터가 없는 달"로 표시됩니다. 공개 후 다시 실행하면 받아집니다.
- 결과 폴더 `customs_outputs/`는 Git 커밋에서 제외됩니다.

## 수집한 실적의 DB 적재와 미등록 HS10 보완

수집기 자체는 CSV를 저장합니다. 다음 적재 작업은 CSV 전체를 검증한 후 월별로 MySQL에 반영합니다.
기본 입력 파일은 `data/raw/customs/customs_raw_<연도>.csv`이며 `--csv`로 한 달 파일도 지정할 수 있습니다.

```powershell
# DB에 쓰지 않고 사전 연결과 신규 HS10 보완 계획까지 확인
python -m backend.app.scheduler.jobs.customs_load --year 2025 --mode check --hs-ver auto
# 검증 결과를 확인한 뒤 실제 적재
python -m backend.app.scheduler.jobs.customs_load --year 2025 --mode load --hs-ver auto
```

- 기존 `(HS10, 판본)`은 이름·출처를 수정하지 않고 사용합니다.
- 미등록 HS10은 같은 판의 상위 HS2·HS4·HS6가 모두 있는 경우에만 추가합니다.
  `auto`에서 후보 판이 여러 개이면 임의로 고르지 않습니다. 원천 판이 확인된 경우에만 `--hs-ver HS2022` 등으로 명시합니다.
- 신규 `name_ko`는 실적 CSV의 유효한 `statKor`를 사용합니다. 빈 값·`-` 등 무효 이름이나 서로 충돌하는 이름은 보류/오류로 분리합니다.
- 신규 `original_name_ko`는 NULL, `depth`는 DB가 10으로 자동 생성하며,
  `source_edition`은 `관세청 수출입실적 2023년 2월`처럼 원천 월을 기록합니다.
- 신규 HS10 추가와 그 월의 수출·수입 원본 및 HS6 집계는 같은 트랜잭션입니다. 월별 적재 실패 시 사전 추가도 롤백됩니다.
  중복 키가 발생해도 기존 사전의 품목명·출처를 덮어쓰지 않습니다.
- `check`는 쓰지 않고 보완 계획을 보고하며, `load`에서만 사전을 추가합니다.
- 판본 불명확·상위 코드 누락·품목명 무효는 기본 중단입니다. `--unmapped-policy hold`를 주면 원문과 사유를
  `data/customs_load_outputs/<실행시각>/held_rows.csv`에 보존하고 나머지를 처리합니다.
  9자리 등 형식 오류는 `--invalid-policy hold`로 별도 보존할 수 있습니다. 중복 키·깨진 CSV 구조는 항상 중단합니다.
- 보류 사유를 해결한 뒤 보류 파일 자체를 `--csv`로 지정해 재실행할 수 있습니다.
  자동으로 연도를 판본으로 간주하거나 코드 자릿수를 보정하지 않습니다.

새 DB는 `01_schema.sql`에 실행 기록용 `hs_code_correlation` ENUM이 포함됩니다.
기존 DB는 수출입 작업 전용 `05_customs_batch_target.sql`을 실행하면 기존 ENUM 값을 보존하며 누락된 값만 추가합니다.
이미 대응표 테이블이 있으면 다른 담당자의 `02_hs_code_correlation.sql`을 다시 실행할 필요는 없습니다.

## 월별 자동 파이프라인

```powershell
python -m backend.app.scheduler.jobs.customs_monthly --schedule
# 특정 월을 즉시 실행하거나 실패 단계에서 재시작
python -m backend.app.scheduler.jobs.customs_monthly --month 202509
# 해당 월을 새 API 캐시에서 다시 수집하고 이후 단계도 다시 실행
python -m backend.app.scheduler.jobs.customs_monthly --month 202509 --refresh
```

기본 실행은 **매월 10일 03:00 Asia/Seoul**, 대상은 전월입니다. 스케줄러 프로세스를 계속 실행해야 합니다.
이 명령은 수출입 작업 전용 스케줄러입니다. 공용 `scheduler/main.py`와 기존 HS 갱신·임베딩 스케줄은 수정하지 않습니다.
공용 스케줄러와 통합해야 하면 담당자가 `customs_monthly.register_jobs(scheduler)`를 호출할 수 있습니다.
시작 시에도 직전 월을 점검하며, 실패한 작업은 같은 대상 월로 다음 날 재시도합니다(최대 30회).
한 달의 동시 실행은 OS 파일 잠금으로 막고, 체크포인트는 `data/customs_load_outputs/monthly/<YYYYMM>/state.json`에 저장합니다.
성공한 수집·적재·계산 단계를 재사용하고 실패 단계부터 재실행합니다. CSV가 변경됐으면 자동 재사용하지 않고 `--refresh`를 요구합니다.

`.env`에서 운영 설정을 바꿀 수 있습니다.

```dotenv
CUSTOMS_MONTHLY_DAY=10
CUSTOMS_MONTHLY_HOUR=3
CUSTOMS_MONTHLY_MINUTE=0
CUSTOMS_ML_ENTRYPOINT=backend.app.ml.<팀_ML_모듈>:<실행함수>
```

월별 순서는 수집 → 검증·사전 보완·적재·집계 → 이력·이상 판정 → ML 연결입니다.
ML 함수는 `run(trade_month="2025-09-01", engine=engine)` 형태의 인자를 받고 `{"status": "SUCCESS", ...}`를 반환해야 합니다.
실패 재실행에 대비해 대상 월을 기준으로 중복 처리를 방지하는 것은 해당 ML 함수에서 처리합니다.
현재 저장소의 ML 구현은 비어 있으므로 실제 함수가 준비되지 않았으면 설정을 비워 둡니다.
이 경우 수출입 적재·계산 후 **WAITING_FOR_ML**로 남으며 ML까지 완료했다고 표시하지 않습니다.
