# HS 코드 마스터 생성·적재·자동 갱신 사용 안내

관세청 **HS부호 단위별 품목명** xlsx로 HS 코드 마스터(2·4·6·10단위)를 만들어 MySQL `hs_code` 테이블에 넣습니다.
HS 판(HS2022 → HS2028 …)이 바뀌면 파일 다운로드부터 적재까지 자동으로 실행합니다.

| 단계 | 코드 | 하는 일 |
|---|---|---|
| ① 다운로드 | `scheduler/jobs/hs_code_refresh.py` | 공공데이터포털에서 최신 xlsx를 받아 `reference/hs_names_YYYYMMDD.xlsx`로 저장 |
| ② CSV 생성 | `scheduler/collectors/hscode/hs_code_build.py` | 6단위 품목명을 OpenAI로 요약해 `hs_code_mst.csv` 생성 |
| ③ 적재 | `scheduler/jobs/hs_code_load.py` | CSV를 검증한 뒤 `hs_code` 테이블에 upsert |
| 자동 실행 | `scheduler/main.py` | 2028년부터 5년마다 1월 1일 00:15(KST)에 ①→②→③ 실행 |

원본 데이터: [관세청_HS부호 단위별 품목명 (공공데이터포털)](https://www.data.go.kr/data/15130660/fileData.do)

## 준비

저장소 루트에서 패키지를 설치합니다.

```powershell
cd "C:\your_folder\SKN33-Final-3Team"
python -m pip install -r backend/requirements.txt
```

저장소 루트 `.env`에 아래 값을 넣습니다. 없으면 `.env.sample`을 복사해 만듭니다.

```
OPENAI_API_KEY=sk-...          # ② 6단위 품목명 요약
MYSQL_HOST=localhost           # ③ 적재
MYSQL_PORT=3306
MYSQL_USER=...
MYSQL_PASSWORD=...
MYSQL_DATABASE=...
```

`hs_code` 테이블은 미리 만들어 두어야 합니다. 적재 코드는 테이블을 만들지 않고, 없으면 오류 로그를 남기고 끝납니다.

## 만들어지는 데이터

`hs_code_mst.csv` 컬럼: `hs_code, depth, name_ko, source_edition, hs_ver`

| 단위 | 행 수(20260101판) | name_ko |
|---|---|---|
| 2단위 | 97 | 관세청 공식 품목명 |
| 4단위 | 1,229 | 관세청 공식 품목명 (`2424` 이사화물 포함) |
| 6단위 | 5,613 | `요약\|6단위 원문` |
| 10단위 | 11,327 | 관세청 공식 품목명 |

- 6단위 목록은 xlsx 6단위 시트가 아니라 **10단위 코드 앞 6자리**로 만듭니다. 6단위 시트에는 6단위가 일부만 있기 때문입니다.
- 6단위 원문은 6단위 시트 → 10단위 `XXXXXX0000` 행 → (`XXXX00`이면) 4단위 이름 순으로 찾습니다.
- 요약은 4·5·6단위 이름을 함께 LLM에 넣어 3~5단어로 만듭니다. 5단어를 넘으면 한 번 더 요청하고, 그래도 넘으면 그대로 둡니다.
- `reference/hs6_summary_overrides_<hs_ver>.csv`의 수동 요약이 LLM 요약보다 우선합니다. 요약이 겹치거나 틀린 코드는 이 파일에 `hs_code,summary`로 추가합니다.
- `hs_ver`는 xlsx 파일명의 판 날짜로 정합니다: 2017~2021 → HS2017, 2022~2027 → HS2022, 2028년부터 5년 단위(HS2028, HS2033 …).
- 적재할 때 6단위 `name_ko`는 `|`로 나눠 `name_ko`(요약)와 `original_name_ko`(원문)에 들어갑니다. 다른 단위의 `original_name_ko`는 NULL입니다.

## 수동 실행

모두 저장소 루트에서 실행합니다.

```powershell
# ② CSV 생성 (기본: reference/의 최신 hs_names_YYYYMMDD.xlsx, 판은 파일 날짜로 자동 결정)
python backend/app/scheduler/collectors/hscode/hs_code_build.py

# ③ 적재 (기본: hscode_outputs/hs_code_mst.csv)
python -m backend.app.scheduler.jobs.hs_code_load

# ①→②→③ 한 번에 (기준일을 바꿔 시험할 때 --date 20280101)
python -m backend.app.scheduler.jobs.hs_code_refresh
```

- 요약은 `hscode_outputs/hs6_summary_cache_<hs_ver>_<모델>.json`에 저장됩니다. 다시 실행하면 캐시에 없는 코드만 API로 요청합니다.
  처음부터 다시 요약하려면 이 캐시 파일을 지웁니다.
- 6단위 5,600여 개를 처음 요약할 때는 OpenAI API를 140번 정도 호출합니다(약 20~30분).

## 자동 갱신 (스케줄러)

```powershell
python -m backend.app.scheduler.main
```

계속 켜 두는 프로세스입니다. 켜면 등록된 잡과 다음 실행 시각이 출력됩니다.

```
등록: hs_code_refresh | 다음 실행 2028-01-01 00:15:00+09:00
```

실행 결과는 상태로 남습니다.

| 상태 | 뜻 | 다음 동작 |
|---|---|---|
| `DONE` | 새 판 다운로드·CSV 생성·적재 완료 | 다음 주기(5년 뒤)까지 대기 |
| `NOT_READY` | 포털 최신 파일이 아직 이전 판 (예: 2028-01-01에 20260101판만 있음) | 다음 날 00:15에 재시도 |
| `ALREADY_LOADED` | DB에 그 판이 이미 있음 (LLM 비용이 드는 재생성 방지) | 대기 |
| `FAILED` | 다운로드·요약·적재 중 오류 | 다음 날 00:15에 재시도 |

- 재시도는 최대 90일입니다. 새 판 파일은 1월 1일 당일보다 늦게 올라오는 경우가 많아서 매일 다시 확인합니다.
- 1월 1일 00:15에 스케줄러가 꺼져 있었어도 6시간 안에 켜지면 실행합니다. 그보다 늦게 켜도, 2028년 이후이고 이번 판이 DB에 없으면 켜자마자 한 번 실행합니다.
- 재시도 예약은 메모리에만 있어서, 재시도 중에 스케줄러를 껐다 켜면 위의 "켜자마자 실행"으로 이어집니다.

## 실행 기록 (batch_run)

실행할 때마다 `batch_run`에 한 건이 남습니다. 공통 코드는 `backend/app/db/batch_log.py`의 `track_batch()`입니다.

| 컬럼 | `HS_CODE_REFRESH` (자동 갱신) | `HS_CODE_LOAD` (적재 단독 실행) |
|---|---|---|
| `source_code` | `KCS_HS_NAMES` | `KCS_HS_NAMES` |
| `target_table` | `hs_code` | `hs_code` |
| 기간 | 기대 HS 판의 적용 기간 (예: HS2028 → 2028-01-01~2032-12-31) | CSV `hs_ver`의 적용 기간 (HS2022 → 2022-01-01~2027-12-31) |
| `status` | `DONE`·`NOT_READY`·`ALREADY_LOADED` → SUCCESS, `FAILED` → FAILED | 성공 SUCCESS, 실패 FAILED |
| `request_count` | 포털 요청 수 + OpenAI 호출 수 (재시도 포함) | 0 |
| `received_count` | xlsx에서 읽은 코드 수 (확인만 하고 끝난 실행은 0) | CSV 행 수 |

- 시작할 때 `RUNNING`으로 넣고 바로 커밋한 뒤, 끝나면 상태·`finished_at`·건수를 갱신합니다. 끝나지 않고 `RUNNING`으로 남은 행은 실행 중 프로세스가 죽은 것입니다.
- `source_code`는 `service_option`을 참조합니다. `KCS_HS_NAMES`가 없으면 첫 실행 때 `SOURCE` 행으로 추가하고, 이미 있으면 건드리지 않습니다.
- 자동 갱신 안에서 부르는 적재는 그 실행 한 건에 포함되어 `HS_CODE_LOAD`를 따로 남기지 않습니다.
- 기록용 DB에 연결하지 못해도 작업은 계속합니다. 이때는 작업 로그에 `batch_run 기록 시작 실패` 경고만 남습니다.

다른 수집기에도 같은 방식으로 붙입니다.

```python
from backend.app.db.batch_log import Source, track_batch

SOURCE = Source("KCS_TRADE", "관세청 품목별 국가별 수출입실적", {"url": "https://www.data.go.kr/data/15100475/openapi.do"})
with track_batch(SOURCE, "EXPORT_COLLECTION", "export_monthly", start, end, logger=log) as run:
    run.request_count += 1      # API를 부를 때마다
    run.received_count += n     # 받은 원본 행 수
    if 실패:
        run.fail()              # 예외 없이 실패로 끝낼 때 (예외가 나면 자동으로 FAILED)
```

## 로그

모두 `backend/app/scheduler/collectors/hscode/hscode_outputs/logs/`에 남습니다(이 폴더는 git에 올라가지 않음).

| 파일 | 내용 |
|---|---|
| `hs_code_refresh_<시각>.log` | 자동 갱신 단계별 진행과 최종 상태 |
| `hs_code_build_<시각>.log` | 요약 진행률, 요약 누락·중복, 500자 초과 검사 |
| `hs_code_load_<시각>.log` | 검증 오류, 적재 결과, DB 오류 traceback |

자동 갱신으로 만든 CSV와 로그는 판별 폴더(`hscode_outputs/HS2028/` 등)에 저장되어 기존 판 결과를 덮어쓰지 않습니다.
요약 캐시는 결과 폴더와 상관없이 항상 `hscode_outputs/`에 판별 파일명으로 두므로, 이미 요약한 코드는 다시 요청하지 않습니다.

## 주의

- **HS2033 이후 주기는 가정값입니다.** HS 판은 HS2017 → HS2022(5년) → HS2028(6년)처럼 간격이 일정하지 않았습니다.
  세계관세기구(WCO)가 다른 연도로 발표하면 `hs_code_build.py`의 `hs_ver_for()`와 `scheduler/main.py`의 `HS_REFRESH_TRIGGER`를 고칩니다.
- 다운로드는 포털 화면의 다운로드 버튼과 같은 요청을 보냅니다. 포털 화면 구조가 바뀌면 `FAILED`와 함께 원인이 로그에 남습니다.
  포털이 자동입력방지(캡차)를 요구하면 우회하지 않고 멈춥니다. 이때는 포털에서 직접 받아 `reference/hs_names_YYYYMMDD.xlsx`로 넣고 `hs_code_refresh`를 수동 실행합니다.
- 관세청 원본에도 오류가 있습니다. 예: 4단위 `7804`의 품목명이 6단위 `780411`과 같게 들어가 있습니다. 공식 원문을 그대로 쓰므로 보정하지 않았습니다.
- 새 판의 요약은 자동으로 들어갑니다. 적재 후 요약 중복·누락 로그를 확인하고, 필요하면 `hs6_summary_overrides_<새 판>.csv`를 만들어 수동 실행으로 다시 적재합니다.
  (DB에 그 판이 이미 있으면 자동 갱신은 `ALREADY_LOADED`로 건너뛰지만, `hs_code_build` → `hs_code_load` 수동 실행은 같은 키를 갱신합니다.)
