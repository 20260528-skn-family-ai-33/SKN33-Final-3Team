# 에이전트 노드 평가 (이슈 86)

planner · 조건분석(check_conditions) · compose_text · 컨텍스트 관리 네 영역을 평가셋으로 재고, 플래너·compose 모델을 비교해 정한다(C-35). 평가 결과로 고도화 전후를 비교한다.

- 평가셋: `backend/evals/agent/cases/*.jsonl`
- 실행기: `backend/evals/agent/run.py`
- 결과: `output/agent_eval/<영역>_<split>_<모델>_<시각>.json` (요약 + 질문별 출력)
- 형식 검사(오프라인): `python -m unittest backend.tests.test_agent_eval_cases`
- 영역별 테스트 계획서(문항 목록·지표·판정 규칙·결과 기록 양식)
  - [planner](agent_eval/PLANNER_TEST_PLAN.md)
  - [조건분석](agent_eval/CONDITIONS_TEST_PLAN.md)
  - [compose_text](agent_eval/COMPOSE_TEXT_TEST_PLAN.md)
  - [컨텍스트 관리](agent_eval/CONTEXT_TEST_PLAN.md)

## 1. 평가 당시 비교 모델

아래 표와 선정 절차는 이슈 86 평가 당시 기준이다. 현재 플래너는 `API_PLANNER_MODEL=gpt-5.6-luna`, 일반·문장형 Writer는 각각 `API_COMPOSE_MODEL=gpt-6-luna`, `API_COMPOSE_TEXT_MODEL=gpt-6-luna`가 기본값이다.

| 모델 | 구분 | 이유 |
|---|---|---|
| `gpt-4.1-mini` | 기준선(평가 당시 기본값) | 개선·교체 효과를 재는 기준 |
| `gpt-5.6-luna` | 후보 | GPT-5.6의 저비용·저지연 등급(추론 모델) |
| `gpt-6-luna` | 후보 | GPT-6의 저비용 등급(추론 모델). 외부 가격표 기준 단가가 gpt-5.6-luna의 약 10분의 1 |

- 모델 ID `gpt-5.6-luna`·`gpt-6-luna`는 외부 목록(OpenRouter·Bedrock·가격 비교 사이트)에서만 확인했다. 실행 전에 우리 키로 `GET /v1/models`에 나오는지 확인한다.
- GPT-5·6 계열은 추론 모델이라 `temperature`를 받지 않는다. `StructuredClient`가 `gpt-5`·`gpt-6`으로 시작하는 모델에는 temperature를 보내지 않게 바꿨다. gpt-6 계열은 문서로 확인하지 못해, 첫 호출에서 오류가 없는지 본다. 추론 강도는 `API_LLM_REASONING_EFFORT`(예: `low`)로 줄 수 있고, 비워 두면 모델 기본값이다.
- temperature 0이 없으므로 같은 질문도 답이 달라질 수 있다. 그래서 LLM 평가는 `--repeat 3`으로 돌려 일관성도 본다.
- 추론 토큰도 `max_output_tokens`(플래너 2000, compose_text 3000)에 포함된다. 잘려서 `incomplete`가 나면 파싱 실패로 집계되니, 실패율이 높으면 상한부터 확인한다.

## 2. 평가셋

| 영역 | dev | holdout | LLM | DB | 무엇을 떼어 보나 |
|---|---|---|---|---|---|
| planner | 44 | 31 | O | X | 질문 → intent·슬롯·하위 질문 분해 |
| conditions | 29 | 16 | X (`--chain`이면 O) | O (조회만) | 정답 슬롯 → 코드 확정·되묻기·실패 |
| compose_text | 10 | 10 | O | X | 고정 digest·최근 대화 → 문장 답변 |
| context | 10 (시나리오, 24턴) | 10 (시나리오, 24턴) | O | O (조회만) | 여러 턴 이어받기·초기화·전환 |

