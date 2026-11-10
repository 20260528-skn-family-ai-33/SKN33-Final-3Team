# compose_text 평가 비교 (holdout)

- 실행 시각(UTC): 2026-11-09 08:57
- 반복: 1회
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 10 | 10 | 10 |
| runs | 10 | 10 | 10 |
| elapsed_seconds ↓ | 29.0 | 44.6 | 41.6 |
| pass_rate | 30.0% | 30.0% | 40.0% |
| latency_ms_p50 ↓ | 2,330.5 | 3,249.0 | 3,030.5 |
| latency_ms_p95 ↓ | 10,245 | 8,072 | 9,061 |
| latency_ms_max ↓ | 10,245 | 8,072 | 9,061 |
| latency_ms_mean ↓ | 2,899 | 4,459 | 4,162 |
| grounded_rate | 40.0% | 30.0% | 40.0% |
| sentence_mismatch_rate ↓ | 27.8% | 41.7% | 30.0% |
| consistent_rate | 100.0% | 100.0% | 100.0% |
| field_accuracy.explanation_empty | 83.3% | 100.0% | 100.0% |
| field_accuracy.forbidden | 100.0% | 100.0% | 100.0% |
| field_accuracy.format | 100.0% | 100.0% | 100.0% |
| field_accuracy.grounded | 40.0% | 30.0% | 40.0% |
| field_accuracy.no_numbers | 100.0% | 100.0% | 100.0% |
| field_accuracy.order | 100.0% | 100.0% | 100.0% |
| field_accuracy.required | 83.3% | 100.0% | 100.0% |
| by_tag.code_to_name | 0.0% | 0.0% | 0.0% |
| by_tag.conversation | 75.0% | 75.0% | 75.0% |
| by_tag.hs6 | 0.0% | 0.0% | 0.0% |
| by_tag.hs_lookup | 0.0% | 0.0% | 0.0% |
| by_tag.multi_candidate | 0.0% | 0.0% | 0.0% |
| by_tag.multi_task | 0.0% | 0.0% | 0.0% |
| by_tag.recall | 0.0% | 0.0% | 0.0% |
| by_tag.recall_empty | 100.0% | 100.0% | 100.0% |
| by_tag.term | 66.7% | 66.7% | 66.7% |
| by_tag.text_ranking | 0.0% | 0.0% | 0.0% |
| by_tag.text_stats | 0.0% | 0.0% | 50.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| k101 | 0/1 | 0/1 | 1/1 |
| k102 | 0/1 | 0/1 | 0/1 |
| k103 | 0/1 | 0/1 | 0/1 |
| k104 | 1/1 | 1/1 | 1/1 |
| k105 | 0/1 | 0/1 | 0/1 |
| k106 | 1/1 | 1/1 | 1/1 |
| k107 | 1/1 | 1/1 | 1/1 |
| k108 | 0/1 | 0/1 | 0/1 |
| k109 | 0/1 | 0/1 | 0/1 |
| k110 | 0/1 | 0/1 | 0/1 |
