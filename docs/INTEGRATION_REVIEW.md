> 커밋 정리 상태 (2026-10-28): 사용자 요청에 따라 아래 변경을 기능별 로컬 커밋 8개로 정리한다. 푸시·PR 생성은 실행하지 않는다. 아래의 미커밋 문구는 각 검수 시점의 기록이다. 이슈 #38의 미완료 화면 연결은 PR에서 별도로 명시한다.

# PR 전 검수 및 수정 (2026-10-28)

현재 작업 트리 전체의 연동 변경을 검토했다. 커밋·푸시·PR 생성은 하지 않았다. 이번 검수의 RDS 접근은 읽기 전용이며 DB 구조나 데이터를 변경하지 않았다.

## 재현한 오류와 수정

| 중요도 | 오류·영향 | 수정 파일 및 처리 |
|---|---|---|
| P2 | 채팅 조회 오류 뒤 다시 불러오기가 busy를 true로 바꿔, 조회가 성공해도 전송 버튼이 잠김 | frontend/src/pages/analysis/Analysis.jsx: 읽기 재시도 revision을 전송 busy와 분리 |
| P2 | 전송 실패 후 다른 채팅방에서 같은 질문을 보내면 이전 client_message_id를 재사용. 요청이 이미 서버에 도달한 경우 다른 방과 충돌할 수 있음 | 같은 파일: 요청 키를 질문뿐 아니라 sessionId에도 연결. 같은 방에서의 재시도는 키 유지 |
| P2 | READY 리포트에서 QUEUED 리포트로 이동할 때 이전 job 상태로 새 본문을 조기 요청 | frontend/src/pages/analysis/ReportView.jsx: 상태·오류를 조회한 리포트 ID에 묶어 해당 ID가 READY일 때만 본문 요청 |
| P2 | 빈 PDF/DOCX 차트에 의미 없는 기본 축이 생성되어 Windows 맑은 고딕에서 마이너스 문자 누락 경고 발생 | backend/app/api/files.py: 빈 결과는 축 없이 자료 없음 안내로 출력 |

가짜 Redis의 expire(key, 0)가 동일 시계 틱에서 간헐적으로 남는 테스트 문제도 발견했다. backend/tests/test_integration_storage.py에서 확실히 과거인 pexpireat(key, 1)로 만료시키고 키가 사라졌는지 확인한다. 실제 Redis의 만료 검증은 별도 로컬 통합 테스트로 유지한다.

## 추가·수정한 검증 파일

- frontend/tests/review-browser.cjs (신규): 실제 로컬 로그인 후 오류·지연 응답을 주입하는 브라우저 회귀 시나리오 3개. 수정 전 모두 실패, 수정 후 모두 통과. 다른 테스트의 실제 API 검증과 구분한다.
- frontend/package.json: `npm run test:review`로 재현 테스트 실행.
- backend/tests/test_integration_storage.py: 만료 테스트의 시간 의존성 제거, 빈 선/막대 차트의 PNG 생성 및 마이너스 글리프 경고 부재 검증 추가.
- docs/INTEGRATION.md, docs/INTEGRATION_REVIEW.md: 실행법과 이번 검수 결과 기록.

## 실행 결과

번들 Python + tmp/run_integration.py 의존 경로 도우미를 사용했다. 일반 가상환경에서는 동일 모듈을 python -m으로 실행할 수 있다.

| 실행 | 결과 |
|---|---|
| `python -m unittest backend.tests.test_backend_api backend.tests.test_intent_search backend.tests.test_chat_review_regressions backend.tests.test_report_news_regressions backend.tests.test_report_input_paths backend.tests.test_pr_search_changes backend.tests.test_integration_storage backend.tests.test_integration_cleanup -q` | 만료 테스트 보완 후 125개 통과. 아래 차트 테스트 추가 전 전체 회귀 결과 |
| 차트 수정 후 `python -m unittest backend.tests.test_integration_storage backend.tests.test_report_news_regressions backend.tests.test_report_input_paths -q` | 새 차트 테스트 포함 28개 통과, 이전 글리프 경고 없음 |
| `RUN_LOCAL_INTEGRATION=1` 후 `python -m unittest backend.tests.test_local_integration -q` | 실제 Docker MySQL·Redis 7개 통과 |
| frontend `npm test` | 14개 통과 |
| frontend `npm run lint`, `npm run build` | 통과 |
| frontend `npm run test:integration` | 실제 API/worker 기반 기존 화면 16개 통과(분석, 리포트 PDF/DOCX, 비밀번호 변경 포함) |
| frontend `npm run test:review` | 오류·지연 재현 3개 통과 |
| `python -m backend.scripts.check_api_schema` (RDS, 읽기 전용) | 필수 테이블·컬럼 누락 없음, Redis 연결 정상. 무역 공개월 2026-08, 기사·환율·국가·HS 검색 정상 |
| `git diff --check` | 오류 없음. 기존 2개 파일 LF/CRLF 안내만 발생 |

## 한계 및 운영 준비 항목

- RDS 공개 조회 및 스키마는 확인했지만, 실제 RDS 회원가입·분석 결과 저장·리포트 생성 등 쓰기 흐름은 실행하지 않았다. 전체 브라우저 검증 대상은 로컬 DB다.
- RDS 검사 중 외부 HS 벡터 검색은 APITimeoutError 후 DB 검색으로 대체되어 결과를 반환했다. 외부 임베딩 서비스의 정상 응답·지연 시간은 보장하지 않는다.
- RDS의 alert_event/model_run/report에는 현재 행이 없다. 이벤트·모델 결과·리포트가 없는 상태를 프론트 연결 오류와 구분해야 한다.
- 공용 API/worker/Redis 배포, S3, 기존 미구현 기능의 완성은 이번 검수 범위가 아니다.
- 기존 최근 알림 팝오버는 알림 조회·읽음 처리와 연결되어 있지만, 별도 팝업 배달 조회/확인 API 연결은 남아 있다. 개인 키워드 차단 편집 UI 및 일부 관리자 화면도 미완료이므로 PR은 part of #38로 연결한다.

---

# 합의 반영 검수 기록 (2026-10-28)

현재 최종 상태: 역할 USER/ADMIN, report.files_json의 size 제거, users.password_change_advised 및 scheduled_delete_at 제거. SUPER_ADMIN은 후속 작업이다. 로컬 검증 후 RDS 적용까지 완료했으며 커밋·푸시는 하지 않았다. 기존 화면 디자인과 Redis 저장 방식은 유지한다.

## 이번 생성/수정 파일과 이유

| 파일 | 이유 |
|---|---|
| [backend/app/api/accounts.py](../backend/app/api/accounts.py) | 회원 응답·가입·비밀번호 변경에서 password_change_advised 제거. ADMIN만 관리자 경로로 분기. |
| [backend/app/api/admin.py](../backend/app/api/admin.py) | scheduled_delete_at 조회·응답 제거. ADMIN이 다른 회원 권한/상태를 변경하며 자기 계정 변경 금지. |
| [backend/app/api/application.py](../backend/app/api/application.py) | API 계약의 ADMIN 인증 조건 적용. |
| [backend/app/api/contracts.json](../backend/app/api/contracts.json) | 관리자 API 역할을 ADMIN 하나로 정리하고 권한 변경 대상 설명 갱신. |
| [backend/app/api/routes.py](../backend/app/api/routes.py) | 환경 설정으로 허용된 디버그 SQL의 관리자 역할 조건을 ADMIN으로 변경. |
| [backend/app/api/security.py](../backend/app/api/security.py) | SUPER_ADMIN 전용 분기 제거, ADMIN 권한 검사로 통일. |
| [backend/app/api/runtime_schema.py](../backend/app/api/runtime_schema.py) | users 확장 정의에서 두 컬럼 제거. |
| [backend/app/api/report_storage.py](../backend/app/api/report_storage.py) | 새 파일 메타데이터에서 size 제거; SHA-256 검증 유지. |
| [backend/migrations/038_integration.sql](../backend/migrations/038_integration.sql) | 새 DB에 두 컬럼을 만들지 않도록 초기 확장 SQL 수정. |
| [backend/scripts/create_api_admin.py](../backend/scripts/create_api_admin.py) | 관리자 생성 스크립트가 ADMIN을 생성하고 삭제된 컬럼을 사용하지 않도록 수정. |
| [backend/scripts/local_integration.py](../backend/scripts/local_integration.py) | 로컬 migrate 명령에 합의된 정리 단계 연결. |
| [backend/scripts/migrate_integration_cleanup.py](../backend/scripts/migrate_integration_cleanup.py) | 신규. 읽기 전용 계획, 변경 전 백업, 두 컬럼 제거, SUPER_ADMIN→ADMIN 및 세션 무효화, 모든 파일 형식의 size 제거, DB 역할 제약, 재실행 검증. |
| [backend/tests/test_backend_api.py](../backend/tests/test_backend_api.py) | 관리자 회귀 테스트 계정을 ADMIN으로 변경. |
| [backend/tests/test_integration_storage.py](../backend/tests/test_integration_storage.py) | 역할 부여/회수·자기 변경 거절·세션 무효화, 삭제 컬럼 부재, 파일 메타데이터 검증. |
| [backend/tests/test_local_integration.py](../backend/tests/test_local_integration.py) | 실제 MySQL/Redis에서 ADMIN·파일 필드·정리 완료·제3 역할 DB 거절 검증. |
| [backend/tests/test_integration_cleanup.py](../backend/tests/test_integration_cleanup.py) | 신규. 모든 파일 형식의 size만 제거하고 다른 정보 보존, 예상 밖 JSON에서 중단 검증. |
| [frontend/src/store.jsx](../frontend/src/store.jsx) | 삭제된 비밀번호 안내 필드 제거. 서버 ADMIN만 관리자 상태로 매핑. |
| [frontend/src/pages/account/Profile.jsx](../frontend/src/pages/account/Profile.jsx) | 삭제된 컬럼에 의존하던 임시 비밀번호 안내 제거. |
| [frontend/src/auth/roles.js](../frontend/src/auth/roles.js) | isAdmin 및 다른 ACTIVE 회원 대상 권한 변경 조건으로 정리. |
| [frontend/src/admin/AdminApp.jsx](../frontend/src/admin/AdminApp.jsx) | 최고 관리자/운영 관리자 구분을 관리자 표기로 통일. |
| [frontend/src/admin/AdminScreen.jsx](../frontend/src/admin/AdminScreen.jsx) | 권한 관리 버튼 표시 기준을 ADMIN으로 변경. |
| [frontend/src/admin/ServerDetail.jsx](../frontend/src/admin/ServerDetail.jsx) | ADMIN에 권한 변경 버튼 표시. 자기 계정의 권한/상태 변경 버튼 숨김. |
| [frontend/src/admin/AdminRoles.jsx](../frontend/src/admin/AdminRoles.jsx) | 남아 있던 관리자 권한 컴포넌트의 최고 관리자·고정 이메일 특례 제거. |
| [frontend/src/admin/screens.js](../frontend/src/admin/screens.js) | 기존 화면 설명의 최고 관리자 문구를 관리자에 맞춤. |
| [frontend/tests/roles.test.mjs](../frontend/tests/roles.test.mjs) | 서버 ADMIN 기준 및 자기 계정/일반 회원/제한 회원 조건 검증. |
| [docs/BACKEND_API.md](../docs/BACKEND_API.md) | 디버그 SQL 접근 역할 설명 수정. |
| [docs/INTEGRATION.md](../docs/INTEGRATION.md) | 최종 컬럼 9개와 역할·파일 구조, 마이그레이션 및 백업/재시작 방법 설명. |
| [docs/INTEGRATION_REVIEW.md](../docs/INTEGRATION_REVIEW.md) | 이번 합의 반영 내역과 실제 검증 결과 기록. |

