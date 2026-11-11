# Report Agent 실제 호출 평가

평가일: 2026-11-09 · 이슈 #82

## 기존 회귀 평가와의 차이

[오프라인 회귀 평가](REPORT_AGENT_EVALUATION.md)는 LLM을 Mock으로 고정하고 승인·접수·대기·파일 생성 흐름을 본다.
이 평가는 Report Agent에서 **LLM이 맡는 부분, 즉 리포트 요청을 이해하는 플래너**를 실제 OpenAI로 실행한다.
운영 경로 `Services.planner → structured → PlannerOutput → rescue_trade_terms·merge_product_phrases → build_plan`을
그대로 쓰고, 실행 예산 장부(MySQL)만 바로 실행하는 어댑터로 바꿨다. 정답은 모델에 보내지 않는다.

**정답은 AI가 작성한 초안이며 사람 검토 전이다.** 결과 JSON은 `PROVISIONAL_UNREVIEWED_GOLD`로 표시한다.
이 수치를 사람이 확정한 검증 세트의 공식 정확도로 발표하면 안 된다. 검토표는 [report_agent_live_gold_review.md](evaluations/report_agent_live_gold_review.md)다.

## 고정 질문·정답

- [report_agent_live_questions.jsonl](evaluations/report_agent_live_questions.jsonl): 32문항과 정답(의도 순서, 리포트 여부, 선행 결과 연결, 수출입, 국가, 품목, 기간)
- [report_agent_live_manifest.json](evaluations/report_agent_live_manifest.json): 모델 호출 전 고정 시각, 질문·프롬프트·스키마 해시, 검토 상태

| 유형 | 문항 | 판단 기준 |
|---|---:|---|
| 조건 기반 리포트 | 8 (L01–L08) | REPORT 하나, 국가·품목·기간 |
| 결과 기반 리포트 | 6 (L09–L14) | 앞 분석 → REPORT, REPORT가 앞 결과를 가리킴(D-90) |
| 수입 리포트 | 4 (L15–L18) | 수입을 수출로 바꾸지 않음(뒤 단계가 D-81로 거부) |
| 리포트 아님 | 6 (L19–L24) | 리포트를 만들지 않음 |
| 여러 요청 | 4 (L25–L28) | 리포트와 다른 요청을 나누고 리포트 조건을 맞게 둠 |
| 이전 대화 이어서 | 4 (L29–L32) | 질문에 없는 조건은 이전 조건을 이어받음 |

정답에 적힌 항목이 모두 맞아야 정답이다. null은 채점하지 않는다. 수출입을 말하지 않으면 시스템 기본값(수출)으로 본다.
결과 기반 리포트의 국가·품목·기간은 조건을 가진 앞 하위 질문에서 본다.

## 결과

모델 `gpt-4.1-mini`(운영 `API_INTENT_MODEL`), 총 비용 추정 **$0.045**, 플래너 해석 실패 1건.

| 채점 | 정답 | 비고 |
|---|---:|---|
| 1판 (원본) | 18/32 (56.25%) | [report_agent_live_result.json](evaluations/report_agent_live_result.json) |
| 2판 (정답 수정 재채점) | **23/32 (71.88%)** | [report_agent_live_result_rescored.json](evaluations/report_agent_live_result_rescored.json) |

| 유형 | 2판 |
|---|---:|
| 조건 기반 리포트 | 6/8 |
| 결과 기반 리포트 | **2/6** |
| 수입 리포트 | 2/4 |
| 리포트 아님 | 6/6 |
| 여러 요청 | 3/4 |
| 이전 대화 이어서 | 4/4 |

### 2판 재채점 (모델 재호출 없음)

출력을 본 뒤 정답 규칙 두 가지가 지나치게 엄격했음을 확인해 [정답 수정](evaluations/report_agent_live_gold_corrections.json)에 이유와 함께 따로 기록했다.
원본 결과와 고정 질문은 바꾸지 않았고 [재채점 스크립트](../backend/scripts/score_report_agent_live.py)로 같은 출력을 다시 채점했다.
**출력을 보고 바꾼 정답이므로 2판은 개발 세트 결과다.**

