# conditions 평가 비교 (dev)

- 실행 시각(UTC): 2026-11-09 08:55
- 반복: 1회, 플래너 출력 사용(--chain)
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 22 | 22 | 22 |
| runs | 22 | 22 | 22 |
| elapsed_seconds ↓ | 77.5 | 78.1 | 76.5 |
| pass_rate | 86.4% | 90.9% | 86.4% |
| latency_ms_p50 ↓ | 2,226.5 | 3,253.0 | 3,435.5 |
| latency_ms_p95 ↓ | 11,521 | 5,524 | 4,442 |
| latency_ms_max ↓ | 22,360 | 5,779 | 5,813 |
| latency_ms_mean ↓ | 3,522 | 3,548 | 3,478 |
| wrong_confirm_rate ↓ | 9.1% | 4.5% | 9.1% |
| unnecessary_ask_rate ↓ | 0.0% | 4.5% | 4.5% |
| hs_candidate_recall_at_5 | 100.0% | 100.0% | 100.0% |
| country_candidate_recall_at_5 | 0.0% | 0.0% | 0.0% |
| consistent_rate | 100.0% | 100.0% | 100.0% |
| field_accuracy.article_period | 100.0% | 100.0% | - |
| field_accuracy.choices_include | 66.7% | 100.0% | 100.0% |
| field_accuracy.compare_period | 100.0% | 100.0% | 100.0% |
| field_accuracy.country_codes | 100.0% | 100.0% | 100.0% |
| field_accuracy.error_contains | 100.0% | 100.0% | 100.0% |
| field_accuracy.hs_candidates | 100.0% | 100.0% | 100.0% |
| field_accuracy.hs_codes | 100.0% | 100.0% | 100.0% |
| field_accuracy.intent | 100.0% | 100.0% | 100.0% |
| field_accuracy.pending_kind | 75.0% | 100.0% | 100.0% |
| field_accuracy.period | 100.0% | 100.0% | 100.0% |
| field_accuracy.period_months | 100.0% | 100.0% | 100.0% |
| field_accuracy.ranking_group | 100.0% | 100.0% | 100.0% |
| field_accuracy.status | 90.9% | 90.9% | 86.4% |
| field_accuracy.task_count | 100.0% | 100.0% | 100.0% |
| field_accuracy.trade_flow | 100.0% | 100.0% | 100.0% |
| by_tag.article_period | 100.0% | 100.0% | 0.0% |
| by_tag.c28 | 0.0% | 0.0% | 0.0% |
| by_tag.compare | 100.0% | 100.0% | 100.0% |
| by_tag.country_code | 100.0% | 100.0% | 100.0% |
| by_tag.country_exact | 100.0% | 100.0% | 100.0% |
| by_tag.country_fuzzy | 0.0% | 0.0% | 0.0% |
| by_tag.country_multi | 100.0% | 100.0% | 100.0% |
| by_tag.hs_search | 100.0% | 100.0% | 100.0% |
| by_tag.import | 100.0% | 100.0% | 100.0% |
| by_tag.interest | 100.0% | 100.0% | 100.0% |
| by_tag.invalid | 100.0% | 100.0% | 100.0% |
| by_tag.korea_reporting | 100.0% | 0.0% | 100.0% |
| by_tag.market | 100.0% | 100.0% | 100.0% |
| by_tag.market_type | 100.0% | 100.0% | 100.0% |
| by_tag.mismatch | 100.0% | 100.0% | 100.0% |
| by_tag.missing_slot | 100.0% | 100.0% | 100.0% |
| by_tag.news | 100.0% | 100.0% | 0.0% |
| by_tag.numeric_hs | 100.0% | 100.0% | 100.0% |
| by_tag.period_unavailable | 100.0% | 100.0% | 100.0% |
| by_tag.ranking_group | 50.0% | 100.0% | 100.0% |
| by_tag.recent_months | 100.0% | 100.0% | 100.0% |
| by_tag.share_basis | 50.0% | 100.0% | 50.0% |
| by_tag.unsupported | 100.0% | 100.0% | 100.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| c001 | 1/1 | 1/1 | 1/1 |
| c002 | 0/1 | 0/1 | 0/1 |
| c003 | 1/1 | 1/1 | 1/1 |
| c004 | 1/1 | 1/1 | 1/1 |
| c006 | 1/1 | 0/1 | 1/1 |
| c007 | 1/1 | 1/1 | 1/1 |
| c008 | 1/1 | 1/1 | 1/1 |
| c009 | 0/1 | 1/1 | 1/1 |
| c010 | 1/1 | 1/1 | 1/1 |
| c011 | 0/1 | 1/1 | 0/1 |
| c012 | 1/1 | 1/1 | 1/1 |
| c013 | 1/1 | 1/1 | 1/1 |
| c014 | 1/1 | 1/1 | 1/1 |
| c015 | 1/1 | 1/1 | 1/1 |
| c016 | 1/1 | 1/1 | 1/1 |
| c021 | 1/1 | 1/1 | 1/1 |
| c022 | 1/1 | 1/1 | 1/1 |
| c023 | 1/1 | 1/1 | 1/1 |
| c024 | 1/1 | 1/1 | 0/1 |
| c025 | 1/1 | 1/1 | 1/1 |
| c026 | 1/1 | 1/1 | 1/1 |
| c027 | 1/1 | 1/1 | 1/1 |
