# 백엔드 실행 및 인계

기준: API 정의서 v0.18(2026-10-23), 같은 날짜 요구사항정의서·화면설계서, 리포트 PDF·요약보고서 DOCX. 현재 연동 작업 브랜치는 `feat/38-Integrate`이다. 최신 설치·저장소·Redis·검증 절차는 [INTEGRATION.md](INTEGRATION.md)를 따른다. 기존 수집·적재 모듈을 변경하지 않고 `backend/app/api`에 HTTP API와 앱 작업 실행기를 추가했다.

## 구현 범위와 활성화 조건

- 총 130개 번호 중 삭제·예약 106~116은 경로를 만들지 않았다. 119개 경로를 등록했으며 90개는 처리 코드를, 29개는 명세의 보류·정책 미정·외부 실행기 미연결에 대한 명시적 오류 응답을 제공한다. 이것은 119개 기능 전체 완료를 뜻하지 않는다. 상세 상태는 [BACKEND_API_STATUS.md](BACKEND_API_STATUS.md)를 참조한다.
- 인증·관심·통계·기사·UN 조회·결과 스냅샷·대화 작업·리포트·구독·알림 조회/전달·일부 관리자 기능을 구현했다. 로컬 통합 검증 후 RDS에는 승인된 스키마 변경만 적용한다. API/worker/Redis 서버 배포는 별도다.
- 저장소는 PM 합의에 따라 기존 users/report/notification_setting 확장, api_snapshot/api_audit_event 신규, 인증 세션/요청 제한 Redis로 구현했다. 신규 테이블 2개와 기존 테이블 컬럼 11개를 명시적으로 적용한다. 036 SQL은 폐기된 제안이며 038 마이그레이션을 사용한다. 앱 시작/요청 시 자동 DDL은 없다.
- 활성 `REPORT_PERIOD`, `REPORT_TYPE` 및 승인된 `NOTIFICATION_TERMS`는 운영/데이터 담당자가 준비한다. 없는 옵션이나 약관을 가짜 값으로 생성하지 않는다. API-09에서 지원 주기를 확인한다. 요청형은 `RPT_ON_DEMAND`, 구독형은 `RPT_WEEKLY`, `RPT_MONTHLY`, `RPT_QUARTERLY` 옵션이 필요하다.
- ML 학습/예측, RAG 검색·생성·문장 검증은 담당자에게 남겼다. 기사 `summary`는 마스킹한 본문의 앞부분 발췌다. 채팅 의도는 `IntentProvider`의 기본 구현 `OpenAIIntentProvider`가 구조화된 JSON 슬롯으로 추출한다. 규칙 기반 의도/국가/기간 파서와 앞 세 단어 품목 탐색은 제거했다. 기본 모델은 `API_INTENT_MODEL=gpt-4.1-mini`이며 HS 임베딩 모델과 별개다.
- `async_job`은 사용하지 않는다. 채팅 실행은 기존 chat_message.result_json, 리포트 실행은 report.content_json의 내부 `_execution` 필드에 저장한다. 이번 PR 검수 반영으로 로컬 실행 상태 파일 의존성을 제거했다. 신규 테이블·컬럼 없이 JSON 규약을 확장하며, 다른 기능의 api_ 저장소 보완안과는 별개다.

## 설치와 실행

Python 3.11 이상에서 저장소 루트를 작업 디렉터리로 사용한다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-api.txt
python -m backend.scripts.check_api_schema
# INTEGRATION.md 절차로 038 마이그레이션과 Redis를 준비한 후:
python -m backend.scripts.create_api_admin
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# 별도 터미널
python -m backend.app.api.worker
```

관리자 생성 명령은 선택적인 최초 운영 설정이다. 실행할 때 비밀번호를 터미널에서 입력하며 기본 비밀번호가 없다. 기존 `admin@example.com`을 덮어쓰지 않는다. 이번 작업에서는 실행하지 않았다.

루트 `.env`에 `backend/.env.api.example`의 키를 설정한다. 기존 수집기의 `MYSQL_*`와 API의 `RDS_MYSQL_*`를 분리했다. `API_DB_TARGET=local`이면 기존 `MYSQL_*`를 사용한다. RDS 연결은 CA 인증서와 호스트명을 검증한다. `.env`/비밀번호는 커밋하지 않는다.

채팅 에이전트가 붙으면 API·worker는 `API_REDIS_URL`(`redis://:<비밀번호>@127.0.0.1:6379/0`)로 Redis에 접속한다. Redis는 `deploy/redis/docker-compose.yml`로 띄우고, 같은 비밀번호를 git에 올리지 않는 `deploy/redis/redis-auth.conf`에도 둔다([채팅 에이전트 설계](AGENT_DESIGN.md) D-07). 지금 코드는 아직 이 값을 읽지 않는다.

