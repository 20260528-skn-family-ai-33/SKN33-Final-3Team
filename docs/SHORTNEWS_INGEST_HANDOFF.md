# 단신속보 일일 적재 인계

> 이름 변경: `kotra_article`은 `total_article`로 변경되었습니다. 기존 테스트 결과는 당시 기록을 보존합니다.

저장소 루트에서 실행합니다. 실행 환경에 `backend/requirements.txt`의 의존성을 준비하고 `.env.sample`의 단신 API·OpenAI·MySQL 항목을 별도 `.env`에 설정합니다. 실제 설정과 인증서, 수집 원문, DB·색인·캐시·로그는 커밋하지 않습니다.

## 적재 전 준비

기존 DB의 테이블·국가 기준정보·최신 `keyword_topic`/`keyword_category` 분류 데이터를 확인합니다. 관리자 ERD 적용 도구는 기본 실행에서 계획을 출력하며 실제 변경은 `--apply`를 명시합니다.

```bash
python db/mysql/migrations/apply_admin_news_erd.py
```

적용할 계획을 검토한 운영 담당자는 `--apply`를 사용합니다. 트리거 적용은 필요한 DB 권한과 RDS 설정을 준비한 뒤 `--include-triggers`로 별도 수행합니다. 이 PR의 로컬 회귀 결과는 실제 RDS 적용 결과가 아닙니다.

## 실행 순서

```bash
# 최신 DB 분류 후보 색인 준비
python -m backend.app.scheduler.jobs.shortnews_keyword_index

# 저장된 원본의 요약·키워드 선택·MySQL·공통 본문 색인
python -m backend.app.scheduler.jobs.shortnews_pipeline --raw "<수집실행폴더>/shortnews_raw.csv"

# 전날과 미완료 날짜 수집·적재
python -m backend.app.scheduler.jobs.shortnews_daily

# 특정 날짜 다시 처리
python -m backend.app.scheduler.jobs.shortnews_daily --date YYYY-MM-DD
```

MySQL만 적재할 때는 `python -m backend.app.scheduler.jobs.shortnews_load --raw "<원본 CSV 또는 JSON>"`를 사용합니다.

상시 실행은 전용 `python -m backend.app.scheduler.shortnews_worker` 또는 통합 `python -m backend.app.scheduler.main`을 사용합니다. 통합 실행은 `SHORTNEWS_DAILY_ENABLED=true`일 때만 단신 작업을 등록하며 기본값은 false입니다. 전용 worker는 이 스위치와 무관하게 단신 작업을 등록합니다. 오전 3시 KST에 전날 전체 페이지를 수집하고 시작 시 미완료 날짜를 복구합니다.

## 저장과 재실행 확인

- MySQL 출처는 `KOTRA_SHORT_NEWS`이며 기사 ID와 출처의 유일키로 같은 기사를 갱신합니다. 본문은 전체 HTML을 정제·마스킹해 `body_masked`에 저장합니다.
- 단신은 HS 태그를 생성하거나 HS 집계에 기여하지 않습니다. 기존 수동 태그·관리자 설정을 임의 삭제하지 않습니다.
- DB 분류에서 선택한 대표어는 `article_tag`에 저장하고 공통 본문 `total_article` 및 키워드 `news_keyword` 색인에 반영합니다. 분류 후보 색인은 별도로 유지합니다.
- `NEWS_VECTOR_ROOT`의 기본값은 `data/rag/index`입니다. 본문 세대·키워드 저장소·DB 식별 기록을 함께 보존합니다. 같은 root에 다른 DB의 자료를 합치지 않습니다.
- 요약은 분석 캐시와 실행별 `shortnews_analysis_results.json`에 남습니다. 입력·분류가 같으면 분석과 임베딩을 재사용합니다.
- `data/shortnews/daily/latest_run.json`과 날짜별 실행 결과를 확인합니다. 수집 및 MySQL·색인이 모두 성공한 날짜만 완료로 기록하며 부분 성공은 재시도합니다.
- 출처+ID 기준 기사 중복, 태그 중복, 반복 모델 호출은 서로 다른 항목으로 확인합니다. 별도 서버에서 같은 기사를 분석하면 캐시가 공유되지 않아 호출 비용이 반복될 수 있습니다.
- 로컬 파일 잠금은 같은 저장소·경로를 공유하는 작업을 조정합니다. 여러 서버를 조정하는 분산 잠금은 아닙니다. API 목록 교체나 완료된 과거 날짜의 지연 공개까지 보장하지 않습니다.

## 로컬 회귀

```bash
python -B backend/tests/shortnews_offline/run_tests.py --repo .
```

합성 입력·가상 API/모델·임시 SQLite/Chroma로 수집·적재·색인·사용량·캐시·부분 실패 복구와 기존 스케줄러 등록을 확인합니다. 실제 MySQL/RDS·운영 API·Windows·서비스 설치·네이티브 프로세스는 별도 검증 항목입니다.

구형 본문 컬렉션이 남아 있으면 먼저 [본문 색인 이관 안내](NEWS_INDEX_MIGRATION.md)를 확인합니다. 기본은 dry-run이며 운영 이관·관리자 필드 backfill은 이번 로컬 검증에서 실행하지 않습니다.

구형 본문 색인의 이관 카탈로그는 `python -m backend.app.scheduler.jobs.news_index_catalog`로 DB와 수집 CSV에서 생성할 수 있습니다. 출처·원본·사전 지정과 국가 순서 보존 규칙은 [본문 색인 이관 안내](NEWS_INDEX_MIGRATION.md)를 따릅니다. 이 명령은 DB 조회와 JSON 작성만 수행합니다.
