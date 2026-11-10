# conditions 평가 비교 (holdout)

- 실행 시각(UTC): 2026-11-09 08:59
- 반복: 1회, 플래너 출력 사용(--chain)
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 11 | 11 | 11 |
| runs | 11 | 11 | 11 |
| elapsed_seconds ↓ | 37.1 | 38.9 | 37.8 |
| pass_rate | 81.8% | 72.7% | 81.8% |
| latency_ms_p50 ↓ | 2,104 | 3,590 | 3,497 |
| latency_ms_p95 ↓ | 16,208 | 4,241 | 4,144 |
| latency_ms_max ↓ | 16,208 | 4,241 | 4,144 |
| latency_ms_mean ↓ | 3,369 | 3,535 | 3,441 |
| wrong_confirm_rate ↓ | 9.1% | 9.1% | 9.1% |
| unnecessary_ask_rate ↓ | 0.0% | 9.1% | 0.0% |
| hs_candidate_recall_at_5 | 50.0% | 50.0% | 50.0% |
| country_candidate_recall_at_5 | 0.0% | 0.0% | 0.0% |
| consistent_rate | 100.0% | 100.0% | 100.0% |
| field_accuracy.compare_period | 100.0% | 100.0% | 100.0% |
| field_accuracy.country_codes | 100.0% | 100.0% | 100.0% |
| field_accuracy.error_contains | 100.0% | 100.0% | 100.0% |
| field_accuracy.hs_candidates | 50.0% | 50.0% | 50.0% |
| field_accuracy.hs_codes | 100.0% | 100.0% | 100.0% |
| field_accuracy.intent | 100.0% | 100.0% | 100.0% |
| field_accuracy.pending_kind | 100.0% | 100.0% | 100.0% |
| field_accuracy.period | 100.0% | 100.0% | 100.0% |
| field_accuracy.period_months | 100.0% | 100.0% | 100.0% |
| field_accuracy.ranking_group | 100.0% | 100.0% | 100.0% |
| field_accuracy.share_basis | 100.0% | 100.0% | 100.0% |
| field_accuracy.sql_template | 100.0% | 100.0% | 100.0% |
| field_accuracy.status | 90.9% | 81.8% | 90.9% |
| field_accuracy.task_count | 100.0% | 100.0% | 100.0% |
| field_accuracy.trade_flow | 100.0% | 100.0% | 100.0% |
| by_tag.c28 | 0.0% | 0.0% | 0.0% |
| by_tag.compare | 100.0% | 100.0% | 100.0% |
| by_tag.country_code | 100.0% | 100.0% | 100.0% |
| by_tag.country_fuzzy | 0.0% | 0.0% | 0.0% |
| by_tag.country_multi | 100.0% | 100.0% | 100.0% |
| by_tag.hs_search | 50.0% | 50.0% | 50.0% |
| by_tag.import | 100.0% | 100.0% | 100.0% |
| by_tag.korea_reporting | 100.0% | 100.0% | 100.0% |
| by_tag.market_type | 100.0% | 0.0% | 100.0% |
| by_tag.missing_slot | 100.0% | 0.0% | 100.0% |
| by_tag.product_share | 100.0% | 100.0% | 100.0% |
| by_tag.ranking_group | 100.0% | 100.0% | 100.0% |
| by_tag.recent_months | 100.0% | 100.0% | 100.0% |
| by_tag.share_basis | 100.0% | 100.0% | 100.0% |
| by_tag.unsupported | 100.0% | 100.0% | 100.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| c101 | 0/1 | 0/1 | 0/1 |
| c102 | 1/1 | 1/1 | 1/1 |
| c103 | 0/1 | 0/1 | 0/1 |
| c104 | 1/1 | 1/1 | 1/1 |
| c105 | 1/1 | 1/1 | 1/1 |
| c106 | 1/1 | 1/1 | 1/1 |
| c107 | 1/1 | 1/1 | 1/1 |
| c108 | 1/1 | 1/1 | 1/1 |
| c111 | 1/1 | 1/1 | 1/1 |
| c112 | 1/1 | 0/1 | 1/1 |
| c116 | 1/1 | 1/1 | 1/1 |
