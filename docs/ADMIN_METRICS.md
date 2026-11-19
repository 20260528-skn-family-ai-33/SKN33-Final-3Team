# 관리자 월별 집계

- 전체 회원: 회원 목록과 동일하게 users 전체 행 수(모든 상태 포함).
- 활성 구독: report_subscription.enabled=true인 주기별 구독 행 수. 회원 수가 아니다.
- GET /admin/overview: current, as_of(UTC), history(현재 월 포함 최근 12개월).
- 이번 달은 현재 값이며 is_current=true. 과거 월은 저장된 월말 관측값을 우선한다. 회원 월말 기록이 없으면 현재 users의 created_at을 KST 월 경계로 비교해 누적 집계한다(member_source=REGISTRATION). 현재 남아 있는 회원 기준이므로 물리적으로 삭제된 회원은 복원할 수 없다. 가입일 누락이 있으면 해당 보완값은 null. 구독 월말 기록이 없으면 현재 enabled=true 행의 enabled_since(시간대 포함)를 KST 월 경계로 누적 집계한다(subscription_source=START_DATE). 해지·재구독 전 이력은 포함되지 않으므로 당시 전체 활성 구독 수와 다를 수 있다. 현재 활성 구독 중 시작일 누락·오류가 있으면 보완값은 null, 실제 0은 0.
- 월말 관측: 매월 1일 00:00:00 KST 실행, 첫 1분 안의 실제 관측값을 직전 월로 기록한다. observed_at은 실제 UTC 관측 시각이다. 정각과 관측 사이의 변동은 실제 관측값에 포함된다.
- 월 PK로 중복 저장·덮어쓰기를 막는다. 꺼져 있던 달은 현재 수치로 소급 생성하지 않는다.
- 명시적 마이그레이션: backend/migrations/20261119_admin_monthly_snapshot.sql. API나 스케줄러에서 테이블을 자동 생성하지 않는다.
- 통합 스케줄러에 admin_monthly_snapshot 작업을 등록한다. API와 같은 API_DB_TARGET을 사용한다.
- 이 작업만 실행: `python -m backend.app.scheduler.jobs.admin_monthly_snapshot`. 프로세스가 월 경계에 실행 중이어야 기록된다. 로컬 실행은 컴퓨터 종료 후 다시 시작해야 한다.
