# 외부 데이터(WDI·BACI·Comtrade) 수집 사용 안내

ML 피처(유망 품목·국가 예측, 시장 유형 분류)에 쓰는 외부 데이터를 받아 `data/external/`에 CSV로 저장합니다.
학습·배치 예측에서 읽는 외부 입력은 **DB에 적재하지 않습니다.** 이번 코드·문서 변경에는 `data/external/`의 수집 결과를 포함하지 않습니다.

| 데이터 | 한 줄 요약 | 범위 | 저장 파일 |
|---|---|---|---|
| World Bank WDI | 국가별 GDP·GDP 성장률·인구·1인당 GDP | 2022~작년 | `data/external/wdi/wdi_2022_<작년>.csv` |
| CEPII BACI (HS22) | 보정된 국가×국가×HS6 연간 교역액을 수입국·HS6별로 집계 | 2022~2024 (V202601) | `data/external/baci/baci_imports_hs22.csv` |
| UN Comtrade | 각국이 신고한 HS6별 수입액. BACI에 아직 없는 최신 연도 보충 | 2025 (신고 마친 국가만) | `data/external/comtrade/comtrade_imports_<연도>.csv` |

국가 키 `country_code`는 관세청 `country.country_code`와 같은 **ISO2**입니다. 국가 시드에 없는 국가·지역은 별도로 연결 상태를 확인해야 합니다.

## 한 번에 받기

처음 받을 때와 수동으로 최신화할 때 모두 아래 명령 하나면 됩니다. 스케줄러가 매월 실행하는 작업과 같습니다.

**준비 (처음 한 번)**

```powershell
cd "C:\your_folder\SKN33-Final-3Team"
python -m pip install -r backend/requirements.txt
```

저장소 루트 `.env`에 아래 값을 넣습니다. 없으면 `.env.sample`을 복사해 만듭니다.

```
UN_COMTRADE_API_KEY=...   # comtradeplus.un.org 무료 키. 없으면 Comtrade만 건너뜀
MYSQL_HOST=...         # batch_run 기록용. 연결이 안 돼도 수집은 계속됨
```

**실행**

```powershell
python -m backend.app.scheduler.jobs.external_refresh
python backend/app/scheduler/collectors/external/external_collect.py check --db
```

첫 줄이 BACI zip 다운로드(약 300MB)·집계 → WDI → Comtrade 순서로 받고, 둘째 줄이 결과를 검사합니다 (`발견된 CSV의 검사 통과`는 존재하는 파일에 대한 검사 결과입니다. 전체 수집 완료와 구분하세요).
처음에는 10~20분 걸립니다 (BACI 다운로드·집계 수 분, Comtrade 신고국당 1초 이상).
다시 실행하면 이미 받은 BACI 판은 건너뛰고(`ALREADY`), Comtrade는 못 받은 나라만 받습니다.

**결과**

```
data/external/
├─ baci/BACI_HS22_V202601.zip        원본 zip
├─ baci/baci_imports_hs22.csv        2022~2024
├─ wdi/wdi_2022_2025.csv
├─ comtrade/comtrade_imports_2025.csv
├─ comtrade/raw/2025/*.json          신고국별 원본 응답 (이어받기용)
└─ logs/                             실행 로그·download_failures.log
```

## 자동 갱신 (스케줄러)

`backend/app/scheduler/main.py`가 **매월 1일 04:00(KST)** 에 `scheduler/jobs/external_refresh.py`를 실행합니다.

1. BACI: 올해 판 `BACI_HS22_V{올해}01.zip`이 없으면 CEPII에서 받아(로그인 불필요) 집계합니다.
   수정판(`V{올해}01c`, `V{올해}01b`)이 있으면 그것을 먼저 받습니다.
   아직 공개 전(404)이면 `NOT_READY`로 끝내고 다음 달에 다시 확인합니다. 이미 받아 집계했으면 `ALREADY`이고 batch_run에 남기지 않습니다.
   `NOT_READY`는 batch_run에 수신 0건 SUCCESS로 남고 실행 로그에는 WARNING으로 찍힙니다.
   새 판을 집계하면 지난 판 zip은 지웁니다.
2. WDI: 2022년~작년 지표를 다시 받습니다 (과거 값도 수정되므로 덮어씀). 이전 기간의 `wdi_*.csv`는 지웁니다.
3. Comtrade: BACI 마지막 연도(연결 연도) ~ 작년을 받습니다. 연결 연도는 BACI 다음 해의 성장률을 같은 Comtrade끼리 비교하는 데만 씁니다.
   BACI가 없거나 3년 넘게 늦으면 호출 한도 때문에 최근 2년만 받습니다. `.env`에 `UN_COMTRADE_API_KEY`가 없으면 건너뜁니다.
   신고국별 응답을 `data/external/comtrade/raw/<연도>/<국가코드>.json`에 남겨, 한도 초과로 끊겨도 다음 실행은 못 받은 나라만 받습니다.
   이미 받은 나라의 수정 신고까지 반영하려면 그 연도의 `raw/<연도>/`를 지우고 실행합니다.
   BACI 새 판이 나오면 연결 연도보다 앞선 해의 `comtrade_imports_<연도>.csv`와 `raw/<연도>/`를 지웁니다.

| 기록 | 위치 |
|---|---|
| 실행 로그 | `data/external/logs/external_refresh_<시각>.log` |
| **실패 로그** (다운로드·수집 실패만 이어 씀) | `data/external/logs/download_failures.log` |
| DB 실행 기록 | `batch_run` (대상 `external_csv`, job `EXTERNAL_BACI`·`EXTERNAL_WDI`·`EXTERNAL_COMTRADE`) |

기존 DB에는 `db/mysql/init/06_external_batch_target.sql`을 한 번 실행해 `batch_run.target_table`에 `external_csv`를 추가해야 합니다.
없으면 batch_run 기록만 경고를 남기고 건너뛰며 수집은 계속됩니다.

수동 실행: `python -m backend.app.scheduler.jobs.external_refresh [--date YYYY-MM-DD]`

## 단계별 수동 실행

저장소 루트에서 실행합니다.

```powershell
# 1) WDI: API 키 없이 바로 받습니다.
python backend/app/scheduler/collectors/external/external_collect.py wdi

# 2) BACI: 받아 둔 HS22 zip을 집계합니다. (압축 해제 불필요)
python backend/app/scheduler/collectors/external/external_collect.py baci data/external/baci/BACI_HS22_V202601.zip

# 3) Comtrade: .env에 UN_COMTRADE_API_KEY가 있어야 합니다. 신고 국가당 1회 호출(무료 키 하루 500회).
python backend/app/scheduler/collectors/external/external_collect.py comtrade --year 2025

# 4) 검사. --db를 붙이면 hs_code(HS2022)·export_summary와 교차 검증까지 합니다.
python backend/app/scheduler/collectors/external/external_collect.py check --db
```

## 컬럼

- WDI: `year, country_code, gdp_usd, gdp_growth_pct, population, gdp_per_capita_usd` (발표 전 값은 빈칸)
- BACI·Comtrade: `year, country_code(수입국), hs6, import_world_usd, import_from_kor_usd` (USD)
  - 한국 점유율 = `import_from_kor_usd / import_world_usd`

## 주의

- **나미비아 코드가 `NA`입니다.** pandas로 읽을 때 `keep_default_na=False, na_values=[""]`를 줘야 결측으로 바뀌지 않습니다.
- 국가 매핑률 검사 기준: WDI 85%(대만·소규모 속령 미발표), BACI 90%(레위니옹·푸에르토리코 등은 프랑스·미국에 합산), Comtrade는 기준 없음(신고 마친 국가만).
- BACI 국가 파일은 대만(490 "Other Asia, nes")과 나미비아의 ISO2가 빈칸이라 코드에서 `TW`·`NA`로 채웁니다. 나머지 빈칸(지역 묶음)은 버립니다.
- 금액 기준: BACI는 수출입 신고를 조정한 **FOB** 값이고, Comtrade 수입액(`primaryValue`)은 운임·보험료를 포함한 **CIF** 값입니다. [CEPII 방법론](https://www.cepii.fr/pdf_pub/wp/2010/wp2010-23.pdf)
  그래서 두 출처를 섞어 성장률을 계산하지 않습니다. BACI 마지막 해는 Comtrade로도 받아 두고(연결 연도), BACI 다음 해의 성장률은 Comtrade끼리 비교합니다.
- BACI와 관세청 수출액은 둘 다 FOB지만 신고·집계·조정 차이로 정확히 같지 않습니다. 교차 검증은 `export_summary`에 12개월이 모두 있는 해만, ±20%로 봅니다.
- Comtrade는 보정 전 신고값이고 HS2022(H6)로 신고한 행만 씁니다. 같은 연도 BACI가 나오면 BACI를 우선합니다.

## 테스트

```powershell
python -m unittest backend/tests/test_external_data.py backend/tests/test_external_refresh.py
```


검사 제한: 빈 디렉터리, 무역 코드·금액 결측은 오류로 처리합니다. 모든 예상 연도·국가의 완전 수집, HS 사전과의 일치, 행 부재가 실제 0인지 여부는 이 검사 통과만으로 보장되지 않습니다. 수집 범위와 원본 보관 정책은 이번 수정으로 변경하지 않았습니다.

Comtrade 대상 응답 행의 `primaryValue`는 집계 전에 검사합니다. 원본 null·비수치·무한대가 있으면 중단하므로 `sum`이 null을 0으로 숨기지 못합니다. 명시적인 0 금액은 정상 입력입니다. 한국 파트너 행 자체가 없는 경우를 0으로 채우는 기존 동작은 별개이며, 이 0을 미진출 판정 근거로 바로 사용하면 안 됩니다.
