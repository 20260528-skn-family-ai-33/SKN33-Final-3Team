# 단신속보 적재 코드 사용 안내

`backend/app/scheduler/jobs/short_news_load.py`는 단신속보 CSV를 정제·마스킹하고, LLM으로 요약·키워드를 뽑아
MySQL(`article`, `article_tag`)과 Chroma(`short_news` 컬렉션)에 적재합니다.

> **결정 (2026-10-23)**: 단신속보에서는 HS 코드(품목) 추출을 하지 않습니다. `article_tag`에는 키워드 행만 넣습니다.

## 준비

| 항목 | 내용 |
|---|---|
| 패키지 | `python -m pip install -r backend/requirements.txt` |
| `.env` (저장소 루트) | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `OPENAI_API_KEY` |
| MySQL | `article`, `article_tag`, `service_option`, `batch_run` 테이블 (`db/mysql/init/01_schema.sql`) |
| 키워드 사전 (선택) | `data/news_keywords/synonyms.csv`·`hypernyms.csv`·`topic_categories.csv` (`services/news_keywords/NEWS_KEYWORDS_USAGE.md`로 생성). 없으면 정제·지역 분리만 합니다 |
| 출처 코드 | `service_option`의 `KOTRA_SHORT_NEWS` 행. 없으면 첫 실행 때 자동으로 추가됩니다 |

입력은 **단신속보 수집 결과를 그대로** 씁니다 (`SHORTNEWS_COLLECT_USAGE.md`).
`--csv`를 주지 않으면 `shortnews_outputs/<실행시각>/shortnews_raw.csv` 중 가장 최근 것을 읽습니다 (수집이 실패해 빈 폴더는 건너뜀).

입력 CSV 컬럼: `nat`, `nttSj`(제목), `kotraNewsUrl`, `othbcDt`(발행일), `nttSn`(기사 ID), `kbc`(무역관), `regn`(권역),
본문은 `nttCn`(수집 원본 HTML) 또는 `body`(텍스트로 바꾼 본문) 중 하나. 한글 UTF-8(BOM 있어도 됨) 파일입니다.

## 실행 방법

명령은 저장소 루트에서 실행합니다. 먼저 단신속보를 수집합니다.

```powershell
python backend/app/scheduler/collectors/shortnews/shortnews_collect.py
```

수집 결과로 소량을 먼저 적재해 확인합니다 (CSV 앞쪽 = 최신 기사부터).

```powershell
python -m backend.app.scheduler.jobs.short_news_load --limit 20
```

전체를 적재합니다.

```powershell
python -m backend.app.scheduler.jobs.short_news_load
```

다른 CSV를 쓰려면 `--csv`로 경로를 줍니다.

```powershell
python -m backend.app.scheduler.jobs.short_news_load --csv "C:\경로\short_news.csv"
```

| 옵션 | 설명 |
|---|---|
| `--csv` | 입력 CSV 경로 (기본: 가장 최근 수집 결과 `shortnews_raw.csv`) |
| `--limit N` | CSV 앞쪽 N건만 처리 (시험용) |
| `--workers N` | 동시에 분석하는 기사 수 (기본 16). 아래 "소요 시간과 병렬 실행" 참고 |
| `--db-url` | SQLAlchemy DB URL (기본: `.env`의 `MYSQL_*`) |
| `--retag` | 이미 넣은 기사의 키워드 태그도 지금 사전 기준 주제어로 다시 넣음 (키워드 사전을 바꾼 뒤) |

중간에 멈추거나(Ctrl+C 포함) 일부 기사가 실패해도 **같은 명령을 다시 실행**하면 남은 기사만 이어서 처리합니다.
분석이 실패한 기사가 남아 있으면 종료 코드 1, `batch_run`은 FAILED로 기록되니 다시 실행하면 됩니다.

## 소요 시간과 병렬 실행

시간은 거의 전부 LLM 분석(기사당 1회, 요약·키워드)에서 씁니다. MySQL·Chroma 적재는 분석이 끝난 뒤 몇 분이면 끝납니다.

| 단계 | 시간 | 비고 |
|---|---|---|
| LLM 분석 (요약·키워드) | 기사당 1~2초 | 모든 기사 |
| MySQL 적재 | 전체 1~2분 | 500건씩 커밋 |
| 기사 임베딩 + Chroma 적재 | 전체 3~5분 | 100건씩 |