- L29–L32(이전 대화 이어서): 정답은 "조건을 비워 이어받음"만 인정했다. 모델은 이전과 **같은 값**을 다시 적었다. 확정 조건이 같아 결과가 같으므로 인정했다.
  다만 플래너 프롬프트는 "비워 두라"고 지시하므로 프롬프트와 출력이 다르다는 점은 아래 발견 사항에 남긴다.
- L22(원인 질문): "리포트 아님" 유형의 기준은 리포트를 만들지 않는 것이다. 원인 질문을 STATISTICS+CAUSE로 나누는 것은 Report Agent 범위 밖이라 허용했다.

## 발견 사항 (모델 동작)

1. **결과 기반 리포트의 연결 누락 (L11–L14, L18):** "그 결과로 리포트"에서 REPORT가 앞 결과를 가리키지 않고(`source_tasks=[]`)
   같은 조건의 독립 리포트를 만든다. 리포트는 만들어지지만 승인한 결과 사본이 아니라 **다시 조회한 값**으로 만들어져 D-90의 의도와 다르다.
   L09·L10처럼 "조회하고 그 결과로", "순위 보여주고 그 결과로"는 연결했다.
2. **리포트만 요청해도 통계 조회를 덧붙임 (L06, L07, L17):** STATISTICS+REPORT로 나눠 불필요한 조회를 한 번 더 한다(답변·비용 증가, 리포트 자체는 정상).
3. **세 단계 요청의 스키마 실패 (L28):** 두 번 모두 `PlannerOutput` 검증에 실패해 플래너가 해석 실패 안내로 끝났다.
4. **이전 조건을 비우지 않고 다시 적음 (L29–L32):** 프롬프트 지시와 다르지만 같은 값이라 결과는 같다.

플래너 프롬프트·코드는 채팅 에이전트 담당 범위라 이 작업에서 바꾸지 않았다. 1·3번은 플래너 담당자에게 개선 후보로 전달한다.

## 재현

```powershell
python -m backend.scripts.evaluate_report_agent_live --freeze --set live          # 질문 고정(모델 호출 없음)
python -m backend.scripts.evaluate_report_agent_live --run --set live --allow-unreviewed-gold --max-cost-usd 1
python -m backend.scripts.score_report_agent_live --input docs/evaluations/report_agent_live_result.json --output docs/evaluations/report_agent_live_result_rescored.json
python -m unittest backend.tests.test_agent_report_live_evaluation               # 채점·안전장치 검증(네트워크 없음)
```

- 앱 설정에서는 `OPENAI_API_KEY`만 읽는다. DB·모델 주소는 받지 않는다. 비용 상한을 넘기 전에 멈춘다.
- 고정 뒤 질문이 바뀌면 실행을 거부한다. 사람 검토 전 정답은 `--allow-unreviewed-gold`가 있어야 실행한다.
- 해시는 CRLF를 LF로 바꾼 뒤 계산한다. 결과에 하네스 커밋과 커밋하지 않은 변경 여부(`dirty`)를 남긴다.

## 한계

- 플래너만 실제 호출한다. 조건 확정(HS·국가 사전), 미리보기, 승인, 접수, 완료는 오프라인 회귀 평가가 맡는다.
- 정답은 AI 초안이며 사람 검토 전이다. 고정 질문 세트 결과이며 실제 사용자 질문 분포나 서비스 전체 정확도가 아니다.
- 비용은 사용량 기반 추정이다. 가격은 [모델 페이지](https://developers.openai.com/api/docs/models/gpt-4.1-mini)(2026-11-09 확인,
  입력 $0.40·캐시 $0.10·출력 $1.60 / 1M 토큰) 기준이며 페이지에 Deprecated 표시가 있다.
- 3번 실패는 1판에서 어느 칸이 틀렸는지 기록하지 못했다. 이후 실행(holdout)부터 오류 위치를 남긴다.
