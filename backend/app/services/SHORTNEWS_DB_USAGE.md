# 단신속보 MySQL 적재

`scheduler/collectors/shortnews/shortnews_collect.py`가 저장한 원본 CSV 또는 JSON을
`scheduler/jobs/shortnews_load.py`로 MySQL에 적재합니다.
기존 해외시장뉴스의 HTML 정제·연락처 마스킹·upsert·태그 생성·일별 집계를 재사용합니다.
기사 내용이나 제목으로 대상을 선별하지 않고 지정한 파일의 모든 행을 처리합니다.

## 2026-10-25 리뷰 반영과 공통 본문 색인 인계

- 제목은 기존 뉴스의 `plain_title` 경로로 HTML 엔티티와 공백을 정리합니다.
  이 공용 함수는 600자로 자르므로 단신 적재기는 정제 후 600자 초과를 먼저 검증하여 조용히 잘린 제목을 적재하지 않습니다.
  본문 HTML 정제와 제목 엔티티 정제는 별개이며 CSV/JSON에 같은 규칙을 적용합니다.
- 분석 캐시와 테스트 파일은 UTF-8을 명시합니다. 원자 저장 실패 시 임시 파일을 닫은 뒤 삭제합니다.
  작업이 생성한 OpenAI·Chroma 클라이언트와 DB 엔진은 종료하고, 주입된 클라이언트는 호출자가 종료합니다.
  프로세스 테스트는 임시 폴더와 로컬 작업만 사용합니다. Windows의 `terminate()`는 SIGTERM 핸들러를
  호출하지 않으므로 해당 POSIX 신호 시험은 Windows에서 제외하고 동일 종료 핸들러의 완료 대기를 별도로 검증합니다.
  Mac의 통과 결과가 Windows 실행 결과를 의미하지는 않습니다.
- `news_keyword`는 실제 `article_tag`에 등장한 키워드와 기사 수를 제공하는 인덱스입니다.
  `keyword_topic` 전체 후보와 범위·메타데이터가 달라 후보 인덱스로 대체하지 않습니다.
  별도 후보 인덱스의 기존·완료·정리 전 검증은 5,000개 페이지로 읽고 모델·차원·분류 버전을 확인합니다.

단신 파이프라인은 `shortnews_vectors.sync_body`에서 공통
`news_embed.run_sources(['KOTRA_SHORT_NEWS'], db_url=..., root=..., embed=..., progress=...)`를 호출합니다.
본문은 검증 후 원자 게시하는 세대별 단일 `kotra_article`에 저장하며, 문서와 임베딩 입력 모두
발행일·국가·원천 분류·제목 헤더 + 정제 본문 청크(1000자/150자 겹침)를 사용합니다.
다른 출처는 보존하며 단신은 HS 태그를 새로 만들거나 본문 메타데이터에 넣지 않습니다.
DB에서 확정한 단신 대표어는 파일 사전으로 다시 바꾸지 않고 목록형 `keywords`로 저장합니다.
국가는 목록형 `countries`입니다. 기존 `short_news` 본문 컬렉션에는 새로 쓰지 않습니다.

요약은 분석 캐시의 `analysis.summary_sentences`와 실행 로그 폴더의
`shortnews_analysis_results.json`에 출처·기사 ID·분류 버전·본문 해시와 함께 보존합니다.
공통 본문 컬렉션의 메타데이터에는 요약을 넣지 않습니다.
`news_keyword`는 전체 DB 기사 태그를 기준으로 동기화하며 분류 후보 인덱스와 구분합니다.
기존 뉴스 실행 경로와 공통 실행 경로는 같은 키워드 저장소 잠금을 사용합니다.

본문 기본 경로는 `NEWS_VECTOR_ROOT` 또는 `data/rag/index`, 공통 키워드는 같은 본문 root의 `keywords` 아래입니다.
`--vector-root`를 명시하면 본문 세대와 공통 키워드(`root/keywords`)를 함께 격리합니다.
`--chroma-path`는 단신 분류 후보 인덱스 경로입니다. 기본 `news_embed.run()`은 공통 본문 세대를 게시하고 같은 root의 공통 키워드를 갱신합니다. 공개 `search()`는 그 활성 세대를 읽습니다. 명시적으로 주입한 구형 클라이언트만 호환 경로로 남습니다.
구형 직접 색인 또는 호환되지 않는 기존 세대는 전환 필요 오류로 실패하며 자동 이관·삭제하지 않습니다.
운영 저장소 이관은 별도 검증이 필요합니다.

공개 검색 예: `news_embed.search(query, root=vector_root, source_codes=['KOTRA_SHORT_NEWS'])`.
저장과 같은 root를 사용하고, source 필터는 top-k 이전에 적용합니다. `ALL`은 준비된 출처만 검색하며
명시 요청한 출처가 아직 준비되지 않았으면 `requested_source_not_ready`로 실패합니다.
국가·키워드·발행일 필터와 본문 레코드만 조회하는 필터를 적용하며 HS 태그 유무로 단신을 제외하지 않습니다.
이 helper는 기사 단위가 아닌 가까운 본문 조각을 반환합니다. 관련 단신의 상위 노출은 합성 벡터로 검증했지만,
관련성 임계값을 임의로 추가하지 않았으므로 n을 늘리면 무관 기사도 반환될 수 있습니다.
관리자 HIDDEN은 색인 재생성 후 제외됩니다. 검색 직전 실시간 DB 노출 조회는 구현하지 않았습니다.
명시 client 주입은 기존 뉴스 컬렉션 시험·호환 경로이며, 기본 검색은 구형 컬렉션에 자동 폴백하거나 빈 컬렉션을 만들지 않습니다.

