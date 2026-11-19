# 관리자 대분류 API 정의 (#137)

기존 API 정의서의 API-101·102·104에 대한 보충 및 신규 API-136·137 정의다. 실행 계약은 `backend/app/api/contracts.json`과 일치한다. 기존 API-104의 기본·출처 미확인 분야 수정 제한과 API-105의 분야 삭제·이관 금지는 유지한다. 새 API는 **중분류가 없는 관리자 대분류**만 다룬다. DB 테이블·컬럼 추가·변경은 없다.

## 조회·생성

- API-101 `GET /api/v1/admin/keyword-categories` (ADMIN): 기존 `items`, `major_names`, `base_majors`에 `editable_major_names`(미사용 관리자 대분류 이름 배열), `major_version`(대분류 목록과 중분류 상태의 불투명 버전 문자열)을 추가한다.
- API-102 `POST /api/v1/admin/keyword-categories` (ADMIN): 기존 `major_name`, 선택 `minor_name`, `reason`, `confirmed` 계약 유지. 중분류 생략 시 대분류만 즉시 저장한다. 응답은 기존 필드와 최신 `categories`(API-101 응답)를 포함한다. 기본 대분류 이름으로 단독 신규 생성은 409다.
- API-104 `PATCH /api/v1/admin/keyword-categories/{category_id}`: 기존 입력·제한 유지. 응답에 최신 `categories`를 추가한다. API-102·104로 삭제되거나 이름이 바뀐 이전 대분류에 중분류를 추가/이동하면 409 `CATEGORY_DELETED`다. 명시적으로 대분류를 다시 생성하는 동작은 가능하다.

## API-136 미사용 대분류 이름 수정

`PATCH /api/v1/admin/keyword-majors` (ADMIN)

| 요청 본문 | 형식 | 규칙 |
|---|---|---|
| major_name | string | 현재 대분류 이름, 필수 |
| new_major_name | string | trim 후 1~40자, 기존·기본 대분류 이름 중복 금지 |
| expected_version | string | API-101의 major_version, 필수 |
| reason | string | trim 후 1~1000자, 필수 |
| confirmed | boolean | 확인창 동의 true |

중분류가 없고 생성 이력으로 확인되는 대분류만 허용한다. 이름을 직접 수정하지 않고 CATEGORY_RENAME 이력의 before_json/after_json에 이전/새 major_name을 기록한다. 성공 200: `operation`, `major_name`(새 이름), `categories`(최신 API-101 응답).

## API-137 미사용 대분류 삭제

`DELETE /api/v1/admin/keyword-majors` (ADMIN)

요청 본문은 API-136에서 `new_major_name`만 제외한다. 같은 사용 여부·확인·버전 검증을 거친다. CATEGORY_DELETE 이력에 이전 major_name과 빈 after_json을 기록한다. 기존 감사 기록은 삭제하지 않는다.

> 운영 주의: 중분류 없는 대분류는 `api_audit_event`(`target_type='CATEGORY'`, `target_id LIKE 'major:%'`)에서만 복원된다. 이 기록을 삭제·아카이브·정리하면 대분류가 목록에서 사라지므로 보존 정책에서 제외한다. 전용 저장소는 후속 검토 과제다. 성공 200: `operation`, `major_name: null`, `categories`.

## 대분류 이름 비교

- `keyword_category.major_name`은 `utf8mb4_0900_ai_ci`라서 DB는 대소문자·악센트·전각/반각만 다른 이름(`AI산업`/`ai산업`/`ＡＩ산업`, `café`/`cafe`)을 같은 값으로 본다. 감사 이력 복원, 생성·수정 중복 검사, 삭제된 대분류 검사, 사용 여부, 기본 분류 비교도 같은 기준을 쓴다.
- MySQL은 `information_schema.COLUMNS`에서 읽은 컬럼 collation의 `WEIGHT_STRING`을 비교 키로 쓴다. SQLite 단위 테스트는 NFKD 정규화·결합 문자 제거·casefold 근사 키를 쓰며, 정확한 동작은 MySQL 테스트로 확인한다.
- 같은 키의 다른 철자로 대분류를 생성하거나 중분류를 추가·이동하면 409 `CATEGORY_EXISTS`이며, 메시지에 이미 등록된 철자를 알려 준다. 서버가 철자를 임의로 바꾸지 않는다.
  - 등록된 철자와 똑같이 입력한 대분류 생성·이름 수정(기본 분류 포함): "같은 이름의 대분류 또는 기본 분류가 이미 있습니다."
  - 철자만 다른 대분류 생성·이름 수정: "이미 'X'(으)로 등록된 대분류가 있습니다. 대소문자·악센트·전각/반각만 다른 이름은 같은 대분류로 봅니다."
  - 철자만 다른 중분류 추가·이동: "이미 'X'(으)로 등록된 대분류가 있습니다. 같은 철자로 입력하세요." 등록된 철자로 다시 보내면 처리된다.
