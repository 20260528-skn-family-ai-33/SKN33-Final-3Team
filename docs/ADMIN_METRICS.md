# 관리자 월별 집계

- main과 동일한 DB 구조를 사용한다. 테이블·컬럼 추가 및 별도 마이그레이션이 필요하지 않다.
- 전체 회원: users 전체 행 수(모든 상태 포함).
- 활성 구독: report_subscription.enabled=true인 주기별 구독 행 수. 회원 수가 아니다.
- GET /admin/overview: current, as_of(UTC), history(현재 월 포함 최근 12개월).
- 이번 달은 현재값이다. 과거 회원 수는 현재 users.created_at을 KST 월 경계로 누적 계산한다(member_source=REGISTRATION).
- 과거 활성 구독은 현재 enabled=true 행의 filters_json.enabled_since(시간대 포함)를 KST 월 경계로 누적 계산한다(subscription_source=START_DATE).
- 가입일 또는 활성 구독 시작일이 누락·오류인 행만 과거 집계에서 제외한다. 응답의 excluded.total_members / excluded.active_subscriptions는 제외 건수다. 정상 날짜를 가진 행은 계속 누적하며 0은 0으로 표시한다. users.created_at의 시간대 없는 값은 기존 UTC 저장 규칙에 따라 정상 처리하고, 구독 enabled_since에는 시간대가 필요하다.
- 이번 달은 미상·오류 행을 포함한 전체 현재값이므로 마지막 달 증가폭이 커 보일 수 있다. 화면에서 회원·구독 각각 제외 건수와 집계 차이를 안내한다.
- DEC-37(과거 월말 회원·구독 스냅샷 저장 계약) 확정 전 임시 방식이다. 월말 수집기 및 월말 수치 고정 저장은 사용하지 않는다.
- 알려진 한계(후속 과제): 전체 회원에는 탈퇴·이용 제한 계정이 포함된다. 회원 탈퇴·삭제, 구독 해지/재시작에 따라 과거 수치도 달라질 수 있다. 과거 수치 보존 방식은 DEC-37 합의 후 별도로 처리한다.

## 대분류 관리

- 기존 api_audit_event의 CATEGORY / major:<이름> 생성·수정·삭제 이벤트를 (created_at, audit_id) 오름차순으로 복원한다. 순서는 결정적이지만 커밋 순서를 의미하지 않는다.
- **운영 주의: `api_audit_event`의 `target_type='CATEGORY'`, `target_id LIKE 'major:%'` 기록을 삭제·보관 이전(아카이브)·정리하지 않는다.** 중분류 없는 관리자 대분류는 이 기록에서만 복원되므로 지우면 목록에서 사라진다. 전용 저장소로 옮기는 것은 후속 검토 과제다.
- 중분류 없는 관리자 대분류만 수정·삭제할 수 있다. 기본 분류와 중분류 삭제·이관 제한은 유지한다.
- API-101은 major_version과 editable_major_names를 반환한다. API-136·137은 확인·사유·expected_version을 받아 잠금 획득 후 최신 상태를 확인한다.
- API-102·104·136·137은 같은 DB별 MySQL 이름 잠금과 같은 연결에서 처리한다. 시작일 집계와 대분류 모두 main의 DB 테이블·컬럼만 사용하며 마이그레이션은 없다.
- 세부 계약과 동시성 검증 방법: [관리자 대분류 API](ADMIN_KEYWORD_API.md).
