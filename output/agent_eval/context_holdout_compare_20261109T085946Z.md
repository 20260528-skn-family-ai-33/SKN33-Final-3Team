# context 평가 비교 (holdout)

- 실행 시각(UTC): 2026-11-09 08:59
- 반복: 1회
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 10 | 10 | 10 |
| runs | 10 | 10 | 10 |
| elapsed_seconds ↓ | 66.7 | 93.2 | 99.1 |
| pass_rate | 20.0% | 50.0% | 70.0% |
| latency_ms_p50 ↓ | 5,412.0 | 7,894.5 | 10,000.5 |
| latency_ms_p95 ↓ | 20,357 | 18,641 | 14,085 |
| latency_ms_max ↓ | 20,357 | 18,641 | 14,085 |
| latency_ms_mean ↓ | 6,670 | 9,324 | 9,910 |
| turn_pass_rate | 33.3% | 54.2% | 79.2% |
| turn_latency_ms_p50 ↓ | 2,220.0 | 3,644.0 | 3,966.0 |
| turn_latency_ms_p95 ↓ | 3,823 | 5,434 | 5,587 |
| turn_latency_ms_max ↓ | 15,340 | 8,577 | 9,178 |
| turn_latency_ms_mean ↓ | 2,762 | 3,885 | 4,129 |
| consistent_rate | 100.0% | 100.0% | 100.0% |
| by_tag.back_and_forth | 0.0% | 0.0% | 0.0% |
| by_tag.compare_followup | 0.0% | 0.0% | 100.0% |
| by_tag.conversation_between | 0.0% | 100.0% | 100.0% |
| by_tag.country_switch | 0.0% | 0.0% | 50.0% |
| by_tag.inherit | 0.0% | 0.0% | 50.0% |
| by_tag.intent_switch | 50.0% | 100.0% | 100.0% |
| by_tag.news | 0.0% | 100.0% | 100.0% |
| by_tag.product_reset | 0.0% | 100.0% | 100.0% |
| by_tag.product_switch | 0.0% | 0.0% | 0.0% |
| by_tag.ranking_followup | 100.0% | 100.0% | 100.0% |
| by_tag.recent_months | 50.0% | 100.0% | 100.0% |
| by_tag.reset | 100.0% | 100.0% | 100.0% |
| by_tag.term_first | 0.0% | 100.0% | 100.0% |
| by_tag.trade_flow | 0.0% | 50.0% | 100.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| s101 | 0/1 | 0/1 | 1/1 |
| s102 | 0/1 | 1/1 | 1/1 |
| s103 | 1/1 | 1/1 | 1/1 |
| s104 | 0/1 | 0/1 | 0/1 |
| s105 | 0/1 | 1/1 | 1/1 |
| s106 | 1/1 | 1/1 | 1/1 |
| s107 | 0/1 | 0/1 | 1/1 |
| s108 | 0/1 | 1/1 | 1/1 |
| s109 | 0/1 | 0/1 | 0/1 |
| s110 | 0/1 | 0/1 | 0/1 |