로컬 HTTP 개발은 `API_COOKIE_SECURE=false`, HTTPS 배포는 `true`로 둔다. 프론트/백엔드의 호스트명을 localhost 또는 127.0.0.1 중 하나로 통일한다. 교차 사이트 배포는 같은 사이트 프록시 구성을 전제로 한다(SameSite=Lax). `API_CORS_ORIGINS`에 정확한 프론트 Origin을 지정한다. API/worker가 같은 파일 저장소에 접근해야 하며, Linux는 `API_REPORT_FONT`에 한글 TTF 글꼴을 지정한다.

- `/docs`, `/openapi.json`: API 요청 필드·인증·보류 이유.
- `/health/live`: 프로세스 확인.
- `/health/ready`: DB 연결, 앱 테이블·추가 컬럼, Redis 연결 확인. 전체 옵션/컬럼/원천 데이터 준비 완료를 뜻하지 않는다.
- `worker --once`: 구독 발행 조건 확인 후 작업 1건 처리.
- 파일은 `runtime/reports`에 보관하며 Git에서 제외한다. 임시 실패 파일 정리와 법정/운영 보존 정책은 이번 변경에 포함하지 않았다.

## 프론트 담당자 연동 계약

현재 src/live와 src/api/client.js가 아래 계약에 따라 실연동한다. 기존 목업 store는 진입점에서 제외했다. 공통 경로는 /api/v1이며 /docs에서 요청 계약을 확인한다.

모든 요청에 `credentials: include`를 사용한다. 로그인/가입의 `csrf_token`을 메모리에 보관하고 수정 요청의 `X-CSRF-Token`에 넣는다. 새로고침 뒤에는 `GET /users/me`가 `csrf_token`을 추가 반환하므로 세션을 복구할 수 있다(원 명세에 대한 호환 확장). 세션 쿠키 자체는 HttpOnly이다. snake_case를 그대로 사용하고 bigint 식별자를 Number로 변환하지 않는다. 배열 query는 `country_codes=US&country_codes=CN`처럼 반복 전달한다.

리포트는 `POST reports/preview` → 확인된 `preview_id`와 `confirm_conditions:true`로 `POST reports` → `GET reports/{id}/status` → 웹/파일 조회 순서다. 동일 제출의 재시도에는 같은 `Idempotency-Key`, 다른 제출에는 새 키를 사용한다. PDF/DOCX 선택 파일이 모두 완성된 뒤 READY가 된다. XLSX는 저장 표로 생성하며 표별 전체 1,000행 제한을 적용한다. DB의 기존 READY 제약에 맞춰 DOCX만 선택해도 내부 보관 PDF를 만들지만, 선택하지 않은 PDF 다운로드는 제공하지 않는다.

채팅은 세션 생성 → `client_message_id`를 포함한 메시지 POST → job 폴링 → NEEDS_INPUT이면 clarification 선택값 제출 순서다. 세션에서 조건이 확정된 뒤에도 같은 메시지 키의 재시도는 같은 job을 돌려준다. 미연결 ML/RAG 요청에는 성공한 모델 결과를 만들지 않는다. SQL은 일반 화면 응답에 없으며 XLSX의 정보 시트에 포함한다. 디버그 SQL은 `API_DEBUG_SQL=true`와 ADMIN 모두 필요하다.

질문 해석 실패, 공개 기간 오류, ML/RAG 연결 대기 및 범위 밖 안내는 마지막 성공 답변의 대화 조건을 지우지 않는다. 성공한 새 질문에서 `all_countries`/`all_products`를 명시하거나 기존 컨텍스트 해제 API를 호출하면 해당 범위를 해제한다. LLM의 `ranking_group`은 COUNTRY 또는 HS6이며 순위 대상이 불명확하면 재질문한다.

복수 품목은 표현마다 API-38 단일 선택을 반복한다. 매번 최신 `clarification_id`로 제출하며, 다음 품목이 남으면 같은 job이 다시 NEEDS_INPUT이 된다. 이미 선택한 품목은 기존 chat_message 실행 JSON에 보관하고 모든 표현을 확인한 뒤 조회한다. 따라서 첫 선택 응답만으로 완료를 가정하면 안 된다. 직접 선택(`hs_item`)도 같은 순서를 따른다. 복수 품목의 통계 답변은 품목별 표를 반환하고 각 표에 `hs_item`을 붙인다. HS4와 하위 HS6가 함께 선택되어도 합산 총계로 중복 계산하지 않는다.

