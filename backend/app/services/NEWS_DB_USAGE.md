# 해외시장뉴스 DB 적재·임베딩 사용 안내

KOTRA 해외시장뉴스 기사를 MySQL과 Chroma(vector DB)에 넣습니다.

| 단계 | 코드 | 하는 일 |
|---|---|---|
| ① 수집 | `scheduler/collectors/news/news_collect.py` | API에서 기사를 받아 `news_raw.csv`로 저장 ([NEWS_COLLECT_USAGE.md](../scheduler/collectors/news/NEWS_COLLECT_USAGE.md)) |
| ② 키워드 정리 | `services/news_keywords/keyword_clean.py` | 동의어·지역·품목을 정리해 `news_keywords_clean.csv` 생성 ([NEWS_KEYWORDS_USAGE.md](news_keywords/NEWS_KEYWORDS_USAGE.md)) |
| ②-1 키워드 분류 적재 | `scheduler/jobs/keyword_topic_load.py` | 분류 틀 → `keyword_category`, 주제어 상태(분류·미분류·검수 대기·동의어/상위어) → `keyword_topic` |
| ③ MySQL 적재 | `scheduler/jobs/news_load.py` (변환: `services/news_load.py`) | 기사 → `article`, 키워드·국가·품목 → `article_tag`, 일별 기사 수 → `news_keyword_daily`·`news_product_daily` |
| ④ 임베딩 | `scheduler/jobs/news_embed.py` | `article` 본문 조각 → Chroma `kotra_article`, 키워드 → `news_keyword` |

②-1·③은 ①·②의 결과 파일을, ④는 ③의 MySQL 테이블을 읽습니다. 순서대로 실행합니다.

## 준비

저장소 루트에서 패키지를 설치하고 `.env`에 MySQL·OpenAI 값을 넣습니다 (`.env.sample` 참고).

```powershell
python -m pip install -r backend/requirements.txt
```

```
MYSQL_HOST=localhost           # ③ 적재, ④ 임베딩이 읽는 DB
MYSQL_PORT=3306
MYSQL_USER=...
MYSQL_PASSWORD=...
MYSQL_DATABASE=trade_target
OPENAI_API_KEY=sk-...          # ④ 임베딩
```

테이블은 미리 만들어 두어야 합니다. 적재 코드는 테이블을 만들지 않고, 없으면 오류 로그를 남기고 끝납니다.
빈 DB라면 `db/mysql/init/`의 SQL을 `01_schema.sql`부터 번호 순서대로 대상 DB에서 실행합니다.

## ②-1 키워드 분류 적재 (`keyword_topic_load`)

키워드 분류 결과(상위어를 키워드로 넣는 방식)를 `keyword_category`·`keyword_topic`에 넣습니다. 기사(`article`)와는 따로 실행할 수 있습니다.

### 임시로 실행해 보기

```powershell
# 1. 테이블 만들기 (처음 한 번). PyCharm Database 콘솔에서 trade_target을 고르고 파일 전체 실행(Ctrl+A → 실행)하거나
#    mysql CLI에서: USE trade_target; source db/mysql/init/07_keyword_topic.sql;
#    이미 있으면 "Table ... already exists"가 나오므로 다시 실행하지 않습니다.

# 2. 미리 보기: DB에 넣지 않고 상태별 개수와 결과 파일만 만듦
python -m backend.app.scheduler.jobs.keyword_topic_load --dry-run

# 3. 적재
python -m backend.app.scheduler.jobs.keyword_topic_load
```

정상이면 마지막에 `적재 완료: 분류 틀 23쌍 | 키워드 24,744행`이 나옵니다. 확인 SQL:

```sql
SELECT status, COUNT(*) FROM keyword_topic GROUP BY status;   -- CLASSIFIED 9166 · PENDING 1948 · UNCLASSIFIED 12943 · SYNONYM 687
SELECT t.keyword_text, t.status, t.canonical_text, c.major_name, c.minor_name
FROM keyword_topic t LEFT JOIN keyword_category c ON c.category_id = t.category_id
WHERE t.keyword_text IN ('AI', '인공지능', 'K뷰티', '전기차보조금', '건강', '로보택시');
```

### 넣는 값 (팀 결정 2026-10-21)

| status | 기준 | 개수 |
|---|---|---|
| `SYNONYM` | 동의어 표기(`synonyms.csv`)와 하위어(`hypernyms.csv`). `canonical_text` = 최종 키워드 (K뷰티 → 뷰티, 전기차보조금 → 전기차) | 687 |
| `CLASSIFIED` | 최종 주제어 중 분야가 정해진 말 (분류표·규칙(일치)·규칙 수정·팀 검수) + **규칙(포함) 추정도 확정** (팀 검수 정확도 약 89%)<br>+ 팀이 미분류로 정한 말 중 규칙 결과가 있는 41개 (LLM 판단 40 · 규칙 수정 1) | 9,166 |
| `UNCLASSIFIED` | 어떤 규칙에도 안 걸리고 기사 3건 미만 | 12,943 |
| `PENDING` | 어떤 규칙에도 안 걸리고 기사 3건 이상 (관리자 분류 대상, 팀 미분류 중 규칙 없는 24개 포함) | 1,948 |

- 팀이 분류표에서 미분류로 정한 말(65)도 규칙을 적용합니다 (팀 결정 2026-10-21). 기사 분야(`kw_category`)는 팀 결정을 그대로 따릅니다.
- `keyword_category`: 분류 틀 23쌍 (`category_rules.TAX`). 이미 있으면 번호를 그대로 둡니다.
- 관리자가 DB에서 바꾼 행(`synced_at`이 NULL이거나 `updated_at`보다 이름)은 덮어쓰지 않고 건너뜁니다 (경고 로그). csv에 먼저 반영하세요.
- 적재한 행은 `updated_at` = `synced_at` = 실행 시각. 전체를 한 트랜잭션으로 넣고 실패하면 롤백합니다.
- `batch_run`에는 기록하지 않습니다 (`target_table` 목록에 키워드 테이블이 없음).

### 결과 파일

| 파일 | 내용 |
|---|---|
| `data/news_keywords/keyword_topic.csv` | 적재한 값 전체 (keyword_text · status · canonical_text · 대분류 · 중분류 · 기사수 · 이유) |
| `data/news_keywords/review/키워드_미분류_PENDING.xlsx` | 시트 `개수` · `팀 미분류 재분류`(65: 바뀐 상태·분야·이유) · `PENDING`(1,948) · `UNCLASSIFIED`(12,943): 키워드 · 기사수 · 이유 · 최근 기사 3건 · 관리자 분류 칸 |
| `data/news_keywords/logs/keyword_topic_load_<시각>.log` | 실행 로그 |

## ③ MySQL 적재 (`news_load`)

```powershell
# 기본 입력: data/news의 최신 news_raw_YYYYMMDD_YYYYMMDD.csv + data/news_keywords/news_keywords_clean.csv
python -m backend.app.scheduler.jobs.news_load

# 입력 파일 지정 (② keyword_clean.py에 넣은 것과 같은 원본을 씁니다)
python -m backend.app.scheduler.jobs.news_load --raw data/news/news_raw_20220101_20261013.csv --clean data/news_keywords/news_keywords_clean.csv
```

| 옵션 | 설명 |
|---|---|
| `--raw` | 원본 CSV (`bbstxSn`·`newsTitl`·`newsBdt`·`othbcDt`·`kotraNewsUrl`·`infoCl`·`cntntSumar` 필요) |
| `--clean` | 키워드 결과 CSV (`bbstxSn`·`othbcDt`·`kw_topic`·`country_all`·`hs6` 필요) |
| `--db-url` | SQLAlchemy DB URL (기본: `.env`의 `MYSQL_*` 값) |

### 넣는 값

