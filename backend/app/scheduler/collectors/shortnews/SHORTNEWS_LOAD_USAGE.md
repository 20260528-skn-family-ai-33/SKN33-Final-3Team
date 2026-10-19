# 단신속보 적재 코드 사용 안내

`backend/app/scheduler/jobs/short_news_load.py`는 단신속보 CSV를 정제·마스킹하고, LLM으로 요약·키워드·품목을 뽑아
HS 6단위 코드를 붙인 뒤 MySQL(`article`, `article_tag`)과 Chroma(`short_news` 컬렉션)에 적재합니다.

## 준비

| 항목 | 내용 |
|---|---|
| 패키지 | `python -m pip install -r backend/requirements.txt` |
| `.env` (저장소 루트) | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`, `OPENAI_API_KEY` |
| MySQL | `article`, `article_tag`, `hs_code`, `service_option`, `batch_run` 테이블. `hs_code`는 적재돼 있어야 합니다 |
| Chroma | 저장소 루트 `chroma_data/`에 `hs_code` 컬렉션 (`python -m backend.app.scheduler.jobs.hs_code_embed`로 생성) |
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

전체를 적재합니다. 18,607건 기준 약 2~3시간 걸립니다.

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

중간에 멈추거나(Ctrl+C 포함) 일부 기사가 실패해도 **같은 명령을 다시 실행**하면 남은 기사만 이어서 처리합니다.
분석이 실패한 기사가 남아 있으면 종료 코드 1, `batch_run`은 FAILED로 기록되니 다시 실행하면 됩니다.

## 소요 시간과 병렬 실행

시간은 거의 전부 2단계 LLM 분석에서 씁니다. 3·4단계(MySQL·Chroma 적재)는 분석이 끝난 뒤 몇 분이면 끝납니다.

| 단계 | 기사 1건당 | 비고 |
|---|---|---|
| LLM 추출 (요약·키워드·품목) | 1~2초 | 모든 기사. 요약을 써야 해서 가장 오래 걸림 |
| 품목명 임베딩 + HS 후보 검색 | 약 0.3초 | 품목이 있는 기사만 (약 70%) |
| LLM 선택 (HS 코드) | 0.5~1초 | 후보가 있는 기사만 |
| MySQL 적재 | - | 500건씩, 전체 1~2분 |
| 기사 임베딩 + Chroma 적재 | - | 100건씩, 전체 3~5분 |

실측(2026-10-15): `--workers 8`에서 200건에 약 66초(분당 약 180건) → 18,500건 약 1시간 40분.
동시 요청 수에 거의 비례해 빨라지므로 기본값 16이면 약 50분, 24면 약 35분이 예상됩니다.

```powershell
python -m backend.app.scheduler.jobs.short_news_load --workers 24
```

- 병렬 방식: 기사 단위로 스레드 N개가 동시에 OpenAI를 호출합니다. 결과 파일 기록은 한 곳에서만 하고, Chroma `hs_code` 조회만 순서대로 합니다.
- 한도: 현재 OpenAI 계정 한도는 `gpt-4.1-mini` 분당 5,000요청·200만 토큰입니다. workers 8일 때 분당 약 430요청·45만 토큰을 쓰므로
  24까지는 한도 안이고, 32 이상은 토큰 한도에 닿을 수 있습니다.
- 한도에 걸리면(429) 기사마다 최대 3번 재시도하고, 그래도 실패한 기사는 다음 실행에서 다시 분석합니다.
  로그에 "분석 실패"가 자주 보이면 workers를 줄여 다시 실행합니다.
- 실행 중에 workers를 바꾸려면 Ctrl+C로 멈추고 새 값으로 다시 실행합니다. 이미 분석한 기사는 캐시에 있어 다시 호출하지 않습니다.

LLM 호출을 기사당 1번으로 합치지 않는 이유: HS 후보는 1차 호출이 뽑은 품목명(예: LNG → 액화천연가스)으로 찾기 때문에
후보를 알기 전에는 2차 선택을 할 수 없습니다. 제목·본문으로 바로 후보를 찾으면 정확도가 크게 떨어졌습니다
(예: '갈륨 수출통제' → 크로뮴, '광섬유' → 부직포). 2차 호출은 짧은 응답이라 전체 시간의 일부이고, 병렬 수를 늘리는 쪽이 효과가 큽니다.

## 처리 순서

### 1. CSV 읽기·정제·마스킹 → `body_masked`
- 본문이 `nttCn`이면 HTML을 텍스트로 바꿉니다 (문단·줄바꿈 태그 → 줄바꿈, `&nbsp;` 등 문자 참조 변환, `<공평경쟁…>` 같은 꺾쇠 글자는 남김).
  제목·국가·무역관·권역의 문자 참조(`&middot;` 등)도 원래 글자로 바꿉니다.
- 필수값(`nttSn`, 제목, 발행일, URL, 본문)이 없거나 날짜가 잘못된 행은 건너뜁니다. 같은 `nttSn`은 처음 행만 씁니다.
- 정제: `\xa0`·`\r` 정리, URL 제거, URL만 있던 출처 표시 줄(`* 원문기사 링크:`, `[원문링크:` 등) 삭제,
  본문 첫 줄이 제목 반복이면 삭제. `자료원: 비엔티안 타임즈`처럼 출처 이름이 있는 줄은 남깁니다.
- 마스킹: `문의 :` 뒤 → `[연락처]`, 이메일 → `[이메일]`, 전화번호 → `[전화번호]`. 공인 이름 등 기사 내용은 가리지 않습니다.

### 2. LLM 분석 (기사당 OpenAI 1~3회, `gpt-4.1-mini`)
1. **추출**: 제목 + 본문 앞 4,000자 → 요약(3문장 이내), 키워드(최대 5개), 품목명(최대 5개).
   품목명은 HS 품목표에서 찾기 쉬운 일반명으로 바꿉니다 (예: LNG → 액화천연가스).
   서비스·인프라·산업 분야·포괄 범주(농산물 등)는 품목에서 뺍니다.
2. **HS 후보 찾기** (품목이 있을 때만, 동의어 처리)
   - `hs_code` 최신 판 6단위의 품목명·원문에 품목명이 들어 있는 코드
   - 4단위 이름에만 품목명이 있으면 그 아래 6단위 (예: 갈륨 → 8112 → 8112xx)
   - Chroma `hs_code` 컬렉션 유사도 상위 5개 (6단위)
3. **선택**: LLM이 위 후보 안에서만 최대 3개를 고릅니다. 후보에 없는 코드는 버립니다 (LLM이 코드를 만들지 않음).

결과는 `shortnews_outputs/short_news_analysis.jsonl`에 기사마다 한 줄씩 쌓입니다. 다시 실행하면 이 파일에 있는 기사는 LLM을 부르지 않습니다.
3회 재시도 후에도 실패한 기사는 파일에 남지 않아 다음 실행에서 다시 분석합니다.

### 3. MySQL 적재
- `article`에 없는 기사(`source_code='KOTRA_SHORT_NEWS'` + `external_id=nttSn`)만 넣습니다.
  `category`는 `short_news`, `title`·`published_date`·`source_url`·`body_masked`는 1단계 값입니다.
- 같은 트랜잭션에서 `article_tag`에 키워드 행(`keyword_text`)과 HS 행(`hs_code`, `hs_ver`)을 넣습니다. 한 행에는 하나만 채웁니다.
- 500건마다 커밋합니다. 국가 태그는 `country` 테이블이 채워진 뒤 따로 추가합니다.

### 4. Chroma 적재 (`short_news` 컬렉션)
- DB에 있지만 컬렉션에 없는 기사만 추가합니다. id는 `KOTRA_SHORT_NEWS:<nttSn>`입니다.
- 임베딩: 제목 + 요약 + 키워드 (`text-embedding-3-large`)
- 문서: 마스킹한 본문 원문
- 메타데이터: `article_id`, `external_id`, `title`, `published_date`, `published_ymd`(정수, 기간 필터용), `source_url`,
  `nat`, `regn`, `kbc`, `summary`, `keywords`, `hs_codes`, `hs_ver`

### 5. 실행 기록
`batch_run`에 한 건 남깁니다 (job `SHORT_NEWS_LOAD`, 대상 `article`, 기간 = 기사 발행일 범위, 수신 = 기사 수, 요청 = OpenAI 호출 수).

## 결과 확인

MySQL:

```sql
SELECT COUNT(*) FROM article WHERE source_code = 'KOTRA_SHORT_NEWS';
SELECT SUM(keyword_text IS NOT NULL) AS keywords, SUM(hs_code IS NOT NULL) AS hs FROM article_tag;
SELECT * FROM batch_run WHERE job_name = 'SHORT_NEWS_LOAD' ORDER BY run_id DESC LIMIT 5;
```

vector DB 검색 (where로 국가·기간 필터):

```powershell
python -c "from backend.app.scheduler.jobs.short_news_load import search; [print(round(d,3), m['title'], m['hs_codes']) for i,d,m in search('전기차 세금', n=3, where={'published_ymd': {'`$gte': 20260901}})]"
```

## 저장 결과

| 위치 | 내용 |
|---|---|
| `shortnews_outputs/short_news_analysis.jsonl` | 기사별 요약·키워드·품목·HS 코드 (재실행 캐시) |
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

OpenAI·MySQL 없이 가짜 LLM·SQLite·메모리 Chroma로 정제·마스킹, 적재, 재실행, 실패 후 재시도를 확인합니다.

```powershell
python -m unittest backend/tests/test_short_news_load.py
```

## 알려진 한계

- HS 태깅 오탐이 일부 남습니다 (예: 물가 기사에 석탄, 전력 인프라 기사에 전기에너지 271600).
  기준을 바꾸려면 `short_news_load.py`의 `EXTRACT_PROMPT`·`PICK_PROMPT`를 고친 뒤 처음부터 다시 적재합니다.
- 키워드는 범용 규칙(명사형, 국가명·일반어 제외)으로 뽑습니다. 팀 분류 조건이 정해지면 `EXTRACT_PROMPT`의 keywords 항목을 바꿉니다.
- HS 판은 `hs_code` 테이블의 가장 큰 `hs_ver`(현재 HS2022)를 씁니다.
- 국가 태그는 아직 넣지 않습니다 (`country` 테이블이 비어 있음).
