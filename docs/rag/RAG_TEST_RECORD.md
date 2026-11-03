# 최종 기사 RAG 로컬 검증

2026-11-03. 브랜치 feat/40-final-article-rag. 기준 origin/main 983e846.

## VS Code 터미널에서 재현

이 작업 폴더에서 데스크탑 저장소의 기존 가상환경을 이용했다.

```sh
cd "$HOME/Documents/ChatGPT/Final/worktrees/rag-final"
PY="$HOME/Desktop/SKN33-Final-3Team/.venv/bin/python"
export PYTHONPATH=".:backend/app/scheduler/collectors/trade_news"
"$PY" -B -m unittest discover backend/tests -p 'test_rag*.py'
"$PY" -B -m unittest discover backend/tests
```

RAG 관련 unittest 40개가 통과했다. 검색·요약, 기사 원문 인용, 발행일 순서, 참고 허용 범위를 로컬 테스트 데이터로 검증했다. 서비스 팩토리도 외부 API 없이 생성됐다. 최신 main의 컬렉션 이름 kotra_article을 유지했고 store.py를 변경하지 않았다. 실제 팀 DB, 유료 모델 생성, 챗봇 API 전체 요청은 검증 범위 밖이다. 기존 개발 작업본의 115개와 이번 40개는 서로 다른 테스트 집합이다.

## 전체 백엔드 결과

동일 가상환경에서 원본 main은 1,007개, 새 브랜치는 1,047개를 실행했다. 양쪽 모두 실패 2건, 오류 26건, 건너뜀 35건이었다. 실패 및 오류의 테스트 이름을 대조해 모두 동일함을 확인했다. 오류는 fastapi, jinja2, joblib, redis, sklearn 모듈 미설치에서 발생했다. 실패 2건은 뉴스 키워드 인덱스 포인터 복구 테스트의 두 조건에서 발생했다. 새 RAG로 추가된 실패는 관측되지 않았지만 백엔드 전체가 통과한 것은 아니다.

## 다섯 패키지 설치 후 재실행 · 2026-11-03

위 결과는 설치 전 기록이다. FastAPI 0.162.0, Jinja2 3.1.6, joblib 1.6.0, redis 6.4.0, scikit-learn 1.9.1 설치 후 동일한 데스크탑 가상환경에서 위 명령으로 다시 검사했다. 기준본은 origin/main 983e846을 새 임시 폴더에 git archive로 꺼낸 내용이고, 비교본은 같은 커밋에서 시작한 새 RAG 작업본이다.

| 결과 | main 원본 | 새 RAG 작업본 |
|---|---:|---:|
| 전체 실행 | 1,390 | 1,430 |
| 실패 | 3 | 3 |
| 오류 | 209 | 209 |
| 건너뜀 | 42 | 42 |

양쪽의 실패·오류 212건은 테스트 이름(하위 조건 포함), traceback의 파일명·함수·행 번호 및 마지막 예외 출력이 일치했다. RAG 전용 검사도 별도로 40개 모두 통과했다.

- 오류 209건은 fakeredis 미설치 183건, lightgbm 미설치 24건, email-validator 미설치 2건이었다. 설치 전에는 일부 테스트 모듈이 로딩 단계에서 빠졌으므로 설치 전후 오류 건수는 직접 비교할 수 없다.
- 실패 3건 중 1건은 ML 완전 일치 점수에서 기대한 1.0 대신 1.0000000000000002가 나온 경우다. 나머지 2건은 뉴스 키워드 인덱스 포인터 복구 테스트에서 포인터 기존 유무 두 조건 모두 기대값 1과 실제값 0이 달랐다.
- 이번 실행에서 새 RAG 때문에 추가된 실패는 관측되지 않았다. 오류 및 건너뜀으로 검증되지 않은 기능까지 정상으로 판정하지 않는다.

## fakeredis·LightGBM·email-validator 설치 후 · 2026-11-03

동일한 Python 3.12 가상환경에 fakeredis[lua] 2.40.0, lightgbm 4.7.0, email-validator 2.3.0을 설치했다. pip check 통과. macOS에서 LightGBM은 기본 경로에서 libomp.dylib를 찾지 못했다. 위 재현 명령에서 전체 테스트 직전에 다음 줄을 실행하면 이 환경의 scikit-learn에 동봉된 OpenMP를 사용한다.

```sh
export DYLD_FALLBACK_LIBRARY_PATH="$HOME/Desktop/SKN33-Final-3Team/.venv/lib/python3.12/site-packages/sklearn/.dylibs${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
```

기준 main은 983e846이다. 임시 폴더에 추출한 main과 새 RAG 작업본에서 동일한 설정으로 unittest 전체를 실행했다.

| 결과 | main | 새 RAG |
|---|---:|---:|
| 전체 실행 | 1,408 | 1,448 |
| 실패 | 11 | 11 |
| 오류 | 5 | 5 |
| 건너뜀 | 49 | 49 |

두 실행에서 실패·오류 16건의 테스트 이름과 하위 조건이 일치했다. 별도 실행한 RAG 전용 검사도 40개 모두 통과했다.

- 오류 5건은 모두 보고서 경로다. matplotlib, docx, playwright, reportlab import 오류 각 1건과 보고서 렌더러 사용 불가(503) 1건이다.
- 실패 11건 중 8건은 보고서 상태·다운로드·폰트 오류 코드에 대한 기대값 불일치다. 나머지는 ML 지표의 부동소수점 차이 1건, 뉴스 키워드 인덱스 포인터 복구 기대값 불일치 2건이다.
- 이전에 미실행된 보고서 검사가 실행되면서 실패가 드러났다. 현재 환경에서 새 RAG 작업본에만 나타난 실패는 없다. 전체 백엔드 검사는 미통과다.

## 챗봇 기사 검색 함수 연결

agents.tools.search_news가 RagService.run으로 RagResponse를 반환하도록 연결했다. 발행일 최신순 요청이 모델 없이 임시 카탈로그의 기사를 반환하는지, 일반 요청은 인덱스가 없을 때 실패를 반환하고 클라이언트를 종료하는지 검사했다. backend.tests.test_rag_chat_tool 2개 통과, RAG 대상 전체 42개 통과. 이 변경 후 전체 백엔드는 다시 실행하지 않았다. 채팅 메시지에서 이 함수를 호출하는 에이전트 실행 경로와 HTTP 종단 간 검증은 남아 있다.

## PR 제출 전 최종 검사

챗봇 도구 연결 후 전체 재실행 결과는 1,450개 실행, 실패 11개, 오류 5개, 건너뜀 49개다. 동일 환경의 main(983e846, 1,408개)과 실패·오류 테스트 이름 및 하위 조건 16개가 모두 일치한다. RAG 전용 42개는 모두 통과했다. 실제 채팅 HTTP 요청 및 유료 모델 호출은 포함하지 않았다.
