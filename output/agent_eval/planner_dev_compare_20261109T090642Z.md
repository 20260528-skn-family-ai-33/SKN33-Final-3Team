# planner 평가 비교 (dev)

- 실행 시각(UTC): 2026-11-09 09:06
- 반복: 3회
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 40 | 40 | 40 |
| runs | 120 | 120 | 120 |
| elapsed_seconds ↓ | 340.6 | 402.4 | 494.8 |
| pass_rate | 66.7% | 87.5% | 90.0% |
| latency_ms_p50 ↓ | 2,272.5 | 3,225 | 3,481 |
| latency_ms_p95 ↓ | 4,617 | 6,160 | 8,074 |
| latency_ms_max ↓ | 45,078 | 7,657 | 20,229 |
| latency_ms_mean ↓ | 2,833 | 3,380 | 4,157 |
| task_count_accuracy | 0.9 | 1.0 | 1.0 |
| parse_failure_rate ↓ | 0.0% | 0.0% | 1.7% |
| model_pass_rate | 65.8% | 87.2% | 89.7% |
| regex_pass_rate | 100.0% | 100.0% | 100.0% |
| consistent_rate | 97.5% | 95.0% | 95.0% |
| field_accuracy.answer_format | 100.0% | 100.0% | 100.0% |
| field_accuracy.article_period | 100.0% | 100.0% | 100.0% |
| field_accuracy.compare_period | 100.0% | 100.0% | 100.0% |
| field_accuracy.comparison_requested | 97.1% | 100.0% | 100.0% |
| field_accuracy.countries | 88.2% | 98.0% | 97.1% |
| field_accuracy.country_mentions | 90.5% | 100.0% | 100.0% |
| field_accuracy.custom_query | 96.8% | 97.6% | 97.6% |
| field_accuracy.custom_query_content | 50.0% | 75.0% | 100.0% |
| field_accuracy.error | 100.0% | 100.0% | 33.3% |
| field_accuracy.intent | 96.8% | 100.0% | 100.0% |
| field_accuracy.period | 88.2% | 97.1% | 100.0% |
| field_accuracy.plan | 100.0% | 100.0% | 100.0% |
| field_accuracy.products | 86.1% | 100.0% | 100.0% |
| field_accuracy.ranking_group | 62.5% | 87.5% | 95.8% |
| field_accuracy.ranking_limit | 100.0% | 100.0% | 100.0% |
| field_accuracy.recent_months | 100.0% | 100.0% | 100.0% |
| field_accuracy.share_basis | 100.0% | 100.0% | 100.0% |
| field_accuracy.source_tasks | 100.0% | 97.6% | 97.6% |
| field_accuracy.target_selections | 100.0% | 100.0% | 100.0% |
| field_accuracy.trade_flow | 97.1% | 99.0% | 99.0% |
| by_tag.abbreviation_not_country | 100.0% | 100.0% | 100.0% |
| by_tag.ambiguous | 0.0% | 100.0% | 66.7% |
| by_tag.binding | 0.0% | 0.0% | 100.0% |
| by_tag.cause | 0.0% | 100.0% | 100.0% |
| by_tag.company_not_country | 100.0% | 100.0% | 100.0% |
| by_tag.compare | 0.0% | 100.0% | 100.0% |
| by_tag.conversation | 100.0% | 100.0% | 100.0% |
| by_tag.country_switch | 0.0% | 100.0% | 100.0% |
| by_tag.custom_query | 33.3% | 100.0% | 100.0% |
| by_tag.followup | 20.0% | 100.0% | 100.0% |
| by_tag.foreign_market | 0.0% | 66.7% | 0.0% |
| by_tag.hs6 | 100.0% | 100.0% | 100.0% |
| by_tag.hs_lookup | 100.0% | 100.0% | 100.0% |
| by_tag.import | 100.0% | 100.0% | 100.0% |
| by_tag.inherit | 0.0% | 100.0% | 100.0% |
| by_tag.korea_partner | 100.0% | 77.8% | 66.7% |
| by_tag.market | 100.0% | 100.0% | 100.0% |
| by_tag.market_type | 100.0% | 100.0% | 100.0% |
| by_tag.mom | 0.0% | 100.0% | 100.0% |
| by_tag.multi_task | 50.0% | 0.0% | 50.0% |
| by_tag.news | 100.0% | 100.0% | 100.0% |
| by_tag.numeric | 100.0% | 100.0% | 100.0% |
| by_tag.out_of_scope | 66.7% | 100.0% | 100.0% |
| by_tag.period_quarter | 100.0% | 100.0% | 100.0% |
| by_tag.period_range | 100.0% | 100.0% | 100.0% |
| by_tag.period_year | 100.0% | 100.0% | 100.0% |
| by_tag.product_merge | 100.0% | 100.0% | 100.0% |
| by_tag.product_multi | 100.0% | 100.0% | 100.0% |
| by_tag.product_share | 0.0% | 0.0% | 100.0% |
| by_tag.quarter | 0.0% | 100.0% | 100.0% |
| by_tag.ranking | 75.0% | 83.3% | 66.7% |
| by_tag.ranking_limit | 100.0% | 100.0% | 100.0% |
| by_tag.recall | 100.0% | 100.0% | 100.0% |
| by_tag.recent_months | 100.0% | 100.0% | 100.0% |
| by_tag.regex | 100.0% | 100.0% | 100.0% |
| by_tag.report | 100.0% | 100.0% | 100.0% |
| by_tag.reset | 0.0% | 100.0% | 100.0% |
| by_tag.share | 0.0% | 22.2% | 66.7% |
| by_tag.shared_condition | 100.0% | 0.0% | 0.0% |
| by_tag.statistics | 75.0% | 100.0% | 100.0% |
| by_tag.term | 100.0% | 100.0% | 100.0% |
| by_tag.term_rescue | 100.0% | 100.0% | 100.0% |
| by_tag.text_format | 0.0% | 100.0% | 100.0% |
| by_tag.too_complex | 100.0% | 100.0% | 33.3% |
| by_tag.volume | 100.0% | 100.0% | 100.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| p001 | 3/3 | 3/3 | 3/3 |
| p002 | 3/3 | 3/3 | 3/3 |
| p003 | 0/3 | 3/3 | 3/3 |
| p004 | 3/3 | 1/3 | 0/3 |
| p005 | 3/3 | 3/3 | 3/3 |
| p006 | 0/3 | 0/3 | 3/3 |
| p007 | 0/3 | 0/3 | 3/3 |
| p008 | 0/3 | 2/3 | 0/3 |
| p009 | 0/3 | 3/3 | 3/3 |
| p010 | 3/3 | 3/3 | 3/3 |
| p011 | 3/3 | 3/3 | 3/3 |
| p012 | 3/3 | 3/3 | 3/3 |
| p013 | 3/3 | 3/3 | 3/3 |
| p014 | 3/3 | 3/3 | 3/3 |
| p015 | 3/3 | 3/3 | 3/3 |
| p016 | 3/3 | 3/3 | 3/3 |
| p017 | 3/3 | 3/3 | 3/3 |
| p018 | 3/3 | 3/3 | 3/3 |
| p019 | 2/3 | 3/3 | 3/3 |
| p020 | 3/3 | 3/3 | 3/3 |
| p021 | 0/3 | 3/3 | 3/3 |
| p022 | 0/3 | 3/3 | 3/3 |
| p023 | 3/3 | 3/3 | 3/3 |
| p024 | 0/3 | 3/3 | 3/3 |
| p025 | 0/3 | 3/3 | 3/3 |
| p026 | 3/3 | 0/3 | 0/3 |
| p027 | 0/3 | 0/3 | 3/3 |
| p028 | 0/3 | 3/3 | 3/3 |
| p029 | 0/3 | 3/3 | 3/3 |
| p030 | 3/3 | 3/3 | 3/3 |
| p031 | 3/3 | 3/3 | 3/3 |
| p032 | 3/3 | 3/3 | 3/3 |
| p033 | 3/3 | 3/3 | 3/3 |
| p034 | 3/3 | 3/3 | 3/3 |
| p035 | 3/3 | 3/3 | 3/3 |
| p036 | 3/3 | 3/3 | 3/3 |
| p037 | 3/3 | 3/3 | 3/3 |
| p038 | 0/3 | 3/3 | 2/3 |
| p039 | 3/3 | 3/3 | 1/3 |
| p040 | 3/3 | 3/3 | 3/3 |
