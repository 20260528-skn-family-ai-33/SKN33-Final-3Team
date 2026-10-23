# Changelog

## 2026-10-23

### Changed
- PATCH-004 follow-up: Remove collection status from the sidebar; open it inside Inbox from collection history, preserving legacy URLs.
- PATCH-004: Add the admin Inbox sidebar entry and consolidate administrator alerts, collection history and member notification delivery into three tabs. Operations alert details and the header bell open this same Inbox. Preserve collection result details, raw logs and the retry confirmation/progress/success/failure demo flow. (commit: pending)
- Keep only the collection status tab in the data menu; remove quality/article tabs and the separate member notification delivery menu. Remove the member search results tab while retaining search and its query.

### Added
- Admin Inbox browser regression checks covering navigation, legacy URLs, collection results/retry, delivery details and member search. Collection execution remains a frontend demo without a backend connection.

## 2026-10-22 (PATCH-003)

### Changed
- PATCH-003: Kakao, Naver and Google buttons now redirect to the real provider login pages (OAuth 2.0 authorization code flow with a random `state`) instead of the mock connection screen. (commit: pending)

### Added
- `/oauth/{provider}` callback handling: state check, code hand-off to `POST /api/auth/oauth/{provider}`, existing/linked account login, new-member completion (LG05), cancel/failure reasons (LG06).
- Dev/preview server token exchange (`server/oauthDevApi.js`) that keeps client secrets server-side, `.env.example`, `.env` ignored by Git, fixed dev port 5173 for the registered redirect URIs.
- 4 token-exchange unit tests and a 12-check social login browser test using mocked providers.
- Logout button at the far right of the user and admin headers (returns to login; protected pages require login again).
- Unified password rule for signup and password change: letters + digits + ASCII symbols, 8+ characters, no spaces/Hangul; live checklist, buttons disabled until satisfied, re-checked in the store.

Real logins require each team's app keys and redirect URI registration; they were not exercised against the live providers.

## 2026-10-22

### Changed
- PATCH-002: Rebuild the frontend to match the 2026-10-22 full mockup (Exchange design, Gothic A1, red/plum palette). The previous teal UI (PATCH-001) is kept in `../borderless-frontend-backup-20261022-patch001`. (commit: pending)
- Restructure `src/` into `pages/`, `components/`, `admin/`, `data/`, `utils/`, `styles/`; replace the inline route switch with a small hash router and a context store.

### Added
- User screens BS01–BS31 and BS24-1 with working validation: centered login/signup over the port photo, signup name field, onboarding HS4→HS6 and continent selection, dashboard all/interest views, rankings, articles, AI chat with condition → 6-stage progress → completion, report viewer with PDF/DOCX/Excel, subscriptions, notification terms consent, consent management, withdrawal reasons (at least one; "other" requires text), password change, interests, inbox.
- Social login (LG02–LG06) and account recovery (LG07–LG13): find ID by name with masked account list, temporary password e-mail preview that actually updates the demo password.
- Admin area with the 6-menu layout: 74 data-driven screen states and confirmation dialogs (`src/admin/screens.js`), member search by ID/name, raw terminal run log, and an interactive keyword taxonomy module (assign/hold/edit/revert, create field, rename, unassign, processing history).
- Demo accounts, unit tests (6) and a Playwright flow test (36 checks), screen-to-route map.

Live data, authentication, AI inference, e-mail delivery and scheduled publishing remain unconnected. No Git commit or deployment was performed.

## 2026-10-18

### Added
- PATCH-001: Implement the 31 mockup screen states as responsive React pages, tabs and dialogs with local demo data. (commit: pending)
- Connect interest settings, news search, rankings, chat/report flows, subscriptions, consent, profile and notification management.
- Add printable reports, paragraph DOCX and Excel XML exports, a screen map and a Korean startup guide.
- Validate production build, lint, 4 data tests, 10 browser scenarios, mobile overflow and exported file structures.

### Changed
- Replace the Vite starter UI and page title with Borderless Sellers.

Backend authentication, live data, AI inference and scheduled delivery remain unconnected. No Git commit or deployment was performed.
