# HS 코드 마스터 생성·적재·자동 갱신 사용 안내

관세청 **HS부호 단위별 품목명** xlsx로 HS 코드 마스터(2·4·6·10단위)를 만들어 MySQL `hs_code` 테이블에 넣습니다.
HS 판(HS2022 → HS2028 …)이 바뀌면 파일 다운로드부터 적재까지 자동으로 실행합니다.

| 단계 | 코드 | 하는 일 |
|---|---|---|
| ① 다운로드 | `scheduler/jobs/hs_code_refresh.py` | 공공데이터포털에서 최신 xlsx를 받아 `reference/hs_names_YYYYMMDD.xlsx`로 저장 |
| ② CSV 생성 | `scheduler/collectors/hscode/hs_code_build.py` | 6단위 품목명을 OpenAI로 요약해 `hs_code_mst.csv` 생성 |
| ③ 적재 | `scheduler/jobs/hs_code_load.py` | CSV를 검증한 뒤 `hs_code` 테이블에 upsert |
| UN 대조 | `scheduler/collectors/hscode/hs_code_un_check.py` | UN Comtrade HS 목록을 받아 CSV 2·4·6단위 코드와 대조 |
| 판 간 대응표 | `scheduler/jobs/hs_correlation_load.py` | UNSD HS 판 간 대응표를 받아 `hs_code_correlation`에 적재 (자동 갱신이 없을 때 실행, 수동 실행도 가능) |
| 자동 실행 | `scheduler/main.py` | 매년 1월 1일 00:15(KST)에 ①→②→③ 실행 (10단위 연간 개정 + HS 판 개정), 이어서 대응표가 없으면 적재 |

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
| 2단위 | 98 | 관세청 공식 품목명 (UN 통계용 `99` 포함) |
| 4단위 | 1,230 | 관세청 공식 품목명 (`2424` 이사화물, UN 통계용 `9999` 포함) |
| 6단위 | 5,614 | `요약\|6단위 원문` (`242400`, UN 통계용 `999999` 포함) |
| 10단위 | 11,327 | 관세청 공식 품목명 |

- 6단위 목록은 xlsx 6단위 시트가 아니라 **10단위 코드 앞 6자리**로 만듭니다. 6단위 시트에는 6단위가 일부만 있기 때문입니다.
- 6단위 원문은 6단위 시트 → 10단위 `XXXXXX0000` 행 → (`XXXX00`이면) 4단위 이름 순으로 찾습니다.
- 요약은 4·5·6단위 이름을 함께 LLM에 넣어 3~5단어로 만듭니다. 5단어를 넘으면 한 번 더 요청하고, 그래도 넘으면 그대로 둡니다.
- UN 통계용 코드 `99`·`9999`·`999999`(품목 미지정)는 관세율표에 없어 고정값으로 넣습니다. 이름은 "품목 미지정(UN 통계용)"(팀 번역),
  6단위 원문은 UN 영문 원문, `source_edition`은 "UN Comtrade 통계용 코드 (한국어 명칭은 팀 번역)"입니다.
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

- `hs_code_refresh`는 DB에 올해 판 코드가 모두 있으면 `ALREADY_LOADED`로 끝나 CSV를 만들지 않습니다. CSV만 다시 만들 때는 ②를 실행합니다.

- 요약은 `hscode_outputs/hs6_summary_cache_<hs_ver>_<모델>.json`에 저장됩니다. 다시 실행하면 캐시에 없는 코드만 API로 요청합니다.
  처음부터 다시 요약하려면 이 캐시 파일을 지웁니다.
- LLM 응답은 비어 있지 않은 문자열이고 `|`가 없으며 500자 이하일 때만 캐시에 저장합니다. 캐시를 읽을 때도 같은 검사를 해서,
  유효하지 않은 값(이전 버전이 남긴 `None` 등)은 버리고 다시 요청합니다. 캐시 폴더는 API 호출 전에 만듭니다(`--cache-dir`).
