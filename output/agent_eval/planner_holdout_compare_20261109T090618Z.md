# planner 평가 비교 (holdout)

- 실행 시각(UTC): 2026-11-09 09:06
- 반복: 1회
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 28 | 28 | 28 |
| runs | 28 | 28 | 28 |
| elapsed_seconds ↓ | 74.1 | 108.9 | 143.6 |
| pass_rate | 60.7% | 92.9% | 82.1% |
| latency_ms_p50 ↓ | 2,267 | 3,203 | 4,155 |
| latency_ms_p95 ↓ | 4,303 | 7,711 | 20,168 |
| latency_ms_max ↓ | 7,285 | 19,027 | 20,180 |
| latency_ms_mean ↓ | 2,744 | 4,032 | 5,318 |
| task_count_accuracy | 0.9 | 1.0 | 0.9 |
| parse_failure_rate ↓ | 0.0% | 0.0% | 7.1% |
| model_pass_rate | 59.3% | 92.6% | 81.5% |
| regex_pass_rate | 100.0% | 100.0% | 100.0% |
| consistent_rate | 100.0% | 100.0% | 100.0% |
| field_accuracy.answer_format | 100.0% | 100.0% | 100.0% |
| field_accuracy.article_period | 100.0% | 100.0% | 100.0% |
| field_accuracy.compare_period | 100.0% | 100.0% | 100.0% |
| field_accuracy.comparison_requested | 91.7% | 100.0% | 100.0% |
| field_accuracy.countries | 95.8% | 95.8% | 100.0% |
| field_accuracy.country_mentions | 100.0% | 100.0% | 100.0% |
| field_accuracy.custom_query | 96.4% | 100.0% | 100.0% |
| field_accuracy.custom_query_content | 0.0% | 100.0% | 100.0% |
| field_accuracy.error | 100.0% | 100.0% | 0.0% |
| field_accuracy.intent | 92.9% | 100.0% | 100.0% |
| field_accuracy.period | 91.7% | 100.0% | 95.8% |
| field_accuracy.plan | 100.0% | 100.0% | 100.0% |
| field_accuracy.products | 92.0% | 100.0% | 100.0% |
| field_accuracy.ranking_group | 50.0% | 100.0% | 83.3% |
| field_accuracy.ranking_limit | 100.0% | 100.0% | 100.0% |
| field_accuracy.recent_months | 100.0% | 100.0% | 100.0% |
| field_accuracy.share_basis | 100.0% | 100.0% | 100.0% |
| field_accuracy.source_tasks | 100.0% | 96.4% | 96.4% |
| field_accuracy.target_selections | 100.0% | 100.0% | 100.0% |
| field_accuracy.trade_flow | 95.8% | 100.0% | 100.0% |
| by_tag.abbreviation_not_country | 100.0% | 100.0% | 100.0% |
| by_tag.ambiguous | 0.0% | 100.0% | 0.0% |
| by_tag.binding | 100.0% | 100.0% | 100.0% |
| by_tag.cause | 0.0% | 100.0% | 100.0% |
| by_tag.company_not_country | 100.0% | 100.0% | 100.0% |
| by_tag.compare | 0.0% | 100.0% | 100.0% |
| by_tag.conversation | 100.0% | 100.0% | 100.0% |
| by_tag.country_switch | 0.0% | 100.0% | 100.0% |
| by_tag.custom_query | 0.0% | 100.0% | 100.0% |
| by_tag.followup | 33.3% | 100.0% | 66.7% |
| by_tag.foreign_market | 0.0% | 100.0% | 100.0% |
| by_tag.half | 0.0% | 100.0% | 100.0% |
| by_tag.hs_lookup | 100.0% | 100.0% | 100.0% |
| by_tag.import | 66.7% | 66.7% | 100.0% |
| by_tag.korea_partner | 100.0% | 100.0% | 100.0% |
| by_tag.market | 100.0% | 100.0% | 100.0% |
| by_tag.mom | 0.0% | 100.0% | 100.0% |
| by_tag.multi_task | 100.0% | 50.0% | 50.0% |
| by_tag.news | 100.0% | 100.0% | 100.0% |
| by_tag.numeric | 100.0% | 100.0% | 100.0% |
| by_tag.out_of_scope | 0.0% | 100.0% | 100.0% |
| by_tag.period_month | 100.0% | 100.0% | 100.0% |
| by_tag.period_year | 100.0% | 100.0% | 100.0% |
| by_tag.product_merge | 100.0% | 100.0% | 100.0% |
| by_tag.ranking | 33.3% | 66.7% | 66.7% |
| by_tag.ranking_limit | 100.0% | 100.0% | 100.0% |
| by_tag.recall | 100.0% | 100.0% | 100.0% |
| by_tag.recent_months | 50.0% | 100.0% | 100.0% |
| by_tag.regex | 100.0% | 100.0% | 100.0% |
| by_tag.report | 100.0% | 100.0% | 100.0% |
| by_tag.reset | 0.0% | 100.0% | 0.0% |
| by_tag.share | 0.0% | 100.0% | 100.0% |
| by_tag.shared_condition | 100.0% | 0.0% | 0.0% |
| by_tag.statistics | 66.7% | 100.0% | 100.0% |
| by_tag.term | 100.0% | 100.0% | 100.0% |
| by_tag.too_complex | 100.0% | 100.0% | 0.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| h001 | 1/1 | 1/1 | 1/1 |
| h002 | 1/1 | 1/1 | 1/1 |
| h003 | 0/1 | 1/1 | 1/1 |
| h004 | 0/1 | 0/1 | 1/1 |
| h005 | 1/1 | 1/1 | 1/1 |
| h006 | 0/1 | 1/1 | 1/1 |
| h007 | 0/1 | 1/1 | 1/1 |
| h008 | 1/1 | 1/1 | 1/1 |
| h009 | 1/1 | 1/1 | 1/1 |
| h010 | 1/1 | 1/1 | 1/1 |
| h011 | 1/1 | 1/1 | 1/1 |
| h012 | 1/1 | 1/1 | 1/1 |
| h013 | 0/1 | 1/1 | 1/1 |
| h014 | 0/1 | 1/1 | 1/1 |
| h015 | 1/1 | 1/1 | 1/1 |
| h016 | 1/1 | 1/1 | 1/1 |
| h017 | 0/1 | 1/1 | 1/1 |
| h018 | 1/1 | 1/1 | 1/1 |
| h019 | 1/1 | 1/1 | 1/1 |
| h020 | 0/1 | 1/1 | 0/1 |
| h021 | 1/1 | 0/1 | 0/1 |
| h022 | 0/1 | 1/1 | 1/1 |
| h023 | 0/1 | 1/1 | 1/1 |
| h024 | 1/1 | 1/1 | 1/1 |
| h025 | 1/1 | 1/1 | 1/1 |
| h026 | 1/1 | 1/1 | 0/1 |
| h027 | 1/1 | 1/1 | 0/1 |
| h028 | 0/1 | 1/1 | 0/1 |
