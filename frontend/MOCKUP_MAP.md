# 화면설계서 번호 ↔ 프론트엔드 경로

2026.10.23 화면설계서 기준입니다. 주소는 `http://localhost:5173/#` 뒤에 붙입니다.
예: `http://localhost:5173/#/account/profile?withdraw=open` → BS24-1 탈퇴 사유 선택

목업에서 한 화면에 여러 상태를 나란히 그린 부분(예: BS14 상태 1~4)은 실제 동작 순서대로 한 화면 안에서 이어집니다.

## 사용자 화면

| 번호 | 화면 | 경로 | 비고 |
|---|---|---|---|
| BS01 | 로그인 | `/login` |  |
| BS02 | 회원가입 | `/signup` |  |
| BS03 | 관심사 등록 / 품목 선택 | `/onboarding` | 회원가입 완료 후 자동 진입 |
| BS04 | 관심사 등록 / 국가 선택 | `/onboarding?step=2` |  |
| BS05 | 관심사 등록 / 완료 | `/onboarding?step=3` |  |
| BS06 | 메인 / 전체 국가 | `/` |  |
| BS07 | 메인 / 내 관심 | `/?view=interest` |  |
| BS13 | 메인 / 알림 팝오버 | `/?alerts=open` | 머리글 종 아이콘을 눌러도 열림 |
| BS08 | 수출입 순위 | `/rank` |  |
| BS09 | 기사 / 공통 진입 | `/articles?q=반도체` |  |
| BS10 | 기사 / 전체기사 | `/articles?tab=all` |  |
| BS11 | 기사 / 단신속보 | `/articles?tab=short` |  |
| BS12 | 기사 / 주요이슈 | `/articles?tab=major&id=a1` |  |
| BS14 | AI분석 / 대화와 리포트 생성 | `/analysis` |  |
| BS15 | AI분석 / 리포트 생성 완료 | `/analysis?chat=c2` | 생성 완료된 채팅방. 새로 만들 때는 BS14 조건 카드에서 `리포트 생성` → 진행(상태 2) → 완료 |
| BS16 | 리포트 센터 / 채팅 생성 | `/account/reports?tab=chat` |  |
| BS17 | 리포트 센터 / 주간 | `/account/reports?tab=weekly` |  |
| BS18 | 리포트 센터 / 월간 | `/account/reports?tab=monthly` |  |
| BS19 | 구독 설정 / 수정 | `/account/subscription` |  |
| BS20 | 구독 삭제 확인 | `/account/reports?tab=monthly&confirm=delete` | 리포트 센터 구독 카드의 `삭제` |
| BS21 | 리포트 / 열람·다운로드 | `/report?id=r-chat-1` |  |
| BS22 | 마이페이지 / 알림설정 | `/account/settings` |  |
| BS23 | 알림 수신 약관 동의 모달 | `/account/settings?terms=open` | 알림설정에서 약관 미동의 상태로 `전체 알림 수신`을 켤 때 |
| BS24 | 마이페이지 / 회원정보 | `/account/profile` |  |
| BS24-1 | 마이페이지 / 회원탈퇴 사유 선택 | `/account/profile?withdraw=open` | 회원정보의 `회원탈퇴` |
| BS25 | 마이페이지 / 비밀번호 변경 | `/account/password` |  |
| BS26 | 마이페이지 / 관심정보 | `/account/interests` |  |
| BS27 | 알림함 / 전체 | `/account/notifications` |  |
| BS28 | 알림함 / 리포트 발행 | `/account/notifications?tab=report` |  |
| BS29 | 알림함 / 주요이슈 | `/account/notifications?tab=major` |  |
| BS30 | 알림함 / 급증 기사 | `/account/notifications?tab=surge` |  |
| BS31 | 리포트 센터 / 분기 | `/account/reports?tab=quarterly` |  |

## 소셜 로그인 · 계정 찾기

