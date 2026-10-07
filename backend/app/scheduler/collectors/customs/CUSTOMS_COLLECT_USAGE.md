# 관세청 수출입실적 API 수집 코드 사용 안내

`customs_collect.py`는 관세청 품목별 국가별 수출입실적 API로 **지정한 연도 1년 치(1~12월, 전체 국가)**를 수집하고
원본 CSV와 수집 통계를 저장하는 독립 실행 코드입니다.

1. `.env`의 `customs` 인증키를 읽습니다.
2. 국가마다 API를 1번 호출해 해당 연도 1~12월의 HS 10단위 품목별 실적을 받습니다.
3. 국가별 원본을 저장한 뒤 하나의 원본 CSV로 합치고, 수집 통계와 실행 로그를 저장합니다.

응답 필드명과 값은 바꾸지 않으며, API가 함께 보내는 `총계` 행도 그대로 보존합니다.
전처리(총계 제외, 열 이름 변경 등)와 MySQL 적재는 포함하지 않습니다.

API 안내: https://www.data.go.kr/data/15100475/openapi.do

## 준비와 인증키 설정

저장소 루트에서 필요한 패키지를 설치합니다.

```powershell
cd "C:\your_folder\SKN33-Final-3Team"
python -m pip install requests python-dotenv
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
```

실제 인증키가 들어 있는 `.env`는 공유하지 않습니다. 팀원에게는 `.env.sample`을 전달합니다.

## 실행 방법

일부 국가로 먼저 확인합니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --year 2025 --countries US,VN
```

2025년 전체 국가(249개)를 수집합니다. 약 250회 호출, 6분 정도 걸립니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --year 2025
```

다른 연도는 `--year` 값만 바꿉니다. 여러 연도는 연도별로 차례대로 실행합니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --year 2024
foreach ($y in 2022..2025) { python backend/app/scheduler/collectors/customs/customs_collect.py --year $y }
```

인증키 파일 위치나 결과 폴더를 지정할 수 있습니다.

```powershell
python backend/app/scheduler/collectors/customs/customs_collect.py --env "C:\your_folder\.env" --countries US
python backend/app/scheduler/collectors/customs/customs_collect.py --output-dir "customs_outputs\sample_run" --countries US
```

## 저장 결과

기본 저장 위치는 코드가 있는 폴더의 `customs_outputs\연도\`입니다.

| 파일 | 내용 |
|---|---|
| `customs_raw_연도.csv` | 원본 필드명의 실적 표 (국가별로 받은 순서, 총계 행 포함). 한글 UTF-8 BOM |
| `customs_collection_연도.json` | 요청 국가 수, 실패 국가, 전체·데이터·총계 행 수, 국가·품목 수, 월 목록, 키 중복, 수출입 합계, 수집 완료 여부 |
| `_cache\국가코드.csv` | 국가별 원본. 중단 후 다시 실행하면 받은 국가는 건너뛰고 이어받습니다 |
| `logs\customs_연도_실행시각.log` | 화면 출력과 같은 실행 로그 |

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

- 전체 연도는 160만 행이 넘어 엑셀 최대 행 수를 넘습니다. 파이썬으로 읽습니다.
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
- 거래가 없는 국가는 빈 원본 파일로 남겨 다시 호출하지 않습니다.
- 공개 중인 연도(예: 당해 연도)는 공개된 달까지만 수집됩니다.
- 결과 폴더 `customs_outputs/`는 Git 커밋에서 제외됩니다.
