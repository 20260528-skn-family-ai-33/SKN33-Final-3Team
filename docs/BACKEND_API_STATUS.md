# API 구현 상태

코드가 있는 경로도 DB 마이그레이션·원천 적재·옵션 준비가 필요하다. 아래 표는 기능 전체 검증 완료표가 아니다.

| API | 경로 | 현재 상태 |
|---|---|---|
| 001 | `POST  /api/v1/auth/signup` | 처리 코드 구현 |
| 002 | `POST  /api/v1/auth/login` | 처리 코드 구현 |
| 003 | `POST  /api/v1/auth/logout` | 처리 코드 구현 |
| 004 | `GET  /api/v1/users/me` | 처리 코드 구현 |
| 005 | `PATCH  /api/v1/users/me` | 처리 코드 구현 |
| 006 | `POST  /api/v1/users/me/email-check` | 처리 코드 구현 |
| 007 | `POST  /api/v1/users/me/password` | 처리 코드 구현 |
| 008 | `DELETE  /api/v1/users/me` | 보류: DEC-46 탈퇴 사유 및 보존·삭제 정책 |
| 009 | `GET  /api/v1/config/options` | 처리 코드 구현 |
| 010 | `GET  /api/v1/data/availability` | 처리 코드 구현 |
| 011 | `GET  /api/v1/reference/hs-codes` | DB 코드·이름 정확 일치 및 HS2022/HS4 Chroma 임베딩 후보; 등록명 보완 검색 |
| 012 | `GET  /api/v1/reference/hs-codes/{hs_code}/children` | 처리 코드 구현 |
| 013 | `GET  /api/v1/reference/countries` | 처리 코드 구현 |
| 014 | `GET  /api/v1/reference/regions` | 처리 코드 구현 |
| 015 | `GET  /api/v1/users/me/interests` | 처리 코드 구현 |
| 016 | `PUT  /api/v1/users/me/interests` | 처리 코드 구현 |
| 017 | `POST  /api/v1/users/me/interests/items` | 처리 코드 구현 |
| 018 | `GET  /api/v1/trade/kpis` | 처리 코드 구현 |
| 019 | `GET  /api/v1/trade/trends` | 처리 코드 구현 |
| 020 | `GET  /api/v1/trade/rankings` | 처리 코드 구현 |
| 021 | `GET  /api/v1/trade/rankings/{country_code}/products` | 처리 코드 구현 |
| 022 | `GET  /api/v1/trade/fast-growing-products` | 처리 코드 구현 |
| 023 | `POST  /api/v1/analysis/promising-markets` | 보류: ML 담당 협의 보류 |
| 024 | `POST  /api/v1/analysis/changes` | 처리 코드 구현 |
| 025 | `POST  /api/v1/analysis/market-shares` | 처리 코드 구현 |
| 026 | `POST  /api/v1/analysis/share-trends` | 처리 코드 구현 |
| 027 | `POST  /api/v1/analysis/market-patterns` | 보류: ML 담당 협의 보류 |
| 028 | `POST  /api/v1/analysis/local-issues` | 통계+기사 본문 발췌; RAG 분석 미연결 |
| 029 | `GET  /api/v1/articles` | 검색/필터; summary는 본문 발췌 |
| 030 | `GET  /api/v1/articles/{article_id}` | 마스킹 본문; AI 요약 미연결 |
| 031 | `GET  /api/v1/issues/major` | 확정 alert_event 조회; 사건 판정기는 별도 담당 |
| 032 | `GET  /api/v1/exchange-rates` | 적재된 fx_daily 최근 두 영업일 일괄 조회·단위/등락 계산; 빈 통화는 UNAVAILABLE |
| 033 | `GET  /api/v1/chat/sessions` | 처리 코드 구현 |
| 034 | `POST  /api/v1/chat/sessions` | 처리 코드 구현 |
| 035 | `GET  /api/v1/chat/sessions/{session_id}/messages` | 처리 코드 구현 |
| 036 | `POST  /api/v1/chat/sessions/{session_id}/messages` | 규칙 기반 의도/조건 해석; 일반 자연어 모델 미연결 |
| 037 | `GET  /api/v1/chat/jobs/{job_id}` | 처리 코드 구현 |
| 038 | `POST  /api/v1/chat/jobs/{job_id}/clarifications` | 처리 코드 구현 |
| 039 | `DELETE  /api/v1/chat/sessions/{session_id}/context` | 처리 코드 구현 |
| 040 | `DELETE  /api/v1/chat/sessions/{session_id}` | 처리 코드 구현 |
| 041 | `GET  /api/v1/results/{result_id}` | 처리 코드 구현 |
| 042 | `GET  /api/v1/results/{result_id}/file` | 처리 코드 구현 |
| 043 | `POST  /api/v1/reports/preview` | DEC-28 period_preset 보류 |
| 044 | `POST  /api/v1/reports` | PDF/DOCX 및 저장 스냅샷; RAG/ML 섹션은 안내 |
| 045 | `GET  /api/v1/reports/{report_id}/status` | 처리 코드 구현 |
| 046 | `GET  /api/v1/reports` | 처리 코드 구현 |
| 047 | `GET  /api/v1/reports/{report_id}` | 처리 코드 구현 |
| 048 | `GET  /api/v1/reports/{report_id}/files/{format}` | 처리 코드 구현 |
| 049 | `GET  /api/v1/users/me/subscriptions` | 처리 코드 구현 |
| 050 | `PUT  /api/v1/users/me/subscriptions` | 활성 옵션·현재 관심·새 발행 기준 필요 |
| 051 | `DELETE  /api/v1/users/me/subscriptions/{subscription_id}` | 처리 코드 구현 |
| 052 | `GET  /api/v1/users/me/notification-settings` | 처리 코드 구현 |
| 053 | `PATCH  /api/v1/users/me/notification-settings` | 처리 코드 구현 |
| 054 | `GET  /api/v1/terms/{terms_type}` | 처리 코드 구현 |
| 055 | `POST  /api/v1/users/me/consents` | 처리 코드 구현 |
| 056 | `GET  /api/v1/notifications` | 처리 코드 구현 |
| 057 | `GET  /api/v1/notifications/unread-count` | 처리 코드 구현 |
| 058 | `GET  /api/v1/notifications/{user_alert_id}` | 처리 코드 구현 |
| 059 | `PATCH  /api/v1/notifications/{user_alert_id}/read` | 처리 코드 구현 |
| 060 | `POST  /api/v1/notifications/read-all` | 처리 코드 구현 |
| 061 | `GET  /api/v1/notifications/popups` | 처리 코드 구현 |
| 062 | `POST  /api/v1/notifications/popups/{delivery_id}/ack` | 처리 코드 구현 |
| 063 | `GET  /api/v1/debug/results/{result_id}/sql` | 처리 코드 구현 |
| 064 | `PATCH  /api/v1/chat/sessions/{session_id}` | 처리 코드 구현 |
| 065 | `GET  /api/v1/reference/keyword-categories` | 처리 코드 구현 |
| 066 | `GET  /api/v1/reference/keyword-topics` | 처리 코드 구현 |
| 067 | `GET  /api/v1/users/me/keyword-blocks` | 처리 코드 구현 |
| 068 | `PUT  /api/v1/users/me/keyword-blocks` | 처리 코드 구현 |
| 069 | `POST  /api/v1/auth/social/{provider}/authorize` | 보류: DEC-34 소셜 계정 저장·연결 및 OAuth 제공자 설정 |
| 070 | `GET  /api/v1/auth/social/{provider}/callback` | 보류: DEC-34 소셜 계정 저장·연결 및 OAuth 제공자 설정 |
| 071 | `GET  /api/v1/auth/social/signup-context` | 보류: DEC-34 소셜 신규 가입 저장 계약 |
| 072 | `POST  /api/v1/auth/social/email-verifications` | 보류: DEC-34 이메일 인증·메일 제공자 설정 |
| 073 | `POST  /api/v1/auth/social/email-verifications/{challenge_id}/verify` | 보류: DEC-34 이메일 인증·메일 제공자 설정 |
| 074 | `POST  /api/v1/auth/social/signup` | 보류: DEC-34 소셜 신규 가입·동의 저장 계약 |
| 075 | `GET  /api/v1/users/me/consents` | 보류: DEC-34 일반 동의 종류와 과거 약관 보존 계약 |
| 076 | `POST  /api/v1/users/me/consents/general` | 보류: DEC-34 일반 동의 종류와 과거 약관 보존 계약 |
| 077 | `GET  /api/v1/un/availability` | 처리 코드 구현 |
| 078 | `POST  /api/v1/analysis/import-market-shares` | 처리 코드 구현 |
| 079 | `GET  /api/v1/admin/overview` | 보류: DEC-37 과거 월말 회원·구독 스냅샷 저장 계약 |
| 080 | `GET  /api/v1/admin/alerts` | 처리 코드 구현 |
| 081 | `GET  /api/v1/admin/users` | 처리 코드 구현 |
| 082 | `GET  /api/v1/admin/users/{user_id}` | 처리 코드 구현 |
| 083 | `PATCH  /api/v1/admin/users/{user_id}/role` | 처리 코드 구현 |
| 084 | `PATCH  /api/v1/admin/users/{user_id}/status` | 처리 코드 구현 |
| 085 | `GET  /api/v1/admin/users/{user_id}/interests` | 관심값 조회; checks는 추가 진단 없음 |
| 086 | `GET  /api/v1/admin/users/{user_id}/exposure` | 관심 노출 조회; checks는 추가 진단 없음 |
| 087 | `GET  /api/v1/admin/data/runs` | 처리 코드 구현 |
| 088 | `GET  /api/v1/admin/data/runs/{run_id}` | 보류: DEC-42 상세 로그·실패 범위 생산자 계약 |
| 089 | `POST  /api/v1/admin/data/runs/{run_id}/retries` | 보류: 데이터 적재 담당자의 실패 범위 재수집 실행기 연결 |
| 090 | `GET  /api/v1/admin/data/quality-issues` | 보류: DEC-38 HS 판본 품질 판정 저장 계약 |
| 091 | `POST  /api/v1/admin/data/quality-issues/{issue_id}/revalidations` | 보류: DEC-41 버전 획득 및 데이터 담당 재검증 실행기 연결 |
| 092 | `GET  /api/v1/admin/articles` | 처리 코드 구현 |
| 093 | `GET  /api/v1/admin/articles/{article_id}` | 처리 코드 구현 |
| 094 | `PATCH  /api/v1/admin/articles/{article_id}/links` | 보류: 태그 변경 이후 집계·벡터 인덱스 반영 실행기 연결 |
| 095 | `PATCH  /api/v1/admin/articles/{article_id}/visibility` | 처리 코드 구현 |
| 096 | `GET  /api/v1/admin/article-sources` | 처리 코드 구현 |
| 097 | `GET  /api/v1/admin/keyword-topics` | 보류: DEC-32 키워드 수동 검수 상태·출처 저장 계약 |
| 098 | `GET  /api/v1/admin/keyword-topics/detail` | 보류: DEC-32 키워드 검수 근거 저장 계약 |
| 099 | `PATCH  /api/v1/admin/keyword-topics/review` | 보류: DEC-49 과거 기사 분류 보존 및 검수 실행기 연결 |
| 100 | `GET  /api/v1/admin/keyword-topics/export` | 처리 코드 구현 |
| 101 | `GET  /api/v1/admin/keyword-categories` | 보류: DEC-32 기본/관리자 분야 출처 저장 계약 |
| 102 | `POST  /api/v1/admin/keyword-categories` | 보류: DEC-32 관리자 분야 출처 저장 계약 |
| 103 | `GET  /api/v1/admin/keyword-categories/{category_id}` | 보류: DEC-32 관리자 분야 출처 저장 계약 |
| 104 | `PATCH  /api/v1/admin/keyword-categories/{category_id}` | 보류: DEC-32 관리자 분야 출처 저장 계약 |
| 105 | `DELETE  /api/v1/admin/keyword-categories/{category_id}` | 보류: DEC-49 분야 삭제·이관 정책 |
| 106 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 107 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 108 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 109 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 110 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 111 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 112 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 113 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 114 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 115 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 116 | `번호 예약·재사용 금지` | 삭제/예약: 등록하지 않음 |
| 117 | `GET  /api/v1/admin/notification-deliveries` | 실제 편입 기록 조회; EXCLUDED/UNKNOWN 과거 저장 미준비 |
| 118 | `GET  /api/v1/admin/notification-deliveries/{user_alert_id}` | 처리 코드 구현 |
| 119 | `GET  /api/v1/admin/users/{user_id}/notification-settings` | 처리 코드 구현 |
| 120 | `GET  /api/v1/admin/notification-events/{event_id}/diagnosis` | 존재 검증 후 UNKNOWN; 과거 제외 이유를 추정하지 않음 |
| 121 | `GET  /api/v1/admin/notification-rules` | 처리 코드 구현 |
| 122 | `GET  /api/v1/admin/audit-events` | 처리 코드 구현 |
| 123 | `GET  /api/v1/admin/audit-events/{audit_id}` | 처리 코드 구현 |
| 124 | `GET  /api/v1/admin/operation-settings` | 처리 코드 구현 |
| 125 | `PATCH  /api/v1/admin/article-sources/{source_code}/collection` | 보류: DEC-41 버전 획득 및 적재 담당자의 출처 중지 실행기 연결 |
| 126 | `GET  /api/v1/admin/notification-terms` | 처리 코드 구현 |
| 127 | `GET  /api/v1/admin/operations/{operation_id}` | 이번 앱의 동기 변경 감사 기록 조회 |
| 128 | `POST  /api/v1/auth/account-recovery/candidates` | 보류: DEC-43/44 계정 후보 노출·복구 운영 기준 |
| 129 | `POST  /api/v1/auth/account-recovery/email` | 보류: DEC-44 메일 발송·반복 제한·복구 기준 |
| 130 | `POST  /api/v1/auth/password-recovery/temporary-password` | 보류: DEC-44 임시 비밀번호 발급·만료·메일 실패 처리 기준 |
