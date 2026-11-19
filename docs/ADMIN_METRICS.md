# 관리자 월별 집계

- main과 동일한 DB 구조를 사용한다. 테이블·컬럼 추가 및 별도 마이그레이션이 필요하지 않다.
- 전체 회원: users 전체 행 수(모든 상태 포함).
- 활성 구독: report_subscription.enabled=true인 주기별 구독 행 수. 회원 수가 아니다.
- GET /admin/overview: current, as_of(UTC), history(현재 월 포함 최근 12개월).
- 이번 달은 현재값이다. 과거 회원 수는 현재 users.created_at을 KST 월 경계로 누적 계산한다(member_source=REGISTRATION).
- 과거 활성 구독은 현재 enabled=true 행의 filters_json.enabled_since(시간대 포함)를 KST 월 경계로 누적 계산한다(subscription_source=START_DATE).
- 가입일 또는 활성 구독 시작일이 누락·오류이면 해당 과거 집계는 null, 실제 0은 0으로 표시한다.
- 월말 수집기 및 월말 수치 고정 저장은 사용하지 않는다. 회원 삭제·구독 해지/재시작에 따라 과거 수치도 달라질 수 있으며 역사적 총계 보존은 후속 과제로 남긴다.

## 대분류 단독 추가

- 기존 api_audit_event의 CATEGORY_CREATE / CATEGORY / major:<이름> 이력에서 대분류를 읽고, keyword_category의 대분류와 합쳐 표시한다.
- 중분류는 기존 keyword_category에 저장한다. 이전 구현이 저장한 동일 형식의 대분류 이력도 그대로 표시한다.
- 대분류 목록의 저장 근거이므로 해당 추가 이력을 삭제하면 중분류 없는 대분류도 목록에서 사라진다.