- 6단위 5,600여 개를 처음 요약할 때는 OpenAI API를 140번 정도 호출합니다(약 20~30분).

## UN 코드 대조

6단위까지는 국제 공통이므로, 새 판 CSV를 만들면 UN Comtrade 목록과 코드를 대조합니다.

```powershell
# 수동으로 만든 CSV (hscode_outputs/hs_code_mst.csv)
python backend/app/scheduler/collectors/hscode/hs_code_un_check.py

# 자동 갱신으로 만든 CSV는 판별 폴더에 있으므로 --csv로 지정 (예: HS2028)
python backend/app/scheduler/collectors/hscode/hs_code_un_check.py --csv backend/app/scheduler/collectors/hscode/hscode_outputs/HS2028/hs_code_mst.csv
```

- 기본은 `hscode_outputs/hs_code_mst.csv`입니다. 자동 갱신 결과(`hscode_outputs/<HS판>/hs_code_mst.csv`)를 검사할 때는 꼭 `--csv`로 지정하세요.
- CSV의 `hs_ver`로 UN 분류를 정합니다 (HS2017 → `H5`, HS2022 → `H6`, HS2028 → `H7` 예상). `--un-class`로 직접 지정할 수 있습니다.
- UN 목록은 `hscode_outputs/un/<분류>.json`에 저장하고, 있으면 다시 받지 않습니다 (`--refresh`로 다시 받기).
- 차이는 이유가 확인된 **알려진 예외**와 **확인 필요**로 나눠 로그와 `hscode_outputs/un/un_check_<판>_<분류>.csv`에 남깁니다.
- 종료 코드: 0 확인 필요 없음, 1 확인 필요 있음, 2 대조 불가 — UN에 그 판 목록이 아직 없음(2026-10 기준 `H7` 미게시),
  다운로드 실패, 받은 UN 목록 형식 오류(HTML 오류 화면 등, 이때 캐시는 교체하지 않음), CSV 파일 없음·판 혼재

HS2022(20260101판) 대조 결과: UN 2단위 97, 4단위 1,229, 6단위 5,613개가 모두 CSV에 있고, CSV에만 있는 3개는 알려진 예외

| 단위 | CSV에만 | UN에만 |
|---|---|---|
| 2단위 | `77` 관세청 표의 유보 류 | 없음 |
| 4단위 | `2424` 관세청 특수코드 이사화물 | 없음 |
| 6단위 | `242400` 관세청 특수코드 이사화물 | 없음 |

UN 통계용 `99`·`9999`·`999999`는 고정값으로 CSV에 넣으므로 UN에만 있는 코드가 없습니다.

## 과거 HS10 보완 (일회성 CSV)

HS10은 매년 추가·삭제되고, DB는 최신 사전(20260101판)으로 처음 적재되므로 그 전에 삭제된 코드는 사전에 없습니다.
2022~2026(9월) 관세청 수출입실적에 나오는 미등록 HS10을 일회성으로 CSV로 만들어 두었습니다 (`hscode_outputs/`, git 제외).
앞으로 새로 생기는 HS10은 연간 자동 갱신으로 들어오므로 이 보완은 과거분 한 번만 필요합니다.

| 파일 | 내용 | 행 |
|---|---|---|
| `hscode_outputs/HS2022/hs_code_hs2022_supplement.csv` | HS4·HS6는 현재 판에 있고 HS10만 없는 코드 (HSK 연간 개정으로 삭제) | 10단위 395 |
| `hscode_outputs/HS2017/hs_code_hs2017.csv` | HS6가 UN HS2017(H5)에만 있는 코드 (2022년 초 실적에 남은 HS2017 코드)와 FK용 상위 행 | 10단위 145, 6단위 81, 4단위 43, 2단위 25 |

```powershell
python -m backend.app.scheduler.jobs.hs_code_load --csv backend/app/scheduler/collectors/hscode/hscode_outputs/HS2017/hs_code_hs2017.csv
python -m backend.app.scheduler.jobs.hs_code_load --csv backend/app/scheduler/collectors/hscode/hscode_outputs/HS2022/hs_code_hs2022_supplement.csv
```

