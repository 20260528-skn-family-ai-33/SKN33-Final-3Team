# 백엔드 실행 및 인계

기준: API 정의서 v0.18(2026-10-23), 같은 날짜 요구사항정의서·화면설계서, 리포트 PDF·요약보고서 DOCX. 작업 브랜치는 `feat/36-backend`이다. 기존 수집·적재 모듈을 변경하지 않고 `backend/app/api`에 HTTP API와 앱 작업 실행기를 추가했다.

## 구현 범위와 활성화 조건

- 총 130개 번호 중 삭제·예약 106~116은 경로를 만들지 않았다. 119개 경로를 등록했으며 90개는 처리 코드를, 29개는 명세의 보류·정책 미정·외부 실행기 미연결에 대한 명시적 오류 응답을 제공한다. 이것은 119개 기능 전체 완료를 뜻하지 않는다. 상세 상태는 [BACKEND_API_STATUS.md](BACKEND_API_STATUS.md)를 참조한다.
- 인증·관심·통계·기사·UN 조회·결과 스냅샷·대화 작업·리포트·구독·알림 조회/전달·일부 관리자 기능을 구현했다. 실제 RDS 쓰기와 배포는 실행하지 않았다.
- **현재 코드 실행에는 앱 저장소 보완이 필요하다.** `api_user_profile`, `api_auth_session`, `api_snapshot`, `api_keyword_block`, `api_audit_event`, `api_rate_limit`, `api_report_file` 7개가 현재 RDS에 없다. `backend/migrations/036_backend_api.sql`은 현재 구현에 대응하는 제안이며 확정 DB 계약이 아니다. 기존 테이블 통합 대안은 아래 DB 재검수 항목에 정리했다. 통합안을 선택하면 코드와 마이그레이션을 함께 변경해야 한다. 이 SQL은 기존 테이블 변경·데이터 시드·수집을 포함하지 않으며 시작/요청 시 자동 DDL은 없다.
- 활성 `REPORT_PERIOD`, `REPORT_TYPE` 및 승인된 `NOTIFICATION_TERMS`는 운영/데이터 담당자가 준비한다. 없는 옵션이나 약관을 가짜 값으로 생성하지 않는다. API-09에서 지원 주기를 확인한다. 요청형은 `RPT_ON_DEMAND`, 구독형은 `RPT_WEEKLY`, `RPT_MONTHLY`, `RPT_QUARTERLY` 옵션이 필요하다.
- ML 학습/예측, RAG 임베딩·검색·생성·문장 검증은 담당자에게 남겼다. `integrations.py`는 인터페이스 정의이며 현재 자동으로 플러그인을 로드하거나 모델을 호출하지 않는다. 기사 `summary` 필드는 현재 마스킹한 본문의 앞부분 발췌다. 자연어 채팅은 등록된 코드·국가·기간을 처리하는 제한적 규칙 파서이다.

## 설치와 실행