| 테이블 | 컬럼 ← 원본 | 규칙 |
|---|---|---|
| `article` | `source_code` ← `KOTRA_MARKET` | 출처가 `service_option`에 없으면 처음 실행 때 SOURCE 행으로 자동 등록 (`batch_log.ensure_source`) |
| | `external_id` ← `bbstxSn`, `title` ← `newsTitl`, `published_date` ← `othbcDt` 앞 10자리, `source_url` ← `kotraNewsUrl`, `category` ← `infoCl` | 제목 600자·URL 2,048자·분류 120자까지 |
| | `body_masked` ← `newsBdt` | HTML 태그·스크립트를 벗기고 이메일·전화번호를 `[이메일]`·`[전화번호]`로 가림. 본문이 비면 요약(`cntntSumar`), 그것도 없으면 제목 |
| `article_tag` | `keyword_text` ← `kw_topic` | 동의어 정리 후 주제어. 사용자 블락은 저장값을 바꾸지 않고 보여 줄 때 거름 (`keyword_block.py`) |
| | `country_code` ← `country_all` | `country` 테이블에 있는 코드만 (없는 코드는 빼고 경고) |
| | `hs_code`·`hs_ver` ← `hs6` | `hs_code` 테이블에 있는 (코드, 판)만. 판은 발행일 기준 (`hs_ver_for`: 2022~2027 → HS2022) |
| `news_keyword_daily` | 날짜 × 키워드 × 국가 | 고유 기사 수 (`DISTINCT article_id`). 국가별 행 + 국가 조건 없는 전체 행(`country_code` NULL) |
| `news_product_daily` | 날짜 × 품목 × 국가 | HS6 태그와 그 앞 4자리 HS4(사전에 있을 때)를 각각 셈 |

- 이메일 가림: `a@b.com`, 원문이 일부 가린 `sales@tco-****.com`, 도메인 끝이 빠진 소문자 주소(`info@nexus4013`).
  `Tech@SG`·`Autism@Work`·`BE@RBRICK`처럼 대문자가 있는 프로그램·상품 이름은 그대로 둡니다.
- 전화번호 가림: `02-3460-7114`, `+82-2-3460-7114`, `(647) 299-9203`. HS 코드 범위(`6210.4010-6210`, `1902-1904-1905`)는
  구분기호가 섞였거나 모두 4자리라 그대로 둡니다. 날짜(`2022.10.13`)·연도 범위(`2022-2023`)도 가리지 않습니다.
- 본문의 `<SaigonTex 2022 현장>` 같은 꺾쇠 글은 사진 설명이라 남습니다 (HTML 태그가 아님).

### 적재 동작

- 같은 `(source_code, external_id)` 기사가 있으면 제목·발행일·URL·본문·분류를 갱신합니다 (`article_id` 유지). 원본에 같은 ID가 두 번 있으면 마지막 행.
- 입력 기사들의 태그는 지우고 새로 넣습니다. 동의어·분야 사전이 바뀌어 ②를 다시 돌려도 이전 태그가 남지 않습니다.
- 일별 집계는 입력 기사의 발행일(발행일이 바뀐 기사는 예전 날짜도)만 지우고, 그날 DB에 있는 **모든 출처** 기사로 다시 셉니다.
  나중에 단신속보가 같은 테이블에 들어와도 함께 셉니다.
- 키워드 결과가 없는 기사(①과 ②의 입력이 다를 때)는 기사만 넣고 태그는 비웁니다 (경고).
- `hs_code` 테이블이 비어 있으면 품목 태그·집계 0건으로 넘어갑니다 (경고). `hs_code_load` 후 다시 실행하면 채워집니다.
- 전체를 한 트랜잭션으로 넣습니다. 중간에 실패하면 모두 롤백되어 아무것도 반영되지 않습니다.
- 실행할 때마다 `batch_run`에 한 건 기록합니다 (job `NEWS_LOAD`, 대상 `article`, 기간 = 원본 기사 발행일 범위, 수신 = 원본 기사 수, API 호출 0).
- 로그: 화면과 `data/news/logs/news_load_<시각>.log`.

### 확인 (MySQL)

```sql
SELECT COUNT(*) FROM article;  -- 기사 수
SELECT a.published_date, a.title,
       GROUP_CONCAT(t.keyword_text SEPARATOR ', ') AS 키워드, GROUP_CONCAT(t.country_code SEPARATOR ', ') AS 국가
FROM article a LEFT JOIN article_tag t ON t.article_id = a.article_id
GROUP BY a.article_id ORDER BY a.published_date DESC LIMIT 5;
-- 기간 키워드 순위 (전체 국가)
SELECT keyword_text, SUM(day_count) AS 기사수 FROM news_keyword_daily
WHERE country_code IS NULL AND metric_date BETWEEN '2026-10-07' AND '2026-10-13'
GROUP BY keyword_text ORDER BY 기사수 DESC LIMIT 10;
```