공통 반환의 `complete/planned/partial/failed`를 검증합니다. MySQL 성공 후 벡터 실패는 일일 결과
`PARTIAL`로 남기고 완료 날짜로 기록하지 않아 재시도합니다. progress 값은 복사해 기록하며 최종 반환값을 기준으로 완료를 판단합니다.


## 실행

### 매일 자동 수집·적재

기존 통합 스케줄러는 `SHORTNEWS_DAILY_ENABLED=true`일 때만 `shortnews_daily`와 시작 복구 작업을 등록합니다.
기본값은 꺼짐입니다. 루트 `.env` 또는 프로세스 환경변수에 설정하며, 환경변수가 우선합니다.
`true`, `1`, `yes`, `on`(대소문자 무관)만 활성화 값이고 그 외에는 등록하지 않습니다.
**매일 오전 3시(Asia/Seoul)에 전날 단신속보의 전체 페이지를 수집하고 전체 본문 요약·최신 DB 키워드 선택 후 MySQL·Chroma에 적재**합니다.
루트 `.env`에 `shortnews`, `OPENAI_API_KEY`, `MYSQL_*` 설정과 DB 테이블을 준비한 뒤 실행합니다.

```bash
SHORTNEWS_DAILY_ENABLED=true python -m backend.app.scheduler.main
```

- 스케줄러 프로세스가 계속 실행 중이어야 합니다. 등록/import 시 API·DB를 호출하지 않으며,
  시작 10초 후 `shortnews_startup` 작업이 전날까지의 미완료 날짜를 점검합니다.