- 10단위 이름은 실적의 품목명, `source_edition`은 `관세청 수출입실적 YYYY년 M월`(처음 나온 달)입니다.
- 관세청 HS2017 한글 사전은 포털에 없어(과거 판은 `관세청_HS부호_20250101`뿐) HS2017 상위 행은 이렇게 채웠습니다.
  2·4단위는 현재 판 한글명 재사용(없으면 UN 영문명), 6단위는 `LLM 한글 번역|UN 영문 원문`입니다.
- 보류(적재 안 함): `9999999290`(품목명 `-`, 상위 `9999`·`999999`는 UN 통계용 코드), `621142900`(9자리).
- HS2022 보완분은 실적 적재(`customs_load`)의 미등록 코드 처리(`fail`·`hold`)를 시험한 뒤 적재합니다.

## HS 판 간 대응표 (hs_code_correlation)

HS 판이 바뀌면(HS2017→HS2022→HS2028 …) 같은 품목의 6단위 코드가 나뉘거나 합쳐집니다. 판을 넘어 실적 추이를 이어 보려면 대응표가 필요합니다.
원천은 [UN 통계국 HS 대응표](https://unstats.un.org/unsd/classifications/Econ)(xlsx)입니다. WCO 대응표(Table I·II)를 바탕으로 만든 표이며,
[WCO 원본](https://www.wcoomd.org/en/topics/nomenclature/instrument-and-tools/hs-nomenclature-2022-edition/correlation-tables-hs-2017-2022.aspx)은 PDF로만 제공됩니다. 팀 요구사항의 'WCO 6단위 매핑표'는 이 UNSD 표로 충족하기로 정했습니다(WCO PDF 직접 추출은 범위 밖).

```powershell
# 테이블 생성 (한 번만, batch_run.target_table에 hs_code_correlation 추가 포함)
mysql -u <user> -p --default-character-set=utf8mb4 <database> -e "source db/mysql/init/02_hs_code_correlation.sql"

# 다운로드 → 적재 (새 판이 나오면 --new HS2028 --old HS2022)
python -m backend.app.scheduler.jobs.hs_correlation_load --new HS2022 --old HS2017
```

| 컬럼 | 내용 |
|---|---|
| `new_ver`·`new_code` / `old_ver`·`old_code` | 새 판·이전 판 6단위 코드 (기본키는 네 컬럼) |
| `relation` | 새 판:이전 판 관계 `1:1`·`1:n`·`n:1`·`n:n` (예: HS2017 `150910` → HS2022 `150920`·`150930`·`150940`은 `n:1`) |
| `group_code` | 대응으로 이어진 코드 묶음의 대표(가장 작은 새 판 코드). 판을 넘는 집계는 이 값으로 묶습니다 |

- 받은 파일은 `hscode_outputs/correlation/<new>to<old>.xlsx`에 두고 다시 받지 않습니다(`--refresh`로 다시 받기, `--xlsx`로 직접 지정).
  새로 받은 파일은 파싱 검증을 통과해야만 캐시로 교체하고, 받아 둔 파일을 읽을 수 없으면(손상·형식 오류) 다시 받습니다.
- 같은 판 쌍의 기존 행을 지우고 새로 넣습니다(한 트랜잭션). 다른 판 쌍은 건드리지 않습니다.
- 자동 갱신(`hs_code_refresh`)은 `DONE`·`ALREADY_LOADED` 뒤에 올해 판과 직전 판(HS2028이면 HS2022)의 대응 쌍이 원천 파일과
  DB에서 모두 같은지 보고, 다르면(없음·일부만 있음·원천 변경) 이 적재를 실행합니다. 같으면 확인만 합니다. 실패하면(UNSD에 아직 없음 등)
  갱신 결과가 `FAILED`가 되어 다음 날 재시도하며, 이때 hs_code는 `ALREADY_LOADED`로 건너뛰고 대응표만 다시 시도합니다.
- HS2022의 신설 호 `8549`(전자 폐기물)·`8524`(평판 디스플레이 모듈) 등은 이전 코드 수백~천여 개와 이어져 있어, 그대로 묶으면 85류 대부분이
  그룹 하나(1,167개)가 됩니다. 이전 코드 100개(`HUB_FANOUT`) 넘게와 이어진 코드 15개는 묶지 않고 자기 코드를 그룹으로 씁니다.
- HS2022↔HS2017 결과: 대응 15,657쌍(`n:n` 10,114, `1:1` 4,097, `n:1` 1,392, `1:n` 54), 비교 그룹 5,246개(최대 38개 묶음).
- `hs_code`를 FK로 참조하지 않습니다. 이전 판 코드는 `hs_code`에 일부만 있어도(실적에 남은 HS2017 등) 대응표는 전체가 필요합니다.
- 6단위 대응만 다룹니다. HSK 10단위의 연간 신구 대응은 포함하지 않습니다.
- 새 판(HS2028) 대응표는 UNSD 파일 이름이 `HS2028toHS2022ConversionAndCorrelationTables.xlsx` 규칙이라고 가정합니다.
  게시 후 이름이 다르면 `hs_correlation_load.py`의 `URL`을 고치고, 90일 재시도 안에 게시되지 않으면
  파일을 직접 받아 `--xlsx <파일>`로 수동 적재합니다.

## 적재 동작

- `(hs_code, hs_ver)`가 같으면 새 값으로 갱신(upsert)합니다. 같은 키가 여러 번 들어오면 마지막 입력이 남습니다.
  (HS 코드 CSV는 같은 키가 두 번 나오면 검증 오류로 막습니다.)
- 적재와 결과 행 수 확인을 한 트랜잭션에서 처리합니다. 어느 단계에서 실패해도 전체가 롤백되어, 로그의 "롤백되어 반영되지 않았습니다"가 실제 결과와 같습니다.

## 자동 갱신 (스케줄러)

```powershell
python -m backend.app.scheduler.main
```

계속 켜 두는 프로세스입니다. 켜면 등록된 잡과 다음 실행 시각이 출력됩니다.
관세청 10단위(HSK)는 매년 1월 1일 개정되고, 6단위 HS 판은 몇 년마다(HS2028 …) 바뀝니다. 매년 실행에서 둘 다 처리합니다.

```
등록: hs_code_refresh | 다음 실행 2027-01-01 00:15:00+09:00
등록: hs_code_refresh_retry | 다음 실행 (켠 직후 점검 1회)
```

실행 결과는 상태로 남습니다.

| 상태 | 뜻 | 다음 동작 |
|---|---|---|
| `DONE` | 올해 판 다운로드·CSV 생성·적재 완료 (새 10단위 추가, 기존 코드 이름·판 갱신) | 내년 1월 1일까지 대기 |
| `NOT_READY` | 포털 최신 파일이 올해 판이 아님: 아직 작년 판(예: 2027-01-01에 20260101판만 있음)이거나, 내년 판이 연말에 미리 올라와 적용일 전(예: 2026-12-20에 20270101판) | 다음 날 00:15에 재시도 |
| `ALREADY_LOADED` | xlsx로 만들 코드가 DB에 모두 있고 올해 판(`source_edition`)으로 적재돼 있음 (LLM 비용이 드는 재생성 방지). 새 코드가 있거나 이전 판으로 적재돼 있으면 다시 생성·적재 | 대기 |
| `FAILED` | 다운로드·요약·적재 중 오류, 또는 대응표 적재 실패 | 다음 날 00:15에 재시도 |

- 재시도는 최대 90일입니다. 새 판 파일은 1월 1일 당일보다 늦게 올라오는 경우가 많아서 매일 다시 확인합니다.
- 새 판에서 빠진 이전 10단위 코드는 지우지 않습니다 (과거 수출입 데이터 연결용). 빠진 개수는 로그에 남습니다.
- 새 6단위가 생긴 경우에만 OpenAI 요약을 요청합니다. 기존 6단위는 요약 캐시를 씁니다.
- 1월 1일 00:15에 스케줄러가 꺼져 있었어도 6시간 안에 켜지면 실행합니다. 또 켤 때마다(연도 제한 없음) 한 번 실행해서,
  놓쳤거나 일부만 적재된 판을 채웁니다. 이미 모두 적재돼 있으면 `ALREADY_LOADED`로 바로 끝나고(LLM 호출 없음) `batch_run`에 SUCCESS 한 건이 남습니다.
- 재시도 예약은 메모리에만 있어서, 재시도 중에 스케줄러를 껐다 켜면 위의 "켤 때마다 실행"으로 이어집니다.

## 실행 기록 (batch_run)

실행할 때마다 `batch_run`에 한 건이 남습니다. 공통 코드는 `backend/app/db/batch_log.py`의 `track_batch()`입니다.

| 컬럼 | `HS_CODE_REFRESH` (자동 갱신) | `HS_CODE_LOAD` (적재 단독 실행) |
|---|---|---|
| `source_code` | `KCS_HS_NAMES` | `KCS_HS_NAMES` |
| `target_table` | `hs_code` | `hs_code` |
| 기간 | 기준일이 속한 해 (예: 2027-01-01~2027-12-31) | CSV `hs_ver`의 적용 기간 (HS2022 → 2022-01-01~2027-12-31) |
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

## 테스트

MySQL·OpenAI·포털 없이 메모리 SQLite와 가짜 단계 함수로 확인합니다.

```powershell
python -m unittest discover backend/tests
```

| 파일 | 확인하는 것 |
|---|---|
| `test_hs_code_build.py` | 6단위 이름 찾는 순서, 요약\|원문 형식, 수동 요약 파일, HS 판 결정, LLM 응답 검증·캐시 폴더 생성 |
| `test_db_crud.py` | 공통 CRUD, 같은 키 마지막 입력 보존, 적재 검증·롤백, 실행 로그, `batch_run` 기록 |
| `test_hs_code_un_check.py` | UN 대조의 알려진 예외·확인 필요 구분, 보고서, 종료 코드 |
| `test_hs_code_refresh.py` | 자동 갱신 상태(DONE·NOT_READY·ALREADY_LOADED·FAILED), 연간 새 판 재적재, 일부만 적재된 판 재적재, 매년 실행·재시도·재시작 시 실행, 판 갱신 뒤 대응표 확인·적재·실패 시 재시도 |
| `test_hs_correlation_load.py` | 대응표 파싱·형식 오류 검출, 비교 그룹(허브 제외), 같은 판 쌍 교체 적재, 테이블 없음 오류 |

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
  세계관세기구(WCO)가 다른 연도로 발표하면 `hs_code_build.py`의 `hs_ver_for()`·`hs_period()`를 고칩니다 (스케줄은 매년이라 그대로).
- 다운로드는 포털 화면의 다운로드 버튼과 같은 요청을 보냅니다. 포털 화면 구조가 바뀌면 `FAILED`와 함께 원인이 로그에 남습니다.
  포털이 자동입력방지(캡차)를 요구하면 우회하지 않고 멈춥니다. 이때는 포털에서 직접 받아 `reference/hs_names_YYYYMMDD.xlsx`로 넣고 `hs_code_refresh`를 수동 실행합니다.
- 관세청 원본에도 오류가 있습니다. 예: 4단위 `7804`의 품목명이 6단위 `780411`과 같게 들어가 있습니다. 공식 원문을 그대로 쓰므로 보정하지 않았습니다.
- 새 판의 요약은 자동으로 들어갑니다. 적재 후 요약 중복·누락 로그를 확인하고, 필요하면 `hs6_summary_overrides_<새 판>.csv`를 만들어 수동 실행으로 다시 적재합니다.
  (DB에 그 판 코드가 모두 있으면 자동 갱신은 `ALREADY_LOADED`로 건너뛰지만, `hs_code_build` → `hs_code_load` 수동 실행은 같은 키를 갱신합니다.)