API-19 및 채팅 추이 표에는 `previous_year_import_usd`(전년 수입액)를 추가했다. 전년 수출액과 각각의 집계 완료 여부로 검증하며, 검증된 월의 누락 거래는 0, 미검증 월은 null이다. 명시한 전년 비교는 방향에 맞는 금액·증감액·증감률을 답변에 표시한다. 전년 금액이 0이면 증감률을 계산하지 않으며, 비교 기간이 미검증이면 재입력을 안내한다.

503 `DATA_NOT_READY`/`FEATURE_NOT_READY`를 빈 목록이나 숫자 0으로 바꾸지 않는다. 해당 패널에 적재/연결 대기 안내를 표시한다. 409 버전 충돌은 최신 값을 재조회한 뒤 사용자가 다시 저장하게 한다. 보류 API-23/27은 422를 반환한다. RAG 미연결 기사 발췌를 AI 요약으로 표시하지 않는다.

### 프록시와 로그인 요청 제한

API_TRUSTED_PROXY_CIDRS에는 실제 리버스 프록시 IP/CIDR만 지정한다(기본 빈 값: 전달 헤더 무시). 앱이 실제 접속 상대를 검증할 수 있도록 uvicorn은 --no-proxy-headers로 실행한다. 신뢰된 상대의 X-Forwarded-For만 오른쪽부터 검사해 첫 비신뢰 주소를 사용한다. 프록시는 전달 헤더를 올바르게 덮어쓰거나 접속 주소를 추가해야 하며 공개 접속을 차단한 내부 프록시 구성에서 검증해야 한다. API_AUTH_IP_LIMIT은 IP당 분당 100회, API_AUTH_EMAIL_LIMIT은 정규화한 이메일당 분당 10회가 기본이다. 두 제한은 별도 해시 키로 기록한다. 프록시를 신뢰 목록에 넣지 않은 배포에서 실제 IP가 자동 복구되지는 않는다.

### HS 검색과 벡터 적재

API-11은 HS 코드·name_ko·original_name_ko 정확 일치 → Chroma hs_code 컬렉션의 HS2022/HS4 임베딩 후보 → 등록 품목명 부분 일치 보완 순으로 최대 5개를 반환한다. 여기서 정확 일치는 DB에 등록된 이름을 비교하는 의미이며 임의 동의어 사전을 만들지 않는다. 후보 코드는 다시 RDS에서 검증하고 자동 확정하지 않는다.

채팅은 별도 `chat_hs_search`로 HS6를 먼저 검색한다. HS6 정확 일치가 한 개이거나, 유효 벡터 후보의 1위 거리·1/2위 차이가 MEDIUM 조건을 만족하면 HS6 후보를 제시한다. 정확 일치가 여러 개이거나 신뢰도가 낮거나 HS6 후보가 없으면 HS4 검색으로 전환해 확인을 요청한다. 모든 후보는 사용자가 선택하며 API-12/38을 통한 직접 선택과 복수 품목 순차 확인을 유지한다. 명시된 4·6자리 코드는 해당 단위로 조회한다. 화면 API-11은 HS4 전용이다.

채팅의 HS6 의미 검색에는 HS 담당자가 같은 컬렉션에 적재한 HS2022/depth=6 벡터가 필요하다. 벡터가 없으면 DB 정확 일치나 HS4 확인·분류 탐색으로 진행한다. 이번 변경에서 HS6 데이터/벡터를 적재하지 않았으며 검색 단위가 달라도 동일 검색어의 질의 임베딩은 재사용한다.

모델은 기존 적재기의 text-embedding-3-large를 사용한다. API_HS_CHROMA_PATH(기본 chroma_data), OPENAI_API_KEY, API_HS_VECTOR_ENABLED를 설정한다. API_HS_MAX_DISTANCE의 기본 0.70은 후보 필터이며 정답 보장값이 아니다. 검색 품질 평가는 담당자 확인이 필요하다. 벡터 연결 실패 시 명시적인 warnings와 등록명 부분 일치만 반환한다. API 요청에서는 적재·컬렉션 생성 작업을 실행하지 않는다.

중복 경로였던 `backend/scripts/sync_hs_vectors.py`는 제거했다. 벡터 적재는 HS 담당의 `python -m backend.app.scheduler.jobs.hs_code_embed`로 일원화한다. 이 기존 작업은 자체 MYSQL_* 연결 설정을 사용하므로 운영 DB 대상은 담당자가 설정한다(비밀번호 포함 URL을 명령에 노출하지 않는다). API 서버는 컬렉션을 읽기만 한다. 과거 로컬 검증 데이터는 HS2 96개/HS4 1,228개이며, 배포 범위(HS2/4 또는 전체), 배포 경로, 갱신/재색인 담당은 별도 확정이 필요하다. 기존 적재기의 '새 ID만 추가' 동작도 유지하며 이번 수정에서 재적재하지 않았다.