## 문서 이동

원본 DOCX와 생성 스크립트 3개, 설계서 추출 TXT, 렌더링 폴더, 관련 Python 캐시 2개를 `C:/Users/t9100/Documents/Codex/2026-09-23/sk/outputs`로 이동했다. 프로젝트의 원본과 빈 output 폴더는 제거했다. DOCX의 이동 전후 SHA-256 일치를 확인했으며 하이라이트와 본문은 편집하지 않았다. 이 문서는 합의 당시 검토 원본이고, 최신 구현 상태는 본 기록을 기준으로 한다.

## 실행 명령과 결과

이 PC에서는 번들 Python을 `tmp/run_integration.py`(의존 경로 추가), `tmp/run_local.py`(로컬 설정 추가)로 실행했다. 아래 표의 모듈 명령은 일반 가상환경에서 같은 작업을 재현하는 형식이다.

| 명령/검증 | 결과 |
|---|---|
| `docker compose -f compose.integration.yml ps` | MySQL 8.4 / Redis 7.4 healthy |
| 로컬 설정으로 `python -m backend.scripts.migrate_integration_cleanup` | 삭제 컬럼 2개, 전환 계정 1개, 정리 리포트 10건 계획 확인 |
| 로컬 설정으로 위 명령에 `--apply` | 백업 후 적용. users 36행/report 10행 보존. 잔여 변경 0 |
| `python -m unittest backend.tests.test_backend_api -v` | 53개 통과 |
| `python -m unittest backend.tests.test_integration_storage -v` | 7개 통과 |
| `RUN_LOCAL_INTEGRATION=1` 후 `python -m unittest backend.tests.test_local_integration backend.tests.test_integration_cleanup -q` | 실제 로컬 DB/Redis 7개 + 정리 단위 테스트 2개, 합계 9개 통과 |
| frontend에서 `npm test` | 14개 통과 |
| frontend에서 `npm run lint`, `npm run build` | 모두 통과 |
| `node tmp/test_admin_ui.cjs` | 실제 ADMIN 로그인·기존 관리자 레이아웃·권한 변경·상태 변경·감사 이력·운영 설정·JS 오류 검증, PASS 로그 6개 |
| RDS 설정으로 `python -m backend.scripts.migrate_integration_cleanup --apply` | 두 컬럼 삭제, USER/ADMIN CHECK 추가. users/report는 전후 모두 0행. 잔여 변경 0 |
| RDS 스키마 + 이 PC Redis를 연결한 `/health/ready` | HTTP 200, ready. 테이블 33개, api_는 snapshot/audit_event만 유지 |
| `tmp/verify_cleanup.py` 읽기 전용 대조 | 기존 리포트 10건은 size 외 모든 메타데이터 동일. 파일 20개 SHA-256 확인. 전환 계정 1개의 auth_version 증가 확인 |
| `git diff --check` | 오류 없음. 기존 2개 파일의 LF/CRLF 안내만 출력 |