- 철자만 고치는 이름 수정(`ai산업` → `AI산업`)은 허용한다. 같은 철자로의 수정은 409다.
- 이전 이력에 같은 키의 대분류가 여러 철자로 저장돼 있으면 먼저 생성된 철자 하나만 복원한다.
- 운영 데이터에 이미 철자만 다른 대분류가 있는지 확인: `SELECT major_name, COUNT(DISTINCT CAST(major_name AS BINARY)) AS spellings FROM keyword_category GROUP BY major_name HAVING spellings > 1;`

## 오류와 동시성

- 로그인 없음 401, 관리자 아님 403, 이름·사유·확인 입력 오류 400.
- 버전 불일치/대상 소멸: 409 `VERSION_CONFLICT`. 현재 중분류 있음: 409 `CATEGORY_IN_USE`. 이름 중복/기본 이름: 409 `CATEGORY_EXISTS`.
- API-102·104·136·137은 DB별 동일한 MySQL `GET_LOCK`을 사용한다. 잠금 획득과 저장은 같은 연결이며 잠금 획득 SQL의 자동 트랜잭션을 종료한 뒤 새 트랜잭션에서 인증·버전·사용 여부를 확인한다.
- 잠금 대기 2초 초과는 409 `CATEGORY_BUSY`, `retryable: true`. GET_LOCK의 NULL/SQL 오류는 503 `CATEGORY_LOCK_ERROR`로 구분한다.
- 커밋/롤백 뒤 RELEASE_LOCK을 호출한다. 연결은 풀에서 분리해 해제 실패 시 풀 재사용을 막는다. 해제 반환값이 1이 아니거나 예외이면 로그·연결 폐기를 수행한다. 커밋 성공 후 정리 오류는 성공 응답을 뒤집지 않으며 본문/커밋 오류는 유지한다.
- 이벤트는 `(created_at, audit_id)` 순서로 복원한다. 없는 대상의 수정·삭제, 존재하는 이름으로의 수정은 무시하는 보조 방어를 둔다. 저장 단계에서는 잠금 아래 재검증하여 충돌을 거절한다.
- SQLite는 이름 잠금을 생략한다. MySQL 외부 쓰기/수집 경로가 분류를 직접 변경하는 경우 이 API의 이름 잠금에 자동 참여하지 않으므로 같은 규칙을 적용해야 한다.

## 검증

단위 테스트(외부 서비스 불필요):

```sh
python -m unittest backend.tests.test_keyword_major_management backend.tests.test_category_transaction
```

실제 동시성은 `compose.integration.yml`의 MySQL 8.4에서 다음과 같이 별도 실행한다. URL은 로컬 호스트만 허용하며 테스트가 무작위 `keyword_test_*` DB를 생성하고 종료 시 삭제한다. **운영 DB/테이블에 DDL을 실행하지 않는다.**

```sh
docker compose -f compose.integration.yml up -d mysql
KEYWORD_MAJOR_MYSQL_URL='mysql+pymysql://root:local-root-only@127.0.0.1:3307/trade_integration' \
  python -m unittest backend.tests.test_keyword_major_mysql -v
```

두 연결의 동일 버전 수정·삭제, 삭제·중분류 추가 경쟁, 예외 롤백 후 다른 연결에서 즉시 재획득, READ COMMITTED 격리 수준, 컬럼 collation 기준 이름 비교(대소문자·악센트·전각 변형, 삭제된 이름의 변형 재등장, 철자만 고치는 수정)를 확인한다. 환경변수가 없으면 이 테스트만 skip한다.
