# 한국은행 ECOS 환율 수집·적재 사용 안내

`currency`(통화 사전)와 `fx_daily`(영업일별 환율)를 채우는 방법입니다.
원천은 한국은행 ECOS Open API 통계표 **731Y001(주요국 통화의 대원화환율)**의 일별(D) 매매기준율입니다.

API 안내: https://ecos.bok.or.kr/api/

| 파일 | 역할 |
|---|---|
| `db/mysql/init/08_currency_seed.sql` | ECOS에서 현재 제공 중인 통화 43개를 `currency`에 등록 (ISO 코드·ECOS 항목 코드·기준 수량) |
| `db/mysql/init/09_country_currency_seed.sql` | `country.currency_code`에 국가별 대표 통화 연결 (251개국 사전 기준 120개국) |
| `collectors/ecos/ecos_collect.py` | ECOS 호출·파싱·검증만 담당. DB에 쓰지 않음 |
| `jobs/fx_daily_load.py` | 환율을 받아 `fx_daily`에 저장하고 `batch_run`에 기록 |
| `scheduler/main.py` | 매일 06:00(KST)과 스케줄러를 켤 때 `fx_daily_load` 실행 |

## 준비와 인증키 설정

저장소 루트에서 필요한 패키지를 설치합니다. 추가 패키지는 없습니다.

```bash
python -m pip install -r backend/requirements.txt
```

ECOS Open API 사이트에서 로그인한 뒤 인증키를 신청합니다. 발급된 키를 저장소 루트 `.env`에 넣습니다.
`.env`가 없으면 `.env.sample`을 복사해 만들고, 이미 있으면 아래 항목만 추가합니다.

```dotenv
ECOS_API_KEY=YOUR_ECOS_API_KEY   # 키 이름은 ecos도 가능 (대소문자 구분 없음). 서버 환경변수가 있으면 .env보다 우선
FX_START_DATE=2026-01-01         # fx_daily가 비어 있을 때 처음 채우기 시작할 날짜
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=YOUR_MYSQL_USER
MYSQL_PASSWORD="YOUR_MYSQL_PASSWORD"
MYSQL_DATABASE=trade_agent
```

실제 키가 들어 있는 `.env`는 공유하지 않습니다. 인증키는 DB에 저장하지 않습니다.

## DB 준비 (처음 한 번)

`currency`·`fx_daily` 테이블은 `01_schema.sql`에 이미 있습니다. 같은 DB에서 다음 순서로 실행합니다.

1. `03_market_region_seed.sql`, `04_country_seed.sql`: 지역·국가 사전 (이미 실행했다면 생략)
2. `08_currency_seed.sql`: 통화 사전
3. `09_country_currency_seed.sql`: 국가 ↔ 통화 연결. 연결할 통화가 먼저 있어야 하므로 반드시 08 다음에 실행

```bash
mysql -u root -p trade_agent -e "source db/mysql/init/08_currency_seed.sql"
mysql -u root -p trade_agent -e "source db/mysql/init/09_country_currency_seed.sql"
```

PyCharm의 대상 DB 쿼리 콘솔에서 파일 내용을 순서대로 실행해도 됩니다. 두 파일 모두 다시 실행할 수 있습니다.

- 08은 이미 있는 통화의 `quote_unit`(1·100단위)을 바꾸지 않습니다. 기존 환율 값의 의미가 바뀌기 때문입니다.
- 09에 없는 국가의 기존 연결은 바꾸지 않습니다. `04_country_seed.sql`을 다시 실행해도 연결은 유지됩니다.

## 실행 방법

DB에 저장하지 않고 ECOS 응답만 확인합니다.

```bash
python -m backend.app.scheduler.collectors.ecos.ecos_collect --start 2026-10-01 --end 2026-10-19 --items 0000001,0000002
```

과거 기간을 적재합니다. 처음 한 번 실행하고, 통화를 새로 추가했을 때도 실행합니다.

```bash
python -m backend.app.scheduler.jobs.fx_daily_load --start 2026-01-01 --end 2026-10-19
```

