# 단신속보뉴스 API 수집 코드 사용 안내

`shortnews_collect.py`는 KOTRA 단신속보뉴스 API의 JSON 또는 XML 응답을 수집하고
원본 CSV·JSON과 수집 통계를 저장하는 독립 실행 코드입니다.

1. `.env`의 `shortnews` 인증키를 읽습니다.
2. 단신속보뉴스 API를 페이지별로 호출합니다.
3. 원본 CSV, 원본 기사 객체 JSON, 수집 통계를 저장합니다.

전처리와 MySQL 적재는 포함하지 않습니다. `news_collect.py`가 없어도 실행할 수 있습니다.
해외시장뉴스의 본문 조회 옵션 `search8=Y`는 사용하지 않습니다.
API가 제공하는 개요내용 `smmarCn`과 원문 링크를 보존하며 원문 사이트 본문을 추가 수집하지 않습니다.

API 안내: https://www.data.go.kr/data/15122662/openapi.do

## 준비와 인증키 설정

수집 코드는 `backend/app/scheduler/collectors/shortnews/`, `.env.sample`은 저장소 루트에 있습니다.
PowerShell에서 해당 폴더로 이동한 뒤 필요한 패키지를 설치합니다.

```powershell
cd "C:\your_folder\SKN33-Final-3Team"
python -m pip install requests python-dotenv
```

팀원은 자신의 저장 경로를 사용합니다. `.env`가 없다면 예시 파일을 복사합니다.

```powershell
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.sample" -Destination ".env"
}
```

`.env`의 예시 값을 발급받은 인증키로 바꾸고 저장합니다.
기존 `.env`가 있으면 덮어쓰지 말고 다음 항목만 추가하거나 수정합니다.

```dotenv
shortnews=YOUR_SHORTNEWS_API_KEY
```

실제 인증키가 들어 있는 `.env`는 공유하지 않습니다.
팀원에게는 `.env.sample`을 전달합니다. 기존 `news` 항목은 유지할 수 있습니다.

## 실행 방법

소량 5건을 먼저 확인합니다.

```powershell
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py --max-pages 1 --page-size 5
```

전체 페이지를 수집합니다.

```powershell
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py
```

기본 페이지 크기는 100건입니다. `--max-pages`의 기본값 0은 전체 페이지를 뜻합니다.

XML 응답이나 제목 검색을 선택할 수 있습니다.

```powershell
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py --format xml --max-pages 1
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py --title "수출" --max-pages 1
```

작성일 검색값은 `--date`로 전달하며 API의 `search2`에 그대로 전달됩니다.
해외시장뉴스의 `--start-date`, `--end-date` 기간 옵션은 제공하지 않습니다.
검색 조건이 없으면 빈 조건으로 전체 목록을 요청합니다.

결과 폴더 또는 인증키 파일 위치를 지정할 수 있습니다.

```powershell
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py --max-pages 1 --output-dir "shortnews_outputs\sample_run"
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py --env "C:\your_folder\.env" --max-pages 1
```

지정한 결과 폴더는 아직 존재하지 않는 새 경로여야 합니다.

## 저장 결과

기본 저장 위치는 코드가 있는 폴더의 `shortnews_outputs\실행시각\`입니다.
매 실행마다 새 폴더를 생성합니다.

| 파일 | 내용 |
|---|---|
| `shortnews_raw.csv` | 원본 필드명의 기사 표. 한글 UTF-8 BOM 사용 |
| `shortnews_raw.json` | 파싱한 원본 기사 객체 목록 |
| `shortnews_collection.json` | 데이터 종류, API 전체 건수, 수집 건수, 수집 완료 여부 |

기본 컬럼은 `kbc`, `nttSn`, `nttSj`, `smmarCn`, `regn`, `nat`, `othbcDt`,
`kotraNewsUrl`, `realAtfileInfoList`이며 추가 응답 필드도 저장합니다.
첨부파일 중첩 정보는 CSV 셀에 JSON 문자열로, JSON 파일에는 중첩 구조로 보존합니다.
HTML은 제거하지 않습니다. CSV의 null과 빈 문자열은 모두 빈칸으로 표시되므로 JSON으로 구분합니다.
XML 응답도 기사 객체를 JSON으로 변환해 저장하며 원본 XML 전문 저장은 포함하지 않습니다.

- `api_total`: 마지막 조회에서 API가 알려준 전체 건수
- `collected`: 이번 실행에서 수집한 건수
- `collection_complete`: 수집 건수가 전체 건수 이상이면 true, 아니면 false

완료 여부는 자동 계산합니다. 수동으로 값을 수정할 필요가 없습니다.

## 검증과 실행 참고

분리 전 동일한 수집 로직으로 실제 단신속보뉴스 JSON 5건 수집·원본 저장을 확인했습니다.
분리된 코드의 독립 실행 구성과 페이지별 수집, 원본 보존, XML 첨부파일 파싱은 테스트로 확인합니다.
전체 수집과 실제 XML API 호출은 아직 실행하지 않았습니다.

현재는 수집 완료 후 파일을 저장합니다. 수집 도중 실패하면 그동안 받은 기사도 저장되지 않으며
자동 재개 기능은 없습니다. 수집 중 API 목록이 갱신될 수 있어 고정 시점 스냅샷은 보장하지 않습니다.

## 저장소 배치 안내

명령은 저장소 루트에서 실행합니다. 기본 인증키 파일은 저장소 루트 `.env`입니다.
통합 `.env.sample`을 복사해 필요한 예시 값을 실제 값으로 바꾸되, 실제 `.env`는 커밋하지 않습니다.
실행 결과는 각 수집 코드 폴더의 실행시각별 결과 폴더에 저장되고 Git 커밋에서 제외됩니다.