- 작성일 검색은 API의 `search2=YYYYMMDD`를 사용합니다. 제목 조건이나 페이지 수 제한은 추가하지 않습니다.
  날짜 형식은 [공공데이터포털 단신속보 명세](https://www.data.go.kr/data/15122662/openapi.do)의 요청 예시를 따릅니다.
- 실행 시작 시 KST 전날을 한 번 확정하며, 수집한 원본 CSV를 `shortnews_pipeline`으로 넘깁니다.
  본문 전체의 모든 구간으로 최신 DB 분류 후보를 조회하고, 본문 전체를 LLM에 전달합니다.
  팀원과 같은 `gpt-4.1-mini`로 최대 3문장 요약·최대 5개 후보 대표어를 선택합니다.
  후보 밖 단어·중복·잘린 응답은 실패 처리하며 기사를 제외하지 않습니다.
  요약은 분석 캐시와 실행별 JSON, 선택 키워드는 MySQL `article_tag` 및 공통 본문 Chroma 메타데이터에 저장합니다.
  분석 캐시는 본문·제목·분류 내용·모델·처리 규칙으로 구분하므로 원문이나 분류가 바뀌면 재분석합니다.
- 원본의 `othbcDt`를 조회 날짜와 대조합니다. 날짜 불일치·누락·잘못된 날짜가 하나라도 있으면
  그 날짜의 적재 전체를 중단하고 원본과 `date_errors.json`을 보존합니다. 오류 행을 조용히 제외하지 않습니다.
- 출처·기사 ID의 유일성과 upsert로 기사 행의 중복 생성을 막습니다. 별도 실행 환경에서는 같은 기사의
  반복 수집·분석·갱신이 가능하며, 분석 캐시가 공유되지 않으면 LLM 비용도 반복 발생할 수 있습니다.
- 동일 출력 루트 잠금, DB별 `data/shortnews/locks/<DB 식별값>.lock`, `max_instances=1`로 중복 실행을 막습니다.
  같은 저장소 경로에서 같은 DB로 실행하면 출력 폴더를 달리 지정해도 동시에 적재하지 않습니다.
  다른 서버나 별도 저장소 사본까지 조정하는 분산 잠금은 아닙니다.
- 해외시장뉴스와 단신속보의 적재기는 `data/news/load_locks/<DB 식별값>.lock`도 공유합니다.
  같은 DB의 기사·태그·공용 일별 집계 트랜잭션은 앞 작업의 커밋/롤백 후에 시작합니다.
  같은 저장소의 공유 잠금을 사용하는 작업끼리는 오래된 조회 결과로 상대 출처의 집계를 덮어쓰지 않습니다.
  수집은 이 공용 잠금 밖에서 수행하며 기존 예약 시간은 변경하지 않습니다.
- 결과는 `data/shortnews/daily/<대상 날짜>/<실행 ID>/`에 원본·수집 통계·`job_result.json`·적재 로그로 남습니다.
- 수집 완료+MySQL·Chroma 저장 성공은 `SUCCESS`, 수집 미완료 또는 MySQL 성공 후 벡터·후속 처리 실패는 `PARTIAL`, MySQL 완료 전 오류는 `FAILED`입니다.
  DB 커밋 후 Chroma 실패도 날짜 완료로 기록하지 않으며 다음 실행에서 같은 기사 ID로 복구합니다.
  `pipeline_result.json`에는 분석·MySQL·Chroma 단계별 결과를 남깁니다.
  API 수집 예외나 수집 통계 누락 시 이전 실행 파일을 대신 적재하지 않습니다. 0건이어도 조회 날짜로 적재 이력을 남깁니다.
- `state.json`의 버전 3 구조에 DB별·날짜별 상태와 처리 규칙 버전(`pipeline_revision`)을 저장합니다. DB 식별값은 접속 대상의 SHA-256이며
  사용자명·비밀번호·접속 URL을 저장하지 않습니다. 한 번의 복구 실행은 시작할 때 확정한 DB를 계속 사용합니다.
  DB가 바뀌면 기존 운영 시작일부터 새 DB로 재처리하고, 원래 DB로 돌아오면 그 DB의 진행 상태를 사용합니다.
  버전 1·2 기록 또는 출처·본문 처리 규칙 버전이 다른 완료 기록은 한 번 재처리하여 새 형식으로 전환합니다.
  현재 `v4-common-body-no-hs` 규칙으로 전환 시 기존 운영 범위의 완료 날짜도 한 번 재처리하여 과거 단신속보의 HS 집계 기여분을 제외합니다.
  이때 기사·기존 HS 태그는 삭제하지 않고 유효한 분석 캐시를 재사용합니다. 운영 범위 밖 날짜는 자동 재처리하지 않습니다.
  새 규칙으로 검증된 완료 기록은 다음 실행에 유지합니다. 이후 출처·본문 계약을 변경하면 `PIPELINE_REVISION`도 갱신해야 합니다.
  정기 실행·재시작 때 실패/미완료/RUNNING 상태와
  운영 시작일 이후 빠진 날짜를 모두 다시 처리합니다. 특정 날짜가 실패해도 나머지 날짜는 계속 처리합니다.
  날짜별 수집 완료와 DB·Chroma 저장 성공이 모두 확인된 날짜만 SUCCESS로 저장합니다.
- 처음 실행할 때 과거 기록이 없으면 전날부터 운영을 시작합니다. 이전 버전의 날짜별 결과 폴더가 있으면
  가장 이른 날짜부터 복구하며, 구버전 성공 기록도 대상 DB와 날짜 검증을 위해 한 번 재확인합니다.
  첫 운영 이전의 이력 없는 기간은 추측해서 수집하지 않습니다.
- 이전 페이지와 같은 기사 ID 묶음이 다시 나타나면 순서·내용 변경 여부와 무관하게 순환으로 감지해 미완료로 종료합니다.
  일부 기사가 겹치거나 처음 보는 ID 조합인 페이지는 계속 수집합니다.
  저장된 해당 날짜 기사는 적재하고 PARTIAL 상태를 남겨 다음 정기 실행·재시작 때 다시 조회합니다.
- 전날은 매 실행 재조회하므로 자정 직후 시작 복구가 성공해도 오전 3시 정기 실행에서 다시 확인합니다.
  전날보다 이전인 복구 날짜는 완료되면 자동 재조회하지 않습니다. 명시적 `--date`는 완료일도 강제로 다시 조회하며,
  같은 기사는 기존 upsert 규칙에 따라 갱신됩니다. API가 완료 판정 후 뒤늦게 공개하는 기사나
  총건수에 드러나지 않는 목록 교체까지 탐지하는 기능은 아닙니다.
- 재개 상태는 API 호출 전에, 완료 상태는 DB 적재 후에 원자적으로 저장합니다. DB 적재 후 상태 저장이
  중단되면 다음 실행에서 upsert로 재처리합니다. 상태 파일 손상 시 임의 초기화하지 않고 실패로 알립니다.
- 날짜별 폴더·결과 파일 저장 오류는 해당 날짜를 미완료로 남기고 뒤 날짜 처리를 계속합니다.
  DB 저장 후 결과 파일이나 체크포인트 저장만 실패해도 재시도 대상으로 남습니다.
  공통 상태 파일을 처음부터 읽거나 저장할 수 없으면 API 호출 전에 실패합니다.

```bash
# 전날까지 미완료 날짜를 지금 복구
python -m backend.app.scheduler.jobs.shortnews_daily

# 실패일·미실행일 재실행
python -m backend.app.scheduler.jobs.shortnews_daily --date 2026-10-22
```

수동 실행은 `--env`, `--output-dir`, `--db-url`도 지원합니다.
`SUCCESS`만 종료 코드 0이며, 불완전 수집·실패·실행 중 중복 호출은 1입니다.

### AWS 상시 운영 준비

`deploy/shortnews/trade-agent-shortnews.service`는 Linux EC2의 systemd용 템플릿입니다.
현재 AWS 서버는 아직 없으므로 실제 설치·부팅 복구·상시 운영은 미검증 상태입니다.
실제 경로와 계정에 맞춰 `WorkingDirectory`, `ExecStart`, `User`, `Group`, `ReadWritePaths`를 지정합니다.
`.env`와 RDS CA 파일을 서버에 별도로 배치하고 `MYSQL_SSL_CA`를 서버의 인증서 경로로 설정합니다.
`data/shortnews`의 상태·캐시 및 `chroma_data`는 재시작·배포 뒤에도 유지되는 저장 위치를 사용합니다.
DB 보안 그룹 접속과 KOTRA·OpenAI HTTPS 연결이 필요합니다.

```bash
# 서버 저장소에서 실행 환경 준비
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
mkdir -p data/shortnews chroma_data
.venv/bin/python -m backend.app.scheduler.shortnews_worker --check
```

`shortnews_worker`는 기존 `shortnews_daily.register_jobs`를 사용해 정기·시작 복구 작업을 등록합니다.
전용 워커와 수동 실행은 `SHORTNEWS_DAILY_ENABLED`의 영향을 받지 않습니다.
활성화 스위치와 파일 잠금은 여러 서버의 동시 실행을 조정하는 분산 잠금이 아닙니다.
통합 스케줄러를 운영할 경우에는 기존 `backend.app.scheduler.main`을 사용합니다.
템플릿은 오류 종료 시 재시작하며, 종료 신호를 받으면 진행 작업을 기다립니다.
이번 로컬 검증은 합성 입력과 가상 작업으로 종료 핸들러의 완료 대기·잠금 해제를 확인합니다.
실제 자식 프로세스 시험과 AWS의 systemd 설치·재부팅 검증은 실행하지 않습니다.
실제 서버 준비 후 서비스 설치·시작·재시작과 전날 작업 결과를 확인해야 운영 검증이 완료됩니다.
상태는 `systemctl status trade-agent-shortnews`와 `journalctl -u trade-agent-shortnews`,
`data/shortnews/daily/latest_run.json`에서 확인합니다.

### 저장된 원본의 요약·분류·Chroma까지 재실행

```bash
.venv/bin/python -m backend.app.scheduler.jobs.shortnews_pipeline --raw "<수집실행폴더>/shortnews_raw.csv"
```

현재 DB 분류로 다시 선택하고 DB 키워드·공통 본문 Chroma 문서 및 메타데이터를 함께 갱신합니다.
입력·분류가 같으면 분석을 재사용하고, Chroma 내용도 같으면 재임베딩하지 않습니다.
MySQL 단독 적재는 아래 `shortnews_load` 명령을 사용합니다.

### 저장된 파일만 적재

저장소 루트에서 실행합니다. 기존 `backend/requirements.txt`의 의존성을 사용합니다.
DB 연결은 루트 `.env`의 `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`,
`MYSQL_DATABASE`를 읽으며 동일 이름의 환경변수가 우선합니다.
RDS 등 TLS 인증서 검증을 사용하는 연결은 `MYSQL_SSL_CA`에 CA 파일을 지정합니다.
상대 경로는 읽는 환경 파일 기준으로 해석하며, 인증서와 서버 호스트 이름을 모두 검증합니다.
`MYSQL_SSL_CA`가 없으면 기존 연결 옵션을 유지합니다. `env`와 `.env`는 서로 다른 파일이므로
기본 실행에는 `.env`를 준비하거나 일일 작업의 `--env`로 실제 파일을 지정해야 합니다.
테이블은 기존 DDL 또는 관리자 ERD에 맞게 미리 준비되어 있어야 합니다.
이 명령은 테이블 생성·변경, API 수집, 원문 링크 크롤링, 임베딩을 실행하지 않습니다.

```bash
# <수집실행폴더>를 실제 수집 결과 폴더명으로 변경합니다.
python -m backend.app.scheduler.jobs.shortnews_load --raw "backend/app/scheduler/collectors/shortnews/shortnews_outputs/<수집실행폴더>/shortnews_raw.csv"

# 동일 수집 결과의 JSON으로도 실행할 수 있습니다.
python -m backend.app.scheduler.jobs.shortnews_load --raw "backend/app/scheduler/collectors/shortnews/shortnews_outputs/<수집실행폴더>/shortnews_raw.json"

# 별도 정제 결과가 있는 경우
python -m backend.app.scheduler.jobs.shortnews_load --raw "data/shortnews/shortnews_raw.csv" --clean "data/shortnews/shortnews_clean.csv"
```

`--db-url`로 SQLAlchemy 연결 URL을 지정하거나 `--log-dir`로 로그 폴더를 변경할 수 있습니다.
기본 로그는 `data/shortnews/logs/shortnews_load_<시각>.log`에 남고 성공은 종료 코드 0, 실패는 1입니다.
`--raw`는 필수입니다. 여러 수집 폴더 중 특정 파일을 임의로 선택하지 않습니다.

## 원본 → DB 매핑

### 최신 분류 후보 인덱스 준비

일일 파이프라인과 인덱스 준비 명령은 실제 Chroma 저장 폴더의 `short_news_keywords.lock`을 공유합니다.
인덱스 작성부터 후보 조회·MySQL·Chroma 저장·최신 분류 재확인·정리까지 잠금을 유지합니다.
다른 작업이 사용 중이면 실패로 반환하며 일일 작업은 다음 실행에서 재시도합니다.
전체 처리 성공 후에만 이전 `short_news_keywords_<버전>` 컬렉션을 정리합니다.
인덱스 단독 준비 명령은 색인 완료와 DB 분류 재확인 후 정리합니다.
이름·모델·차원·분류 버전이 단신속보 인덱스 계약과 일치하는 컬렉션만 삭제하며,
`short_news`, `news_keyword` 및 식별되지 않은 컬렉션은 보존합니다.
실패·미완성 색인은 기존 버전을 삭제하지 않습니다. 정리는 Chroma 컬렉션 삭제이며 디스크 파일의 즉시 축소를 보장하지 않습니다.

```bash
python -m backend.app.scheduler.jobs.shortnews_keyword_index
```

DB의 `keyword_topic`, `keyword_category`를 같은 트랜잭션으로 읽고 전체 키워드를 임베딩합니다.
분류 상태에 따른 제외 없이 원래 상태와 동의어의 최종 대표어·분야를 함께 기록합니다.
모델은 기존 뉴스와 같은 `text-embedding-3-large`이며 기본 저장소는 `chroma_data/`입니다.
`--env`, `--chroma-path`로 경로를 지정할 수 있습니다.
`short_news_keywords_<분류 내용 해시>` 컬렉션을 사용하며 기존 `news_keyword`는 수정하지 않습니다.
동일 분류로 재실행하면 완료 키워드를 재사용하고 실패한 배치 이후부터 이어서 처리합니다.
분류 내용이 달라지면 다른 컬렉션을 생성하며, 완료 직전 DB 버전을 다시 확인합니다.
이 명령은 기사 요약·키워드 선택·기사 Chroma 적재를 실행하지 않습니다.

### 기사 필드

| 원본/설정 | DB | 처리 |
|---|---|---|
| `KOTRA_SHORT_NEWS` | `article.source_code` | 해외시장뉴스 `KOTRA_MARKET`과 구분; `service_option`에 없으면 SOURCE로 등록 |
| `nttSn` | `article.external_id` | 문자열 ID 유지; 앞자리 0 보존 |
| `nttSj` | `article.title` | 제목 저장 |
| `othbcDt` | `article.published_date` | ISO 날짜·날짜/시간의 앞 10자리 또는 YYYYMMDD; 수집일로 대체하지 않음 |
| `kotraNewsUrl` | `article.source_url` | API가 제공한 링크 저장 |
| `body` 또는 `nttCn` | `article.body_masked` | 텍스트 `body` 우선, 없으면 `nttCn` HTML을 텍스트로 변환한 뒤 이메일/전화번호 마스킹; 본문이 비면 전체 적재 실패 |
| 분류 미제공 | `article.category` | NULL; 지역 `regn`을 기사 분야로 오인하지 않음 |
| `nat` | `article_tag.country_code` | DB 국가 코드·국가명 및 기존 지역 사전 국가 별칭으로 매핑 |

파일 읽기의 필수 컬럼은 `nttSn, othbcDt`입니다. 누락되거나 null인 선택 필드는 CSV·JSON 모두 빈 문자열로 읽습니다.
적재 시에는 `body` 또는 HTML을 제거한 `nttCn`에 비어 있지 않은 본문이 반드시 있어야 합니다.
본문 누락이 한 행이라도 있으면 기사·태그·집계를 변경하지 않고 입력 전체를 실패시킵니다.
일일 작업은 원본을 보존하고 해당 날짜를 재시도 대상으로 남기며, 제목으로 본문을 대체하거나 행을 제외하지 않습니다.
CSV는 UTF-8/UTF-8 BOM, JSON은 수집기에서 생성한 기사 객체 목록을 받습니다.
`kbc`, `regn`, 첨부파일 등 별도 대상 컬럼이 없는 원본 정보는 수집 파일에 그대로 남습니다.
팀원 적재기와 같은 `KOTRA_SHORT_NEWS` 출처 및 `body` → `nttCn` 본문 선택 순서를 사용합니다.
`body`는 이미 텍스트인 값으로 취급하며 HTML 파서로 다시 변환하지 않습니다. `smmarCn`은 본문으로 사용하지 않습니다.
원문 사이트 본문을 추가 수집하지 않으며, 원본 응답의 추가 필드는 그대로 보존합니다.
적재 트랜잭션에서 기존 `KOTRA_SHORTNEWS` 기사의 출처만 `KOTRA_SHORT_NEWS`로 전환합니다.
`article_id`, 연결된 참조·태그·관리자 정보는 유지하고 영향받는 날짜의 집계를 다시 계산합니다.
조회 결과가 0건이어도 기존 출처 전환을 수행하며, 이후 적재·집계 오류가 나면 전환까지 함께 롤백합니다.
이미 두 출처에 같은 기사 ID가 있으면 기존 행을 임의 병합·삭제하지 않고 충돌 오류로 적재를 중단합니다.
이 경우 기존 기사·관리자 정보·외부 참조를 확인하여 통합해야 하며, 해당 실행을 성공으로 기록하지 않습니다.

## 선택 정제 결과

`--clean`의 필수 컬럼은 `nttSn, kw_topic, country_all`입니다.
국가·키워드의 여러 값은 기존 뉴스와 같이 `|`로 구분합니다.
예시는 실제 기사 원문이 아닌 입력 형식 설명입니다.

```csv
nttSn,kw_topic,country_all
001,AI|반도체,US|CN
```

**단신속보는 HS 태그 적재 및 HS 기반 품목 집계를 제공하지 않습니다.**
일일 파이프라인과 수동 정제 파일 적재에 동일하게 적용합니다.
과거 파일의 추가 `hs6` 컬럼은 무시하며, 값이 있으면 경고와 `ignored_hs_rows`에 기록합니다.
기사·국가·키워드 적재는 계속 진행합니다.

- MySQL 단독 적재기 `shortnews_load`는 키워드를 자동 추출하지 않습니다.
  일일 `shortnews_pipeline`에서 최신 DB 후보 중 키워드를 선택합니다.
- 정제 행이 있으면 키워드·국가 태그를 교체합니다. 빈 값은 해당 종류의 태그를 제거합니다.
- 정제 행이 없는 기사는 원본 `nat`로 국가만 갱신하고 기존 키워드를 보존합니다.
- 국가 코드는 실제 `country`에 존재하는 코드만 연결합니다. 미매핑은 기록하며 기사 자체는 적재합니다.
- 기존 단신속보 HS 태그와 관리자 정보는 삭제·변경하지 않습니다. 보존된 HS 태그도 품목 집계에는 포함하지 않습니다.
- 일별 키워드 집계는 MARKET·단신의 고유 기사만 다시 셉니다(신문 자동 포함 없음). 품목 집계에서는 단신속보의 현재·구형 출처를 제외하고
  MARKET 출처만 계산합니다. 발행일이 바뀌면 이전 날짜와 새 날짜를 모두 재계산합니다.
  결과의 `product_daily`는 해당 날짜에 재계산한 **KOTRA 시장뉴스의 공용 집계 행 수**이며 단신속보 품목 수가 아닙니다.
- 변경 코드만 배포해도 과거 모든 집계가 즉시 바뀌지는 않습니다. 운영 범위는 처리 규칙 전환으로 재처리하며,
  범위 밖 과거 집계는 필요한 날짜를 명시적으로 재처리해야 합니다.

## 관리자 ERD 호환

2026-10-22 관리자 전체 ERD와 같은 폴더의 부속 생성 SQL에 있는 필수 JSON 구조를 반영했습니다.
실제 DB 컬럼을 조회하므로 관리자 컬럼 추가 전 기존 스키마에서도 실행할 수 있습니다.
공용 ORM이나 DB 스키마를 자동으로 변경하지 않습니다.

| 추가 컬럼 | 처리 |
|---|---|
| `article.visibility_status` | 신규 기사는 VISIBLE; 재적재 시 기존 값 유지 |
| `article.first_ingested_at` | 신규 기사 적재 시 KST 기록; 재적재 시 최초 값 유지 |
| `article.admin_meta_json` | 신규 기사에 schema_version·visibility·수동 태그 플래그·classification_input·classification_state 초기값 저장; 기존 JSON 유지 |
| `batch_run.detail_json` | schema_version=1과 성공 결과 또는 실패 예외 종류 기록 |

`admin_meta_json.country_tags_manual=true`이면 국가 태그를 덮어쓰지 않습니다.
기존 HS 태그는 수동 여부와 관계없이 보존하되 단신속보 집계에서는 제외합니다.
MySQL 단독 적재기의 초기 관리자 분류 상태는 PENDING/revision 0입니다.
일일 파이프라인은 요약·키워드 선택과 태그 갱신을 수행하지만 관리자 분류 상태·이력 테이블은 변경하지 않습니다.
`classification_input`에는 적재 시점의 정책 코드·기본 CSV 사전·DB의 `keyword_category`와
`keyword_topic` 내용을 보관한 JSON 파일 경로와 SHA-256을 저장합니다.
스냅샷은 `data/news/classification_snapshots/<SHA-256>.json`에 생성되며 재적재 시 기존 기사의
스냅샷 참조·최초 수신 시각은 유지합니다. 사전/정책 테이블이 없으면 그 부재도 null로 기록합니다.
외부 정제 파일을 과거에 생성한 정책을 소급 복원하는 기능은 아닙니다.
이 디렉터리는 Git 제외 대상이며, DB와 함께 보존·백업해야 합니다.
스냅샷 저장 실패 또는 기존 파일의 해시 불일치 시 기사 트랜잭션은 롤백합니다.
관리자 분류 근거(`article_classification`)와 다른 관리자 테이블은 이 적재 작업에서 변경하지 않습니다.
`news_keyword_daily`·`news_product_daily` 적재 규칙은 기존과 같습니다.
조회용 `v_article_day`·`v_article_country_day`는 아래 관리자 마이그레이션 적용 후
공개 KOTRA 시장뉴스(`KOTRA_MARKET`, `VISIBLE`)만 집계합니다. 단신 포함 두 KOTRA 집계는 `v_article_integrated_*`, 출처별 조회는 `v_article_source_*`로 분리합니다.

### 기사·배치 관리자 스키마 적용

기준은 `관리자_ERD_SQL_기존배치유지_20261022`입니다.
`db/mysql/migrations/20261023_admin_news_erd.sql`은 기사 관리 컬럼 3개,
`batch_run`의 `detail_json`·작업/재시도 연결, `article_classification`,
외래키 대상 `async_job`, 관련 트리거 3개와 기사 집계 뷰 6개를 적용합니다.
회원·채팅·리포트 등 다른 관리자 기능의 스키마 변경은 포함하지 않습니다.
기존 기사에 알 수 없는 최초 적재 시각·분류 스냅샷을 만들어 넣지 않습니다.

저장소 루트에서 실행합니다. 이전 날짜의 마이그레이션이 필요하면 먼저 적용하고,
기사 집계 뷰 최종 정의는 이 마이그레이션으로 적용합니다.

```bash
# 조회만: 적용할 단계 확인
.venv/bin/python db/mysql/migrations/apply_admin_news_erd.py
# 적용: 기존 행·DDL 백업 후 순서대로 실행 (트리거는 기본 보류)
.venv/bin/python db/mysql/migrations/apply_admin_news_erd.py --apply
```

백업과 단계별 기록은 Git 제외 경로 `data/db_backups/admin_news_erd_<시각>/`에
소유자 전용 권한으로 저장합니다. DDL은 자동 커밋되므로 전체 롤백되는 작업이 아닙니다.
성공한 단계는 실제 정의까지 확인한 뒤 재실행 시 건너뜁니다. MySQL 8.4 기준 정의는
`20261023_admin_news_contract.json`에 있으며 컬럼 자료형·기본값·NULL 허용,
인덱스 열·유일성, 외래키 대상·삭제 동작, CHECK 식을 비교합니다. 다른 정의가 있으면
첫 DDL 전에 중단하며 기존 객체를 임의로 덮어쓰지 않습니다. SQL 파일 해시도 확인하므로
마이그레이션 SQL 변경 시 기준 정의를 ERD와 함께 검토해야 합니다.
중단 후 재개에서는 해당 실행이 실제 보충한 SQL NULL만 기본 JSON으로 변환한 예상값과 비교합니다.
기존 비NULL JSON·기사 값·행 수는 그대로 검사하고 예상 밖 변경은 `FAILED_PRESERVATION`으로 기록합니다.
기존 해외시장뉴스·단신속보 적재와 공용 배치 기록은 스키마 조회 전 로컬 공용 잠금을 획득합니다.
로컬 기사 적재 잠금과 DB 마이그레이션 잠금을 사용하지만
다른 서버의 비협조적 적재 프로세스까지 정지시키지는 않습니다.

트리거는 기본적으로 `DEFERRED`, 전체는 `PARTIAL`(종료 코드 2)로 기록합니다.
명시적으로 트리거 적용을 요청한 경우에도 RDS 오류 1419가 발생하면 해당 단계는 `BLOCKED`, 전체는 `PARTIAL`
(종료 코드 2)로 기록하고 독립적인 뷰 적용은 계속합니다. 트리거를 생략한 상태를 완료로 표시하지 않습니다.
2026-10-23 적용 시 테이블·컬럼·제약·뷰는 반영됐으나 트리거 3개는 이 오류로 대기 중입니다.
AWS 접속 권한 준비 후 RDS 파라미터 그룹의 `log_bin_trust_function_creators`를 확인·변경하고
`.venv/bin/python db/mysql/migrations/apply_admin_news_erd.py --apply --include-triggers`로
재실행해야 합니다. 이번 수정에서는 트리거를 적용하지 않았습니다.
사용자 정의 파라미터 그룹을 이미 사용하면 이 값은 동적 적용 가능하며,
그룹을 새로 연결하는 경우 적용·재부팅 절차를 별도 확인합니다.
[AWS 공식 안내](https://repost.aws/knowledge-center/rds-mysql-functions).

```bash
# 실제 MySQL에서 시험 행을 모두 롤백하며 검증 (AUTO_INCREMENT 번호 공백은 남을 수 있음)
ADMIN_NEWS_MYSQL_TEST=1 .venv/bin/python -m unittest backend.tests.test_admin_news_mysql -v
```

실제 DB 테스트는 일반 테스트 실행에서는 제외됩니다. 트리거가 아직 없으면 관련 2개 시험을
명시적으로 제외하고, JSON·FK·분류 중복 제약, 네 출처의 공개/숨김 집계, 기존 뉴스·단신속보의
반복 적재 및 관리자 설정 보존, 공용 배치 성공/실패 기록을 검증합니다.

2026-10-23 관리자 스키마 반영 후 실검증: 2026-10-22 기사 5건의 수집·MySQL·Chroma 처리가
두 차례 성공했고 날짜/국가 집계 뷰도 5건을 반환했습니다. 중간 수집 실패 1회는 DB 적재 전에
중단됐고 다음 실행에서 복구됐습니다. 기사 ID·본문, 태그·집계 값, 기존 배치 500건은 보존됐으며
중복 그룹은 0건입니다. 기존 적재 규칙상 자동 태그와 일별 지표를 재생성하므로 내부 tag_id·metric_id는
달라질 수 있습니다. 실제 MySQL 검증은 6개 통과·트리거 대기 제외 2개입니다.

## 재실행과 실패

- 같은 `(source_code, external_id)`는 기존 article_id를 유지하며 갱신합니다.
- 한 입력에 중복 ID가 있으면 기존 뉴스와 같이 마지막 행을 쓰고 중복 건수를 보고합니다.
- 원본 ID/날짜 오류나 ERD 문자열 길이 초과는 행을 몰래 제외하거나 자르지 않고 전체 적재를 실패시킵니다.
- 기사·태그·일별 집계는 한 트랜잭션으로 처리하여 중간 실패 시 전체 롤백합니다.
- 실행 이력은 별도 트랜잭션의 `batch_run`에 SHORTNEWS_LOAD / article / API 호출 0으로 남깁니다.
  파일/날짜 검증에 실패하거나 DB에 연결하지 못한 경우에는 로그만 남고 이력 행이 없을 수 있습니다.
- 배치 종료 이력 갱신만 실패하면 기사 처리 결과와 별도로 경고를 남깁니다.
- API 수집 완료 여부는 적재 성공과 별개입니다. 이 코드는 지정한 파일 전체를 적재하며 수집 범위를 판단하지 않습니다.

## 로컬 검증

```bash
python -B backend/tests/shortnews_offline/run_tests.py --repo .
```

2026-10-27 PR32 독립 후보본에서 347건 통과, 9건 건너뜀, 네이티브 프로세스 시험 4건 제외를 확인했습니다. 하위 검증 23건도 통과했습니다. 건너뛴 시험은 실제 MySQL 전용 8건과 구형 스키마의 관리자 전용 1건입니다. 새 프로세스에서 진입점·스케줄러 구성·공통 사용량 함수·CLI·requirements 참조 11건을 별도로 확인했습니다.

SQLite의 기존/관리자 확장 스키마에서 중복 갱신, 출처별 ID 분리, 관리자 설정·수동 태그 보존, 부분 실패 롤백과 재시도를 검증합니다. 합성 본문·사전·임베딩으로 MySQL 적재 로직→공통 본문 색인, 사용량·캐시 재사용, 부분 실패 후 복구를 확인합니다. 테스트가 읽은 애플리케이션 모듈은 모두 해당 독립 후보본의 실제 소스였습니다.

기존 작업 10개를 유지하며 단신 정기·시작 복구 작업 2개는 `SHORTNEWS_DAILY_ENABLED=true`일 때만 등록합니다. 등록 확인은 예약을 시작하지 않습니다. 실제 실행기 시작은 지정된 가상 작업 시험 2건만 허용하며 종료를 확인합니다. worker 종료는 가상 스케줄러로 완료 대기·잠금 해제를 확인했습니다.

실제 API·모델·MySQL/RDS·Windows·AWS·새 환경 의존성 설치는 이번 검증에서 실행하지 않았습니다. 설치된 로컬 런타임에서 확인한 결과입니다.

### 사용량과 저장소 확인

분류 색인·후보 질의·LLM 분석·본문 색인·전역 키워드 단계를 `pipeline_result.json`의 `usage`에 따로 기록합니다. 실행별 `run_id`가 다르며, 캐시 적중은 현재 실행 호출·토큰을 증가시키지 않습니다. 잘못된 모델 응답도 이미 보고된 usage는 보존합니다. 응답이나 usage가 없으면 `unreported_calls`로 구분하며 전체 사용량을 확정하지 않습니다. 실패와 PARTIAL에서도 보고된 사용량을 남깁니다. 독립 분류 색인 명령도 `candidate_index_run.json`에 이번 실행 사용량과 실패 상태를 기록합니다.

공통 `root/database.json`은 DB 식별 해시만 저장하며 다른 DB 자료가 기존 출처와 섞이는 것을 막습니다. DB가 다르거나 기존 색인의 출처 DB가 확인되지 않으면 자동 게시하지 않습니다. 본문·전역 키워드 root와 별도 분류 후보 경로를 함께 확인합니다.

## 기존 본문 색인 이관

기본 본문은 `NEWS_VECTOR_ROOT` 또는 `data/rag/index`의 활성 세대 `kotra_article`, 공통 키워드는 같은 root의 `keywords/news_keyword`입니다. `chroma_data`는 단신 분류 후보와 명시적 구형 클라이언트 경로이며, 구형 본문을 자동 삭제·이관하지 않습니다.

`legacy_direct_index_migration_required`가 발생하면 [기존 본문 색인 이관 안내](../../../docs/NEWS_INDEX_MIGRATION.md)의 원본 writer 중지·검증 카탈로그 준비·dry-run·`--apply` 순서를 따릅니다. `python -m backend.app.scheduler.jobs.news_index_migrate --help`로 인자를 확인할 수 있습니다. 원본 파일은 읽기만 하고 임시 복제본에서 벡터를 배치 복사합니다. 모델 호출은 없으며 기존 ID·문서·벡터와 타 출처를 보존합니다. 형식·모델·카탈로그가 맞지 않으면 게시하지 않습니다.

실제 MySQL/RDS·운영 Chroma 이관 및 Windows 실행은 이번 검증에 포함되지 않습니다. 최초 수신 시각과 당시 분류 입력의 근거가 없는 기존 행은 현재 값으로 backfill하지 않습니다. 자세한 필드 의미·복구·제약도 위 안내에 기록했습니다.