Python 3.11 이상에서 저장소 루트를 작업 디렉터리로 사용한다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-api.txt
python -m backend.scripts.check_api_schema
# DB 담당자가 036_backend_api.sql을 검토·적용한 후:
python -m backend.scripts.create_api_admin
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
# 별도 터미널
python -m backend.app.api.worker
```

관리자 생성 명령은 선택적인 최초 운영 설정이다. 실행할 때 비밀번호를 터미널에서 입력하며 기본 비밀번호가 없다. 기존 `admin@example.com`을 덮어쓰지 않는다. 이번 작업에서는 실행하지 않았다.

루트 `.env`에 `backend/.env.api.example`의 키를 설정한다. 기존 수집기의 `MYSQL_*`와 API의 `RDS_MYSQL_*`를 분리했다. `API_DB_TARGET=local`이면 기존 `MYSQL_*`를 사용한다. RDS 연결은 CA 인증서와 호스트명을 검증한다. `.env`/비밀번호는 커밋하지 않는다.

로컬 HTTP 개발은 `API_COOKIE_SECURE=false`, HTTPS 배포는 `true`로 둔다. 프론트/백엔드의 호스트명을 localhost 또는 127.0.0.1 중 하나로 통일한다. 교차 사이트 배포는 같은 사이트 프록시 구성을 전제로 한다(SameSite=Lax). `API_CORS_ORIGINS`에 정확한 프론트 Origin을 지정한다. API/worker가 같은 파일 저장소에 접근해야 하며, Linux는 `API_REPORT_FONT`에 한글 TTF 글꼴을 지정한다.

- `/docs`, `/openapi.json`: API 요청 필드·인증·보류 이유.
- `/health/live`: 프로세스 확인.
- `/health/ready`: DB 연결과 앱 테이블 존재 확인. 전체 옵션/컬럼/원천 데이터 준비 완료를 뜻하지 않는다.
- `worker --once`: 구독 발행 조건 확인 후 작업 1건 처리.
- 파일은 `runtime/reports`에 보관하며 Git에서 제외한다. 임시 실패 파일 정리와 법정/운영 보존 정책은 이번 변경에 포함하지 않았다.

## 프론트 담당자 연동 계약

이번 변경에는 frontend 파일을 포함하지 않는다. API 호출 클라이언트와 PR #34의 localStorage/mock store 교체는 프론트 담당자가 구현한다. 공통 경로는 `/api/v1`이며 `/docs`에서 요청 계약을 확인할 수 있다. 아래 내용은 프론트 담당자에게 전달할 백엔드 인증·응답 계약이다.

모든 요청에 `credentials: include`를 사용한다. 로그인/가입의 `csrf_token`을 메모리에 보관하고 수정 요청의 `X-CSRF-Token`에 넣는다. 새로고침 뒤에는 `GET /users/me`가 `csrf_token`을 추가 반환하므로 세션을 복구할 수 있다(원 명세에 대한 호환 확장). 세션 쿠키 자체는 HttpOnly이다. snake_case를 그대로 사용하고 bigint 식별자를 Number로 변환하지 않는다. 배열 query는 `country_codes=US&country_codes=CN`처럼 반복 전달한다.

리포트는 `POST reports/preview` → 확인된 `preview_id`와 `confirm_conditions:true`로 `POST reports` → `GET reports/{id}/status` → 웹/파일 조회 순서다. 동일 제출의 재시도에는 같은 `Idempotency-Key`, 다른 제출에는 새 키를 사용한다. PDF/DOCX 선택 파일이 모두 완성된 뒤 READY가 된다. XLSX는 저장 표로 생성하며 표별 전체 1,000행 제한을 적용한다. DB의 기존 READY 제약에 맞춰 DOCX만 선택해도 내부 보관 PDF를 만들지만, 선택하지 않은 PDF 다운로드는 제공하지 않는다.

채팅은 세션 생성 → `client_message_id`를 포함한 메시지 POST → job 폴링 → NEEDS_INPUT이면 clarification 선택값 제출 순서다. 세션에서 조건이 확정된 뒤에도 같은 메시지 키의 재시도는 같은 job을 돌려준다. 미연결 ML/RAG 요청에는 성공한 모델 결과를 만들지 않는다. SQL은 일반 화면 응답에 없으며 XLSX의 정보 시트에 포함한다. 디버그 SQL은 `API_DEBUG_SQL=true`와 SUPER_ADMIN 모두 필요하다.

503 `DATA_NOT_READY`/`FEATURE_NOT_READY`를 빈 목록이나 숫자 0으로 바꾸지 않는다. 해당 패널에 적재/연결 대기 안내를 표시한다. 409 버전 충돌은 최신 값을 재조회한 뒤 사용자가 다시 저장하게 한다. 보류 API-23/27은 422를 반환한다. RAG 미연결 기사 발췌를 AI 요약으로 표시하지 않는다.

## DB 및 담당자 연결 계약

통계는 HS2022와 `country_role=COUNTRY`만 합산한다. 월별 데이터가 존재하고 그 월 전체를 덮는 가장 최근 `batch_run`이 SUCCESS인 경우 공개한다. 실패/진행 중인 최신 실행이 있으면 그 기간을 제외한다. 수출·수입 공통 가용 범위를 메인 공개 기간으로 사용한다. 없는 관측은 0으로 추정하지 않는다. UN은 월/연과 CIF/FOB를 분리하고 누락 범위를 warnings에 표시한다.

앱이 생성하는 datetime은 UTC이다. 원천 `DATE`는 통계월/기사일이다. 기존 적재 코드가 저장한 naive `DATETIME`의 시간대는 데이터 담당자가 확인해야 한다. 프론트 표시 시간대는 Asia/Seoul로 통일한다.

리포트 worker는 60초마다 활성 구독을 확인한다. 월간은 구독 저장 당시 최신월 이후 새로 검증된 월, 분기는 구독 시작 기준 이후 3개월 모두 검증된 미발행 분기, 주간은 지난주 기사·품목/키워드 일별 집계 배치가 모두 정상 완료된 경우 생성한다. 분기는 최신 공개월이 다음 달로 넘어가도 누락분을 복구하고 기존 발행 키로 중복을 방지한다. 월간/분기는 통계 테이블을 읽을 뿐 적재 작업을 호출하지 않는다. 주간은 통계 섹션이 없다. 기존 구독에 `filters_json.enabled_since`가 없으면 자동 과거 발행을 하지 않으며 설정 재저장이 필요하다. 가입 시점의 데이터보다 과거인 모든 월을 재발행하는 기능은 없다.

`event_delivery.deliver_event(store, event_id)`는 판정 담당자가 확정한 `alert_event`를 알림함에 전달하는 연결 함수다. 알림 ON·최신 약관 동의·활성 회원·관심/차단 조건과 중복을 검사한다. **일별 집계 및 급증/주요 이슈 사건을 생산하는 판정기는 구현하지 않았다.** 이미 생성된 사건을 조회하고 전달할 준비만 되어 있다. 주요 이슈 7일/8주 비교 구간은 요구사항의 미확정 항목이므로 임의로 결정하지 않았다. 구독 리포트 발행 사건은 리포트 worker가 만들며 요청형 리포트는 알림함 사건을 만들지 않는다.

무역 급증/급감 임계값은 확정 요구사항 기본값을 사용한다. 활성 `service_option.option_code=TRADE_CHANGE_THRESHOLDS`의 JSON으로 `minimum_amount_usd`, `minimum_delta_usd`, `surge_pct`, `drop_pct`, `major_pct`를 재설정할 수 있다. 행 생성/수정 도구는 추가하지 않았다. 일회성 추정은 적재된 `anomaly_code`만 읽고 재학습/재판정하지 않는다.

## DB 재검수 및 팀 결정 필요 사항

2026-10-27 RDS를 읽기 전용으로 다시 확인했다. 기존 테이블은 31개이며 앞선 검사와 DDL 변경이 없었다. 다음 내용은 저장 정보의 부족과 설계 대안을 구분한 것으로, 신규 7개 테이블 일괄 적용 요청이 아니다.

| 대상 | 부족한 정보 / 재사용 및 보완안 |
|---|---|
| `users` | 현재 user_id/email/password_hash/landing_page만 있다. 이름(name), 권한(role), 계정 상태(account_status), 가입일(created_at), 최근 로그인(last_login_at)을 추가하면 api_user_profile을 통합할 수 있다. 임시 비밀번호 변경 안내·탈퇴 상태/사유·삭제 예정일은 해당 정책 확정 시 보완한다. 기존회원의 실제 이름·가입일을 임의 생성하지 않는다. |
| `report` | conditions_json/content_json/pdf_uri로 조건·결과·PDF 경로를 재사용한다. files_json 또는 DOCX 경로를 보완하면 api_report_file을 통합할 수 있다. JSON 필드와 파일 보관 계약 합의가 필요하다. |
| `chat_message` / 분석 결과 | intent_json/result_json을 채팅 결과 원본으로 재사용할 수 있다. 채팅 밖 분석 결과, 리포트 조건 확인 TTL, 팝업 delivery 묶음은 별도 저장/토큰 계약이 필요하다. api_snapshot 전체를 채팅 테이블로 옮기면 대화에 속하지 않는 결과를 처리할 수 없다. |
| 인증 세션 | chat_session은 대화방이다. 로그인 토큰 해시·CSRF·만료·로그아웃 상태는 api_auth_session 또는 Redis 같은 세션 저장소가 필요하다. |
| 개인 키워드 차단 | user_interest는 국가/HS6 관심 전용이고 keyword_topic은 공용 사전이다. 사용자별 대표어·선택 표현을 보관할 api_keyword_block 또는 별도 사용자 설정 확장이 필요하다. |
| 관리자 감사 이력 | article.admin_meta_json의 현재 상태, alert_event의 회원 알림, batch_run의 수집 이력과 다르다. 처리자·대상·사유·변경 전후·시각·결과를 보존하는 감사 저장소가 필요하다. |
| 요청 제한 | api_rate_limit은 현재 코드의 DB 기반 선택이다. Redis/게이트웨이를 사용하면 DB 테이블은 생략할 수 있으나 요청 제한 구현을 함께 교체해야 한다. |
| 기존 구조 재사용 | async_job, report_subscription.filters_json, notification_setting, user_consent, user_alert, article.visibility_status/admin_meta_json, article_classification, batch_run, service_option은 기존 구조를 사용한다. JSON 컬럼 존재만으로 과거 데이터/미기록 정보가 복원되지는 않는다. |

전체 요구사항에는 추가 미확정 사항도 있다. 소셜 provider+subject 연결과 password_hash NOT NULL 정합성 및 일반 동의 종류(DEC-34), 복구 토큰/임시값 만료/메일 결과(DEC-44), 탈퇴 사유·삭제 정책(DEC-46), 키워드 수동 처리 출처·보류/분야 생성 출처(DEC-32), 과거 분류 표시 보존(DEC-49), 월말 회원 통계·당시 알림 전달 제외 사유(DEC-37), 범위별 실패·재처리 기록(DEC-42)은 별도 합의가 필요하다. 기사별 분류 적용 기록은 기존 article_classification을 활용하되 이름 변경·삭제 후 과거 표시 보존까지 해결됐다고 간주하지 않는다. ML/RAG 및 원천 적재 설계는 담당자 범위로 남긴다.

## 템플릿과 충돌 처리

| 항목 | 적용 내용 / 남은 확인 |
|---|---|
| 프론트 PR #34 인증 | PR의 `/api/auth/oauth/{provider}`·accessToken/localStorage 흐름과 최신 명세의 `/api/v1/auth/social/...`·서버 세션이 다르다. 최신 명세의 세션 방식을 적용했다. OAuth 제공자 저장/연결·이메일 인증은 미연결이다. |
| 부분 Conditions / 기간 프리셋 | API-34 부분 입력 설명 및 API-43 preset과 공통 Conditions 필수 period가 충돌한다(DEC-28). 현재는 유효한 완전 조건을 받고 preset은 503으로 보류한다. 명시 비교 기간은 임의로 전년으로 바꾸지 않는다. |
| 기사 출처 | 실제 RDS의 단신 출처는 `KOTRA_SHORT_NEWS`다. 일부 문서의 다른 코드로 데이터를 바꾸지 않고 실제 저장 코드로 조회한다. |
| 리포트 PDF | 표지·목차·핵심 요약·지표/추이·수출국·분석 보류/기사·출처 순서와 녹색/베이지 스타일을 코드로 재현했다. 첨부 PDF 페이지를 배경으로 복제하는 방식은 아니며 픽셀 단위 일치는 보장하지 않는다. |
| 요약 DOCX | 첨부 DOCX의 결재란·조사환경·현황·결론·후속 검토 표를 보존한 `summary.docx`를 채우고 상세 분석을 뒤에 붙인다. 예시 수치/이름/기존 그래프를 실제 결과로 사용하지 않는다. 부서·직위·성명·전화번호 입력 API가 없으므로 빈 기입란을 둔다. |
| ML / RAG | 사용자 요청대로 구현 제외. GROWTH_RISK는 POLICY_PENDING, RAG 기사 섹션은 미연결 안내. 모델 추론·AI 검증 성공을 가장하지 않는다. |
| 추가 앱 저장소 | 기존 users에는 이름·권한·세션 등이 없어 api_ 테이블로 분리했다. 이 DDL과 JSON 확장은 구현 제안이며 기존 DB 확정 계약으로 취급하지 않는다. |
| 정책 미확정 | 탈퇴/보존, 계정 복구, 소셜 연결, 과거 통계 스냅샷, 기사 검수/벡터 반영, 분야 삭제 등은 상태표의 DEC 항목이 확정돼야 활성화한다. |

## 검증

```powershell
python -m unittest backend.tests.test_backend_api -v
python -m compileall -q backend/app/api backend/main.py backend/scripts
git diff --check
```

테스트 DB는 SQLite이며 `backend/tests/fixtures/api_schema.json`은 실제 RDS에서 읽은 컬럼 구조의 검증용 사본이다. 테스트 데이터는 메모리 DB에만 만든다. MySQL의 JSON CHECK·generated column·인덱스 잠금·동시 worker 경합은 SQLite가 재현하지 않으므로 배포 전 MySQL 통합 검증이 필요하다.

RDS에서 읽기 전용으로 연결/테이블 준비 상태/한글 HS 검색/국가/통계 공개기간/기사/환율 미준비 응답을 확인했다. 실제 회원 가입·파일 생성·관리자 변경은 RDS에서 실행하지 않았다. PR #34 프론트 전체 화면 연결·브라우저 E2E, SMTP/OAuth, ML/RAG는 테스트하지 않았다. DOCX 생성·표/치환/ZIP 검증은 했지만 LibreOffice가 없어 페이지 렌더링 검수는 못 했다. PDF는 렌더링해 확인했다.
