# compose_text 테스트 계획서 (이슈 86)

## 1. 목적
문장형 답변 노드(D-98·D-99·D-101)가 도구 결과와 최근 대화만 근거로, 문장 수 상한을 지켜, 질문에 맞는 내용의 답을 쓰는지 측정한다. 평가 당시에는 기준선과 후보 모델을 비교해 Writer 모델을 선정하는 것이 목적이었다(C-35). 현재 문장형 Writer의 기본값은 `API_COMPOSE_TEXT_MODEL=gpt-6-luna`다.

## 2. 범위
| 포함 | 제외 |
|---|---|
| `compose_input`(입력 구성: 고정 결론 분리, recent 추가) | 표·차트 답변(`compose`, `COMPOSE_PROMPT`만 쓰는 경로) |
| `COMPOSE_TEXT_PROMPT` + LLM 구조화 출력(`Draft`, 3000토큰, 최대 2회) | 기사 인용([n]) 답변 |
| `assemble`(하위 질문 순서·문장 상한 D-50/D-98) | 재생성 1회(verify NEEDS_REVIEW 뒤 다시 쓰기) |
| `verify`(digest 숫자 대조, 대화 숫자 `check_history`, HS 코드 `check_codes`) | 대화방 제목(첫 답변 title) |
| 하위 질문별 결론 내용(필수·금지 표현, 순서) | 설명 문단(explanation)의 내용. 숫자는 verify가 본다 |

## 3. 평가 모델
| 모델 | 구분 | 비고 |
|---|---|---|
| `gpt-4.1-mini` | 기준선 | 평가 당시 기본값 |
| `gpt-5.6-luna` | 후보 | 추론 모델 |
| `gpt-6-luna` | 후보 | 추론 모델 |

## 4. 환경·전제
- `.env`에 `OPENAI_API_KEY`가 있어야 한다. DB는 쓰지 않는다.
- 도구 결과는 문항에 적은 고정 digest를 쓴다. 형식은 실제 TREND·RANKING·HS_CODES digest와 같다.
- 국가·품목 이름은 DB 대신 실행기 이름표(국가 7개)와 문항의 `product_names`로 넣는다.
- 시간 상한은 `API_COMPOSE_TIMEOUT_SECONDS`(20초)이다.

## 5. 테스트셋
**파일**: `backend/evals/agent/cases/compose_text.jsonl` (20문항: dev 10 / holdout 10)

### 5.1 문항 형식
| 칸 | 내용 |
|---|---|
| `question` | 전체 질문 |
| `recent` | (선택) 최근 대화(질문·intent·요약). 이전 대화 묻기의 근거 |
| `product_names` | 품목 코드 → 표시 이름 |
| `tasks[]` | 하위 질문마다 `intent`, `question`, `answer_format`(TEXT), `conditions`, `digest` |
| `expect.conclusions[]` | 하위 질문 순서대로 결론 채점 규칙. 개수가 하위 질문 수와 같아야 한다 |
| `expect.explanation_empty` | (선택) 설명 문단이 비어 있어야 하는지 |

`expect.conclusions[]`의 칸은 다음과 같다. 정규식은 결론 문장을 소문자로 바꾸고 숫자의 천 단위 쉼표를 지운 뒤 적용한다. 예를 들어 "2,840만"은 `2840만`으로 맞춰야 한다.

| 칸 | 판정 |
|---|---|
| `reference` | 모범 결론. 채점에는 쓰지 않고 결과 파일·검수에 쓴다. 단위 테스트가 모범 결론이 규칙을 통과하는지 확인한다 |
| `required` | 정규식이 모두 맞아야 한다(대답해야 할 내용) |
| `forbidden` | 정규식이 하나라도 맞으면 실패(반대 결론·부정·다른 코드) |
| `ordered` | 정규식이 모두 이 순서로 나와야 한다(순위) |
| `no_numbers` | 결론에 숫자가 없어야 한다(용어 설명·대화 없음) |

### 5.2 유형 분포
| 유형 | 태그 | dev | holdout |
|---|---|---|---|
| 결과를 말로 설명 | text_stats·text_ranking·recent·hs6 | 4 | 4 |
| 이전 대화 묻기 | recall·recall_empty·recall_number | 3 | 3 |
| 용어 설명 | term | 2 | 3 |
| HS 조회 | hs_lookup·multi_candidate·code_to_name | 2 | 2 |
| 복합 질문 | multi_task | 1 | 2 |

숫자는 그 유형 태그가 하나라도 붙은 문항 수다(한 문항이 여러 유형에 걸칠 수 있다). holdout은 유형마다 2문항 이상이다.