첫 제한 환경 백엔드 테스트는 출력 없이 지연되어 중단한 뒤 권한 있는 환경에서 개별 결과를 출력하여 53개 통과를 확인했다. 초기 MySQL 제약 테스트는 드라이버가 IntegrityError가 아닌 OperationalError(3819)를 내는 차이를 수정한 뒤 통과했다. 첫 관리자 브라우저 검증은 이전 실행의 동일 감사 문구가 2개여서 선택자가 실패했고, 선택자를 수정해 새 계정으로 재검증했다.

변경 전 백업(비밀번호·세션 토큰 미포함):
- `runtime/migration-backups/local-038-cleanup-20261028T052459568994Z.json`
- `runtime/migration-backups/rds-038-cleanup-20261028T052822785749Z.json`

로컬 API와 worker는 변경 전에 중지했고 새 코드로 다시 실행했다. 로컬 검증용 회원/리포트는 RDS에 복사하지 않았다. 036_backend_api.sql의 폐기된 제안 이력과 전환/거절 테스트에는 이전 역할·컬럼 이름이 남아 있지만 현재 런타임은 사용하지 않는다.

## 이번에 검증하지 않은 부분

- 공용 서버 배포 및 공용 Redis 연결은 하지 않았다. RDS 준비 상태 검사는 이 PC의 코드·Redis를 사용했다.
- 일반 사용자 화면 전체의 기존 브라우저 16개 시나리오는 이번 수정에서 재실행하지 않았다. 회원/리포트/인증은 API 테스트로, 변경된 관리자 화면은 실제 브라우저로 확인했다.
- S3, OAuth, 계정 복구·탈퇴 등 기존 보류 기능은 이번 범위가 아니다.

---

아래는 앞선 프론트 연결 작업의 기록이다. 이번 역할·컬럼 정리의 최신 상태는 위 내용을 우선한다.

# 연동 작업 검수 기록 — 기존 프론트 연결로 수정 (2026-10-28)

브랜치 `feat/38-Integrate`. 커밋·푸시하지 않음. 이번 수정에서는 백엔드/DB/Redis 코드를 유지하고 프론트 연결 방식을 바로잡았다. RDS 마이그레이션을 다시 실행하지 않았다.

## 1. 변경 범위와 파일별 이유

진입점을 `src/main.jsx → AppProvider(store.jsx) → App.jsx/pages`로 복구했다. main.jsx는 복구 후 원래 HEAD와 같아서 최종 Git diff에 나타나지 않는다. 잘못 추가했던 `frontend/src/live/` 전체와 `frontend/src/styles/live.css`는 삭제했다(미추적 파일이어서 Git 삭제 diff에는 남지 않음).

기존 `mockup.css`, `app.css`, 이미지와 페이지 라우트·공통 배너·메뉴·카드·모달을 사용한다. 실제 서버가 제공하지 않는 수치·본문·기능은 만들어 표시하지 않는다. 리포트 지면의 섹션 내용/표/차트 개수는 서버가 저장한 결과에 따라 달라진다.

### 프론트 파일

