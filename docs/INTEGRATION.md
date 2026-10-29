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
python -m playwright install chromium
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

## 정기 리포트 구독과 자동 발행

### PR 리뷰 추가 확인 (2026-10-29)

- 관리자 정책을 사용자와 다시 확인했다. USER/ADMIN 두 역할을 유지하며 **모든 ADMIN**이 다른 ACTIVE 회원의 관리자 권한을 부여·회수하고 개발용 SQL 조회 API를 사용할 수 있다. 특정 이메일이나 최초 관리자만 허용하는 정책은 적용하지 않는다. 자기 계정의 권한·상태 변경 금지, 사유·확인·버전 검사, 세션 무효화와 감사 이력은 유지한다. SQL API는 `API_DEBUG_SQL=true`일 때만 본인 소유의 저장된 결과에 대한 SQL을 조회하며 임의 SQL 실행 기능이 아니다. DEC-48 원문을 확인했다는 의미는 아니다.
- 프로젝트에서 현재 연결하는 로컬 MySQL 검증 DB와 RDS를 읽기 전용으로 확인했다. 두 DB 모두 `api_user_profile`, `api_report_file`, `api_keyword_block`, `api_auth_session`, `api_rate_limit`이 없고 기존 `users`, `report`, `notification_setting`이 존재한다. 따라서 이 두 DB에는 036 데이터 이관 대상이 없다. 이 확인은 다른 팀원의 별도 DB까지 보장하지 않는다.
- 036은 적용하지 않는 폐기 제안이다. **036을 별도로 적용한 DB의 자동 이관은 지원하지 않는다.** 이 경우 `migrate_integration`은 기존 데이터를 보호하기 위해 중단한다. 해당 테이블을 삭제해 우회하지 말고 먼저 백업한 후 이름·권한·파일 경로·차단 키워드와 기존 컬럼의 충돌을 검토해 별도 이관해야 한다. 현재 두 DB에 이관 대상이 없음을 확인하는 리뷰 대안을 선택했으며, 데이터 복사·삭제나 RDS 스키마 변경은 실행하지 않았다.

### 설정 및 발행

구독 저장과 발행에는 `service_option`의 주기 코드 `WEEKLY`, `MONTHLY`, `QUARTERLY`(REPORT_PERIOD)와 리포트 종류 `RPT_WEEKLY`, `RPT_MONTHLY`, `RPT_QUARTERLY`(REPORT_TYPE)가 필요하다. 다음 명령은 Settings에 지정된 DB를 사용한다. 시작할 때 자동으로 등록하지 않으며, 로컬 seed나 테스트 약관을 RDS에 복사하지 않는다.

```powershell
# 대상 DB 설정을 확인한 뒤 계획을 검토한다. 기본 명령은 DB 읽기만 수행한다.
python -m backend.scripts.register_report_options
python -m backend.scripts.register_report_options --apply
```

등록 전 값과 계획은 `runtime/migration-backups`에 저장한다. 누락된 6개 코드만 추가하고, 기존 코드가 비활성 상태이면 활성화한다. 기존 라벨·설정 값과 다른 서비스 코드는 유지한다. 예상과 다른 option_type이면 적용 전에 중단하며, 적용 후 재실행은 변경 0건이어야 한다. 2026-10-29 RDS에 위 6개 코드를 정식 등록했다. 이 명령은 요청형 리포트 코드를 등록하지 않는다.

동작 순서는 구독 API 저장 → `report_subscription`의 관심 품목·국가·형식 및 가입 시점 저장 → 워커의 주기적 적재 완료 확인 → 리포트 작업 생성 → 데이터 조회와 HTML PDF 생성 → 리포트센터 다운로드다. API와 워커는 동일 DB·파일 저장 경로를 사용해야 한다. 기존 DOCX 생성과 파일 저장 방식은 유지한다.

- 발행일은 한국 시간(Asia/Seoul) 기준이며, 해당일 00:00 이후 워커가 확인한다. 주간은 매주 월요일에 직전 월요일~일요일 자료, 월간은 매월 15일에 전월 자료, 분기는 1·4·7·10월 20일에 직전 분기 자료를 발행한다. 데이터가 미리 준비되어도 발행일 전에는 생성하지 않는다.
- 구독 시작일 이후의 발행일부터 대상이 된다. 발행 당일 가입하면 다음 회차부터 적용하며 가입 전에 지난 발행일은 소급하지 않는다. 저장된 `last_observed_month`는 과거 설정과의 호환을 위해 남기지만 발행 대상은 `enabled_since`와 정기 발행일로 판단한다. 가입 때 데이터가 이미 있어도 다음 발행일의 대상 자료가 누락되지 않는다.
- 월간은 해당 월, 분기는 해당 3개월 모두 수출·수입 적재 완료를 확인한다. 주간은 `API_WEEKLY_NEWS_SOURCES`로 지정한 필수 출처별로 각 날짜를 포함하는 최신 `batch_run(target_table='article')`이 SUCCESS여야 한다. 현재 수집기는 기사·태그·일별 집계를 같은 트랜잭션으로 완료한 뒤 article 배치 하나를 기록하므로, 별도의 일별 집계 테이블 배치를 요구하지 않는다. 누락·RUNNING·FAILED이면 발행일이 지나도 완료될 때까지 기다린다.
- 같은 구독·주기·기간은 generation_key로 중복 발행을 막는다. 내부 `enqueue_due(..., user_id=...)`는 검증 계정으로 범위를 좁힐 때만 사용하며 일반 워커는 전체 활성 구독을 검사한다.
- 자동 발행은 워커가 켜져 있어야 동작한다. 로컬 PC를 끄면 발행도 멈춘다. 재가동하거나 데이터가 늦게 준비되면 가입 이후 발행일이 지난 미발행 회차를 확인해 발행한다. 이미 생성된 회차는 재생성하지 않는다. 따라서 표시 날짜는 정기 발행 예정일이며 데이터 지연·서비스 중단 시 실제 생성은 늦어질 수 있다.