**dev와 holdout 사용 규칙**
- 프롬프트·코드 개선은 dev 결과만 보고 한다.
- holdout은 모델 비교와 최종 확인에만 쓰고, 영역마다 개선 한 바퀴에 한 번만 돌린다. 실패 사례를 보고 프롬프트를 고치면 holdout이 dev가 되므로, 고친 뒤에는 새 holdout을 만든다.
- holdout은 dev와 표현·국가·품목을 바꿔 썼다(같은 유형, 다른 문장). 계획서의 유형 묶음마다 holdout이 2문항 이상 있다.

**태그**: 각 질문에 유형 태그(`followup`, `custom_query`, `product_merge`, `c28` 등)를 달았다. 요약의 `by_tag`로 어떤 유형이 약한지 본다.

**정답 표기**
- `null`은 생략(이어받기), `[]`는 명시적인 전체 범위다. 둘을 다르게 채점한다.
- 해석이 둘 다 맞는 칸은 `{"$in": [후보, ...]}`로 적는다(예: "2025년 수출 순위"의 products는 null과 [] 모두 허용).
- 국가는 정식 국가명으로, 품목은 질문 원문 표현으로 적는다(코드는 conditions가 정한다).

## 3. 영역별 방식과 기준

아래 목표치는 **제안값**이다. 기준선(gpt-4.1-mini) 결과를 본 뒤 확정한다.

**단위와 시간**
- 결과 JSON의 비율 지표(`*_rate`·`field_accuracy`·`*_recall_at_5`)는 0~1이다(1 = 100%). 문서의 목표와 결과 기록은 %로 적는다.
- 낮을수록 좋은 지표: `wrong_confirm_rate`·`unnecessary_ask_rate`·`parse_failure_rate`·`sentence_mismatch_rate`. 나머지 비율은 높을수록 좋다.
- 수동 평가만 1~5점이다.
- 시간은 실행 1회의 `latency_ms_p50/p95/max/mean`(ms)과 평가 전체 `elapsed_seconds`(초)로 남긴다. context는 턴별 `turn_latency_ms_*`도 남긴다. crash로 시간을 재지 못한 실행은 지연 계산에서 뺀다.

### 3.1 planner

**방식**: 실행과 같은 순서로 돈다. 정규식 처리(`explicit_country_ranking`) → LLM(`PLANNER_PROMPT`, 최대 2회) → 코드 안전망(`postprocess`: 용어 살리기·품목 표현 합치기) → `build_plan`. 실행 코드와 어긋나지 않게 `postprocess`를 planner.py에 두고 runtime과 함께 쓴다.

**채점**
- 질문 1건 통과 = 하위 질문 수가 같고, 하위 질문마다 intent와 채점 칸이 모두 맞을 때.
- 기본 칸(products·countries·period·recent_months·trade_flow·answer_format·comparison_requested·compare_period·article_period)은 정답에 없으면 null이 정답이다. 그래서 질문에 없는 조건을 지어내면 틀린다.
- intent 전용 칸은 그 intent에서만 기본 칸으로 본다. 다른 intent에서는 정답에 적힌 경우에만 본다.
  - `ranking_group`: RANKING·MARKET_SHARE
  - `share_basis`: MARKET_SHARE
  - `ranking_limit`: RANKING·PROMISING_MARKET·MARKET_TYPE
- 항상 보는 것: custom_query 유무, source_tasks·target_selections(생략하면 `[]`), 국가 mention이 질문 원문에 있는지, 계획 생성
- 정답에 적힌 경우 보는 것: `custom_query_patterns`(custom_query 문장 내용)
- CONVERSATION·OUT_OF_SCOPE·HS_LOOKUP은 정답에 적힌 슬롯만 본다.
- 하위 질문이 3개를 넘는 질문은 `QUESTION_TOO_COMPLEX`가 나와야 맞다.
- 자세한 규칙과 문항 목록은 [planner 계획서](agent_eval/PLANNER_TEST_PLAN.md)에 있다.

| 지표 | 목표(제안) |
|---|---|
| 질문 통과율 (주 지표) | dev ≥ 85%, holdout ≥ 80% |
| intent 정확도 | ≥ 95% |
| 칸별 정확도 (products·countries·period) | ≥ 95% |
| 하위 질문 수 정확도 | ≥ 95% |
| 파싱 실패율 (2회 모두 실패) | ≤ 2% |
| 반복 일관성 (3회 판정이 같은 질문 비율) | ≥ 90% |
| 지연 p95 | ≤ 10초 (`API_INTENT_TIMEOUT_SECONDS`) |