기간 없이 실행하면 스케줄러와 같은 동작(최근 기간 갱신)을 합니다.

```bash
python -m backend.app.scheduler.jobs.fx_daily_load
```

| 조건 | 받는 기간 |
|---|---|
| 기본 | 오늘(KST) 포함 최근 2일 |
| 그 통화의 마지막 기준일이 그보다 오래됨 (주말·휴일·중단 뒤, 한 통화만 비어 있던 날) | 그 통화의 마지막 기준일부터 오늘까지 |
| 그 통화가 아직 한 건도 없음 (처음 적재, 새로 추가한 통화) | `.env`의 `FX_START_DATE`부터 오늘까지 |

기간은 **통화마다 따로** 정합니다. 한 통화의 최신 날짜가 다른 통화의 빈 날짜를 가리지 않습니다.

## 자동 실행

```bash
python -m backend.app.scheduler.main
```

- `fx_daily_load`: 매일 06:00(KST)에 실행합니다. 새벽에는 당일 환율이 아직 없어서 **전 영업일까지** 반영됩니다.
- `fx_daily_startup`: 스케줄러를 켜고 30초 뒤 한 번 실행해, 꺼져 있던 동안 빠진 날을 채웁니다.
- 실패해도 따로 재시도하지 않습니다. 다음 실행이 통화별 마지막 기준일부터 다시 받습니다.
- **한 번에 하나만 실행됩니다.** 정기 작업과 시작 작업이 겹치거나(예: 05:59에 스케줄러를 켬), 수동 실행·다른 서버와 겹치면
  늦게 시작한 쪽은 `이미 실행 중이라 이번 실행은 건너뜁니다` 로그만 남기고 끝납니다 (같은 프로세스는 실행 잠금,
  다른 프로세스는 MySQL `GET_LOCK`). 오래 걸린 실행이 나중에 저장하면서 더 최신 값을 덮는 일을 막기 위해서입니다.
- 공용 스케줄러라 HS 코드 갱신·수출입 작업도 함께 켜집니다. 환율만 확인할 때는 위의 `fx_daily_load` 명령을 직접 실행합니다.

## 저장 규칙

- `base_date`는 ECOS 응답의 기준 영업일(`TIME`)입니다. 실행한 날짜가 아닙니다.
- `rate_krw`는 `currency.quote_unit`만큼의 외화를 원으로 나타낸 값입니다. **JPY·IDR·VND는 100단위**입니다
  (JPY 850.24 = 100엔당 850.24원). 화면에 표시할 때는 반드시 `quote_unit`과 함께 읽습니다.
- 통화마다 ECOS 응답의 금액 단위가 `원`인지, 항목명의 기준 수량(예: `원/일본엔(100엔)`의 100)이 `quote_unit`과 같은지 확인합니다.
  하나라도 다르면 아무것도 저장하지 않습니다.
- 0 이하·숫자 아님·요청하지 않은 통화·기간 밖 날짜·중복 날짜도 저장 전에 오류로 막습니다.
- 여러 페이지로 받을 때 ECOS가 알려준 전체 건수와 실제로 받은 건수가 다르거나 중간 페이지가 비면 저장하지 않습니다
  (일부만 받은 기간을 정상 수집으로 기록하지 않게).
- 모든 통화를 받은 뒤 한 트랜잭션으로 저장합니다. (기준일, 통화)가 이미 있으면 값만 갱신하므로 다시 실행해도 중복이 생기지 않습니다.
- 실패하면 롤백되어 기존 환율이 그대로 남습니다. 행을 삭제하거나 0으로 바꾸지 않습니다.
- 실행마다 `batch_run`에 한 건 기록합니다 (job `FX_DAILY_LOAD`, 대상 `fx_daily`, 출처 `ECOS`).
  출처 `ECOS`가 `service_option`에 없으면 자동으로 추가합니다.
- 실행 로그는 `data/ecos_outputs/logs/fx_daily_load_<시각>.log`에 남습니다. `data/`는 Git에서 제외됩니다.