검색어는 Unicode 정규화·공백 정리·대소문자 정규화 후 2~120자로 제한한다. 2자 미만은 임베딩 없이 빈 결과를 반환한다. API-11은 신뢰 프록시 검증을 거친 IP당 기본 분당 60회(`API_HS_IP_LIMIT`)로 제한하며, Redis 요청 제한 저장소를 사용한다. 캐시 적중도 요청 제한에 포함한다. 검색 결과와 질의 벡터는 각각 프로세스당 최대 256개, 300초 TTL로 캐시하며 같은 키의 동시 요청은 합친다. 여러 워커 사이의 캐시는 공유하지 않는다. 기준 데이터 재적재 후 API_HS_CACHE_VERSION을 변경/재시작하거나 TTL 만료를 기다린다. API 요청의 OpenAI 임베딩은 기본 timeout 3초, 자동 재시도 0회다(초기 라이브러리/Chroma 로딩 시간은 별도).

정확 일치는 confidence=EXACT, 유사 후보는 LOW 또는 MEDIUM이다. 기본 거리 상한 0.70 외에 1위 거리 0.45 이하와 1·2위 차이 0.08 이상을 모두 만족할 때만 MEDIUM으로 표시한다. 이는 정답 확률이 아니며 모든 결과는 requires_selection=true다. 비슷한 후보 또는 빈 결과에도 browse.url을 반환한다. API-12는 ROOT→HS2, HS2→HS4, HS4→HS6를 조회한다. API-38은 품목 되묻기에서 기존 choice_ids 대신 직접 선택한 hs_item을 받을 수 있으며 두 필드 동시 제출은 거절한다. 새 국가/품목/기간 슬롯과 응답 확장은 프론트 연동 시 함께 반영해야 한다.

신뢰도는 DB에 존재하는 HS2022/HS4 후보를 검증하고 코드 중복을 제거한 뒤 계산한다. 제외된 벡터 ID의 점수는 사용하지 않으며, 유효 후보가 하나뿐이거나 등록명 부분 일치만 있으면 LOW로 표시한다.

### 채팅 슬롯 추출과 프론트 연동

LLM 호출은 intent·국가의 원문 표현/한국어 이름·품목 원문 표현·기간·비교 기간·수출입 방향을 한 번에 추출한다. SQL이나 추론한 HS 코드를 받지 않는다. 원문 근거, country/hs_code 사전, 공개 기간을 서버에서 검증한다. 잘못된 국가·기간·숫자 품목, 응답 파싱 실패 등은 작업 FAILED 대신 재입력 안내(NO_DATA/NEEDS_REVIEW)로 돌려준다. 품목은 HS6 우선 후보 선택을 요청하며 후보가 없어도 분류에서 직접 선택할 수 있다. 일반적으로 같은 작업의 품목/관심 확인 후에는 기존 메시지 실행 JSON의 intent_slots를 재사용해 LLM을 재호출하지 않는다. 모델 실패 시 규칙 파서로 되돌아가지 않는다. 비교는 현재 고정 SQL이 지원하는 같은 기간의 전년 대비만 허용하고 다른 비교는 재입력을 요청한다.

국가명은 공백 제거·정규화 후 DB 이름 정확 일치가 우선이며, 없을 때 포함 일치 후보가 하나면 채택한다. 체코→체코공화국, 러시아→러시아 연방 등을 허용하되 콩고처럼 여러 후보면 전체 국가명을 다시 요청한다. 다른 유효 국가가 함께 있어도 모호한 국가를 조용히 버리지 않는다. SK/TV 등 모호한 두 글자 영문 약어 제외 정책은 유지한다. 숫자 HS4/HS6는 HS 접두어 없이도 HS2022 사전에 존재하면 인정한다. 연도·금액·수량 문맥은 코드로 오인하지 않으며 미등록 코드는 재입력을 안내한다.

임베딩 실패로 warnings가 붙은 검색 결과는 캐시하지 않는다. 다음 요청에서 즉시 다시 검색하며, 복구 후 정상 결과만 300초 캐시한다. 검색 결과 캐시는 HS4/HS6별로 구분한다.

기본 제공자는 store=False, timeout 기본 10초, SDK 자동 재시도 0회로 동작한다. 마스킹된 질문과 허용된 이전 조건만 전송하며 회원 정보나 DB 실적을 보내지 않는다. 팀 의도 모델은 db.intent_provider에 IntentProvider 구현을 주입하여 교체할 수 있다. ML/RAG 본문 생성은 연결하지 않았다. 구현 방식은 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)를 참고했다.

현재 live/interests.jsx가 사전 API를 호출한다. 기존 목업 Hs4Search는 현재 진입점에 포함하지 않는다. 아래 규칙을 적용했다.