## ④ 임베딩 (`news_embed`)

`article`의 본문을 조각으로 나눠 Chroma(`chroma_data/`, git 제외)의 `kotra_article` 컬렉션에, 키워드(주제어)를 `news_keyword` 컬렉션에 넣습니다.
관련 기사 찾기(RAG)와 비슷한 키워드 추천에 씁니다. **MySQL에는 있지만 컬렉션에 아직 없는 기사만 임베딩해 추가합니다.**

```powershell
# 비용 확인: OpenAI를 부르지 않고 새로 넣을 조각 수·글자 수만 출력
python -m backend.app.scheduler.jobs.news_embed --dry-run

# 수동 실행 (처음 한 번은 전체, 이후에는 새 기사·바뀐 기사만)
python -m backend.app.scheduler.jobs.news_embed

# 키워드(news_keyword)만 임베딩: 기사 본문 조각은 건드리지 않음 (24,057개, 약 20원)
python -m backend.app.scheduler.jobs.news_embed --keywords-only

# 임베딩 모델을 바꿨을 때: 두 컬렉션을 지우고 전부 다시 임베딩
python -m backend.app.scheduler.jobs.news_embed --rebuild
```

- 모델: `text-embedding-3-large`(3,072차원). `hs_code_embed`와 같은 모델이라 검색 질의 임베딩을 하나로 씁니다.
- 처음 전체 (2022.01~2026.10, 14,607건): 조각 105,272개 + 키워드 24,226개, 약 9,550만 자. 첫 임베딩 비용은 약 9~15달러(추정),
  요청 1,053번이라 OpenAI 계정의 분당 한도에 따라 30분~2시간 걸립니다. 이후 매일은 새 기사만이라 비용이 거의 없습니다.
- 조각: 본문을 문단(줄) 단위로 약 1,000자씩, 앞 조각의 끝 150자를 겹쳐 나눕니다. 조각마다 머리줄을 붙입니다.
  ```
  [2022-01-03 | CZ | 외부전문가 기고] [기고] 체코에서 거주용 부동산 구매정보
  필자는 2010년부터 체코에서 현지 공인중개사로 …
  ```
- `kotra_article`: id는 `<article_id>:<조각 번호>`, 메타데이터는 `article_id`·`external_id`·`source_code`·`title`·`published_date`·
  `published_ymd`(정수, 기간 필터)·`category`·`countries`·`keywords`(목록, `$contains` 필터)·`chunk`·`body_hash`입니다.
- `news_keyword`: id·문서는 키워드 글자 그대로, 메타데이터는 `keyword`·`article_count`(그 키워드가 붙은 기사 수)입니다.
- 이미 있는 기사는 다시 임베딩하지 않습니다. 제목·본문이 바뀐 기사(`body_hash`가 다름)만 그 기사의 조각을 지우고 다시 넣습니다.
  키워드는 기사 수만 바뀌면 메타데이터만 갱신합니다 (OpenAI 호출 없음).
- MySQL에서 지워진 기사·키워드는 컬렉션에서 지우지 않습니다 (`hs_code_embed`와 같음). 최신으로 다시 만들려면 `--rebuild`를 실행합니다.
- 실패하면 이미 추가한 배치는 남고, 실패한 조각은 컬렉션에 없으므로 다음 실행에서 다시 추가됩니다. 분당 한도(429)는 자동으로 기다렸다 재시도합니다.
- `batch_run`에는 기록하지 않습니다(대상이 MySQL 테이블이 아님, `hs_code_embed`와 같음). 로그는 `data/news/logs/news_embed_<시각>.log`입니다.

### 검색

```python
from backend.app.scheduler.jobs.news_embed import search, search_keywords

# 질의와 가까운 기사 조각 [(id, 거리, 문서, 메타데이터)] (거리가 작을수록 가까움, cosine)
search("베트남 전기차 배터리 시장", n=5)
search("반도체 수출 규제", n=5, country="US", keyword="반도체", date_from="2026-01-01", date_to="2026-10-13")

# 뜻이 가까운 키워드 [(키워드, 거리, 기사 수)]
search_keywords("배터리", n=10)
```

