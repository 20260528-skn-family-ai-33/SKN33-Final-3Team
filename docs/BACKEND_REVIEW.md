# 백엔드 검수 기록

브랜치: `feat/36-backend`. 로컬 커밋 대상으로 정리했으며 푸시·RDS 마이그레이션 적용은 하지 않았다. 기존 데이터 적재 코드와 ML/RAG 구현은 추가하지 않았다.

## 생성 파일과 이유

| 파일 | 생성 이유 |
|---|---|
| `backend/.env.api.example` | 비밀번호 없는 API 설정 예시 |
| `backend/app/api/__init__.py` | API 패키지 선언 |
| `backend/app/api/accounts.py` | 회원가입·로그인·관심 버전 관리·비밀번호 변경 |
| `backend/app/api/admin.py` | 회원/기사 관리 및 변경 전후 감사 기록 |
| `backend/app/api/application.py` | HTTP 서버·요청 검증·인증 적용·공통 오류·OpenAPI |
| `backend/app/api/chat.py` | 대화 저장·제한적 조건 파싱·관심 적용 확인·비동기 답변 |
| `backend/app/api/common.py` | 오류·날짜·비율·직렬화·마스킹 공통 함수 |
| `backend/app/api/config.py` | 로컬 수집 설정과 분리한 RDS/TLS/API 설정 |
| `backend/app/api/contracts.json` | v0.18의 API 번호·경로·입력·인증 계약 |
| `backend/app/api/database.py` | 기존 테이블 reflection과 트랜잭션 조회/저장 도구 |
| `backend/app/api/event_delivery.py` | 확정 알림 사건의 동의·관심·차단 검증 및 중복 없는 전달 |
| `backend/app/api/files.py` | 저장 결과의 PDF·DOCX·XLSX와 차트 생성 |
| `backend/app/api/integrations.py` | ML/RAG/의도 추출 담당자용 인터페이스 |
| `backend/app/api/jobs.py` | 기존 채팅 메시지·리포트와 지속 로그를 이용한 멱등성·잠금·재시작 복구(async_job 미사용) |
| `backend/app/api/news.py` | 기사 검색·노출 필터·차단 키워드·환율 조회 |
| `backend/app/api/notifications.py` | 약관·수신 설정·알림함·미확인 수·팝업 확인 |
| `backend/app/api/reference.py` | HS·국가·지역·키워드 기준정보 조회 |
| `backend/app/api/reports.py` | 조건 확인·리포트 스냅샷·목록/파일·구독 저장 |
| `backend/app/api/results.py` | 본인 소유 결과 스냅샷과 표 조회 |
| `backend/app/api/routes.py` | API 번호별 실제 서비스 연결 및 미확정 기능 차단 |
| `backend/app/api/runtime_schema.py` | 앱 전용 추가 저장소 7개의 정의 |
| `backend/app/api/schemas.py` | 복합 입력 DTO 및 형식 검증 |
| `backend/app/api/security.py` | bcrypt·서버 세션·CSRF·역할·요청 제한 |
| `backend/app/api/subscriptions.py` | 검증된 새 통계/기사 집계를 읽는 구독 발행 트리거 |
| `backend/app/api/trade.py` | 수출입 KPI·추이·순위·비중·저장 일회성 플래그 조회 |
| `backend/app/api/un_market.py` | UN 월/연·CIF/FOB 분리 조회 및 비교 |
| `backend/app/api/worker.py` | 대화/리포트 작업 실행·재시도·구독 트리거 반복 |
| `backend/app/templates/reports/layout.json` | 첨부 PDF에서 반영한 리포트 섹션/스타일 정의 |
| `backend/app/templates/reports/summary.docx` | 첨부 요약보고서의 결재/본문 표를 보존한 치환용 템플릿 |
| `backend/main.py` | uvicorn 진입점 |
| `backend/migrations/036_backend_api.sql` | 기존 원천 테이블을 바꾸지 않는 앱 저장소 DDL |
| `backend/requirements-api.txt` | API/문서 생성/테스트 실행 의존성 분리 |
| `backend/scripts/check_api_schema.py` | 실제 DB에 쓰지 않는 연결/준비 상태 조회 |
| `backend/scripts/create_api_admin.py` | 명시 실행 시에만 최초 최고 관리자 생성 |
| `backend/tests/api_support.py` | SQLite 테스트 DB/샘플 자료 구성 |
| `backend/tests/fixtures/api_schema.json` | RDS 컬럼 구조의 검증용 사본(실제 행 데이터 없음) |
| `backend/tests/test_backend_api.py` | 인증·권한·소유권·통계·구독·알림·리포트·작업 통합 테스트 |
| `docs/BACKEND_API.md` | 실행·DB/담당자 인계·명세 충돌·제한 설명 |
| `docs/BACKEND_API_STATUS.md` | 130개 API 번호의 구현/보류/삭제 상태 |
| `docs/BACKEND_REVIEW.md` | 요청한 생성 파일·이유·명령 결과·미검증 사항 정리 |