## 전일 대비 등락 조회

등락 금액·등락률은 저장하지 않고, 조회할 때 같은 통화의 직전 영업일 행과 비교해 계산합니다.
월요일 값은 금요일과 비교됩니다.

```sql
SELECT f.base_date, f.currency_code, u.quote_unit, f.rate_krw,
       f.rate_krw - LAG(f.rate_krw) OVER w                       AS change_krw,
       ROUND((f.rate_krw / LAG(f.rate_krw) OVER w - 1) * 100, 2) AS change_pct
FROM fx_daily f JOIN currency u USING (currency_code)
WINDOW w AS (PARTITION BY f.currency_code ORDER BY f.base_date)
ORDER BY f.base_date DESC, f.currency_code;
```

## 확인 SQL

```sql
SELECT COUNT(*) FROM currency;                                   -- 43
SELECT COUNT(*) FROM country WHERE currency_code IS NOT NULL;    -- 120 (04 seed 243개국만 있으면 116)
SELECT currency_code, COUNT(*) AS days, MIN(base_date), MAX(base_date)
FROM fx_daily GROUP BY currency_code ORDER BY currency_code;
SELECT run_id, status, period_start, period_end, request_count, received_count
FROM batch_run WHERE job_name = 'FX_DAILY_LOAD' ORDER BY run_id DESC;
```

## 통화·국가 연결 기준

- **통화:** ECOS 731Y001 일별 항목 중 지금도 제공되는 43개입니다. 제공이 끝난 항목은 제외했습니다
  (`0000004`~`0000011` 유로 도입 전 통화, `0000027`·`0000030` 2016년에 끝난 위안 시리즈).
- **국가:** CLDR(유니코드 공통 로케일 데이터)의 국가별 대표 법정통화가 ECOS 제공 통화일 때만 연결했습니다.
  대표 통화를 ECOS가 제공하지 않는 131개국은 다른 통화로 대체하지 않고 NULL로 둡니다 (환율 카드에는 "환율 미제공").
- **특수 코드:** 대한민국(KR, 원화가 기준이라 환율 없음)·채널제도(JG, 관세청 특수 코드)·북한(KP)·서사하라(EH)는 NULL입니다.
- **복수 통화 국가:** 부탄 INR, 레소토·나미비아 ZAR, 팔레스타인 ILS, 짐바브웨 USD로 연결했습니다.
  아이티(HTG)·파나마(PAB)는 대표 통화가 ECOS에 없어 NULL입니다.
- **보정:** 불가리아는 2026-01-01 유로 도입으로 EUR입니다 (CLDR 데이터에 아직 반영되지 않아 직접 지정).

## 통화를 추가할 때

1. ECOS 항목 목록에서 항목 코드와 항목명의 기준 수량을 확인합니다.
2. `08_currency_seed.sql`에 `('ISO코드','이름','항목코드',기준수량)` 한 줄을 추가해 실행합니다.
3. 그 통화를 쓰는 국가를 `09_country_currency_seed.sql`에 추가해 실행합니다.
4. `.env`에 `FX_START_DATE`가 있으면 다음 실행(매일 06:00 또는 기간 없는 실행)이 그 날짜부터 새 통화를 자동으로 채웁니다.
   없으면 과거 기간 적재 명령(`--start`·`--end`)을 실행합니다.

코드는 바꾸지 않습니다. 수집 대상은 `currency`에서 `ecos_item_code`가 있는 통화입니다.

## 테스트

실제 ECOS·MySQL 없이 가상 응답과 SQLite로 확인합니다.

```bash
python -m unittest backend/tests/test_ecos_collect.py backend/tests/test_fx_daily_load.py
```

## 팀 확인이 필요한 항목

- 매일 조회 범위 2일 (`fx_daily_load.py`의 `RECENT_DAYS`). 2일보다 오래된 원천 정정은 반영되지 않습니다.
- 아이티·파나마를 USD로 연결할지 여부.