| 구분 | 파일 | 생성/수정 이유 |
|---|---|---|
| 수정 | [frontend/.env.example](../frontend/.env.example) | API 경로·개발 프록시 설정 예시 유지. |
| 수정 | [frontend/README.md](../frontend/README.md) | 기존 화면 진입점과 실제 테스트 기준으로 설명 수정. |
| 수정 | [frontend/package-lock.json](../frontend/package-lock.json) | 앞선 작업의 Playwright 의존성 고정 유지. |
| 수정 | [frontend/package.json](../frontend/package.json) | 실제 기존 화면 연동 테스트를 test:e2e/test:integration에 연결. |
| 수정 | [frontend/src/App.jsx](../frontend/src/App.jsx) | 세션 복구를 기다린 뒤 기존 화면을 열고 오류/재조회 처리. |
| 수정 | [frontend/src/admin/AdminApp.jsx](../frontend/src/admin/AdminApp.jsx) | 기존 관리자 틀에서 API 데이터 바인딩과 실제 대상 상세 연결. |
| 수정 | [frontend/src/admin/AdminScreen.jsx](../frontend/src/admin/AdminScreen.jsx) | 기존 운영 카드 유지, 가짜 회원/구독 추이 제거 및 실제 알림 조회. |
| 수정 | [frontend/src/admin/keywords/KeywordAdmin.jsx](../frontend/src/admin/keywords/KeywordAdmin.jsx) | 기존 분류 UI 유지, 서버 미구현 응답 표시 및 가상 분류 데이터 제외. |
| 수정 | [frontend/src/auth/roles.js](../frontend/src/auth/roles.js) | 이메일이 아니라 서버 ADMIN 역할로 관리자 UI 권한 판별. |
| 수정 | [frontend/src/components/charts.jsx](../frontend/src/components/charts.jsx) | 기존 차트 유지, 큰 실제 금액 라벨과 단일 관측 스파크라인 처리. |
| 수정 | [frontend/src/components/layout.jsx](../frontend/src/components/layout.jsx) | 기존 레이아웃 유지, 현재 동작에 맞게 목업 안내 문구 수정. |
| 수정 | [frontend/src/components/pickers.jsx](../frontend/src/components/pickers.jsx) | 기존 HS4→HS6/국가 선택 모양 유지, 실제 검색·하위 코드·대륙 API 연결. |
| 수정 | [frontend/src/data/catalog.js](../frontend/src/data/catalog.js) | 가상 국가·품목 목록을 API 기준정보 메모리 캐시로 교체. |
| 수정 | [frontend/src/pages/account/Inbox.jsx](../frontend/src/pages/account/Inbox.jsx) | 기존 목록/상세를 서버 알림으로 표시하고 실제 기사 ID로 연결. |
| 수정 | [frontend/src/pages/account/Interests.jsx](../frontend/src/pages/account/Interests.jsx) | 기존 편집 UI에서 서버 version 기반 관심정보 저장. |
| 수정 | [frontend/src/pages/account/NotificationSettings.jsx](../frontend/src/pages/account/NotificationSettings.jsx) | 기존 토글/약관 모달에서 실제 약관 본문·버전·동의 기록·설정 저장. |
| 수정 | [frontend/src/pages/account/Password.jsx](../frontend/src/pages/account/Password.jsx) | 실제 비밀번호 변경 후 모든 세션 무효화에 맞춰 재로그인으로 이동. |
| 수정 | [frontend/src/pages/account/Profile.jsx](../frontend/src/pages/account/Profile.jsx) | 이메일 계정 비밀번호 진입 수정, 미구현 일반 동의/탈퇴의 가짜 완료 방지. |
| 수정 | [frontend/src/pages/account/ReportCenter.jsx](../frontend/src/pages/account/ReportCenter.jsx) | 기존 탭·검색·정렬·삭제 모달 유지, 서버 리포트 갱신·실제 파일·구독 해지 연결. |
| 수정 | [frontend/src/pages/account/Subscription.jsx](../frontend/src/pages/account/Subscription.jsx) | 기존 주기/양식 카드 유지, 활성 주기 조회와 구독 PUT 연결. |
| 수정 | [frontend/src/pages/analysis/Analysis.jsx](../frontend/src/pages/analysis/Analysis.jsx) | 기존 채팅방/대화 카드/조건 선택/모달 유지, 실제 작업·되묻기·조건 확인·생성 연결. |
| 수정 | [frontend/src/pages/analysis/ReportView.jsx](../frontend/src/pages/analysis/ReportView.jsx) | 기존 배너·보라색 리포트 지면 유지, 저장 섹션/실제 차트·막대와 서버 파일 다운로드. |
| 수정 | [frontend/src/pages/auth/FindAccount.jsx](../frontend/src/pages/auth/FindAccount.jsx) | 기존 입력 폼 유지, 복구 API의 준비 중 응답 표시. 가짜 계정/메일 성공 제거. |
| 수정 | [frontend/src/pages/auth/Login.jsx](../frontend/src/pages/auth/Login.jsx) | 기존 로그인 폼을 실제 로그인 API 및 서버 시작 경로에 연결. |
| 수정 | [frontend/src/pages/auth/Onboarding.jsx](../frontend/src/pages/auth/Onboarding.jsx) | 기존 3단계 관심사 화면을 실제 저장 완료 후 이동하도록 변경. |
| 수정 | [frontend/src/pages/auth/Signup.jsx](../frontend/src/pages/auth/Signup.jsx) | 실제 회원가입 결과를 기다리고 서버 중복/검증 오류 표시. |
| 수정 | [frontend/src/pages/auth/SocialLogin.jsx](../frontend/src/pages/auth/SocialLogin.jsx) | 기존 인증 화면 틀 유지, 미연결 소셜 인증을 준비 중으로 안내. |
| 수정 | [frontend/src/pages/home/Dashboard.jsx](../frontend/src/pages/home/Dashboard.jsx) | 기존 히어로·카드·티커·차트·UN 패널에 실제 조회값 연결. |
| 수정 | [frontend/src/pages/market/Articles.jsx](../frontend/src/pages/market/Articles.jsx) | 기존 검색·탭·상세 펼침 유지, 서버 검색/페이지/본문 조회. |
| 수정 | [frontend/src/pages/market/Rank.jsx](../frontend/src/pages/market/Rank.jsx) | 기존 필터·막대 표 유지, 동적 공개 기간·실제 순위/추이·XLSX 연결. |
| 수정 | [frontend/src/store.jsx](../frontend/src/store.jsx) | localStorage의 가상 계정·데이터·완료 타이머를 서버 상태와 비동기 저장으로 교체. |
| 수정 | [frontend/tests/data.test.mjs](../frontend/tests/data.test.mjs) | 가상 수치 계산 대신 실제 응답 변환·큰 ID·null·비밀번호 검증 검사. |
| 수정 | [frontend/tests/roles.test.mjs](../frontend/tests/roles.test.mjs) | 이메일만으로 관리자 UI가 활성화되지 않는지 검사. |
| 수정 | [frontend/vite.config.js](../frontend/vite.config.js) | FastAPI 프록시 유지, 개발용 소셜 인증 우회 플러그인 제외. |
| 생성 | [frontend/src/admin/ServerDetail.jsx](../frontend/src/admin/ServerDetail.jsx) | 기존 card/Modal 부품으로 실제 대상·버전·사유·확인을 갖춘 상태/권한/노출 변경. |
| 생성 | [frontend/src/admin/useServerScreen.js](../frontend/src/admin/useServerScreen.js) | 기존 관리자 화면 정의에 실제 회원·기사·수집·감사·운영 데이터를 연결. |
| 생성 | [frontend/src/api/adapters.js](../frontend/src/api/adapters.js) | 서버 ID·상태·국가 코드·금액을 기존 UI 표현으로 변환. null과 0 구분. |
| 생성 | [frontend/src/api/client.js](../frontend/src/api/client.js) | 쿠키·CSRF·서버 오류·멱등 키·파일 다운로드 공통 처리 유지. |
| 생성 | [frontend/src/api/hooks.jsx](../frontend/src/api/hooks.jsx) | 요청 취소·이전 응답 차단·검색 지연·오류·페이지 처리. 별도 화면/스타일 없음. |
| 생성 | [frontend/src/components/ApiResults.jsx](../frontend/src/components/ApiResults.jsx) | 기존 LineChart/표/막대로 서버 결과 snapshot 표시 및 원본 XLSX 다운로드. |
| 생성 | [frontend/src/pages/home/useDashboard.js](../frontend/src/pages/home/useDashboard.js) | 메인용 무역/환율/기사 응답을 기존 카드 구조로 조합. |
| 생성 | [frontend/src/pages/home/useMarketSeries.js](../frontend/src/pages/home/useMarketSeries.js) | 복수 관심 HS6와 상위 품목별 실제 추이 조회, 오래된 조건 응답 취소. |
| 생성 | [frontend/src/pages/home/useUnData.js](../frontend/src/pages/home/useUnData.js) | 관심 HS6의 UN 공개 기간과 실제 점유율 조회. 미수집 지표는 미제공 표시. |
| 생성 | [frontend/tests/api-client.test.mjs](../frontend/tests/api-client.test.mjs) | 앞선 작업의 CSRF·쿠키·오류·파일 계약 검사 유지. |
| 생성 | [frontend/tests/integration-browser.cjs](../frontend/tests/integration-browser.cjs) | 기존 화면으로 실제 로컬 API/DB/Redis/worker 흐름 16개 검증. |

