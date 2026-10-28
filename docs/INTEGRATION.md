# 프론트·백엔드 연동과 Redis 실행 안내

`feat/38-Integrate`의 실행 기준이다. 프론트는 `/api/v1`, API/worker는 같은 MySQL과 파일 저장 경로를 사용한다. Redis는 인증 세션과 요청 제한에만 사용한다.

## 확정 저장소

| 이전 제안 | 이번 구현 |
|---|---|
| api_user_profile | users에 name, role, account_status, created_at, last_login_at, withdrawn_at, auth_version 추가 |
| api_report_file | report.files_json에 형식별 저장 위치·SHA-256·생성 시각 저장 |
| api_keyword_block | notification_setting.keyword_blocks_json에 사용자별 대표어와 선택 표현 저장 |
| api_snapshot | 신규 테이블. 조회 결과, 리포트 조건 확인, 팝업 묶음 보관 |
| api_audit_event | 신규 테이블. 관리자 변경 주체·사유·대상·변경 전후 기록 |
| api_auth_session | Redis session 키 |
| api_rate_limit | Redis limit 키 |

추가 컬럼 9개(users 7개, 나머지 테이블 각 1개), 신규 테이블 2개다. 역할은 USER/ADMIN만 사용한다. 모르는 과거 회원 이름·가입일은 NULL이다. withdrawn_at이 있어도 탈퇴 기능은 정책 보류 상태다. 기존 chat_message.result_json/report.content_json 작업 규약은 유지한다.

PDF/DOCX는 현재 API_REPORT_DIRECTORY 아래 파일로 저장한다. DB에는 파일 내용을 넣지 않는다. API/worker가 다른 서버면 같은 파일 볼륨이 필요하다. S3는 report_storage.py 구현, IAM/bucket/보존 정책, 기존 파일 이전을 별도로 적용해야 하며 이번에 연결하지 않았다.

## 팀원 로컬 실행

Docker Desktop, Python 3.11 이상, Node.js 20.19 이상 또는 22.12 이상을 준비한다. Python은 저장소 루트와 동일 가상환경에서 실행한다. 루트 `.env`를 덮어쓰지 않는다. local_integration은 DB/Redis를 고정된 로컬 대상으로 설정한다.

```powershell
docker compose -f compose.integration.yml up -d --wait
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-api-test.txt
python -m backend.scripts.local_integration schema
python -m backend.scripts.local_integration migrate
python -m backend.scripts.local_integration seed
python -m backend.scripts.local_integration check
```

schema는 저장소의 테이블 정의만 복제하므로 RDS 접근 권한이 없어도 된다. 최초 빈 DB에서만 실행한다. 이미 초기화했다면 schema를 반복하지 않고 migrate만 실행한다. 기존 데이터를 삭제하거나 DB를 초기화하지 않는다. seed는 미국·중국·화장품·통계·기사·환율·약관의 **가상 검증 데이터**를 로컬에 만든다. RDS 데이터/회원을 복사하지 않는다.

migrate에는 2026-10-28 합의 정리도 포함된다. 실행 전 API/worker를 종료한다. password_change_advised, scheduled_delete_at을 제거하고, SUPER_ADMIN은 ADMIN으로 전환하며 해당 세션을 무효화한다. 기존 report.files_json의 각 형식에서 size만 제거한다. 변경 대상 값은 runtime/migration-backups에 먼저 백업하며, 동일 상태에서 재실행하면 추가 변경이 없다.

세 터미널에서 다음을 실행한다. Python 터미널마다 가상환경을 활성화한다.