### 3.2 conditions (check_conditions)

**방식**
- 기본은 **정답 슬롯**을 넣어 조건 확인만 따로 본다. 이 노드에는 LLM이 없어 모델과 상관없다.
- `--chain`을 주면 플래너 출력 슬롯으로 돌린다. 기본 결과와의 차이가 플래너 오류가 조건 확정까지 번지는 정도다. 일부러 틀린 슬롯을 넣은 `unit_only` 문항은 플래너로 재현되지 않아 뺀다.
- DB는 설정된 RDS를 조회만 하고, 질문마다 rollback한다. REPORT 미리보기와 선행 결과 연결(resolve_bindings)은 평가 밖이다.

**채점**
실제 상태에 따라 볼 칸이 다르다. 한 문항에 확정용 정답과 되묻기용 정답을 함께 적을 수 있다.

| 실제 상태 | 보는 칸 |
|---|---|
| DONE | 확정 국가·HS 코드(`country_codes`·`hs_codes`), 기간·최근 N개월·비교 기간, 수출입, 기사 기간, 순위 기준·개수, 비중 기준, SQL 템플릿 |
| NEEDS_INPUT | 되묻기 종류(`pending_kind`), 선택지(`choices_include`), HS 후보 상위 5개에 정답 접두(`hs_candidate_prefixes`) |
| FAILED | 실패 문구(`error_contains`) |

- 품목명 검색은 DB 품목명이 정확히 하나만 일치하면 확정하고, 아니면 후보로 되묻는다. 그래서 `hs_codes`와 `hs_candidate_prefixes`를 함께 적는다.
- `country_candidate`를 지정한 문항에서는 국가 되묻기 후보 상위 5개에 정답 국가가 있어야 통과한다. 적중률도 별도로 집계한다.
- 결과의 `quality_gate_pass`는 문항 통과율 90% 이상과 `wrong_confirm_count` 0건을 모두 요구한다. `--chain` JSON에는 플래너 출력을 함께 기록해 원문 슬롯과 최종 조건을 비교할 수 있다.

| 지표 | 목표(제안) |
|---|---|
| **잘못 확정률** (DONE인데 판정 칸이 하나라도 틀림, 또는 DONE이 허용되지 않은 문항에서 DONE) | **0건** (필수) |
| 질문 통과율 | ≥ 95% |
| 불필요 되묻기율 (되물을 필요가 없는데 되물음) | ≤ 5% |
| HS 후보 recall@5 (`hs_candidate_recall_at_5`) | ≥ 80% (참고: 임베딩 상위 5개 일치 72%) |
| 국가 후보 recall@5 (`country_candidate_recall_at_5`) | C-28 비교용 |

**C-28 국가 유사도 임계값**: `--country-threshold 0.5/0.6/0.7`로 각각 돌린다. 잘못 확정 0건을 지키는 값 중 불필요 되묻기가 가장 적은 값을 고르고, 같으면 국가 후보 recall이 높은 값을 고른다. 자세한 내용은 [조건분석 계획서](agent_eval/CONDITIONS_TEST_PLAN.md)에 있다.

### 3.3 compose_text

**방식**
- 사례마다 고정 digest(TREND·RANKING·HS_CODES)와 최근 대화(recent)를 넣고, `compose_input` → LLM(`COMPOSE_TEXT_PROMPT`) → `assemble`(문장 상한 검사) → `verify`(숫자 대조)로 실행과 같은 경로를 탄다.
- DB 없이 돈다. 국가·품목 이름은 사례에 적은 값으로 넣는다.

**채점**
- 형식: `assemble`을 통과(하위 질문 순서·문장 상한).
- 근거 일치: `verify` 결과가 PASS. 숫자는 digest 값, CONVERSATION은 recent 요약, HS_LOOKUP은 결과 코드만 허용한다.
- 결론 내용: 하위 질문마다 결론 문장에 정규식 규칙을 적용한다. 문장은 소문자로 바꾸고 천 단위 쉼표를 지운 뒤 적용한다.
  - `required`: 대답해야 할 내용. 모두 맞아야 한다
  - `forbidden`: 반대 결론·부정·다른 코드. 하나라도 맞으면 실패
  - `ordered`: 순위 순서
  - `no_numbers`: 숫자를 쓰면 안 됨
