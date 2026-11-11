# context 평가 비교 (dev)

- 실행 시각(UTC): 2026-11-09 08:53
- 반복: 1회
- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.

## 지표

| 지표 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| cases | 10 | 10 | 10 |
| runs | 10 | 10 | 10 |
| elapsed_seconds ↓ | 107.6 | 99.6 | 92.3 |
| pass_rate | 50.0% | 60.0% | 70.0% |
| latency_ms_p50 ↓ | 5,668.0 | 9,337.5 | 9,263.0 |
| latency_ms_p95 ↓ | 33,803 | 17,206 | 12,749 |
| latency_ms_max ↓ | 33,803 | 17,206 | 12,749 |
| latency_ms_mean ↓ | 10,748 | 9,952 | 9,228 |
| turn_pass_rate | 70.8% | 62.5% | 79.2% |
| turn_latency_ms_p50 ↓ | 2,260.0 | 3,871.0 | 3,578.5 |
| turn_latency_ms_p95 ↓ | 25,129 | 6,273 | 5,494 |
| turn_latency_ms_max ↓ | 31,899 | 7,665 | 6,453 |
| turn_latency_ms_mean ↓ | 4,437 | 4,146 | 3,845 |
| consistent_rate | 100.0% | 100.0% | 100.0% |
| by_tag.back_and_forth | 100.0% | 0.0% | 100.0% |
| by_tag.compare_followup | 0.0% | 100.0% | 100.0% |
| by_tag.conversation_between | 100.0% | 100.0% | 100.0% |
| by_tag.country_switch | 100.0% | 0.0% | 100.0% |
| by_tag.inherit | 100.0% | 50.0% | 100.0% |
| by_tag.intent_switch | 0.0% | 100.0% | 100.0% |
| by_tag.news | 0.0% | 100.0% | 100.0% |
| by_tag.period_switch | 100.0% | 100.0% | 0.0% |
| by_tag.product_reset | 0.0% | 100.0% | 100.0% |
| by_tag.product_switch | 0.0% | 0.0% | 0.0% |
| by_tag.ranking_followup | 100.0% | 100.0% | 0.0% |
| by_tag.recent_months | 100.0% | 100.0% | 0.0% |
| by_tag.reset | 0.0% | 0.0% | 100.0% |
| by_tag.term_first | 100.0% | 100.0% | 0.0% |
| by_tag.trade_flow | 100.0% | 100.0% | 100.0% |

## 문항별 통과 (통과 수 / 반복 수)

| 문항 | gpt-4.1-mini | gpt-5.6-luna | gpt-6-luna |
|---|---|---|---|
| s001 | 1/1 | 0/1 | 1/1 |
| s002 | 0/1 | 0/1 | 1/1 |
| s003 | 1/1 | 1/1 | 1/1 |
| s004 | 1/1 | 1/1 | 0/1 |
| s005 | 0/1 | 0/1 | 0/1 |
| s006 | 1/1 | 0/1 | 1/1 |
| s007 | 1/1 | 1/1 | 0/1 |
| s008 | 0/1 | 1/1 | 1/1 |
| s009 | 0/1 | 1/1 | 1/1 |
| s010 | 0/1 | 1/1 | 1/1 |