### 유지한 백엔드·DB·Redis 변경 (앞선 작업)

아래 파일은 앞선 연동 작업의 변경이며 이번 프론트 재연결에서 수정하지 않았다.

| 구분 | 파일 | 기존 변경 이유 |
|---|---|---|
| 수정 | [backend/.env.api.example](../backend/.env.api.example) | Redis 접속 주소와 키 prefix 설정 예시 추가. |
| 수정 | [backend/app/api/accounts.py](../backend/app/api/accounts.py) | 회원 프로필을 users로 통합하고 실연동 로그인 이동 경로 수정. |
| 수정 | [backend/app/api/admin.py](../backend/app/api/admin.py) | users의 권한/상태를 변경하고 auth_version으로 기존 세션 무효화. |
| 수정 | [backend/app/api/application.py](../backend/app/api/application.py) | Redis 주입과 DB 컬럼·Redis readiness 검사 추가. |
| 수정 | [backend/app/api/chat.py](../backend/app/api/chat.py) | 메시지 조회에 공개 job 정보를 포함해 새로고침 후 작업 복구. |
| 수정 | [backend/app/api/event_delivery.py](../backend/app/api/event_delivery.py) | 알림 전달 대상의 활성 상태를 users에서 조회. |
| 수정 | [backend/app/api/news.py](../backend/app/api/news.py) | 개인 키워드 차단을 notification_setting.keyword_blocks_json으로 통합. |
| 수정 | [backend/app/api/reports.py](../backend/app/api/reports.py) | report.files_json과 파일 저장 클래스로 PDF/DOCX 관리. |
| 수정 | [backend/app/api/routes.py](../backend/app/api/routes.py) | Redis 로그아웃, 리포트 메타데이터 기반 다운로드 연결. |
| 수정 | [backend/app/api/runtime_schema.py](../backend/app/api/runtime_schema.py) | 신규 api_ 테이블을 2개로 축소하고 기존 테이블 컬럼 확장 정의. |
| 수정 | [backend/app/api/security.py](../backend/app/api/security.py) | Redis 세션/요청 제한, TTL, 전체 기기 세션 무효화 구현. |
| 수정 | [backend/app/api/subscriptions.py](../backend/app/api/subscriptions.py) | 구독 발행 대상의 활성 상태를 users에서 조회. |
| 수정 | [backend/migrations/036_backend_api.sql](../backend/migrations/036_backend_api.sql) | 이전 7개 테이블 제안임을 표시하고 실행 금지 안내. |
| 수정 | [backend/requirements-api.txt](../backend/requirements-api.txt) | Python Redis 클라이언트 의존성 추가. |
| 수정 | [backend/scripts/check_api_schema.py](../backend/scripts/check_api_schema.py) | 추가 컬럼과 Redis 연결을 준비 상태 검사에 포함. |
| 수정 | [backend/scripts/create_api_admin.py](../backend/scripts/create_api_admin.py) | 최초 관리자 생성 시 별도 프로필 없이 users에 저장. |
| 수정 | [backend/tests/api_support.py](../backend/tests/api_support.py) | 통합 스키마·가상 Redis 주입 및 실제 MySQL 제약과 호환되는 fixture. |
| 수정 | [backend/tests/test_backend_api.py](../backend/tests/test_backend_api.py) | 프로필·키워드 저장 위치가 변경된 계약 테스트 수정. |
| 생성 | [backend/app/api/redis_store.py](../backend/app/api/redis_store.py) | Redis 세션 키·TTL·원자적 요청 제한·장애 응답을 모듈화. |
| 생성 | [backend/app/api/report_storage.py](../backend/app/api/report_storage.py) | 로컬 파일 저장/읽기 경계, 경로 이탈 방지, SHA-256 확인. S3 연결은 후속. |
| 생성 | [backend/migrations/038_integration.sql](../backend/migrations/038_integration.sql) | 신규 테이블 2개·컬럼 11개·인덱스 1개 SQL 기록. |
| 생성 | [backend/requirements-api-test.txt](../backend/requirements-api-test.txt) | Redis 테스트에 필요한 fakeredis/Lua 의존성 분리. |
| 생성 | [backend/scripts/local_integration.py](../backend/scripts/local_integration.py) | 로컬 DB 고정 실행, 데이터 없는 스키마 복제, 가상 데이터, API/worker 실행 지원. |
| 생성 | [backend/scripts/migrate_integration.py](../backend/scripts/migrate_integration.py) | 읽기 전용 계획, 명시 적용, 잠금, 정의 검증, 재실행, DDL/행 수 기록. |
| 생성 | [backend/tests/fixtures/integration_schema.sql](../backend/tests/fixtures/integration_schema.sql) | RDS 접근 없이 같은 MySQL 제약으로 검증할 테이블 정의 사본. 원본 행 없음. |
| 생성 | [backend/tests/test_integration_storage.py](../backend/tests/test_integration_storage.py) | 세션 만료/무효화, Redis 장애/동시성, 파일 무결성, 작업 복구 6개 검사. |
| 생성 | [backend/tests/test_local_integration.py](../backend/tests/test_local_integration.py) | 실제 Docker MySQL·Redis에서 스키마/인증/결과/리포트/채팅/관리자 6개 통합 검사. |
| 생성 | [compose.integration.yml](../compose.integration.yml) | localhost 전용 MySQL 8.4/Redis 7.4, 건강 검사, 영속 볼륨, Redis AOF 구성. |

