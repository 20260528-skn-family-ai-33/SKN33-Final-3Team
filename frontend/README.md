# Borderless Sellers 프론트엔드

기존 화면을 유지한 React 프론트엔드다. `src/main.jsx → AppProvider(store.jsx) → App.jsx → pages/`가 실행된다. 별도로 만들었던 `src/live/`와 `styles/live.css`는 제거했다. 기존 `mockup.css`, `app.css`, 이미지와 공통 레이아웃을 사용한다.

```powershell
npm ci
npm run dev -- --host 127.0.0.1
```

화면: http://127.0.0.1:5173. Vite가 `/api/v1`을 로컬 FastAPI 8000 포트로 전달한다. 설치·DB·Redis·worker 설명은 [연동 실행 안내](../docs/INTEGRATION.md), 파일별 변경 이유와 검증은 [검수 기록](../docs/INTEGRATION_REVIEW.md)을 참고한다.

## 연결 방식

- 로그인·관심사 등록·마이페이지·기사·순위는 기존 화면의 이벤트와 데이터만 API에 연결한다.
- `store.jsx`는 세션/관심정보/알림/구독/리포트를 서버에서 조회한다. 계정·비밀번호·서비스 결과를 localStorage에 저장하지 않는다. 예외로 관리자 알림(API-80)은 서버 읽음 저장소가 정해질 때까지 읽은 알림 ID만 브라우저에 임시 저장한다(`admin/useAdminAlertCount.js`).
- `api/client.js`는 HttpOnly 쿠키, 메모리 CSRF, 오류, 파일 다운로드를 처리한다. 프론트는 Redis에 직접 연결하지 않는다.
- 메인 카드·막대·도넛·스파크라인, 채팅 답변의 표/차트는 실제 수치로 그린다. 누락값은 0으로 만들지 않는다.
- 리포트는 조건 확인 → 명시적 생성 → 서버 상태 조회 → 저장된 PDF/DOCX/XLSX 다운로드 순서다. 브라우저에서 가짜 파일이나 완료 상태를 만들지 않는다.
- 관리자 페이지의 기존 배너·사이드 메뉴·표·모달에 회원/기사/감사/수집 기록 API를 연결한다.
- 소셜 로그인 버튼·전용 가입/오류 화면·프론트 OAuth 연결 코드는 제거했다. 이메일 로그인을 사용하며 백엔드 OAuth 계약은 변경하지 않았다.

## 검증

```powershell
npm test
npm run lint
npm run build
npm run test:integration
npm run test:review
```

`npm test`는 API 연결, 응답 변환·검증, 역할, 화면 간 조건 전달 및 요청 ID를 검증한다. 제거된 소셜 로그인 흐름의 브라우저 검증 스크립트도 함께 제거했다.

`test:integration`과 `test:e2e`는 동일한 실제 로컬 연동 검사다. API/worker/Vite, Docker MySQL·Redis, Chrome이 필요하다. `CHROMIUM`으로 다른 Chromium 실행 경로를 지정할 수 있다. 결과와 화면은 `tmp/browser-original/`에 저장한다. 계정·리포트는 로컬 검증용으로 생성한다. 기존 `tests/browser.cjs`와 미사용 목업 데이터 모듈은 디자인 참고 자료이며 현재 검증/실행 진입점에 연결하지 않는다.

기존 프론트에 없던 별도 키워드 차단 편집 화면은 새로 만들지 않았다. 차단 API와 DB 저장 구현은 유지하며, 기존 알림 설정에 편집 UI를 추가할지는 팀의 화면 범위로 정하면 된다.

최근 알림 팝오버는 알림 목록 조회·읽음 저장을 사용한다. 별도의 팝업 배달 조회/확인 API 연결은 후속 작업이며, 팝업 확인과 읽음은 서로 다른 상태다. `test:review`는 실제 로컬 로그인 뒤 오류·지연 응답을 주입하여 채팅 재시도와 리포트 전환 회귀를 검증한다.