1. 검색어가 **2글자 이상이고 마지막 입력 후 300ms가 지난 때**에 API-11 호출. 한 조건만 만족하는 OR가 아니다.
2. 입력 변경/컴포넌트 해제 시 타이머와 이전 fetch를 취소하고, 취소 이후 도착한 응답도 버린다. 429는 즉시 반복 호출하지 않는다.
3. SIMILAR는 '비슷한 후보'로 표시하고 자동 확정하지 않는다. 결과 유무와 무관하게 browse.url의 분류 선택 버튼을 표시한다.
4. ROOT/HS2/HS4의 children을 순차 호출하여 직접 선택한다. 관심 품목은 기존처럼 HS6를 저장한다. 채팅 직접 선택은 API-38에 clarification_id와 hs_item을 제출한다.
5. 캐시는 백엔드가 담당하므로 별도의 프론트 캐시 구현이 필수는 아니다. 서버 캐시만으로 300ms 대기 UI가 구현되지는 않는다.

PR #34 적용 지점은 `frontend/src/components/pickers.jsx`의 `Hs4Search`다. 현재 `onConfirm(group)`의 `{code,name,items}` 구조를 유지하려면 API-11의 `{hs_code,name_ko}`를 code/name으로 매핑하고, HS4 선택 시 API-12의 HS6 목록을 `items: rows.map(row => [row.hs_code,row.name_ko])`로 전달한다. 로컬 hs4Groups를 실제 후보와 섞으면 없는 코드가 다시 노출되므로 API 연동 시 해당 mock 검색을 교체한다.

직접 선택 예: `POST /api/v1/chat/jobs/{job_id}/clarifications`에 `{"clarification_id":"...","hs_item":{"hs_code":"190230","hs_ver":"HS2022"}}`를 세션/CSRF 헤더와 함께 보낸다. 예시 코드는 실제 사전 존재 여부를 검증받는다.

### 환율과 알림 조회

fx_daily는 2026-10-27 기존 적재기로 RDS에 43개 통화/8,557건 적재되어 있다. 환율 조회는 통화 정보를 한 번 읽고 윈도 함수로 각 통화의 최근 두 영업일을 한 번에 조회한다. quote_unit(예: JPY 100)을 유지하고 직전 영업일 기준 등락을 계산한다. 실제 0과 미적재를 혼동하지 않으며 빈 통화는 계속 UNAVAILABLE이다. 이번 수정은 새 수집기를 추가하거나 정기 적재를 자동 설정하지 않는다.

알림은 차단 키워드·기사 노출 조건을 SQL EXISTS/JSON 조건으로 필터링하고 COUNT와 LIMIT/OFFSET으로 조회한다. 페이지의 기사·태그는 묶어서 읽으며, 상세 조회는 ID 조건을 적용한다. 팝업은 미확인 대상 최대 100개를 전달하고 확인 이후 남은 대상을 다음 호출에 제공한다. ⑩ 관리자 기사 목록의 영향 리포트 계산은 미확정 요청에 따라 그대로 유지한다.

## DB 및 담당자 연결 계약

### async_job 없는 채팅·리포트 실행

report.status/generation_key/retry_count, chat_message의 조건·결과 저장 구조를 재사용한다. conditions_json은 생성 조건으로 유지한다. content_json은 완료 결과와 내부 `_execution`을 함께 보관하도록 이번 검수 요청에 따라 확장했다. 이 필드는 원 DB 문서에 이미 확정된 필드가 아니며, 프론트 응답·문서 본문에는 노출하지 않는다.

| 기능 | 저장·처리 위치 |
|---|---|
| 채팅 접수·상태 조회 | 질문 USER 행을 접수 시 chat_message에 한 번 저장한다. API의 job_id는 해당 message_id다. 사용자 소유권은 chat_session과의 연결로 확인한다. |
| 대기·되묻기·재시작 | USER result_json의 `_execution`(version=1)에 요청 키, 확정 전 조건, 상태, 되묻기, 실행 토큰·유효 시각을 보관한다. 이 실행 메타데이터는 이번 구현의 JSON 확장이며 원문에 이미 정의된 필드라고 취급하지 않는다. 프론트에는 기존 Job 응답으로 변환하고 내부 메타데이터를 그대로 노출하지 않는다. |
| 완료 답변 | ASSISTANT content_masked/intent_json/result_json에 답변·확정 조건·결과를 저장한다. 같은 질문의 재시도·되묻기는 USER 행을 추가하지 않는다. |
| 리포트 작업 등록 | report의 QUEUED 행과 generation_key UNIQUE를 사용한다. 기존 요청형·구독형 리포트 ID를 그대로 사용한다. |
| 리포트 전체 상태 | report.status/retry_count가 기준이다. READY 결과는 재실행하지 않는다. |
| 리포트 실행 상태 | report.content_json._execution에 실행 토큰·유효 시각·시도 횟수·재시도 가능 시각·단계·오류를 저장한다. report 행의 상태와 같은 DB 트랜잭션으로 갱신한다. 과거 로컬 JSON/JSONL 파일은 읽지 않는다. |

