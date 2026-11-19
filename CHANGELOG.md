# Changelog

## 2026-11-19

### Changed
- PATCH-001 (#106): Unify search button colors and account/admin subtabs; remove redundant account and member detail descriptions. Show article role badges beside cards under their corresponding answers. (commit: pending)
- PATCH-001 (#106): Display member signup/subscription start dates and up to 12 monthly member/subscription totals using existing start dates. Historical totals may change with the current population; immutable history is deferred. (commit: pending)
- PATCH-001 (#106): Add independent inline major/minor creation with automatic selection and name-only saves. Persist standalone majors in existing category audit events and minor categories in keyword_category. (commit: pending)
- PATCH-001 (#106): Match the main DB schema without migrations: remove added table definitions, SQL files and the month-end collector. No database DDL or data deletion is performed. (commit: pending)