### 5.3 문항 목록
| id | split | 질문 | intent | 근거 | 결론 채점 | 확인점 |
|---|---|---|---|---|---|---|
| k001 | dev | 2025년 하반기 베트남 기초화장품 수출이 전반적으로 늘었는지, 가장 컸던 달은 언제인지 말로 설명해줘 | STATISTICS·TEXT | TREND 6개월 | 필수: 베트남·기초화장품·수출·증가·"12월 가장 큼". 금지: 감소 결론, 증가 부정 | 추세 판단 + 최대 월 |
| k002 | dev | 이전 질문 뭐였더라? | CONVERSATION | recent 2턴 | 필수: "미국은?" 또는 미국 수출 질문 언급. 금지: 부정 표현 | 마지막 질문(미국은?) 답, 설명 없음 |
| k003 | dev | 아까 뭐 물어봤지? | CONVERSATION | recent 없음 | 필수: 질문·대화가 없다. 금지: "있었습니다". 숫자 없음 | 대화가 없다고 답 |
| k004 | dev | HS코드가 뭐야? | CONVERSATION | 없음 | 필수: 국제/무역 + 상품/품목 + 분류. 숫자 없음 | 용어 설명에 "6자리" 같은 숫자 금지 |
| k005 | dev | 전동 면도기 HS코드 알려줘 | HS_LOOKUP | 후보 2개(8510·851010) | 필수: HS2022, 전동 면도기, 8510과 면도기/상위 분류를 붙여 씀, 851010과 전동 면도기/세부 품목을 붙여 씀 | 대표 하나만 고르지 않고 코드와 이름을 짝지어 나열 |
| k006 | dev | 330499는 무슨 품목이야? | HS_LOOKUP | 1개 | 필수: HS2022, 330499와 기초화장품을 붙여 씀. 금지: 330410·330420 | 코드 지어내기 금지 |
| k007 | dev | 2025년 화장품 수출 상위 국가 3곳을 말로 정리해줘 | RANKING·TEXT | 순위 3개·비중 | 순서: 중국 → 미국 → 일본. 금지: 일본·미국이 가장 많다 | 순위 순서 |
| k008 | dev | 앞에서 본 내용을 이어서, 수출 흐름과 수출액이 가장 컸던 달을 말로 정리해줘 | STATISTICS·TEXT | TREND + recent | k001과 같음 | 숫자는 digest만, recent는 맥락 |
| k009 | dev | FTA가 뭐고, 2025년 하반기 베트남 기초화장품 수출이 전반적으로 늘었는지 줄었는지 말로 알려줘 | CONVERSATION + STATISTICS·TEXT | 용어 + TREND | 1번: 자유무역협정 + 관세/장벽, 숫자 없음. 2번: 베트남·기초화장품·수출·증가 | 하위 질문별 결론 분리 |
| k010 | dev | 아까 12월 수출액이 얼마였지? | CONVERSATION | recent 요약(2,840만 달러) | 필수: "12월 … 2840만 달러". 금지: 2610만 | 요약 숫자만 그대로 |
| k101 | holdout | 2024년 상반기 일본 라면 수출이 전반적으로 늘었는지, 가장 컸던 달은 언제인지 문장으로 알려줘 | STATISTICS·TEXT | TREND 6개월 | 필수: 일본·라면·수출·증가·"6월 가장 큼" | 추세 판단 + 최대 월 |
| k102 | holdout | 지금 보이는 최근 대화 중 첫 질문이 뭐였지? | CONVERSATION | recent 3턴 | 필수: 2024년·일본·라면·수출·흐름/추이 | 첫 턴 찾기, 설명 없음 |
| k103 | holdout | 라면 HS코드 뭐야? | HS_LOOKUP | 후보 2개(1902·190230) | 필수: HS2022, 1902와 파스타, 190230과 그 밖의 파스타를 짝지어 씀 | |
| k104 | holdout | 원산지 규정이 뭔가요? | CONVERSATION | 없음 | 필수: 상품 + 원산지 + 판정/결정. 숫자 없음 | |
| k105 | holdout | 2025년 미국 수출 상위 품목 3개를 말로 정리해줘 | RANKING·TEXT | HS6 순위(억 달러 단위) | 순서: 승용차 → 메모리 → 석유제품. 금지: 메모리·석유제품이 가장 많다 | 큰 금액 단위 표기 |
| k106 | holdout | 방금 내가 뭘 물어봤더라? | CONVERSATION | recent 없음 | 필수: 질문·대화가 없다. 금지: "있었습니다". 숫자 없음 | 대화가 없다고 답 |
| k107 | holdout | 인코텀즈가 뭐야? | CONVERSATION | 없음 | 필수: 무역/거래 + 조건/규칙 + 인도/비용/위험. 숫자 없음 | "인코텀즈 2020" 같은 숫자 금지 |
| k108 | holdout | 851010은 무슨 품목이야? | HS_LOOKUP | 1개 | 필수: HS2022, 851010과 전동 면도기를 짝지어 씀. 금지: 851020·851030 | 코드 → 품목명 |
| k109 | holdout | HS코드가 뭐고, 2025년 화장품 수출 상위 국가 3곳을 말로 알려줘 | CONVERSATION + RANKING·TEXT | 용어 + 순위 3개 | 1번: 국제/무역 + 상품 + 분류, 숫자 없음. 2번 순서: 중국 → 미국 → 일본 | 용어 + 순위 복합 |
| k110 | holdout | 아까 첫 질문이 뭐였고, 2024년 상반기 일본 라면 수출은 전반적으로 늘었는지 말로 알려줘 | CONVERSATION + STATISTICS·TEXT | recent 2턴 + TREND | 1번: 미국·3304/화장품·수출, 금지: "중국은". 2번: 일본·라면·수출·증가 | 이전 대화 + 추세 복합 |