리포트 작업 발견·선점은 RDS만 사용하므로 API/worker의 로컬 디렉터리가 달라도 실행할 수 있다. 완성 PDF/DOCX 다운로드에는 여전히 동일 경로의 공유 파일 볼륨이 필요하다. 파일 전달용 객체 저장소 연결은 별도 작업이다. `_execution` 없는 기존 미완료 report 행은 상태·retry_count·generation_key로 실행 정보를 구성해 재개한다. 원래 요청 조건이 현재 생성기 계약에 맞아야 하며, 과거 오류 내역을 복원하지는 않는다. 기존 worker를 종료하고 새 버전으로 전환한다.

중복 질문은 사용자 행 잠금과 요청 키 조회로, 동시 채팅 처리는 대화방→메시지 순서의 잠금으로 막는다. 워커는 기존 chat_message.result_json과 report.status/content_json에서 실행 상태와 재시도/만료 시각을 SQL로 거른 다음 테이블별 API_JOB_SCAN_SIZE(기본 100개)를 적용한다. 완료 이력과 아직 실행할 수 없는 작업은 후보 상한에 포함하지 않아 과거 이력을 100개씩 읽으며 2초씩 기다리지 않는다. 후보가 잠겨 있으면 다음 호출에서 그 뒤를 확인하고, 선점에 성공하면 해당 종류의 커서를 초기화해 오래된 만료 작업도 다시 확인한다. 전체 큐의 엄격한 FIFO는 보장하지 않는다. 실행 유효 시각은 10분이고 실행 중 대상 행 잠금은 유지한다. 재시작 시 만료 작업을 새 토큰으로 재개하며 이전 토큰의 저장은 거부한다. 테이블·컬럼 추가나 운영 DDL 적용은 없다. 채팅 JSON 필터 자체의 DB 스캔 비용은 남으므로 대규모 운영에서는 EXPLAIN/부하 측정 후 기존 테이블의 인덱스를 별도로 검토해야 한다.

리포트는 최초 실행을 포함해 API_REPORT_MAX_ATTEMPTS(기본 3회)까지 시도한다. 회차당 1회 즉시 재시도하고 다시 실패하면 DELAYED, API_REPORT_RETRY_DELAY_SECONDS(기본 300초) 후 다음 시도를 한다. 총 상한 도달·재시도 불가 오류는 FAILED로 종료하고 retryable=false를 반환한다. retry_count는 최초를 제외한 실제 재실행 횟수다. 대상이 살아 있는 NOT_FOUND 오류도 FAILED로 처리한다. 구독 예약 실패는 별도로 기록하고 5초 이후 재시도하며, 그동안 채팅·리포트 작업 처리는 계속한다. 작업 처리 루프 자체의 DB 오류는 기록 후 5초 뒤 재시도한다. --once에서도 예약 실패와 별개로 작업 처리를 한 번 시도한 다음 예약 오류를 종료 코드에 반영한다. 실제 MySQL 동시 워커/공유 볼륨 장애 검증은 별도로 필요하다.

이전 async_job 기반 코드를 운영했다면 자동 이전은 제공하지 않는다. 기존 미완료 작업을 확인하고 별도 이관 기준을 정해야 한다. 이번 작업에서는 실제 RDS에 작업을 등록하거나 데이터를 이관하지 않았다.

통계는 HS2022와 `country_role=COUNTRY`만 합산한다. 그 월 전체를 덮는 가장 최근 batch_run이 SUCCESS인 경우 공개한다. 실패/진행 중인 최신 실행이 있으면 그 기간을 제외한다. 수출·수입 공통 가용 범위를 메인 공개 기간으로 사용한다. 검증된 월 안에서 선택 국가·품목의 거래 행이 없으면 추이·순위 금액을 0으로 처리한다. 검증되지 않은 기간을 0으로 채우지 않으며 UN의 결측 표시 규칙은 별개로 유지한다. 검증 월·공개 범위·데이터 버전은 요청 Store 안에서 재사용하며 새 요청/스케줄 회차에서는 다시 확인한다.

API-24 변화 목록은 검증된 현재·비교 월의 거래 키 합집합을 비교한다. 누락 거래를 0으로 보아 신규 진출은 `new_entries`(성장률 null), 거래 중단은 `drops`(-100%)에 포함한다. 기존 최소 거래액·증감액 기준과 국가/품목 필터는 그대로 적용한다. 비교 월이 미검증이면 0으로 채우지 않고 오류를 반환한다. 성장률이 정의되지 않는 신규 거래를 API-22의 성장률 순위에 임의 삽입하지 않는다.

