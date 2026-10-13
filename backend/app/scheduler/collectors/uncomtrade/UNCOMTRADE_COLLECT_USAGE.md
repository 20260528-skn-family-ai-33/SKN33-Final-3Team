# UN Comtrade 수집 코드 사용 안내

`uncomtrade_collect.py`는 SK네트웍스 수출입 품목(HS 6단위)에 대해, **상대 나라 쪽 무역 데이터**를 UN Comtrade에서 받아
원본 CSV와 수집 통계를 저장합니다. 한국 쪽 수출입 실적은 관세청 데이터에 이미 있으므로 UN에서는 상대국 데이터만 받습니다.

| 구분 | UN 요청 조건 | 알 수 있는 것 |
|---|---|---|
| 수출 품목 (배터리, PTA, 정수기 …) | 보고국 = 우리가 수출하는 나라, 방향 = 그 나라의 **수입**(M), 상대국 = 전 세계(0)·한국(410) | 그 나라 시장 규모와 **한국산 비중** |
| 수입 품목 (와인, 타이어, 메탄올 …) | 보고국 = 우리가 수입하는 나라, 방향 = 그 나라의 **수출**(X), 상대국 = 전 세계(0)·한국(410) | 그 나라 공급 규모와 **한국행 비중** |

1. `skn_items_hscode.md`(SK네트웍스 품목 HS Code 목록)에서 품목·HS 6단위를 읽습니다.
2. 관세청 원본(`--customs-csv`, 여러 개 가능)의 **한국 거래국 전부**를 모든 품목의 대상 나라로 씁니다.
3. UN 보고국 코드표로 국가코드(US) → UN 숫자코드(842)로 바꿉니다. UN에 없는 나라(대만, 작은 영토 등)는 제외하고 기록합니다.
4. 기간마다 UN에 데이터를 올린 나라 목록을 먼저 받아, 데이터가 있는 나라에만 요청합니다.

응답 컬럼(47개)과 값은 바꾸지 않습니다. 비중 계산·전처리는 포함하지 않습니다.

## 인증키 (권장)

키 없이도 동작하지만(공개 API: 1회 1개월·500행, 사용량 한도에 걸리면 약 30분 대기 후 자동 재개),
무료 키가 있으면 1회에 12개 기간을 받아 **수 분 안에** 끝납니다.

1. https://comtradeplus.un.org 회원가입 → My API portal에서 `comtrade subscription`의 **Primary** 키 복사
2. 저장소 루트 `.env`에 한 줄 추가 (값은 공유·커밋하지 않습니다)

```
UN_COMTRADE=복사한키
```

## 실행

```powershell
$d = "관세청 데이터(년도별) 폴더 경로"
# 월별 (2024.01~2025.12)
python backend/app/scheduler/collectors/uncomtrade/uncomtrade_collect.py --customs-csv "$d\customs_raw_2022.csv" "$d\customs_raw_2023.csv" "$d\customs_raw_2024.csv" "$d\customs_raw_2025.csv" "$d\customs_raw_2026.csv"
# 연간 (2024, 2025): 월별을 올리지 않는 나라까지 포함
python backend/app/scheduler/collectors/uncomtrade/uncomtrade_collect.py --freq A --customs-csv (위와 같음)
```

| 옵션 | 설명 |
|---|---|
| `--customs-csv` | 필수. 대상 나라를 정할 관세청 원본 (여러 개 가능) |
| `--freq` | `M` 월별(기본), `A` 연간 |
| `--scope` | `customs` 관세청 거래국 전부(기본), `item` 품목별 거래국만 |
| `--top` | `--scope item`일 때 품목별 상위 N개국 (0이면 전부) |
| `--start` / `--end` | 기간 YYYYMM (기본 `202401` ~ `202512`, 연간은 연도만 사용) |
| `--items-md` | 품목 목록 md (기본: 같은 폴더의 `skn_items_hscode.md`) |
| `--delay` | 공개 API 호출 사이 대기 초 (기본 2) |
| `--output-dir` | 결과 폴더 (기본: `un_comtrade_outputs/{기간}_{scope}/`, git 제외) |

받은 호출은 `_cache/`에 저장되어 다시 실행하면 건너뜁니다.

## 결과 파일

| 파일 | 내용 |
|---|---|
| `un_comtrade_raw_{기간}.csv` | UN 응답 원본 47개 컬럼 (컬럼 설명은 UN_Comtrade CSV 컬럼 안내 PDF) |
| `un_targets.csv` | 품목 × 상대 나라 대상 목록: 관세청 기준 한국 금액(`kr_usd`, 거래 없으면 0)·비중, UN 코드 (UN에 없으면 빈칸) |
| `skn_items.csv` | md에서 읽은 품목 목록 |
| `un_reporters.json` | UN 보고국 코드표 원본 |
| `un_comtrade_collection_{기간}.json` | 호출 수, 행 수, 실패 호출, UN 미보고 나라, 대상×월 누락 목록 |

분석 시 주의: 전 세계(`partnerCode=0`) 행에는 한국 행이 이미 포함되어 있으므로 두 행을 더하지 않습니다.
한국산 비중 = 한국 행 `primaryValue` ÷ 전 세계 행 `primaryValue`.

## 테스트

```powershell
python -m unittest backend/tests/test_uncomtrade_collect.py
```

예상 결과: `Ran 11 tests ... OK` (API를 호출하지 않는 테스트입니다)

## 수집 결과 (2026-10-12, 관세청 2022~2026 거래국 247개국 기준)

| 구분 | 행 | 데이터가 있는 나라 |
|---|---|---|
| 월별 2024.01~2025.12 | 101,635 | 112개국 (2024년 107, 2025년 99) |
| 연간 2024·2025 | 12,451 | 150개국 (연간에만 있는 나라 41개 포함) |
| 월별 또는 연간 | | **153개국** (UN에서 받을 수 있는 최대) |

UN에 없는 나라 30개(대만, 괌, 모나코 등)와 2024~2025 데이터를 올리지 않은 나라(베트남, UAE, 러시아 등)는 받을 수 없습니다.