```powershell
# 터미널 1, 저장소 루트
python -m backend.scripts.local_integration api
# 터미널 2, 저장소 루트
python -m backend.scripts.local_integration worker
# 터미널 3
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

화면은 http://127.0.0.1:5173, API 문서는 http://127.0.0.1:8000/docs, 준비 상태는 http://127.0.0.1:8000/health/ready 이다. 직접 가입한다. 이전 목업 체험 계정은 실제 계정이 아니다. worker가 꺼져 있으면 리포트/채팅이 대기한다. 실제 자연어 의도 추출에는 OPENAI_API_KEY가 필요하다. 도우미는 HS 벡터 검색을 끄고 DB 사전 검색을 사용한다.

MySQL: 127.0.0.1:3307 / trade_integration / integration / local-integration-only. Redis: 127.0.0.1:6379. Compose의 공개 비밀번호는 로컬 검증 전용이며 공용 서버 설정으로 복사하지 않는다. 포트는 외부 인터페이스에 열지 않는다.

종료: API/worker/Vite에서 Ctrl+C 후 `docker compose -f compose.integration.yml stop`. 데이터 볼륨은 유지된다.

## Redis 입문

Docker가 Redis 서버를 실행하고 Python redis 라이브러리가 접근한다. 프론트에는 Redis 주소·비밀번호·라이브러리가 필요하지 않다. 이 로컬 구성에는 Redis Cloud 회원가입이나 유료 관리형 서비스가 필요하지 않다.

흐름: 브라우저 → FastAPI → Redis(세션/횟수) 및 MySQL(회원/관심/결과).

- 로그인 시 무작위 토큰을 브라우저 HttpOnly 쿠키에 넣는다. Redis에는 토큰의 해시를 키로 회원 번호·CSRF 해시·auth_version을 저장한다. 비밀번호는 넣지 않는다.
- 인증 시 Redis 세션과 MySQL 회원 상태/auth_version을 함께 확인한다. Redis가 회원 DB를 대신하지 않는다.
- TTL은 남은 유효 초다. 기본 세션 만료는 12시간(API_SESSION_HOURS)이며 요청마다 연장하지 않는다. 만료된 키는 Redis가 정리한다.
- 로그아웃은 해당 키를 삭제한다. 비밀번호/권한/상태 변경은 users.auth_version을 올려 모든 기기의 기존 세션을 무효화한다. 옛 키는 TTL로 정리된다.
- 로그인/가입 IP·이메일, HS 검색의 요청 횟수는 Lua로 증가와 만료 설정을 원자적으로 실행한다. 여러 API 프로세스가 같은 Redis를 쓰면 횟수를 공유한다. 초과 429, Redis 장애 503이다. 장애 시 제한을 우회하지 않는다.
- 로컬 앱 prefix는 borderless:integration:, 테스트는 무작위 별도 prefix다. prefix는 이름 구분이며 보안 격리는 아니다. 공용 배포는 별도 인스턴스/ACL 등 접근 제어가 필요하다.
- AOF와 Docker 볼륨으로 재시작에 대비한다. AOF 기본 동기화 간격 때문에 장애 직전 기록은 일부 유실될 수 있다. 세션 유실 시 재로그인이 필요하다. noeviction은 메모리 부족 시 기존 세션을 임의로 퇴출하지 않도록 한다. 공용 배포의 메모리 한도·감시·접근 제어·TLS는 별도 설정한다.

```powershell
docker compose -f compose.integration.yml ps
docker compose -f compose.integration.yml exec -T redis redis-cli ping
docker compose -f compose.integration.yml exec -T redis redis-cli DBSIZE
```

PONG은 연결 성공, DBSIZE는 키 개수이며 사용자 수와 다르다. 다른 팀원은 자기 Docker에서 같은 구성으로 검증한다. 공용 백엔드에는 그 서버가 접근할 공용 Redis가 필요하다. 내 PC의 localhost Redis가 자동 공유되지는 않는다.

## RDS 마이그레이션

038_integration.sql이 현재 변경 내역이다. 036_backend_api.sql은 이전 제안이므로 실행하지 않는다. 앱 시작 시 자동 DDL은 없다. 아래 명령은 Settings의 대상 DB를 사용한다(기본 RDS). 로컬 작업은 위 도우미를 사용한다.

```powershell
python -m backend.scripts.migrate_integration --record tmp/integration-plan.json
# 로컬 검증 후 승인된 대상에만 명시적 적용
python -m backend.scripts.migrate_integration --apply --record tmp/integration-migration.json
# API/worker 중지 후 역할·컬럼·파일 메타데이터 정리 (기본은 읽기 전용 계획)
python -m backend.scripts.migrate_integration_cleanup
python -m backend.scripts.migrate_integration_cleanup --apply
```

적용 전 DDL·행 수·SQL을 기록하고 잠금 아래 변경한다. 이후 스키마·행 수를 확인하며 동일 스키마 재실행은 SQL 0개다. MySQL DDL은 자동 커밋이므로 전체 변경의 트랜잭션 롤백은 불가능하다. 부분 실패 원인을 해결한 뒤 재실행할 수 있다. 이전 폐기 대상 api_ 5개 테이블이 존재하면 데이터 유실 없이 중단하고 별도 이관을 요구한다. 이번 적용 전 대상에는 그 5개가 없었다.

RDS 스키마 적용은 서버 배포와 별개다. 공용 API/worker/Redis, HTTPS 프록시, 운영 약관/옵션, 파일 저장소가 필요하다. 로컬 가상 계정·기사·약관·파일은 RDS로 이관하지 않는다.

정리 스크립트는 USER/ADMIN 이외의 새 역할 저장을 막는 ck_users_api_role 제약도 만든다. 관리자 역할 변경은 ADMIN이 다른 ACTIVE 회원에 대해 사유·확인·버전을 제출하여 실행한다. 자기 계정의 역할·이용 상태 변경은 금지한다. SUPER_ADMIN은 후속 작업 범위다. 컬럼 삭제 후에는 반드시 이번 코드로 API/worker를 재시작해야 한다. 036 SQL은 폐기된 제안 이력으로만 보관한다.

## 테스트

```powershell
python -m unittest backend.tests.test_backend_api backend.tests.test_intent_search backend.tests.test_chat_review_regressions backend.tests.test_report_news_regressions backend.tests.test_report_input_paths backend.tests.test_pr_search_changes backend.tests.test_integration_storage -q
$env:RUN_LOCAL_INTEGRATION='1'
python -m unittest backend.tests.test_local_integration -q
Remove-Item Env:RUN_LOCAL_INTEGRATION
cd frontend
npm test
npm run lint
npm run build
# 로컬 API/worker/Vite와 설치된 Chrome 필요
npm run test:integration
# 채팅 재시도·리포트 전환 오류를 주입하는 별도 브라우저 회귀 검증
npm run test:review
```

Chrome이 없으면 Playwright Chromium 설치 후 CHROMIUM 환경변수로 실행 파일 경로를 지정한다. 통합 테스트는 localhost 전용이며 로컬 DB에 가상 회원/리포트를 남긴다. Redis는 테스트 자기 prefix만 정리하며 FLUSHDB를 사용하지 않는다. 브라우저 증빙은 tmp/browser-original에 저장한다.

진입점은 src/main.jsx → AppProvider(store.jsx) → 기존 App.jsx/pages다. 별도로 만들었던 live 화면과 전용 CSS를 제거했고 기존 스타일·배너·카드·모달을 기준으로 API를 연결했다. `test:e2e`도 기존 화면을 검증하는 `test:integration`과 같은 명령이다. npm test는 14개이며 목업 수치 생성 테스트를 실제 응답 변환 테스트로 교체했다.

503은 가짜 숫자/빈 성공값으로 대체하지 않는다. 409는 재조회·재저장, 429는 대기 안내다. ML/RAG, OAuth, 계정 복구·탈퇴, 일부 관리자 기능은 기존 미연결/정책 보류 상태다. 기존 UN 패널은 관심 HS6 기준으로 공개 기간과 점유율 API를 조회한다. 수집되지 않은 값/서버에서 제공하지 않는 지표는 만들어 넣지 않는다. 개인 키워드 차단의 서버 저장 기능은 유지하지만 기존 화면에는 전용 편집 UI가 없어 이번에 새 페이지를 추가하지 않았다.