HS 단계가 있던 때 실측(2026-10-15): `--workers 8`에서 분당 약 180건(18,500건 약 1시간 40분).
HS 후보 검색·선택 호출이 빠져 기사당 호출이 1회로 줄었으므로, 기본값 16이면 전체 30~40분 정도로 예상합니다 (실측 아님).

```powershell
python -m backend.app.scheduler.jobs.short_news_load --workers 24
```

- 병렬 방식: 기사 단위로 스레드 N개가 동시에 OpenAI를 호출합니다. 결과 파일 기록은 한 곳에서만 합니다.
- 한도: 2026-10 기준 OpenAI 계정 한도는 `gpt-4.1-mini` 분당 5,000요청·200만 토큰입니다. workers 24까지는 한도 안입니다.
- 한도에 걸리면(429) 기사마다 최대 3번 재시도하고, 그래도 실패한 기사는 다음 실행에서 다시 분석합니다.
  로그에 "분석 실패"가 자주 보이면 workers를 줄여 다시 실행합니다.
- 실행 중에 workers를 바꾸려면 Ctrl+C로 멈추고 새 값으로 다시 실행합니다. 이미 분석한 기사는 캐시에 있어 다시 호출하지 않습니다.
- 오래 돌릴 때는 PC 절전을 꺼 둡니다. 절전으로 네트워크가 끊겨 멈춘 적이 있습니다 (다시 실행하면 이어짐).

## 처리 순서

### 1. CSV 읽기·정제·마스킹 → `body_masked`
- 본문이 `nttCn`이면 HTML을 텍스트로 바꿉니다 (문단·줄바꿈 태그 → 줄바꿈, `&nbsp;` 등 문자 참조 변환, `<공평경쟁…>` 같은 꺾쇠 글자는 남김).
  제목·국가·무역관·권역의 문자 참조(`&middot;` 등)도 원래 글자로 바꿉니다.
- 필수값(`nttSn`, 제목, 발행일, URL, 본문)이 없거나 날짜가 잘못된 행은 건너뜁니다. 같은 `nttSn`은 처음 행만 씁니다.
- 정제: `\xa0`·`\r` 정리, URL 제거, URL만 있던 출처 표시 줄(`* 원문기사 링크:`, `[원문링크:` 등) 삭제,
  본문 첫 줄이 제목 반복이면 삭제. `자료원: 비엔티안 타임즈`처럼 출처 이름이 있는 줄은 남깁니다.
- 마스킹: `문의 :` 뒤 → `[연락처]`, 이메일 → `[이메일]`, 전화번호 → `[전화번호]`. 공인 이름 등 기사 내용은 가리지 않습니다.

### 2. LLM 분석 (기사당 OpenAI 1회, `gpt-4.1-mini`)
- 제목 + 본문 앞 4,000자 → 요약(3문장 이내), 키워드(최대 5개, 명사형, 국가명·일반어 제외).
- 결과는 `shortnews_outputs/short_news_analysis.jsonl`에 기사마다 한 줄씩 쌓입니다. 다시 실행하면 이 파일에 있는 기사는 LLM을 부르지 않습니다.
- 3회 재시도 후에도 실패한 기사는 파일에 남지 않아 다음 실행에서 다시 분석합니다.
- HS 단계가 있던 때 만든 캐시 줄에는 `products`·`hs_codes`가 남아 있을 수 있지만 쓰지 않습니다.

### 3. 키워드 정리 → 주제어
- LLM 키워드를 해외시장뉴스와 같은 규칙(`keyword_clean.process_article`)으로 정리합니다: 정제 → 지역 분리 → 연도 분리 → 동의어 대표어 → 상위어.
  예: `LNG 수입` → `LNG수입`, `중국경제` → `경제`.
- 사전 파일이 없으면 경고를 남기고 정제·지역 분리만 합니다.
- 캐시에는 LLM 원래 키워드를 두고, 적재할 때마다 지금 사전으로 바꿉니다.
- 지금은 사전에 없는 말도 그대로 태그로 넣습니다. 최신 키워드 분류(`keyword_topic`, Chroma `news_keyword`)에서 **고르는** 방식은 후속 작업입니다.

### 4. MySQL 적재
- `article`에 없는 기사(`source_code='KOTRA_SHORT_NEWS'` + `external_id=nttSn`)만 넣습니다.
  `category`는 `short_news`, `title`·`published_date`·`source_url`·`body_masked`는 1단계 값입니다.
- 같은 트랜잭션에서 `article_tag`에 주제어 행(`keyword_text`)을 넣습니다.
- 500건마다 커밋합니다. 국가 태그는 아직 넣지 않습니다 (아래 "알려진 한계").

