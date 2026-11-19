# Changelog

## 2026-11-19

### Changed
- PATCH-001 (#106): Unify search button colors and account/admin subtabs; remove redundant account and member detail descriptions. Show article role badges beside cards under their corresponding answers. (#137)
- PATCH-001 (#106): Display member signup/subscription start dates and up to 12 monthly member/subscription totals using existing start dates. Historical totals may change with the current population; immutable history is deferred. (#137)
- PATCH-001 (#106): Add independent inline major/minor creation with automatic selection and name-only saves. Persist standalone majors in existing category audit events and minor categories in keyword_category. (#137)
- PATCH-001 (#106): Match the main DB schema without migrations: remove added table definitions, SQL files and the month-end collector. No database DDL or data deletion is performed. (#137)

- Restore keyword assignment confirmation; keep inline category errors visible. (#137)
- Allow confirmed rename/delete of unused standalone majors, with audit replay, version checks and a shared MySQL connection lock; preserve existing category restrictions. (#137)
- Count known dates without discarding whole monthly series and show excluded member/subscription counts. Retain original evidence badges and replace long reference markers with inline reference tags. (#137)
- Remove the local LIST.md from this PR and ignore it. (#137)
- Compare major names with the keyword_category column collation (case, accent and width variants are the same major); report the registered spelling and allow spelling-only renames. (#137)