한 기사에서 여러 조각이 나올 수 있으므로 기사 목록으로 보여 줄 때는 `article_id`로 묶고, 제목·태그는 MySQL `article`·`article_tag`에서 가져옵니다.

## 테스트

```powershell
python -m unittest backend/tests/test_news_load.py backend/tests/test_news_embed.py backend/tests/test_keyword_topic_load.py
```

예상 결과: `Ran 32 tests ... OK` (`test_news_load.py` 14 · `test_news_embed.py` 12 · `test_keyword_topic_load.py` 6, MySQL·OpenAI 없이 SQLite·가짜 임베딩·메모리 Chroma로 실행).
중간의 `ERROR`·`WARNING` 로그는 실패 상황(테이블 없음, 없는 국가 코드, OpenAI 일시 오류, 모델 불일치)을 일부러 만든 테스트의 로그입니다.

| 테스트 파일 | 확인하는 것 |
|---|---|
| `test_news_load.py` | 최신 원본 파일 고르기, HTML 벗기기·이메일·전화번호 가림(HS 범위·프로그램 이름 제외), 빈 본문 대체, 국가·전체 고유 기사 수, 기사·태그·집계·출처·batch_run 적재, 재실행 시 갱신·옛 태그 교체, 발행일 변경, 중복 ID, 키워드 결과 없는 기사, 테이블 없음, 롤백 |
| `test_keyword_topic_load.py` | 상태 판정(분류·규칙(포함) 확정·팀 미분류·3건 기준 PENDING), 동의어·하위어 → 최종 키워드, 분류 틀 23쌍·번호 유지, 재실행 갱신, 관리자 DB 수정 보존, 없는 분야면 롤백 |
| `test_news_embed.py` | 조각 나누기(문단·겹침·긴 문단·빈 본문), 메타데이터, 재실행 시 추가 없음·바뀐 기사만 교체, 키워드 기사 수 갱신, 키워드만(--keywords-only), dry-run, 국가·키워드·기간 검색, 모델 불일치 시 --rebuild, 실패 배치 재처리 |

## 실제 데이터 결과 (2022.01.03~2026.10.13, 14,607건)

`keyword_topic`(2026-10-21, 상위어 적용 후): 24,744행 = CLASSIFIED 9,166 · PENDING 1,948 · UNCLASSIFIED 12,943 · SYNONYM 687, `keyword_category` 23행.
아래 `article`·`article_tag`·`news_keyword_daily`는 상위어 적용 후 재적재(2026-10-21) 결과입니다. 키워드 태그 24,057종이 모두 `keyword_topic`에 있고, SYNONYM 말은 태그에 남지 않습니다.

| 항목 | 결과 |
|---|---|
| `article` | 14,607건 (본문 최대 35,843자, 원본 HTML 최대 11MB) |
| `article_tag` | 86,244건 = 키워드 71,672 (CLASSIFIED 47,659 · UNCLASSIFIED 14,776 · PENDING 9,237) + 국가 14,572 + 품목 0 (`hs_code` 적재 전) |
| `news_keyword_daily` | 138,894행 = 국가별 71,045 + 전체 67,849. `article_counts.py`의 CSV 집계와 행·숫자 모두 일치 (상위어 적용 전: 142,209행) |
| `country` 에 없는 국가 코드 | 0개 |
| 재실행 | 기사 번호·행 수·집계 합계가 그대로 (batch_run만 1건씩 늘어남) |
| 소요 | 약 3분 (본문 HTML 정리가 대부분) |

## 알려진 한계

- 품목 태그·집계는 `hs_code` 적재 후 채워집니다. 지금은 KOTRA HS 칸(`hs6`)만 쓰고, 키워드·제목으로 찾은 품목은 HS 사전 완료 후 추가합니다.
- 이름·회사명 같은 개인정보는 가리지 않습니다 (규칙으로 구분하기 어려움). 이메일·전화번호만 가립니다.
- 매일 자동 실행(스케줄러)은 ① 수집·② 키워드 정리와 함께 연결할 때 등록합니다. 지금은 수동 실행입니다.
- 단신속보(`collectors/shortnews`)는 출처 코드를 정한 뒤 같은 테이블에 넣습니다. 일별 집계는 이미 모든 출처를 함께 셉니다.