| 번호 | 화면 | 경로 | 비고 |
|---|---|---|---|
| LG02 | 카카오 인증 연결·복귀 | `/login/kakao` → 카카오 로그인 → `/oauth/kakao` | 키가 없으면 설정 안내 |
| LG03 | 네이버 인증 연결·복귀 | `/login/naver` → 네이버 로그인 → `/oauth/naver` | 키가 없으면 설정 안내 |
| LG04 | 구글 인증 연결·복귀 | `/login/google` → 구글 로그인 → `/oauth/google` | 키가 없으면 설정 안내 |
| LG05 | 소셜 신규 회원 · 추가 정보 확인 | `/signup/social` | 소셜 인증 후 연결된 계정이 없을 때 자동 이동 |
| LG06 | 로그인 실패·취소 상태 및 구현 계약 | `/login/error?provider=kakao&reason=cancel` | 취소·state 불일치·토큰 교환 실패 시 자동 이동 |
| LG07 | 아이디 찾기 | `/find-id` |  |
| LG08 | 아이디 찾기 · 가입 계정 선택 | `/find-id?step=select` | 아이디 찾기에서 `홍길동` 입력 |
| LG09 | 아이디 찾기 · 회원정보 없음 | `/find-id?step=none` | 등록되지 않은 이름 입력 |
| LG10 | 아이디 찾기 · 메일 발송 완료 | `/find-id?step=sent` | LG08에서 `계정 선택` |
| LG11 | 비밀번호 찾기 | `/find-password` |  |
| LG12 | 비밀번호 찾기 · 임시 비밀번호 발송 | `/find-password?step=sent` | `홍길동` + `honggd@example.com` 입력 |
| LG13 | 비밀번호 찾기 · 회원정보 없음 | `/find-password?step=none` | 일치하는 회원이 없을 때 |

## 관리자 화면 (admin@example.com 로그인)