주간 발행의 기본 필수 출처는 일별 수집 작업이 있는 `HANKYUNG,FNNEWS`다. `service_option`의 SOURCE 활성 여부는 조회 가능한 출처 목록이며, 활성 출처를 모두 발행 필수 조건으로 취급하지 않는다. 수동 적재인 `KOTRA_MARKET`과 선택 운영인 `KOTRA_SHORT_NEWS`는 기본 필수 조건에서 제외한다. 이 변경은 보고서에 포함할 기사 출처를 필터링하는 기능이 아니며, 기존 기간·관심 범위의 기사 조회는 유지한다.

```dotenv
API_WEEKLY_NEWS_SOURCES=HANKYUNG,FNNEWS
# 단신 일별 수집도 실제 운영하고, 발행에 반드시 필요할 때만 추가한다.
# API_WEEKLY_NEWS_SOURCES=HANKYUNG,FNNEWS,KOTRA_SHORT_NEWS
```

설정을 생략하면 기본 두 출처를 사용한다. 빈 값·알 수 없는 출처 코드는 설정 오류이며, 발행 검사에서는 **주간 구독만 건너뛰고 월간·분기는 계속 등록**한다. 매 검사 회차에 한 번 `weekly_subscription_schedule_skipped` 경고와 `API_WEEKLY_NEWS_SOURCES` 설정 오류 원인을 남긴다. 설정을 바로잡으면 아직 발행하지 않은 주간 회차를 다시 검사하고, 이미 등록된 월간·분기 회차는 중복 등록하지 않는다. 지정 출처의 SOURCE/ARTICLE_SOURCE 옵션이 없거나 비활성이면 준비되지 않은 것으로 판단한다. 한 출처가 빠졌다고 나머지 출처만으로 발행하지 않는다. 수집 서버와 발행 워커가 다를 수 있으므로 필수 목록을 수집 활성화 플래그에서 자동 추론하지 않는다. API와 워커에 같은 목록을 설정하고 변경 후 워커를 재시작한다. 워커의 첫 검사부터 전달받은 설정을 사용한다.

이 설정은 수집 작업을 켜지 않는다. 실제 운영에서는 별도 수집 스케줄러와 `NEWS_DAILY_ENABLED=true`가 필요하고, 단신을 추가했다면 `SHORTNEWS_DAILY_ENABLED=true` 및 정상적인 일별 적재 운영도 필요하다. 플래그만 켠 것으로 완료를 간주하지 않으며 실제 배치 SUCCESS를 검사한다. 기본값에서는 선택 출처의 과거 적재 이력이나 실패가 발행을 막지 않고, 필수 출처의 실패는 기존처럼 지연 발행 대상으로 남는다.

2026-10-29 정기 발행일 적용 이전의 실제 RDS 검증에서 테스트 구독 3개 저장, 월간·분기 워커 자동 발행, 수집 완료된 과거 주차로 주간 스케줄 실행, PDF/DOCX/XLSX 다운로드와 중복 방지를 확인했다. 실제 배치 기록은 수정하지 않았다. 당시 최신 무역 데이터는 2026-08이며 직전 주 기사 수집은 전체 출처가 완료되지 않아 주간 발행이 대기했다. 이후 리뷰에서 수동 KOTRA 출처까지 필수로 요구하면 기다려도 해결되지 않는 구조임을 확인하여 위의 필수 출처 설정으로 수정했다. 과거 주차 검증은 현재 주차의 자동 수집·발행 운영을 검증한 것이 아니다. 테스트 계정·구독·리포트·스냅샷·서버 파일은 검증 후 정리하고 정식 서비스 코드는 유지했다. `backend.tests.test_report_subscriptions`는 발행일 경계 외에도 과거 KOTRA 적재만 있는 상태에서 일별 HANKYUNG·FNNEWS 성공으로 12월 22일·29일 각각 한 건 발행, 필수 출처 누락·실패 차단, 선택 출처 실패, 명시적 출처 설정, 최초 워커 실행 및 중복 방지를 검증한다.

```powershell
python -m unittest backend.tests.test_report_subscriptions backend.tests.test_backend_api backend.tests.test_pr43_regressions -q
```

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
