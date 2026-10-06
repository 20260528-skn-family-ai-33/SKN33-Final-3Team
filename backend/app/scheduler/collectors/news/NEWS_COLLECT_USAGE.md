# 해외시장뉴스 API 수집 코드 사용 안내

`news_collect.py`는 KOTRA 해외시장뉴스 API에서 기사를 받아 원본 파일로 저장하는 코드입니다.
컴퓨터에 기존 CSV가 없어도 실행할 수 있습니다. JSON 또는 XML 응답을 읽고,
기사 제목·본문·요약·키워드 등을 페이지별로 수집합니다.

처리 순서는 다음과 같습니다.

1. `.env`의 `news` 인증키를 읽습니다.
2. 해외시장뉴스 API를 호출합니다. 본문·요약·키워드를 받기 위해 `search8=Y`를 전달합니다.
3. 기사들을 원본 CSV와 JSON으로 저장하고 수집 건수·완료 여부를 기록합니다.

이 파일의 실행 범위는 원본 수집과 저장까지입니다. 전처리와 MySQL 적재는 포함하지 않습니다.
본문 HTML과 결측값은 원본 상태로 보존합니다. CSV에서는 null과 빈 문자열이 모두 빈칸으로
표시되므로 두 값을 구분하려면 원본 JSON을 확인하세요.

API 안내: https://www.data.go.kr/data/15034831/openapi.do

## 준비 파일

수집 코드는 `backend/app/scheduler/collectors/news/`, `.env.sample`은 저장소 루트에 있습니다.
Python과 공공데이터포털에서 발급받은 해당 API의 인증키가 필요합니다.
`news_pipeline.py`, MySQL 관련 코드와 패키지는 필요하지 않습니다.

## 실행 방법

PowerShell에서 파일이 있는 폴더로 이동합니다.
아래 경로는 현재 프로젝트 기준이며 팀원은 자신의 저장 위치로 바꿉니다.

```powershell
cd "C:\your_folder\SKN33-Final-3Team"
python -m pip install requests python-dotenv
```

### 1. 인증키 설정

해당 폴더에 `.env`가 없다면 `.env.sample`을 `.env`라는 이름으로 복사합니다.
기존 `.env`가 있다면 복사해 덮어쓰지 말고 기존 파일의 `news` 항목만 설정합니다.

```powershell
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.sample" -Destination ".env"
}
```

`.env`를 텍스트 편집기로 열어 다음 예시 값을 실제 인증키로 바꾼 뒤 저장합니다.

```dotenv
news=YOUR_NEWS_API_KEY
```

실제 인증키가 들어 있는 `.env`는 공유하지 않습니다. 팀원에게는 `.env.sample`을 전달합니다.
인코딩된 키와 디코딩된 키를 모두 사용할 수 있으며, 요청 전에 인코딩을 한 번 해제합니다.

### 2. 소량 수집으로 확인

```powershell
python backend/app/scheduler/collectors/news/news_collect.py --max-pages 1 --page-size 5
```

한 페이지에서 최대 5건을 수집합니다. 콘솔에 표시된 결과 폴더를 열어 원본 파일을 확인합니다.

### 3. 전체 수집

```powershell
python backend/app/scheduler/collectors/news/news_collect.py
```

기본적으로 페이지당 최대 100건을 조회합니다. `--max-pages`를 지정하지 않으면 전체 페이지를
수집합니다. 전체 수집 완료 여부는 코드가 자동 계산하므로 직접 true로 수정하지 않습니다.

### 4. 선택 옵션

XML 응답으로 한 페이지를 수집합니다.

```powershell
python backend/app/scheduler/collectors/news/news_collect.py --format xml --max-pages 1
```

게시일 기간을 지정합니다. 시작일과 종료일은 YYYYMMDD 형식입니다.

```powershell
python backend/app/scheduler/collectors/news/news_collect.py --start-date 20220101 --end-date 20261006
```

결과 폴더를 직접 지정합니다. 지정한 폴더는 아직 존재하지 않는 새 경로여야 합니다.

```powershell
python backend/app/scheduler/collectors/news/news_collect.py --max-pages 1 --output-dir "news_outputs\sample_run"
```

다른 위치의 인증키 파일을 사용합니다.

```powershell
python backend/app/scheduler/collectors/news/news_collect.py --env "C:\your_folder\.env" --max-pages 1
```

## 저장 결과

기본 저장 위치는 `news_collect.py`가 있는 폴더의 `news_outputs\실행시각\`입니다.
매번 새로운 폴더를 만들며 기존 결과는 덮어쓰지 않습니다.

| 파일 | 내용 |
|---|---|
| `news_raw.csv` | API의 원본 필드명으로 저장한 기사 표. HTML 유지, UTF-8 BOM 사용 |
| `news_raw.json` | 파싱한 원본 기사 객체 목록. JSON 수집에서는 null과 자료형 유지 |
| `news_collection.json` | API 전체 건수, 수집 건수, 전체 수집 완료 여부 |

수집 통계의 의미는 다음과 같습니다.

- `api_total`: 마지막 페이지 조회 시 API가 알려준 전체 기사 수
- `collected`: 이번 실행에서 수집한 기사 수
- `collection_complete`: 수집 건수가 API 전체 건수 이상이면 true, 아니면 false

XML을 선택해도 기사 목록은 JSON 파일로 변환해 저장합니다. 원본 XML 응답 전문을 저장하는
기능은 포함되어 있지 않습니다.

## 검증 범위와 실행 참고

기존 수집 로직은 실제 API에서 JSON 기사 5건을 받아 저장하는 것을 확인했습니다.
분리된 `news_collect.py`는 문법과 가상 응답을 이용한 페이지별 수집·원본 저장 테스트를 통과했습니다.
전체 수집과 실제 XML API 호출은 아직 검증하지 않았습니다.

현재는 수집이 끝난 뒤 파일을 저장합니다. 중간에 수집이 실패하면 그동안 받은 기사도 저장되지
않으며 자동 재개 기능은 없습니다. API 목록은 수집 도중 갱신될 수 있어 고정 시점의 전체 기사
스냅샷을 보장하지 않습니다.

## 저장소 배치 안내

명령은 저장소 루트에서 실행합니다. 기본 인증키 파일은 저장소 루트 `.env`입니다.
통합 `.env.sample`을 복사해 필요한 예시 값을 실제 값으로 바꾸되, 실제 `.env`는 커밋하지 않습니다.
실행 결과는 각 수집 코드 폴더의 실행시각별 결과 폴더에 저장되고 Git 커밋에서 제외됩니다.

## 전처리·MySQL 적재 코드 실행

요청된 MySQL 적재 코드의 의존성으로 `news_pipeline.py`와 `news_requirements.txt`를 함께 포함합니다.
원본 수집 기능과 별도로 실행합니다. 전체 수집·실제 MySQL 서버 적재는 미검증입니다.

```powershell
python -m pip install -r backend/app/scheduler/collectors/news/news_requirements.txt
python backend/app/scheduler/collectors/news/news_pipeline.py --raw-csv "원본CSV경로"
python backend/app/scheduler/collectors/news/news_load_mysql.py --csv "전처리CSV경로"
```

MySQL 서버에서 `news_db`를 먼저 만들고 루트 `.env`의 MYSQL 설정을 실제 값으로 변경합니다.
기존 값은 새 CSV 값이 비어 있으면 유지하며, 원본 JSON과 중복 식별 정보는 병합합니다.