- 문항마다 모범 결론(`reference`)을 두고, 모범 결론은 통과하고 반례는 실패하는지 단위 테스트로 확인한다.
- 대화·HS만 있는 답은 설명 문단이 비어 있어야 한다(`explanation_empty`).
- 자세한 내용은 [compose_text 계획서](agent_eval/COMPOSE_TEXT_TEST_PLAN.md)에 있다.

| 지표 | 목표(제안) |
|---|---|
| 형식 통과율 | ≥ 98% |
| 근거 일치율 (질문 단위) | ≥ 95% |
| 문장 불일치율 (숫자 있는 문장 중 MISMATCH) | ≤ 2% |
| 질문 통과율 | ≥ 90% |
| 지연 p95 | ≤ 20초 (`API_COMPOSE_TIMEOUT_SECONDS`) |
| 수동 검토 (holdout 10건, 2명이 자연스러움·정확성 1~5점) | 평균 ≥ 4 |

### 3.4 context (여러 턴)

**방식**
- 시나리오의 턴을 차례로 실행한다: planner(LLM) → check_conditions(DB) → 실제 `completed_context`로 대화 컨텍스트를 갱신한다.
- 요약은 시나리오에 적은 정답 요약을 넣어 compose와 떼어 본다.
- 컨텍스트에는 하위 질문이 하나이고 조건 확인이 DONE인 턴, 또는 대화형 턴만 남긴다. 실제 흐름처럼 되묻기에 걸리거나 실패한 턴은 남기지 않는다.
- 품목은 숫자 HS 코드로 써서 품목 검색 결과가 결과에 섞이지 않게 했다.

**채점**
- 턴마다 다음을 비교한다.
  - intent
  - 조건 확인 상태(분석 턴 DONE, 대화형 턴 NO_STEP)
  - 하위 질문 수(1개)
  - 확정 조건(국가·HS·기간·비교·수출입·기사 기간·순위 기준·개수)
- 저장한 턴은 컨텍스트가 바르게 갱신됐는지(`context_saved`)도 본다.
- 시나리오는 모든 턴이 맞아야 통과다.
- 자세한 내용은 [컨텍스트 계획서](agent_eval/CONTEXT_TEST_PLAN.md)에 있다.

| 지표 | 목표(제안) |
|---|---|
| 턴 통과율 | ≥ 90% |
| 컨텍스트 저장 정확도 (`context_saved`) | 100% |
| 시나리오 통과율 | ≥ 80% |
| 유형별(`inherit`·`reset`·`country_switch`·`product_switch`·`conversation_between`) | 각 유형 실패 사례를 원인별로 기록 |

## 4. 모델 결정 규칙

1. 영역마다 세 모델(기준선 + 후보 2개)로 **dev**를 `--repeat 3`으로 돌린다. 결과를 보고 개선할 점을 정한다.
2. 개선을 마친 뒤 세 모델로 **holdout**을 한 번 돌린다.
3. 선택 기준(순서대로):
   - conditions `--chain`에서 `quality_gate_pass`를 충족한 모델을 비교한다.
   - holdout 질문 통과율이 높은 모델을 고른다. 차이가 3%p 미만이면 지연 p95와 비용이 낮은 쪽을 고른다.
   - 지연 p95가 현재 시간 상한(플래너 10초, compose 20초)을 넘으면, 상한을 늘리는 비용(실행 예산 180초 안의 몫, D-85)까지 함께 보고 정한다.
4. 플래너와 compose 모델은 각각 선정한다.
5. 결정한 내용은 AGENT_DESIGN.md C-35에 기록한다.

## 5. 실행 명령