기존 파일 변경: `.gitignore`에 `runtime/`을 추가했다. 로컬 `.env`는 API 전용 RDS 설정을 사용하며 Git에서 제외한다. 기존 `MYSQL_*` 수집 설정은 유지한다.

프론트 파일과 API 클라이언트 테스트는 이번 변경에서 제외했다. 실제 화면·API 클라이언트 연결은 프론트 담당자 범위이며 백엔드 연동 계약만 문서로 제공한다.

## 실행 명령과 결과

| 명령/작업 | 결과 |
|---|---|
| `git status --short`, `git branch --show-current` | feat/36-backend 확인, 기존 추적 파일 변경은 .gitignore |
| `git show origin/feat/33-frontend-screens:...` | PR #34 관련 프론트 코드의 mock store/OAuth 흐름 확인 |
| openpyxl / python-docx / pypdf로 첨부 문서 추출 | API 계약·요구사항·화면·템플릿 대조 |
| `pip install --target tmp/backend_build/deps ...` | 전역 Python을 바꾸지 않고 API/차트 검증 의존성 설치 |
| `python -m backend.scripts.check_api_schema` | RDS TLS 인증 연결 성공; 한글 HS 2건·국가 249개·무역 최신월 2026-08·기사 조회 성공; 빈 환율은 UNAVAILABLE |
| `python -m unittest backend.tests.test_backend_api -v` | 32개 통과; async_job 없는 SQLite DB에서 실행 |
| `python -m compileall -q ...` | 새 Python 모듈 컴파일 성공 |
| `git diff --check` | 공백 오류 없음 |
| `pdftoppm ... report-test.pdf ...` | 생성 PDF 6쪽 렌더링·육안 확인 |
| `render_docx.py report-test.docx ...` | LibreOffice 미설치로 렌더링 실패. DOCX ZIP/표/치환 및 예시값 제거는 별도 확인 |

초기 테스트에서 발견한 가입 인자 충돌, 공개 인증의 CSRF 처리, RDS 한글 HS 검색 collation 충돌을 수정했다. Windows 샌드박스에서 TestClient 이벤트 루프의 로컬 소켓이 막혀 동일한 SQLite 테스트를 권한 확장 실행했다.

추가 검수에서 채팅 기사 기간 필터, 기사 조회의 무역 통계 의존성, 미발행 분기 복구를 수정했다. 이전 async_job 기반 되묻기 NULL 오류는 해당 테이블 의존성을 제거하는 방식으로 대체했다. 테스트는 질문 중복 없이 되묻기 재개, 생성 시점 순서, 삭제된 질문 미실행, 재시작·만료 실행권 복구, 이전 워커의 결과 저장 거부, 회차별 즉시 재시도/DELAYED, 완료 로그 직후 DB 롤백 복구를 포함한다. 실제 RDS 쓰기·동시 워커 검증을 대체하지 않는다.

## 테스트하지 못한 부분

- RDS에 추가 DDL 적용 후 회원/세션/리포트 쓰기. 실제 RDS 데이터와 스키마는 읽기만 했다.
- MySQL JSON CHECK·generated column·고유 인덱스와 동시 worker/트랜잭션 경합·부하.
- PR #34 화면 전체에 API를 연결한 브라우저 E2E. 프론트 변경은 이번 범위에서 제외했다.
- ML/RAG 및 일반 자연어 해석 모델, 기사 급증/주요 이슈 판정 생산자, OAuth/메일/복구·미정 정책 API.
- DOCX를 Word/LibreOffice에서 펼친 페이지별 레이아웃 및 여러 길이의 실데이터 리포트 디자인.
- HTTPS 운영 쿠키·프록시·공유 파일 볼륨·장기 보존/정리·실제 운영 스케줄.

## 충돌과 준비 필요 사항

90개 경로의 처리 코드와 29개 보류 경로를 구분한다. 119개 등록 경로 전체가 완성·운영 검증된 상태는 아니다. 현재 코드는 추가 앱 테이블 7개를 참조하지만, 7개 분리 구조가 팀의 확정 DB 설계는 아니다. 기존 테이블 통합 대안과 추가 저장 정보는 [BACKEND_API.md](BACKEND_API.md)의 DB 재검수 항목을 참조한다. 리포트/주기/약관 옵션도 준비해야 한다. 부분 Conditions/기간 프리셋 DEC-28, OAuth 계약, 실제 기사 출처 코드, PDF/DOCX 파일 저장 구조, 탈퇴·복구·관리자 검수 정책의 차이는 같은 문서에 정리했다. API별 목록은 [BACKEND_API_STATUS.md](BACKEND_API_STATUS.md)를 참조한다.