앱이 생성하는 datetime은 UTC이다. 원천 `DATE`는 통계월/기사일이다. 기존 적재 코드가 저장한 naive `DATETIME`의 시간대는 데이터 담당자가 확인해야 한다. 프론트 표시 시간대는 Asia/Seoul로 통일한다.

리포트 worker는 60초마다 활성 구독을 확인한다. 월간은 구독 저장 당시 최신월 이후 새로 검증된 월, 분기는 구독 시작 기준 이후 3개월 모두 검증된 미발행 분기, 주간은 지난주 기사·품목/키워드 일별 집계 배치가 모두 정상 완료된 경우 생성한다. 분기는 최신 공개월이 다음 달로 넘어가도 누락분을 복구하고 기존 발행 키로 중복을 방지한다. 월간/분기는 통계 테이블을 읽을 뿐 적재 작업을 호출하지 않는다. 주간은 통계 섹션이 없다. 기존 구독에 `filters_json.enabled_since`가 없으면 자동 과거 발행을 하지 않으며 설정 재저장이 필요하다. 가입 시점의 데이터보다 과거인 모든 월을 재발행하는 기능은 없다.

`event_delivery.deliver_event(store, event_id)`는 판정 담당자가 확정한 `alert_event`를 알림함에 전달하는 연결 함수다. 알림 ON·최신 약관 동의·활성 회원·관심/차단 조건과 중복을 검사한다. **일별 집계 및 급증/주요 이슈 사건을 생산하는 판정기는 구현하지 않았다.** 이미 생성된 사건을 조회하고 전달할 준비만 되어 있다. 주요 이슈 7일/8주 비교 구간은 요구사항의 미확정 항목이므로 임의로 결정하지 않았다. 구독 리포트 발행 사건은 리포트 worker가 만들며 요청형 리포트는 알림함 사건을 만들지 않는다.

무역 급증/급감 임계값은 확정 요구사항 기본값을 사용한다. 활성 `service_option.option_code=TRADE_CHANGE_THRESHOLDS`의 JSON으로 `minimum_amount_usd`, `minimum_delta_usd`, `surge_pct`, `drop_pct`, `major_pct`를 재설정할 수 있다. 행 생성/수정 도구는 추가하지 않았다. 일회성 추정은 적재된 `anomaly_code`만 읽고 재학습/재판정하지 않는다.

## 이전 DB 검토 기록 및 후속 정책 사항

아래 표는 2026-10-27의 설계 대안 기록이다. 현재 확정안은 INTEGRATION.md를 우선한다. 당시 RDS를 읽기 전용으로 다시 확인했다. 기존 테이블은 31개이며 앞선 검사와 DDL 변경이 없었다. 다음 내용은 저장 정보의 부족과 설계 대안을 구분한 것으로, 신규 7개 테이블 일괄 적용 요청이 아니다.

| 대상 | 부족한 정보 / 재사용 및 보완안 |
|---|---|
| `users` | 현재 user_id/email/password_hash/landing_page만 있다. 이름(name), 권한(role), 계정 상태(account_status), 가입일(created_at), 최근 로그인(last_login_at)을 추가하면 api_user_profile을 통합할 수 있다. 임시 비밀번호 변경 안내·탈퇴 상태/사유·삭제 예정일은 해당 정책 확정 시 보완한다. 기존회원의 실제 이름·가입일을 임의 생성하지 않는다. |
| `report` | conditions_json/content_json/pdf_uri로 조건·결과·PDF 경로를 재사용한다. files_json 또는 DOCX 경로를 보완하면 api_report_file을 통합할 수 있다. JSON 필드와 파일 보관 계약 합의가 필요하다. |
| `chat_message` / 분석 결과 | intent_json/result_json을 채팅 결과 원본으로 재사용할 수 있다. 채팅 밖 분석 결과, 리포트 조건 확인 TTL, 팝업 delivery 묶음은 별도 저장/토큰 계약이 필요하다. api_snapshot 전체를 채팅 테이블로 옮기면 대화에 속하지 않는 결과를 처리할 수 없다. |
| 인증 세션 | chat_session은 대화방이다. 로그인 토큰 해시·CSRF·만료·로그아웃 상태는 api_auth_session 또는 Redis 같은 세션 저장소가 필요하다. |
| 개인 키워드 차단 | user_interest는 국가/HS6 관심 전용이고 keyword_topic은 공용 사전이다. 사용자별 대표어·선택 표현을 보관할 api_keyword_block 또는 별도 사용자 설정 확장이 필요하다. |
| 관리자 감사 이력 | article.admin_meta_json의 현재 상태, alert_event의 회원 알림, batch_run의 수집 이력과 다르다. 처리자·대상·사유·변경 전후·시각·결과를 보존하는 감사 저장소가 필요하다. |
| 요청 제한 | api_rate_limit은 현재 코드의 DB 기반 선택이다. Redis/게이트웨이를 사용하면 DB 테이블은 생략할 수 있으나 요청 제한 구현을 함께 교체해야 한다. |
| 기존 구조 재사용 | chat_session/chat_message, report/report_subscription, notification_setting, user_consent, user_alert, article.visibility_status/admin_meta_json, article_classification, batch_run, service_option은 기존 구조를 사용한다. async_job은 사용하지 않는다. JSON 컬럼 존재만으로 과거 데이터/미기록 정보가 복원되지는 않는다. |

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
| 추가 앱 저장소 | PM 합의대로 기존 3개 테이블 확장, api_snapshot/api_audit_event 추가, Redis 세션/요청 제한을 적용한다. |
| 정책 미확정 | 탈퇴/보존, 계정 복구, 소셜 연결, 과거 통계 스냅샷, 기사 검수/벡터 반영, 분야 삭제 등은 상태표의 DEC 항목이 확정돼야 활성화한다. |

