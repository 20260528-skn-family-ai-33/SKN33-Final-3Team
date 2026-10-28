# 해외시장뉴스 DB 적재·임베딩 사용 안내

> 이름 변경: `kotra_article`은 `total_article`로 변경되었습니다. 기존 테스트 결과는 당시 기록을 보존합니다.

KOTRA 해외시장뉴스 기사를 MySQL과 Chroma(vector DB)에 넣습니다.

| 단계 | 코드 | 하는 일 |
|---|---|---|
| ① 수집 | `scheduler/collectors/news/news_collect.py` | API에서 기사를 받아 `news_raw.csv`로 저장 ([NEWS_COLLECT_USAGE.md](../scheduler/collectors/news/NEWS_COLLECT_USAGE.md)) |
| ② 키워드 정리 | `services/news_keywords/keyword_clean.py` | 동의어·지역·품목을 정리해 `news_keywords_clean.csv` 생성 ([NEWS_KEYWORDS_USAGE.md](news_keywords/NEWS_KEYWORDS_USAGE.md)) |
| ③ MySQL 적재 | `scheduler/jobs/news_load.py` (변환: `services/news_load.py`) | 기사 → `article`, 키워드·국가·품목 → `article_tag`, 일별 기사 수 → `news_keyword_daily`·`news_product_daily` |
| ③-1 키워드 분류 적재 | `scheduler/jobs/keyword_topic_load.py` | 분류 틀 → `keyword_category`, 주제어 상태(분류·미분류·검수 대기·동의어/상위어) → `keyword_topic` |
| ④ 임베딩 | `scheduler/jobs/news_embed.py` | `article` 본문 조각 → Chroma `total_article`, 키워드 → `news_keyword` |