문서 `docs/INTEGRATION.md`, `frontend/README.md`, 이 검수 기록은 현재 진입점·실행 방법·검증 범위로 정리했다. `docs/BACKEND_API.md`의 앞선 저장소 설명 변경은 유지했다.

## 2. DB·Redis 유지 사항

- `api_user_profile` → users의 7개 컬럼, `api_report_file` → report.files_json, `api_keyword_block` → notification_setting.keyword_blocks_json.
- 신규 테이블은 api_snapshot, api_audit_event만 유지.
- Redis는 인증 세션/요청 횟수 제한만 담당. 프론트는 Redis를 직접 호출하지 않고 `/api/v1`을 호출한다.
- 리포트는 현재 로컬 파일 저장, DB에는 위치/크기/해시 등 메타데이터 저장. S3 전환은 별도 작업.
- 앞선 RDS 반영: 컬럼 11개·테이블 2개·인덱스 1개(14 SQL), 이후 재검사 계획 0개. 당시 총 33개 테이블. 이번에는 RDS DDL/회원 쓰기/테스트 데이터 이관을 실행하지 않았다.

## 3. 실행한 명령과 결과

이 PC의 Python은 `C:\Users\t9100\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`를 사용했다. `tmp/run_integration.py`는 저장소와 테스트 의존성 경로를 넣는 로컬 런처다. 팀원용 표준 명령은 INTEGRATION.md 참고.