| 번호 | 화면 | 경로 |
|---|---|---|
| BS32 | 운영 현황 | `/admin/ops/BS32` |
| BS33 | 관리자 알림 | `/admin/inbox/BS33` |
| BS34 | 운영 현황 | `/admin/ops/BS34` |
| BS35 | 관리자 알림 | `/admin/inbox/BS35` |
| BS36 | 운영 현황 | `/admin/ops/BS36` |
| BS37 | 관리자 알림 | `/admin/inbox/BS37` |
| BS38 | 운영 현황 | `/admin/ops/BS38` |
| BS39 | 관리자 알림 | `/admin/inbox/BS39` |
| BS40 | 회원·접근 권한 | `/admin/members/BS40` |
| BS41 | 회원·접근 권한 | `/admin/members/BS41` |
| BS42 | 회원·접근 권한 | `/admin/members/BS42` |
| BS43 | 회원·접근 권한 | `/admin/members/BS43` |
| BS44 | 탈퇴 회원 상세 | `/admin/members/BS44` |
| BS45 | 회원·접근 권한 | `/admin/members/BS45` |
| BS46 | 이용 제한 회원 상세 | `/admin/members/BS46` |
| BS47 | 이용 제한을 해제할까요? | `/admin/members/BS47` |
| BS48 | 회원 상세 | `/admin/members/BS48` |
| BS49 | 회원 검색 결과 | `/admin/members/BS49` |
| BS50 | 회원 상세 | `/admin/members/BS50` |
| BS51 | 운영자 상세 | `/admin/members/BS51` |
| BS52 | 운영 권한을 회수할까요? | `/admin/members/BS52` |
| BS53 | 운영자 상세 | `/admin/members/BS53` |
| BS54 | 회원 상세 | `/admin/members/BS54` |
| BS55 | 회원 권한 변경 | `/admin/members/BS55` |
| BS56 | 콘텐츠 관리자 권한을 부여할까요? | `/admin/members/BS56` |
| BS57 | 회원 상세 | `/admin/members/BS57` |
| BS58 | 회원 이용을 제한할까요? | `/admin/members/BS58` |
| BS59 | 회원 상세 | `/admin/members/BS59` |
| BS60 | 회원 이용 제한을 해제할까요? | `/admin/members/BS60` |
| BS61 | 회원 상세 | `/admin/members/BS61` |
| BS63 | 회원 상세 · 관심사·맞춤 서비스 | `/admin/members/BS63` |
| BS64 | 데이터 수집 현황 | `/admin/inbox/BS64` |
| BS65 | 수출 통계 수집 상세 | `/admin/inbox/BS65` |
| BS66 | 기사 수집 상세 | `/admin/inbox/BS66` |
| BS67 | 환율 수집 상세 | `/admin/inbox/BS67` |
| BS68 | 수집 실행 로그 | `/admin/inbox/BS68` |
| BS69 | 실패 범위를 다시 수집할까요? | `/admin/inbox/BS69` |
| BS70 | 재수집 진행 중 | `/admin/inbox/BS70` |
| BS71 | 수출 통계 수집 상세 | `/admin/inbox/BS71` |
| BS72 | 수출 통계 수집 상세 | `/admin/inbox/BS72` |
| BS73 | 데이터 품질 점검 | `/admin/data/BS73` |
| BS74 | HS 판본 미확인 데이터 | `/admin/data/BS74` |
| BS75 | 확인한 판본으로 재검증할까요? | `/admin/data/BS75` |
| BS76 | HS 판본 미확인 데이터 | `/admin/data/BS76` |
| BS77 | 데이터 수집·품질 · 기사·출처 | `/admin/data/BS77` |
| BS78 | 기사 검수 | `/admin/data/BS78` |
| BS79 | 기사 검수 | `/admin/data/BS79` |
| BS80 | 기사 내용·출처 | `/admin/data/BS80` |
| BS81 | 기사 연결 정보 수정 | `/admin/data/BS81` |
| BS82 | 기사 검수 | `/admin/data/BS82` |
| BS83 | 기사 노출을 제외할까요? | `/admin/data/BS83` |
| BS84 | 기사 검수 | `/admin/data/BS84` |
| BS85 | 노출 제외 기사 | `/admin/data/BS85` |
| BS86 | 기사 노출을 복구할까요? | `/admin/data/BS86` |
| BS87 | 기사 검수 | `/admin/data/BS87` |
| BS88 | 기사 출처 현황 | `/admin/data/BS88` |
| BS89 | 미분류 키워드 | `/admin/keywords?status=todo&kw=k3` — 미분류 키워드 · 반덤핑신규분석 선택 |
| BS90 | 미분류 키워드 · 새 분야 만들기 | `/admin/keywords?kw=k4&modal=new-field` — 골드카드 선택 → `+ 맞는 분야가 없으면 새 분야 만들기` |
| BS91 | 분류함 · 분야 수정 | `/admin/keywords?status=done` — 분류함 · 분야 수정 |
| BS92 | 분류 틀 관리 | `/admin/keywords/fields` — 분류 기준 관리(분류 틀 표·검색) |
| BS93 | 분류 틀 관리 · 분야 상세 | `/admin/keywords/fields/f01` — 관세·무역규제 분야 상세(기본 분야) |
| BS94 | 분류 틀 관리 · 이름 수정 | `/admin/keywords/fields/<새 분야 id>?modal=rename` — BS90에서 만든 관리자 분야 상세의 `이름 수정` |
| BS95 | 분류 틀 관리 · 키워드 지정 해제 | `/admin/keywords/fields/<새 분야 id>?modal=unassign&kw=k4` — 관리자 분야에 지정된 키워드 칩의 × → 지정 해제 |
| BS96 | 키워드 처리 이력 | `/admin/keywords/history` — 키워드 처리 이력(지정·보류·수정·해제 기록) |
| BS149 | 회원 알림 전달 내역 | `/admin/inbox/BS149` |
| BS150 | 회원 리포트 알림 전달 상세 | `/admin/inbox/BS150` |
| BS151 | 회원 급증 알림 전달 상세 | `/admin/inbox/BS151` |
| BS152 | 알림 편입 제외 내역 | `/admin/inbox/BS152` |
| BS153 | 회원 수신 설정 확인 | `/admin/inbox/BS153` |
| BS154 | 약관 응답 이력 | `/admin/inbox/BS154` |
| BS155 | 기사량 알림 판정 기준 | `/admin/inbox/BS155` |
| BS156 | 관리자 처리 이력 | `/admin/history/BS156` |
| BS157 | 처리 이력 상세 | `/admin/history/BS157` |
| BS158 | 처리 이력 상세 | `/admin/history/BS158` |
| BS159 | 공통 운영 설정 | `/admin/history/BS159` |
| BS160 | 정기 발행 일정 | `/admin/history/BS160` |
| BS161 | 기사 출처 운영 설정 | `/admin/history/BS161` |
| BS162 | 출처의 신규 수집을 중지할까요? | `/admin/history/BS162` |
| BS163 | 기사 출처 운영 설정 | `/admin/history/BS163` |
| BS164 | 기사 수집을 재개할까요? | `/admin/history/BS164` |
| BS165 | 기사 출처 운영 설정 | `/admin/history/BS165` |
| BS166 | 알림 약관 버전 | `/admin/history/BS166` |

알림함 수집 이력: `/admin/inbox/collection`. 이전 운영 알림·수집 상세·회원 알림 전달 URL도 같은 화면으로 열립니다. 품질 점검·기사·출처 및 검색 결과는 독립 탭에서 제거했습니다.

관리자 메뉴 첫 화면: 운영 현황 `/admin`, 회원·권한 `/admin/members/BS40`, 키워드·분류 체계 `/admin/keywords`, 알림함 `/admin/inbox/BS33`, 처리 이력·운영 설정 `/admin/history/BS156`.
회원 검색(BS49)은 `/admin/members/BS49?q=검색어&by=all|id|name` 형식으로 실제 목록을 거릅니다.