③은 ①·②의 결과 파일을, ③-1은 ③의 `article_tag`와 ②의 결과 파일을, ④는 ③의 MySQL 테이블을 읽습니다. **① → ② → ③ → ③-1 → ④ 순서**로 실행합니다.
② 키워드 정리와 ③-1은 동의어·상위어·분류표 사전(`data/news_keywords/`, git 제외)이 없으면 멈춥니다 — 새로 받은 저장소라면
[NEWS_KEYWORDS_USAGE.md](news_keywords/NEWS_KEYWORDS_USAGE.md)의 "다시 만들기"로 사전을 먼저 만듭니다.

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
기사·배치 관리자 ERD와 출처별·통합 집계 뷰를 적용할 때는
[SHORTNEWS_DB_USAGE.md의 마이그레이션 절차](SHORTNEWS_DB_USAGE.md#기사배치-관리자-스키마-적용)를 따릅니다.
기존 뉴스 적재기는 신규 기사의 필수 관리자 JSON을 공급하고 재적재 시 관리자 값을 보존합니다.

## ③ MySQL 적재 (`news_load`)

```powershell
# 기본 입력: data/news의 최신 news_raw_YYYYMMDD_YYYYMMDD.csv + data/news_keywords/news_keywords_clean.csv
python -m backend.app.scheduler.jobs.news_load

# 입력 파일 지정 (② keyword_clean.py에 넣은 것과 같은 원본을 씁니다)
# 키워드 결과가 일부 기간뿐이면(--since) 결과가 있는 기사의 태그만 바꾸고, 나머지 기사의 태그·집계는 그대로 둡니다
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
| | `external_id` ← `bbstxSn`, `title` ← `newsTitl`, `published_date` ← `othbcDt` 앞 10자리, `source_url` ← `kotraNewsUrl`, `category` ← `infoCl` | 제목 600자·URL 2,048자·분류 120자까지. 제목의 HTML 문자(`&#39;`·`&middot;`·`&lsquo;` 등, 2,431건)는 글자로 바꿈 (두 번 감싼 `&amp;#39;`까지) |
| | `body_masked` ← `newsBdt` | HTML 태그·스크립트를 벗기고 이메일·전화번호를 `[이메일]`·`[전화번호]`로 가림. 본문이 비면 요약(`cntntSumar`, 같은 방식으로 정리), 그것도 없으면 제목 |
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
- 키워드 결과가 있는 기사의 태그를 교체합니다. 동의어·분야 사전이 바뀌어 ②를 다시 돌려도 이전 자동 태그가 남지 않습니다. 관리자 ERD의 수동 지정 플래그가 켜진 국가·HS 태그는 보존합니다.
- 일별 집계는 입력 기사의 발행일(발행일이 바뀐 기사는 예전 날짜도)만 재계산합니다. `news_keyword_daily`는 시장뉴스+단신(구형 별칭 포함), `news_product_daily`는 시장뉴스만 셉니다. 신문 출처는 이 두 물리 집계에 포함하지 않습니다.
- 키워드 결과가 없는 기사(①과 ②의 입력이 다를 때, ②를 일부 기간만 돌렸을 때)는 기사만 갱신하고 기존 태그는 그대로 둡니다
  (새 기사면 태그 없음, 경고).
- `hs_code` 테이블이 비어 있으면 품목 태그·집계 0건으로 넘어갑니다 (경고). `hs_code_load` 후 다시 실행하면 채워집니다.
- 전체를 한 트랜잭션으로 넣습니다. 중간에 실패하면 모두 롤백되어 아무것도 반영되지 않습니다.
- 같은 저장소 경로·DB를 쓰는 해외시장뉴스/단신속보 적재기는 `data/news/load_locks/`의 공용 잠금으로
  트랜잭션을 차례로 실행합니다. 앞 작업의 커밋/롤백 후에 조회하므로 공용 일별 집계가 서로 덮어써지지 않습니다.
  다른 서버·별도 저장소 사본까지 조정하는 분산 잠금은 아닙니다.
- 실행할 때마다 `batch_run`에 한 건 기록합니다 (job `NEWS_LOAD`, 대상 `article`, 기간 = 원본 기사 발행일 범위, 수신 = 원본 기사 수, API 호출 0).
- 로그: 화면과 `data/news/logs/news_load_<시각>.log`.

### 관리자 ERD 호환

관리자 ERD 컬럼이 있으면 신규 기사에 `visibility_status=VISIBLE`, `first_ingested_at`,
필수 `admin_meta_json`을 넣고, 공통 배치 기록에 `detail_json={"schema_version":1}`을 넣습니다.
구형 스키마에서는 이 추가 컬럼을 사용하지 않습니다. 재적재는 기존 관리자 JSON·공개 상태·최초 시각을 유지합니다.
`classification_input`은 적재 시점의 정책 코드·CSV 사전·DB 정책을 저장한 파일 경로와 SHA-256이며,
초기 분류 상태는 PENDING/revision 0입니다. 스냅샷은 `data/news/classification_snapshots/`에 보관하므로
DB와 함께 백업해야 합니다. 상세 보존·실패 규칙은 [단신속보 사용 안내](SHORTNEWS_DB_USAGE.md#관리자-erd-호환)와 같습니다.

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

## ③-1 키워드 분류 적재 (`keyword_topic_load`) — `news_load` 다음에 실행

키워드 분류 결과(상위어를 키워드로 넣는 방식)를 `keyword_category`·`keyword_topic`에 넣습니다. 기사 수(PENDING 기준)를 `article_tag`에서 세므로 ③ `news_load` 다음에 실행합니다. 먼저 실행해도 키워드 결과 CSV에만 있는 키워드는 CSV 기사 수로 넣어 빠지지 않습니다(경고 표시).

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

정상이면 마지막에 `적재 완료: 분류 틀 23쌍 | 키워드 24,744행 · 지운 행 0`이 나옵니다. 확인 SQL:

```sql
SELECT status, COUNT(*) FROM keyword_topic GROUP BY status;   -- CLASSIFIED 9099 · PENDING 1952 · UNCLASSIFIED 12986 · SYNONYM 707
SELECT t.keyword_text, t.status, t.canonical_text, c.major_name, c.minor_name
FROM keyword_topic t LEFT JOIN keyword_category c ON c.category_id = t.category_id
WHERE t.keyword_text IN ('AI', '인공지능', 'K뷰티', '전기차보조금', '건강', '로보택시');
```

### 넣는 값 (팀 결정 2026-10-21)

| status | 기준 | 개수 |
|---|---|---|
| `SYNONYM` | 동의어 표기(`synonyms.csv`)와 하위어(`hypernyms.csv`). `canonical_text` = 최종 키워드 (K뷰티 → 뷰티, 전기차보조금 → 전기차) | 707 |
| `CLASSIFIED` | 최종 주제어 중 분야가 정해진 말 — 코드 안 규칙(`category_rules.classify`: 팀 분류 예외 · 규칙(일치·끝말·포함) · 규칙 수정 · LLM 판단). **추정도 확정** (팀 결정 2026-10-22) | 9,099 |
| `UNCLASSIFIED` | 어떤 규칙에도 안 걸리고 기사 3건 미만 | 12,986 |
| `PENDING` | 어떤 규칙·예외 목록에도 안 걸리고 기사 3건 이상 (관리자 분류 대상) | 1,952 |

- 팀이 분류표에서 미분류로 정한 말(65)도 규칙을 적용합니다 (팀 결정 2026-10-21). 기사 분야(`kw_category`)는 팀 결정을 그대로 따릅니다.
- `keyword_category`: 분류 틀 23쌍 (`category_rules.TAX`). 이미 있으면 번호를 그대로 둡니다.
- PENDING 기준 기사 수는 **DB `article_tag`의 키워드별 고유 기사 수(누적)**로 셉니다 (`--counts db`, 기본). `article_tag`가 비어 있으면 키워드 결과 CSV로 셉니다 (`--counts csv`).
- 관리자가 DB에서 바꾼 행(`synced_at`이 NULL이거나 `updated_at`보다 이름)은 덮어쓰지 않고 건너뜁니다 (경고 로그). csv에 먼저 반영하세요.
- 이번 결과에 없는 키워드(사전·데이터 변경으로 사라진 말)는 지웁니다. 관리자가 DB에서 바꾼 행은 남기고 경고합니다.
- DB 적재를 먼저 하고 결과 파일을 씁니다. 결과 엑셀이 열려 있어 저장하지 못해도 DB 적재는 그대로이고 경고만 남깁니다.
- 적재한 행은 `updated_at` = `synced_at` = 실행 시각. 전체를 한 트랜잭션으로 넣고 실패하면 롤백합니다.
- `batch_run`에는 기록하지 않습니다 (`target_table` 목록에 키워드 테이블이 없음).

### 결과 파일

실행할 때마다 만들어지는 확인용 파일입니다. 코드는 이 파일들을 읽지 않고(기준은 DB), 모두 git에서 제외합니다.

| 파일 | 내용 |
|---|---|
| `data/news_keywords/keyword_topic.csv` | 적재한 값 전체 (keyword_text · status · canonical_text · 대분류 · 중분류 · 기사수 · 이유) |
| `data/news_keywords/review/keyword_pending_review.xlsx` | 시트 `개수` · `PENDING` · `UNCLASSIFIED`: 키워드 · 기사수 · 이유 · 최근 기사 3건 · 관리자 분류 칸 |
| `data/news_keywords/logs/keyword_topic_load_<시각>.log` | 실행 로그 |

## ④ 임베딩 (`news_embed`)

`article`의 본문은 `NEWS_VECTOR_ROOT`(기본 `data/rag/index`)의 활성 세대 안 `total_article` 컬렉션에, 키워드(주제어)는 같은 root의 `keywords/news_keyword`에 넣습니다. `chroma_data` 직접 접근 예시는 아래에 남긴 과거 검증 기록에만 해당합니다.
관련 기사 찾기(RAG)와 비슷한 키워드 추천에 씁니다. **MySQL에는 있지만 컬렉션에 아직 없는 기사만 임베딩해 추가합니다.**

```powershell
# 비용 확인: OpenAI를 부르지 않고 새로 넣을 조각 수·글자 수만 출력
python -m backend.app.scheduler.jobs.news_embed --dry-run

# 수동 실행 (처음 한 번은 전체, 이후에는 새 기사·바뀐 기사만)
python -m backend.app.scheduler.jobs.news_embed

# 키워드(news_keyword)만 DB와 맞춤: 새 키워드 추가 · 기사 수 갱신 · 사라진 키워드 삭제 (기사 본문 조각은 건드리지 않음)
python -m backend.app.scheduler.jobs.news_embed --keywords-only

# 키워드 컬렉션만 지우고 다시 임베딩 (24,037개, 약 2~3분 · 약 20원)
python -m backend.app.scheduler.jobs.news_embed --keywords-only --rebuild

# 임베딩 모델을 바꿨을 때: 두 컬렉션을 지우고 전부 다시 임베딩 (--dry-run을 같이 주면 지우지 않고 개수만 셈)
python -m backend.app.scheduler.jobs.news_embed --rebuild
```

- 모델: `text-embedding-3-large`(3,072차원). `hs_code_embed`와 같은 모델이라 검색 질의 임베딩을 하나로 씁니다.
- 처음 전체 (2022.01~2026.10, 14,607건): 조각 105,272개(조각당 평균 약 843토큰) + 키워드 24,037개. 실제 사용량은
  약 8,890만 토큰 · **약 $11.5**, 실행 시간은 약 50분이었습니다(배치 100조각에 2초 안팎, 2026-10-22). 이후 매일은 새 기사만이라
  하루 약 30건 · 215조각 기준 약 $0.02입니다.
- 조각: 본문을 문단(줄) 단위로 약 1,000자씩, 앞 조각의 끝 150자를 겹쳐 나눕니다. 조각마다 머리줄을 붙입니다.
  ```
  [2022-01-03 | CZ | 외부전문가 기고] [기고] 체코에서 거주용 부동산 구매정보
  필자는 2010년부터 체코에서 현지 공인중개사로 …
  ```
- `total_article`: id는 `<article_id>:<조각 번호>`, 메타데이터는 `article_id`·`external_id`·`source_code`·`title`·`published_date`·
  `published_ymd`(정수, 기간 필터)·`category`·`countries`·`keywords`(목록, `$contains` 필터)·`chunk`·`body_hash`입니다.
- `news_keyword`: id·문서는 키워드 글자 그대로, 메타데이터는 `keyword`·`article_count`(그 키워드가 붙은 기사 수)입니다.
- 이미 있는 기사는 다시 임베딩하지 않습니다. 조각 문서가 바뀐 기사(제목·본문, 또는 머리줄의 발행일·국가·분류)와 조각이 일부만 들어간
  기사(중간에 멈춘 실행)만 그 기사의 조각을 지우고 다시 넣습니다 — 임베딩이 머리줄까지 포함한 문서로 계산되므로, 검색 결과 문서와 필터가
  같은 정보가 되게. 키워드만 바뀐 기사는 메타데이터만 갱신하고(OpenAI 호출 없음), 마지막 키워드를 지운 기사는 메타데이터에서도 지웁니다.
- 키워드는 DB와 맞춥니다: 기사 수만 바뀌면 메타데이터만 갱신하고, DB에서 사라진 키워드(사전이 바뀌어 동의어·하위어가 된 말)는 지웁니다.
  DB 키워드가 하나도 없으면 지우지 않습니다.
- MySQL에서 지워진 기사는 컬렉션에서 지우지 않습니다 (`hs_code_embed`와 같음). 최신으로 다시 만들려면 `--rebuild`를 실행합니다.
- `--dry-run`은 무엇도 지우거나 넣지 않습니다 (`--rebuild --dry-run`은 지운 뒤 새로 넣을 개수만 셈).
- 실패하면 이미 추가한 배치는 남고, 실패한 조각은 컬렉션에 없으므로 다음 실행에서 다시 추가됩니다. 분당 한도(429)는 자동으로 기다렸다 재시도합니다.
- `batch_run`에는 기록하지 않습니다(대상이 MySQL 테이블이 아님, `hs_code_embed`와 같음). 로그는 `data/news/logs/news_embed_<시각>.log`입니다.
- 로그에는 배치마다 OpenAI가 센 누적 토큰과 예상 비용(`PRICE_PER_M` = 100만 토큰당 $0.13)이 남습니다.
  예: `기사 조각 임베딩 10,000/58,755 | 토큰 8,331,144 (약 $1.08)`. OpenAI 가격이 바뀌면 `PRICE_PER_M`만 고칩니다.
- 컬렉션은 5,000개씩 나눠 읽습니다(`get_all`). Chroma(SQLite)는 한 번에 약 3만 2천 개를 넘게 읽으면
  `too many SQL variables`로 실패합니다. 검색에서 `article_id` 목록으로 거를 때도 한 번에 1,000개 이하로 넘깁니다.

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

예상 결과: `Ran 48 tests ... OK` (`test_news_load.py` 17 · `test_news_embed.py` 21 · `test_keyword_topic_load.py` 10, MySQL·OpenAI 없이 SQLite·가짜 임베딩·메모리 Chroma로 실행).
중간의 `ERROR`·`WARNING` 로그는 실패 상황(테이블 없음, 없는 국가 코드, OpenAI 일시 오류, 모델 불일치)을 일부러 만든 테스트의 로그입니다.

| 테스트 파일 | 확인하는 것 |
|---|---|
| `test_news_load.py` | 최신 원본 파일 고르기, HTML 벗기기·이메일·전화번호 가림(HS 범위·프로그램 이름 제외), 제목 HTML 문자 정리, 빈 본문 대체, 국가·전체 고유 기사 수, 기사·태그·집계·출처·batch_run 적재, 재실행 시 갱신·옛 태그 교체, 발행일 변경, 중복 ID, 키워드 결과 없는 기사, 일부 기간 결과여도 나머지 기사 태그 유지, 테이블 없음, 롤백 |
| `test_keyword_topic_load.py` | 상태 판정(분류·규칙(포함) 확정·3건 기준 PENDING), 동의어·하위어 → 최종 키워드, 분류 틀 23쌍·번호 유지, 재실행 갱신, 관리자 DB 수정 보존, 사라진 키워드 삭제(관리자 행은 보존), 결과 파일이 잠겨도 DB 적재, 사전이 없으면 적재 안 함, CSV에만 있는 키워드 포함, 없는 분야면 롤백 |
| `test_news_embed.py` | 조각 나누기(문단·겹침·긴 문단·빈 본문), 메타데이터, 재실행 시 추가 없음·바뀐 기사만 교체, 키워드 기사 수 갱신, 키워드만(--keywords-only), dry-run(--rebuild여도 안 지움), 사라진 키워드 삭제, 키워드만 바뀌면 메타데이터만 갱신·마지막 키워드 삭제 반영, 국가·발행일이 바뀌면 다시 임베딩, 일부만 들어간 기사 다시 넣기, 국가·키워드·기간 검색, 모델 불일치 시 --rebuild, 실패 배치 재처리, OpenAI 토큰·비용 집계, 컬렉션 나눠 읽기 |

## 실제 데이터 결과 (2022.01.03~2026.10.13, 14,607건)

`keyword_topic`(2026-10-22, 규칙·예외를 코드로 옮긴 사전): 24,744행 = CLASSIFIED 9,099 · PENDING 1,952 · UNCLASSIFIED 12,986 · SYNONYM 707, `keyword_category` 23행.
아래 `article`·`article_tag`·`news_keyword_daily`는 상위어 적용 후 재적재(2026-10-21) 결과입니다. 키워드 태그 24,057종이 모두 `keyword_topic`에 있고, SYNONYM 말은 태그에 남지 않습니다.

| 항목 | 결과 |
|---|---|
| `article` | 14,607건 (본문 최대 35,843자, 원본 HTML 최대 11MB) |
| `article_tag` | 86,109건 = 키워드 71,537 (CLASSIFIED 47,482 · UNCLASSIFIED 14,815 · PENDING 9,240) + 국가 14,572 + 품목 0 (`hs_code` 적재 전) |
| `news_keyword_daily` | 138,747행 = 국가별 70,923 + 전체 67,824. `article_counts.py`의 CSV 집계와 행·숫자 모두 일치 (상위어 적용 전: 142,209행) |
| `country` 에 없는 국가 코드 | 0개 |
| 재실행 | 기사 번호·행 수·집계 합계가 그대로 (batch_run만 1건씩 늘어남) |
| 소요 | 약 3분 (본문 HTML 정리가 대부분) |
| 원본과 전수 대조 (2026-10-22) | 기사·제목·발행일·URL·본문·분류, 기사별 키워드·국가 태그, 일별 집계, 키워드 분류 연결 24개 항목 모두 일치. 제목 HTML 문자 0건, 본문에 남은 이메일·전화번호 0건(가린 기사 594건 · 105건) |
| 국가가 없는 기사 | 55건 (모두 `KOTRA 본사`·`전세계` 등 국가가 정해지지 않은 기획리포트) |

Chroma (2026-10-22, `news_embed` 완료 후):

| 항목 | 결과 |
|---|---|
| `kotra_article` | 105,272조각 = 기사 14,607건 전부 (2022 22,690 · 2023 20,663 · 2024 20,551 · 2025 22,600 · 2026 18,768) |
| `news_keyword` | 24,037개 |
| 구조 점검 | id 형식 오류 0, 조각 번호 빠진 기사 0, 필수 메타데이터 없는 조각 0, 벡터 3,072차원·길이 1 |
| `--dry-run` | 새로/다시 넣을 기사 0건 (Chroma 문서·메타데이터가 MySQL과 같음) |
| 사용량 | 약 8,890만 토큰 · 약 $11.5 |

### 확인 명령 (Chroma)

저장소 루트에서 `python -c`로 실행합니다. PowerShell 5.1은 인자 안의 큰따옴표를 지우므로 코드에는 작은따옴표만 씁니다.

```powershell
# 1. 컬렉션 개수·설정 → kotra_article 105272 · news_keyword 24037, text-embedding-3-large · cosine
$code = @'
import chromadb
c = chromadb.PersistentClient(path='chroma_data')
for x in c.list_collections():
    print(x.name, x.count(), x.metadata)
'@
python -c $code

# 2. MySQL 기사와 Chroma 기사 비교 → 빠진 기사 0 · MySQL에 없는 기사 0
$code = @'
import chromadb
from sqlalchemy import text
from backend.app.db.session import make_engine
from backend.app.scheduler.jobs.news_embed import get_all
col = chromadb.PersistentClient(path='chroma_data').get_collection('kotra_article')
ids_chroma = {m['article_id'] for m in get_all(col, ['metadatas'])['metadatas']}
with make_engine().connect() as c:
    ids_db = set(c.execute(text('select article_id from article')).scalars())
print('MySQL', len(ids_db), '| Chroma', len(ids_chroma), '| 빠진 기사', len(ids_db - ids_chroma), '| MySQL에 없는 기사', len(ids_chroma - ids_db))
'@
python -c $code

# 3. 검색 (질문 하나만 임베딩, 비용 거의 없음) → 모두 VN · 2025-01-01 이후, 베트남 화장품·소비재 규제 기사
$code = @'
from backend.app.scheduler.jobs.news_embed import search
for id_, dist, doc, m in search('베트남 화장품 수입 규제가 강화되고 있다', n=5, country='VN', date_from='2025-01-01'):
    print(round(dist, 3), m['published_date'], m.get('countries'), m['title'][:45])
'@
python -c $code

# 4. 정확한 단어가 든 조각 → CBAM이 든 조각 299개 · 기사 99건
$code = @'
import chromadb
col = chromadb.PersistentClient(path='chroma_data').get_collection('kotra_article')
g = col.get(where_document={'$contains': 'CBAM'}, include=['metadatas'], limit=2000)
print('조각', len(g['ids']), '| 기사', len({m['article_id'] for m in g['metadatas']}))
'@
python -c $code
```

## 알려진 한계

- 품목 태그·집계는 `hs_code` 적재 후 채워집니다. 지금은 KOTRA HS 칸(`hs6`)만 쓰고, 키워드·제목으로 찾은 품목은 HS 사전 완료 후 추가합니다.
- 이름·회사명 같은 개인정보는 가리지 않습니다 (규칙으로 구분하기 어려움). 이메일·전화번호만 가립니다.
- 매일 자동 실행(스케줄러)은 ① 수집·② 키워드 정리와 함께 연결할 때 등록합니다. 지금은 수동 실행입니다.
- 단신속보(`collectors/shortnews`)는 `scheduler/jobs/shortnews_load.py`로 `KOTRA_SHORT_NEWS` 출처의 같은 테이블에 적재합니다. [SHORTNEWS_DB_USAGE.md](SHORTNEWS_DB_USAGE.md)를 참고하세요. 키워드 일별 집계는 시장뉴스와 단신만, 품목 일별 집계는 시장뉴스만 셉니다. 신문 출처는 출처별 조회 뷰에서 따로 셉니다.


### 공통 색인·집계 계약 보완 (2026-10-25)

공통 `news_embed.run_sources`와 예약 경로는 검증을 마친 본문 세대만 원자적으로 게시합니다. 위 구형 직접-client 예시와 달리 기본 본문은 단일 `total_article`을 검증 후 게시하며, 지정 출처의 숨김·삭제를 다음 세대에 반영하고 다른 출처는 보존합니다. 문서와 임베딩 입력은 동일한 발행일·국가·분류·제목 헤더+본문 청크(1000자/150자 겹침)입니다. 메타데이터만 달라지면 벡터를 재사용합니다. 키워드 전역 조회가 실패하면 기존 후보 삭제를 수행하지 않습니다.

기존 KOTRA 전용 두 뷰는 유지하고 출처별 뷰와 시장뉴스+단신 통합 뷰를 분리합니다. `news_keyword_daily`는 시장뉴스+단신(구형 출처 별칭 포함), `news_product_daily`는 시장뉴스만 집계하며 신문을 자동 편입하지 않습니다. 단신의 기존 수동 HS 태그는 SQL에 보존하지만 HS 집계·본문 벡터에 넣지 않습니다. 실패 기간을 0건으로 채우지 않으며 수집 상태는 실행 이력과 함께 해석해야 합니다.

집계 뷰 전환은 `db/mysql/migrations/20261023_admin_news_erd.sql`의 관리자 마이그레이션으로 적용합니다. 여섯 뷰 정의와 갱신된 SQL 지문을 함께 반영합니다. 이번 로컬 작업에서는 실제 MySQL에 적용하지 않았습니다.


### 통합 실행 계약 (2026-10-26)

기본 `news_embed.run`은 공통 세대를 게시하고 공통 키워드를 갱신합니다. 위 `chroma_data` 직접 클라이언트 예시는 구형 명시 주입 호환 경로입니다. 운영 기본·CLI `--keywords-only`·`run_sources`·`search_keywords`는 모두 `NEWS_VECTOR_ROOT` 또는 `data/rag/index` 아래 `keywords/news_keyword`를 사용합니다. 본문과 키워드의 root를 함께 보존해야 합니다.

root의 `database.json`에는 접속 자격 증명을 제외한 DB 식별 해시만 저장합니다. 다른 DB로 같은 root를 사용하면 `source_database_mismatch`, 이미 색인이 있지만 식별 기록이 없으면 `source_database_unbound`로 중단하며 자동 합치거나 초기화하지 않습니다. 기존 색인의 이관은 별도 검토 대상입니다.

신문·단신의 DB 확정 대표어는 파일 사전으로 다시 바꾸지 않습니다. 색인 작업은 사용한 snapshot을 성공·실패 시 모두 닫습니다. 생성한 OpenAI 제공자는 작업이 닫고, 명시 주입한 제공자는 호출자가 닫습니다.

사용량은 응답 검증 전에 기록합니다. `reported_responses`와 `unreported_calls`를 구분하며, 보고되지 않은 토큰 수를 확인된 0토큰·0원으로 간주하지 않습니다. 알려진 토큰의 비용은 추정치입니다. 단신 전체 사용량의 본문·키워드 단계와 `chroma_result.usage`는 같은 사용량이므로 합산하지 않습니다.

## 기존 본문 색인 이관

기본 본문은 `NEWS_VECTOR_ROOT` 또는 `data/rag/index`의 활성 세대 `total_article`, 공통 키워드는 같은 root의 `keywords/news_keyword`입니다. `chroma_data`는 단신 분류 후보와 명시적 구형 클라이언트 경로이며, 구형 본문을 자동 삭제·이관하지 않습니다.

`legacy_direct_index_migration_required`가 발생하면 [기존 본문 색인 이관 안내](../../../docs/NEWS_INDEX_MIGRATION.md)의 원본 writer 중지·검증 카탈로그 준비·dry-run·`--apply` 순서를 따릅니다. `python -m backend.app.scheduler.jobs.news_index_migrate --help`로 인자를 확인할 수 있습니다. 원본 파일은 읽기만 하고 임시 복제본에서 벡터를 배치 복사합니다. 모델 호출은 없으며 기존 ID·문서·벡터와 타 출처를 보존합니다. 형식·모델·카탈로그가 맞지 않으면 게시하지 않습니다.

실제 MySQL/RDS·운영 Chroma 이관 및 Windows 실행은 이번 검증에 포함되지 않습니다. 최초 수신 시각과 당시 분류 입력의 근거가 없는 기존 행은 현재 값으로 backfill하지 않습니다. 자세한 필드 의미·복구·제약도 위 안내에 기록했습니다.