## 리포트 비교 조건과 결과표 재사용

API-43은 최종 월초·월말 및 최신 공개 월로 조정한 분석 기간을 기준으로 비교 기간을 확정한다. `MOM`은 한 달 분석의 직전 월이며 여러 달을 지정하면 `422 UNSUPPORTED_COMPARISON`으로 안내한다. `YOY`·`YOY_PERIOD`는 같은 분석 기간을 12개월 앞당긴다. 12개월을 넘는 분석에서 전년 동기 기간이 일부 겹치는 것은 허용한다. 명시한 비교 기간이 이 규칙과 다르면 400이며, 임의로 다른 기간으로 바꾸지 않는다. `CUSTOM`은 명시한 비교 기간이 필수이고 분석 시작 전에 끝나야 한다. `NONE`은 비교 기간을 받지 않고 비교 계산·증감률 문구를 생성하지 않는다. 월 단위 조정은 비교 기간에도 적용하며 조정 사항을 `applied_defaults`에 기록한다. 비교 데이터가 미수집이면 기존 정책대로 리포트에 경고와 null 증감률을 제공한다.

새 RESULT 스냅샷에는 공통 `Conditions` 필드만 저장한다. 리포트의 sections·formats·request_hash 등 내부 정보는 report 행에 보관한다. 이전 RESULT에 내부 필드가 섞여 있어도 API-43은 소유권·유효기간·종류를 확인한 후 분석 필드만 읽는다. 직접 입력에는 기존의 엄격한 필드 검증을 유지한다. `BOTH` 추이 결과를 수출로 임의 변환하지 않으며 `422 UNSUPPORTED_TRADE_FLOW`와 수출만 선택해 재조회하라는 안내를 반환한다. `IMPORT`도 기존 수출 전용 리포트 정책에 따라 422이다.

## 기존 백엔드 단계 검증 기록

```powershell
python -m unittest backend.tests.test_backend_api -v
python -m compileall -q backend/app/api backend/main.py backend/scripts
git diff --check
```

테스트 DB는 SQLite이며 `backend/tests/fixtures/api_schema.json`은 실제 RDS에서 읽은 컬럼 구조의 검증용 사본이다. 테스트 DB 생성 시 async_job은 제외하고 전체 API 테스트를 실행한다. 테스트 데이터는 메모리 DB에만 만든다. MySQL의 JSON CHECK·generated column·인덱스 잠금·동시 worker 경합은 SQLite가 재현하지 않으므로 배포 전 MySQL 통합 검증이 필요하다.

RDS에서 읽기 전용으로 연결/테이블 준비 상태/한글 HS 검색/국가/통계 공개기간/기사/환율 미준비 응답을 확인했다. 실제 회원 가입·파일 생성·관리자 변경은 RDS에서 실행하지 않았다. PR #34 프론트 전체 화면 연결·브라우저 E2E, SMTP/OAuth, ML/RAG는 테스트하지 않았다. DOCX 생성·표/치환/ZIP 검증은 했지만 LibreOffice가 없어 페이지 렌더링 검수는 못 했다. PDF는 렌더링해 확인했다.


최신 실연동 검증은 INTEGRATION.md 명령과 INTEGRATION_REVIEW.md 결과를 참조한다. 아래 기존 기록의 프론트/로컬 MySQL 미검증 항목은 이번 통합 검증으로 보완한다.