| 명령/검증 | 결과 |
|---|---|
| git status --short, git diff | 기존 변경과 원래 화면 구조 확인. 커밋/푸시 미실행. |
| Docker compose ps | MySQL 8.4 / Redis 7.4 healthy. |
| npm test | 14개 통과(API 클라이언트 4, 응답/검증 5, 역할 1, 기존 개발 OAuth 모듈 4). |
| npm run lint | 경고/오류 없음. |
| npm run build | Vite 배포 빌드 성공. |
| npm run test:integration | 원래 화면 기준 실제 브라우저 16개 검사 통과, JS 오류 없음. |
| PY tmp/prepare_admin_ui.py + node tmp/test_admin_ui.cjs | 로컬 3307 전용 검증 계정 생성. 관리자 레이아웃/권한 변경/이용 제한/감사 이력/운영 설정/JS 오류 없음 6개 확인. |
| RUN_LOCAL_INTEGRATION=1, PY tmp/run_integration.py unittest backend.tests.test_local_integration -q | 실제 MySQL·Redis 통합 6개 통과(6.348초). |
| 화면 캡처 확인 | 기존 대시보드와 리포트 지면 확인. 큰 금액 차트 라벨과 다운로드 버튼 표시 수정. |

검증 중 수정한 사항: 작업 폴더가 잘못된 최초 명령 및 Windows Python 인코딩 오류는 올바른 경로/-X utf8로 재실행했다. 기존 체크박스/토스트 구조에 맞게 브라우저 선택자를 수정했고, 실제 채팅의 품목 확인 단계를 테스트에 포함했다. 메인 추이의 미지원 scope query를 제거하고 HS6별 실제 조회로 교체했다. 건강 확인 주소 `/health`는 404이며 `/health/ready`를 사용한다.

브라우저 16개: 가입, 기존 3단계 관심사, 세션 복원, 관심정보 재조회, 순위/XLSX, 기사 검색/상세, 약관/알림 설정, 구독/해지, 채팅 생성/이름 변경, 실제 분석/품목 확인/차트·표, 조건 확인/리포트/PDF·DOCX/재열람, 일반회원 관리자 차단, 미구현 일반 동의 안내, 로그아웃/재로그인, 비밀번호 변경/옛 비밀번호 거부, JS 오류·계정 localStorage 미사용.

앞선 백엔드 회귀 122개 통과 기록은 유지하며 이번 프론트 변경 후 전체 122개를 다시 실행하지는 않았다. 이번 검증은 실제 로컬 DB/Redis 6개와 위 프론트 검사다.

## 4. 테스트하지 못한 부분·남은 범위

- AWS 공용 API/worker/Redis 배포, TLS/CORS/프록시, 팀원 PC 신규 설치는 미검증.
- RDS 회원 가입/권한 변경/리포트 생성 쓰기 시나리오는 실행하지 않음. 로컬 테스트 자료는 RDS에 복사하지 않음.
- RDS의 REPORT_PERIOD/REPORT_TYPE/NOTIFICATION_TERMS는 이전 확인에서 0건. 실제 서비스 활성화에는 승인된 옵션·약관이 필요함.
- S3·실제 소셜 인증·메일·탈퇴·일반 약관·ML/RAG·관리자 키워드 분류/일부 운영 기능은 기존 미연결 상태. 준비 중/실제 서버 오류를 표시함.
- 개인 키워드 차단 API/저장 기능은 유지하지만 기존 프론트에는 편집 화면이 없어서 새 페이지는 추가하지 않음. 편집 UI를 기존 설정 화면에 넣을지는 별도 화면 범위 결정이 필요함.
- UN 패널은 실제 API에 연결했으나 로컬 seed에 UN 관측값이 없어 빈 결과 확인까지 수행. 서버가 제공하지 않는 수입 점유율 등은 미제공 표시.
- 관리자 전체 BS 화면/기사 노출 변경/모든 알림 종류/대량 데이터 페이지를 브라우저로 전수 검증하지 않음. 이번 관리자 브라우저 쓰기 검사는 회원 권한·상태에 한정.
- 주간·월간 장기 스케줄, Redis 장애/재시작 복구·부하, 다양한 자연어 질문 품질은 미검증. 실제 질문 1개와 품목 확인을 거친 리포트 흐름은 확인.
- PDF/DOCX 파일 생성·다운로드는 확인했지만 인쇄 문서 전체의 시각 검수는 수행하지 않음. #39 HTML 템플릿 연결과 backend/app/api/files.py 변경은 수행하지 않음.
- 모든 화면의 픽셀 비교나 모바일 기기별 검증은 수행하지 않음. 기존 CSS와 디자인 자산을 유지했고 주요 데스크톱 흐름을 확인.

## 로컬 증빙

`tmp/browser-original/result.json`, `dashboard.png`, `report.png`, `admin.png`, `ranking.xlsx`, `report.pdf`, `report.docx`. 실패 캡처가 남아 있을 수 있으나 최종 결과는 result.json과 최종 캡처를 기준으로 한다. tmp의 관리자 fixture는 로컬 검증용이며 커밋 대상이 아니다.

로컬 화면 http://127.0.0.1:5173, API 8000, MySQL 3307, Redis 6379. Redis 입문 설명은 INTEGRATION.md에 유지했다. 커밋·푸시하지 않았다.
