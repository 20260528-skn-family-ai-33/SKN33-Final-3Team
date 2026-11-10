# compose_text 평가 비교 (dev)

- 실행 시각(UTC): 2026-11-09 08:53
- 반복: 3회
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 10 | 10 | 10 |
| runs | 30 | 30 | 30 |
| elapsed_seconds ↓ | 86.0 | 147.0 | 124.0 |
| pass_rate | 13.3% | 33.3% | 43.3% |
| latency_ms_p50 ↓ | 2,148.0 | 4,364.0 | 3,724.5 |
| latency_ms_p95 ↓ | 4,282 | 7,881 | 6,659 |
| latency_ms_max ↓ | 16,084 | 10,054 | 6,764 |
| latency_ms_mean ↓ | 2,863 | 4,895 | 4,132 |
| grounded_rate | 13.3% | 33.3% | 43.3% |
| sentence_mismatch_rate ↓ | 39.7% | 33.3% | 38.5% |
| consistent_rate | 90.0% | 70.0% | 70.0% |
| field_accuracy.explanation_empty | 100.0% | 100.0% | 100.0% |
| field_accuracy.forbidden | 100.0% | 100.0% | 97.0% |
| field_accuracy.format | 100.0% | 100.0% | 100.0% |
| field_accuracy.grounded | 13.3% | 33.3% | 43.3% |
| field_accuracy.no_numbers | 77.8% | 100.0% | 100.0% |
| field_accuracy.order | 100.0% | 100.0% | 100.0% |
| field_accuracy.required | 75.8% | 97.0% | 100.0% |
| by_tag.code_to_name | 0.0% | 0.0% | 66.7% |
| by_tag.conversation | 33.3% | 58.3% | 75.0% |
| by_tag.hs_lookup | 0.0% | 0.0% | 33.3% |
| by_tag.multi_candidate | 0.0% | 0.0% | 0.0% |
| by_tag.multi_task | 0.0% | 0.0% | 0.0% |
| by_tag.recall | 0.0% | 33.3% | 100.0% |
| by_tag.recall_empty | 100.0% | 100.0% | 100.0% |
| by_tag.recall_number | 0.0% | 0.0% | 0.0% |
| by_tag.recent | 0.0% | 33.3% | 33.3% |
| by_tag.term | 16.7% | 50.0% | 50.0% |
| by_tag.text_ranking | 0.0% | 66.7% | 0.0% |
| by_tag.text_stats | 0.0% | 11.1% | 22.2% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| k001 | 0/3 | 0/3 | 1/3 |
| k002 | 0/3 | 1/3 | 3/3 |
| k003 | 3/3 | 3/3 | 3/3 |
| k004 | 1/3 | 3/3 | 3/3 |
| k005 | 0/3 | 0/3 | 0/3 |
| k006 | 0/3 | 0/3 | 2/3 |
| k007 | 0/3 | 2/3 | 0/3 |
| k008 | 0/3 | 1/3 | 1/3 |
| k009 | 0/3 | 0/3 | 0/3 |
| k010 | 0/3 | 0/3 | 0/3 |