### 5. Chroma 적재 (`short_news` 컬렉션)
- DB에 있지만 컬렉션에 없는 기사만 추가합니다. id는 `KOTRA_SHORT_NEWS:<nttSn>`입니다.
- 임베딩: 제목 + 요약 + 키워드 (`text-embedding-3-large`)
- 문서: 마스킹한 본문 원문
- 메타데이터: `article_id`, `external_id`, `source_code`, `title`, `published_date`, `published_ymd`(정수, 기간 필터용), `source_url`,
  `nat`, `regn`, `kbc`, `summary`, `keywords`(주제어)

### 6. 실행 기록
`batch_run`에 한 건 남깁니다 (job `SHORT_NEWS_LOAD`, 대상 `article`, 기간 = 기사 발행일 범위, 수신 = 기사 수, 요청 = OpenAI 호출 수).

## 결과 확인

MySQL:

```sql
SELECT COUNT(*) FROM article WHERE source_code = 'KOTRA_SHORT_NEWS';
SELECT COUNT(*) FROM article_tag t JOIN article a USING (article_id)
WHERE a.source_code = 'KOTRA_SHORT_NEWS' AND t.keyword_text IS NOT NULL;
SELECT * FROM batch_run WHERE job_name = 'SHORT_NEWS_LOAD' ORDER BY run_id DESC LIMIT 5;
```

vector DB 검색 (where로 국가·기간 필터):

```powershell
python -c "from backend.app.scheduler.jobs.short_news_load import search; [print(round(d,3), m['title'], m['keywords']) for i,d,m in search('전기차 세금', n=3, where={'published_ymd': {'`$gte': 20260901}})]"
```

## 저장 결과

| 위치 | 내용 |
|---|---|
| `shortnews_outputs/short_news_analysis.jsonl` | 기사별 요약·키워드 (재실행 캐시) |
| `shortnews_outputs/logs/short_news_load_<시각>.log` | 실행 로그 |
| `chroma_data/` (저장소 루트) | Chroma `short_news` 컬렉션 |

모두 Git 커밋에서 제외됩니다.

## 처음부터 다시 적재하기

프롬프트를 바꿨거나 결과를 새로 만들어야 할 때 세 곳을 모두 지운 뒤 다시 실행합니다.
캐시 파일만 남기면 LLM을 다시 부르지 않고 DB·Chroma만 다시 채웁니다.

MySQL에서 실행합니다.

```sql
DELETE FROM article WHERE source_code = 'KOTRA_SHORT_NEWS';
```

PowerShell에서 Chroma 컬렉션과 캐시 파일을 지웁니다.

```powershell
python -c "import chromadb; from backend.app.scheduler.jobs.hs_code_embed import CHROMA_PATH; chromadb.PersistentClient(path=str(CHROMA_PATH)).delete_collection('short_news')"
Remove-Item "backend\app\scheduler\collectors\shortnews\shortnews_outputs\short_news_analysis.jsonl"
```

`article`을 지우면 `article_tag`는 FK(ON DELETE CASCADE)로 함께 지워집니다.

## 테스트

OpenAI·MySQL 없이 가짜 LLM·SQLite·메모리 Chroma로 정제·마스킹, 수집 CSV 읽기, 적재, 재실행, 실패 후 재시도, 키워드 다시 태깅을 확인합니다.

```powershell
python -m unittest backend/tests/test_short_news_load.py
```

## 알려진 한계

- 키워드는 LLM이 자유롭게 뽑은 말을 규칙으로 정리한 것이라 최신 키워드 분류와 맞지 않을 수 있습니다.
  최신 분류에서 고르는 방식으로 바꾸는 것이 다음 작업입니다.
- `--retag`는 DB 태그만 바꿉니다. 이미 넣은 Chroma 메타데이터의 `keywords`는 그대로라, 바꾸려면 컬렉션을 지우고 다시 적재합니다.
- 국가 태그는 아직 넣지 않습니다. CSV `nat`은 "러시아연방" 같은 한글 국가명이라 `country.name_ko`로 국가코드를 찾아야 합니다.
  2026-10 기준 단신속보 국가명 66개 중 58개가 국가 시드 `name_ko`와 같고, 8개(러시아연방, 아랍에미리트, 체코 등)는 표기가 달라 대응표가 필요합니다.
- 수집은 실행할 때마다 전체를 받습니다. 매일 실행하려면 새 기사만 받는 증분 수집과 스케줄 등록이 필요합니다.
