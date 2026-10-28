# RAG 후속 변경 이관 — 2026-10-28

기준 커밋 `272417d`, 브랜치 `feat/40-common-article-rag`에서 Desktop 후속 변경을 대조했다. 작업 시작 시 대상 worktree는 깨끗했다. 이미 반영된 공통 검색·근거 요약·평가 도구와 `total_article` 통일은 유지했다.

## 반영 범위

- `news_embed --rebuild`를 실제 본문 전체 재임베딩과 공통 키워드 재구축에 연결했다. 선택 출처는 DB에서 갱신하고 다른 출처는 검증된 카탈로그에서 보존한다. 모델·차원 변경 시 이전 벡터를 열지 않고 재구축한다.
- 키워드는 새 세대에서 검증한 뒤 활성 포인터를 전환한다. 기존 저장소와 독자는 보존하며, 일반 갱신·검색도 활성 키워드 세대를 사용한다. 실제 저장 차원과 모델의 호환성을 검사한다.
- 단신 분류용 임베딩과 공통 본문·키워드 임베딩을 분리했다. 기본 공통 설정은 기존 `text-embedding-3-large`, 3072차원을 유지한다.
- 처리 중 취소·실패가 사용량 기록, 자원 종료, 최종 보고서 저장 오류에 가려지지 않도록 했다. 빌린 자원은 닫지 않는다.
- 이관 검토에서 키워드 포인터 복구까지 실패하는 경우를 추가 보완했다. 검증된 ready 세대를 failed로 덮지 않으며, 실행 실패와 복구 오류는 유지한다. 복구가 실제 적용된 뒤 오류가 발생하는 경우도 검사한다.
- 테스트 선택 경로를 임시 작업 폴더 이동 전에 절대 경로로 고정했다. 기존 Windows 환경·의존성 경로 처리는 보존했다.

단순 문서 재배치, 기존 검증 삭제, UTF-8 지정 제거는 가져오지 않았다. 수집 범위·필터·검수 기준은 변경하지 않았다. 실제 재임베딩이나 운영 저장소 전환은 실행하지 않았다.

## 보존

Desktop의 backend·docs·data 파일 3,521개를 이관 전후 SHA-256으로 비교해 변경 0건을 확인했다. 원본 미커밋 작업을 수정하지 않았으며 로컬 Dev·원문·색인을 Git 변경에 포함하지 않는다. 검수 진행 요약은 [HUMAN_HANDOFF](HUMAN_HANDOFF.md)를 따른다.

## 검증

설치된 Python 환경을 사용하되 import 경로는 대상 worktree로 고정했다. 격리 실행기는 네트워크·비밀 설정 읽기·운영 DB·운영 Chroma 접근을 차단하고 임시 SQL/Chroma와 합성 응답을 사용한다.

```bash
python -B backend/tests/shortnews_offline/run_tests.py --repo .
python -B backend/tests/shortnews_offline/run_tests.py --repo . backend/tests/test_shortnews_worker.py backend/tests/test_news_keyword_lifecycle.py backend/tests/test_rag_*.py backend/tests/trade_news/test_resource_cancellation.py
python -B backend/tests/trade_news/offline_checks/run.py
python -B backend/tests/trade_news/offline_checks/run.py --smoke all
git diff --check
```

최종 결과(시험 묶음 사이 중복 있음):

| 검증 묶음 | 결과 |
|---|---|
| 기사·단신 기본 회귀 | 624 passed, 9 skipped, 4 deselected; 30 subtests passed |
| RAG·취소·키워드 복구 추가 회귀 | 253 passed, 4 deselected; 43 subtests passed |
| 신문사 수집·공통 색인 회귀 | 530 passed, 15 skipped, 29 deselected |
| import·등록·usage·CLI 도움말 | 11개 모두 exit 0 |
| diff 공백 검사 | 통과 |

각 pytest 묶음의 경고 1건은 이미 import된 `anyio`의 assertion rewrite 경고다. 제외·건너뛴 시험은 각 격리 실행기의 기존 정책을 유지하며 실제 외부 연결 검증 완료로 세지 않는다.

특정 테스트 실행에도 격리 정책상 `test_shortnews_worker.py`를 포함해 네이티브 워커 4건이 실행 전에 제외되는지 확인한다.

초기 실행에서 공통 임베딩 모듈 누락, 상대 테스트 경로 처리, 테스트 선택 시 네이티브 제외 검사, 구형 키워드 모형의 차원 미제공 문제를 확인했다. 누락 모듈·경로·모형을 보완하고 지정된 제외 정책을 유지한 상태로 재검증했다. 테스트 모형 수정은 차원 검사를 우회하지 않고 실제 호환 벡터를 제공한다.

실제 OpenAI 호출·운영 DB·Windows 실기·Dev 검색 품질·Holdout·세 사용처 서비스 통합/성능 검증은 이번 결과에 포함하지 않는다. 기능 회귀 통과를 검색 품질 완료로 기록하지 않는다.