```bash
# 실행 전: 모델 ID 확인, .env에 OPENAI_API_KEY와 RDS 조회 계정 필요
.venv/Scripts/python.exe -m backend.evals.agent.run planner --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna --repeat 3

.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev --country-threshold 0.5
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev --chain --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna

.venv/Scripts/python.exe -m backend.evals.agent.run compose_text --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna --repeat 3
.venv/Scripts/python.exe -m backend.evals.agent.run context --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna
# holdout은 개선을 마친 뒤 같은 명령에 --split holdout (repeat 없이)

# 추론 강도를 바꿔 볼 때 (PowerShell): $env:API_LLM_REASONING_EFFORT='low'
# 한 질문만: --case p021
```

모델을 쉼표로 나열하면 차례로 돌리고 영역마다 비교 리포트 하나(`<영역>_<split>_compare_<시각>.md`, 같은 이름의 `.json`)를 남긴다.

호출 수(dev 기준, 모델 하나): planner 44×3, compose_text 10×3, context 24턴, conditions `--chain` 24(`unit_only` 5문항 제외). 재시도를 포함하면 최대 두 배다.

### 예상 비용 (2026-11-09 추정, 평가셋 확정 뒤 다시 계산)

- **호출 수**: 모델 하나에 약 290번이다. dev(repeat 3)와 holdout(1회)을 합쳐 플래너형 호출 약 250번(planner 163·context 48·conditions chain 35), compose 40번이다.
- **호출당 토큰(추정)**
  - 입력: 플래너는 프롬프트·스키마·질문을 합쳐 약 4,000토큰, compose_text는 약 3,000토큰이다. 모델 하나에 입력은 약 100만 토큰이 된다.
  - 출력: gpt-4.1-mini는 약 300토큰이다. GPT-5 계열은 추론 토큰을 포함해 약 1,500토큰으로 잡았다.

| 모델 | 단가 (입력/출력, 100만 토큰당) | 예상 비용 | 재시도 최대 포함 |
|---|---|---|---|
| gpt-4.1-mini (기준선) | $0.40 / $1.60 | 약 $0.6 | 약 $1.1 |
| gpt-5.6-luna | $1.00 / $6.00 (LLM Reference 기준, 미확정) | 약 $3.5 | 약 $7 |
| gpt-6-luna | $0.10 / $0.50 (가격 비교 사이트 기준, 미확정) | 약 $0.3 | 약 $0.6 |
| **합계** | | **약 $4.4** | **약 $8.7** |

- 추론 모델이 아닌 gpt-4.1-mini는 출력을 호출당 약 300토큰으로 잡았다.
- luna 단가는 출처마다 다르다. gpt-5.6-luna는 OpenRouter 기준 $0.20/$1.20이고, 이 단가라면 약 $0.7이다. 실행 전에 OpenAI 공식 가격표로 확인한다.
- 추론 토큰 수가 가장 큰 변수다. dev 몇 건을 먼저 돌려 실제 사용량으로 다시 계산한다.
- conditions 기본 실행과 C-28 임계값 비교는 LLM을 쓰지 않아 비용이 없다.
- 개선 뒤 dev를 다시 돌리면 한 바퀴에 모델당 약 70~80%가 더 든다.

## 6. 주의와 남은 일

- **HS 정답 확인 필요**: 첫 실행 전에 RDS `hs_code`(HS2022)에 다음 정답 코드가 있는지, 품목명이 문항 표현과 맞는지 확인한다. 실패하면 먼저 정답 오류인지 본다.
  - 확정 정답(`hs_codes`): 330499·851010·190230
  - 후보 정답(`hs_candidate_prefixes`): 8542·8703
- **기간 기준**: `period_months`는 RDS의 최신 공개 월을 끝으로 계산한다. 2024·2025년 절대 기간 사례는 그 기간의 데이터가 RDS에 있어야 DONE이 된다.
- **평가 밖**: REPORT 미리보기, 선행 결과 연결(INPUT_SELECTION), 되묻기 답 해석(ClarificationReading LLM), 유망시장 모델 품질.
- **결과 파일**: `output/agent_eval`은 git에서 제외되어 있지 않다. 커밋할지는 따로 정한다.
- 표본이 작아 1건이 planner dev 2.5%p·holdout 약 3.6%p, compose_text 10%p다. 모델 차이가 이보다 작으면 같은 수준으로 본다.
