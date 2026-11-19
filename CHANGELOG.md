# Changelog

## 2026-11-19

### Fixed (Bug Fixes)
- PATCH-001: Sync approved button colors, account tabs, and article role badges/placement into the running development checkout. Remove the duplicate member detail heading card and expose stored membership/subscription start dates. Monthly history aggregation remains pending. (commit: pending)
- PATCH-001: Connect current member and enabled periodic subscription counts to the admin dashboard; remove empty historical charts. Monthly history remains unavailable. (commit: pending)
- PATCH-001: Add up to 12 monthly member/subscription observations with gaps for missing records, a current-month point, and an idempotent KST month-boundary collector. Unify all admin subtabs as red underline navigation. (commit: pending)
- PATCH-001: Fill missing historical member points from stored signup dates using KST month boundaries; preserve recorded snapshots and subscription gaps. (commit: pending)
- PATCH-001: Backfill subscription chart gaps from start dates of currently active subscriptions, explicitly label this cohort basis, and prefer saved month-end totals. (commit: pending)
- PATCH-001: Let keyword reviewers create a major category with its first subcategory, or add a subcategory under the selected major, and select the saved category immediately without losing the review reason. (commit: pending)
- PATCH-001: Add independent inline major/minor creation in keyword review, including persistent standalone majors; reuse the review reason without category popups. (commit: pending)
- PATCH-001: Allow major/minor category saves with a name alone; record an automatic category-addition audit description when the keyword review reason is empty. (commit: pending)