모든 문항의 `forbidden`에는 "아니다·않다·무관·틀렸다" 같은 부정 표현이 들어 있다. 맞는 단어를 쓰고도 결론을 부정하는 답을 막기 위해서다.

## 6. 실행 방법
```bash
.venv/Scripts/python.exe -m backend.evals.agent.run compose_text --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna --repeat 3
# 개선 완료 뒤 1회
.venv/Scripts/python.exe -m backend.evals.agent.run compose_text --split holdout --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna
```
세 모델을 한 명령으로 차례로 돌리고, 결과를 한 쌍의 파일로 남긴다: `output/agent_eval/compose_text_<split>_compare_<시각>.md`(모델별 지표·문항별 통과 비교 표)와 같은 이름의 `.json`(모델별 요약과 문항별 전체 결과). `.json`의 `detail.sentences`에 생성 문장 전체가, `detail.references`에 모범 결론이 남아 수동 검토에 쓴다.

## 7. 평가 지표
| 지표 | 정의 | 계산 | 목표(제안) |
|---|---|---|---|
| **문항 통과율** (주 지표) | 형식·근거·결론 규칙(필수·금지·순서·숫자 없음)·설명 없음이 모두 맞은 실행 | `pass_rate` | ≥ 90% |
| 형식 통과율 | 스키마 검증과 `assemble` 통과(하위 질문 순서·문장 상한) | `field_accuracy.format` | ≥ 98% |
| **근거 일치율** | `verify` 결과 PASS인 실행(모든 문장·요약이 근거와 일치) | `grounded_rate` | ≥ 95% |
| 문장 불일치율 | 숫자가 있는 문장 중 MISMATCH 비율 | `sentence_mismatch_rate` | ≤ 2% |
| 필수 내용 충족률 | 결론별 `required` 충족 | `field_accuracy.required` | ≥ 95% |
| 금지 위반률 | 결론별 `forbidden` 위반 | 1 - `field_accuracy.forbidden` | 0% |
| 순서 정확도 | 순위 문항의 `ordered` 충족 | `field_accuracy.order` | 100% |
| 숫자 없음 준수 | `no_numbers` 결론에서 숫자를 쓰지 않음 | `field_accuracy.no_numbers` | 100% |
| 반복 일관성 | 3회 판정이 같은 문항 비율 | `consistent_rate` | ≥ 90% |
| 지연 p50·p95 | 입력 구성~verify 소요(ms) | 실행별 측정 | p95 ≤ 20,000ms |
| 수동 평가 | holdout 10건을 2명이 자연스러움·정확성 각 1~5점. 모범 결론을 참고 | 평균 | ≥ 4.0 |

## 8. 판정 규칙
1. 금지 위반이나 숫자 지어내기(근거 불일치)가 반복되는 모델은 뺀다. 숫자 오류는 사용자 신뢰에 직접 영향을 준다.
2. 문항 통과율이 가장 높은 모델을 고른다. 표본이 작아 dev·holdout 모두 1건이 10%p다. 그래서 차이가 이보다 작으면 근거 일치율 → 지연 p95 → 비용 순으로 고른다.
3. 실패 원인을 다음 셋으로 나눈다. verify 오탐이면 compose가 아니라 verify 개선 항목으로 기록한다.
   - 프롬프트
   - verify 오탐(정상 문장을 MISMATCH로 판정)
   - 채점 정규식 오탐(맞는 답을 규칙이 놓침)

## 9. 위험·주의
- 표본이 20문항으로 작다. 결과는 경향으로 보고, 차이가 크지 않으면 수동 평가를 함께 본다.
- 정규식 규칙은 모범 결론과 반례로 단위 테스트(`test_compose_references_pass_and_review_counterexamples_fail`)를 한다. 규칙을 바꾸면 반례도 함께 고친다.
- HS 조회 답에 "HS 2022"처럼 띄어 쓰면 verify가 2022를 코드로 읽어 불일치로 판정한다. 채점 규칙은 띄어쓰기를 허용한다. 프롬프트가 "HS2022"로 쓰게 하니 드물지만, 실패하면 verify 오탐인지 먼저 확인한다.
- 추론 모델은 추론 토큰이 3000토큰 상한에 포함된다.

## 10. 결과 기록 양식
비율 칸은 %로 적는다. 결과 JSON의 비율은 0~1이다(1 = 100%). ↓ 표시는 낮을수록 좋은 지표이고, 나머지는 높을수록 좋다. 시간은 결과 JSON의 `latency_ms_*`(실행 1회)와 `elapsed_seconds`(평가 전체)를 옮긴다.

| 모델 | split | 통과율 | 형식 | 근거 일치 | 문장 불일치 ↓ | 필수 | 금지 위반 ↓ | 순서 | 숫자 없음 | 일관성 | p50(ms) ↓ | p95(ms) ↓ | 최대(ms) ↓ | 총 소요(초) | 수동 평가(1~5) | 비고 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | | | | | |
