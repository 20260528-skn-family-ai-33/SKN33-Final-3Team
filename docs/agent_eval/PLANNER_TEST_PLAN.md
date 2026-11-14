# planner 테스트 계획서 (이슈 86)

## 1. 목적
플래너가 사용자 질문을 하위 질문·intent·원문 슬롯으로 정확히 나누는지 측정한다. 평가 당시에는 기준선(`gpt-4.1-mini`)과 후보 모델을 비교해 플래너 모델을 선정하는 것이 목적이었다(C-35). 현재 기본값은 `API_PLANNER_MODEL=gpt-5.6-luna`다. 프롬프트·안전망을 개선한 전후도 비교한다.

## 2. 범위
| 포함 | 제외 |
|---|---|
| `explicit_country_ranking`(정규식 처리) | 조건 확정(코드 매핑) → [CONDITIONS_TEST_PLAN.md](CONDITIONS_TEST_PLAN.md) |
| `PLANNER_PROMPT` + LLM 구조화 출력(최대 2회 시도) | 되묻기 답 해석(`ClarificationReading`) |
| `postprocess`(무역 용어 살리기·품목 표현 합치기) | 리포트 조건 수정 입력(`direct_input`) |
| `PlannerOutput` 검증, `build_plan`(하위 질문 수 상한) | 단계 실행 결과 |

## 3. 평가 모델
| 모델 | 구분 | 비고 |
|---|---|---|
| `gpt-4.1-mini` | 기준선 | 평가 당시 기본값 |
| `gpt-5.6-luna` | 후보 | 추론 모델, temperature 없음 |
| `gpt-6-luna` | 후보 | 추론 모델, temperature 없음 |

추론 강도는 `API_LLM_REASONING_EFFORT`. 비워 두면 모델 기본값이다. 필요하면 `low`로 한 번 더 돌린다.

## 4. 환경·전제
- `.env`에 `OPENAI_API_KEY`가 있어야 한다. DB는 쓰지 않는다.
- 접수 시각은 `2026-11-09T09:00+09:00`으로 고정한다.
- 모델 ID는 실행 전에 `GET /v1/models`로 확인한다.
- 시간 상한은 `API_INTENT_TIMEOUT_SECONDS`(10초)이고, `--timeout`으로 바꿀 수 있다.

## 5. 테스트셋
**파일**: `backend/evals/agent/cases/planner.jsonl` (75문항: dev 44 / holdout 31)

### 5.1 문항 형식
| 칸 | 내용 |
|---|---|
| `question` | 사용자 질문 |
| `context` | (선택) 이전 조건 `conditions`, 최근 대화 `recent`(질문·intent·요약) |
| `expect.tasks[].intent` | 기대 intent. 둘 다 맞으면 `{"$in":[...]}` |
| `expect.tasks[].slots` | 기대 슬롯. 적지 않은 기본 칸은 null이 정답이다(7장 채점 규칙) |
| `expect.tasks[].custom_query` | custom_query가 있어야 하면 `true`. 생략하면 없어야 한다 |
| `expect.tasks[].custom_query_patterns` | (선택) custom_query 문장에 모두 들어 있어야 할 정규식(대소문자 무시) |
| `expect.tasks[].source_tasks` | 선행 질문 번호. 생략하면 `[]` |
| `expect.tasks[].target_selections` | 선행 결과에서 대상을 고르는 규칙(`source_task`·`dimension`·`limit`·`order_by`). 생략하면 `[]` |
| `expect.error` | 거절이 정답인 문항(`QUESTION_TOO_COMPLEX`) |

**정답 표기 규칙**
- `null`은 생략(이어받기), `[]`는 명시적인 전체 범위다.
- 해석이 둘 다 맞으면 `{"$in":[...]}`로 적는다.
- 국가는 정식 국가명, 품목은 원문 표현으로 적는다.

### 5.2 intent 분포(하위 질문 기준)
| intent | 수 | intent | 수 |
|---|---|---|---|
| STATISTICS | 24 | HS_LOOKUP | 3 |
| RANKING | 9 | LOCAL_ISSUES | 2 |
| CONVERSATION | 6 | REPORT | 2 |
| MARKET_SHARE | 4 | MARKET_TYPE | 2 |
| CAUSE | 4 | OUT_OF_SCOPE | 2 |
| PROMISING_MARKET | 4 | | |

### 5.3 유형 분포 (dev/holdout)
| 유형 | 태그 | dev | holdout |
|---|---|---|---|
| 기간 해석 | period_year·period_quarter·period_range·period_month·recent_months | 4 | 4 |
| 후속 질문·이어받기 | followup·inherit·country_switch·reset·recall·text_format | 5 | 3 |
| 복합 질문 | multi_task·binding·shared_condition | 2 | 2 |
| 템플릿 밖 계산 | custom_query(quarter·mom·volume·half) | 3 | 2 |
| 순위 | ranking·ranking_limit·regex·hs6·ambiguous | 5 | 4 |
| 비중 | share·product_share·foreign_market | 3 | 2 |
| 대화·용어·범위 밖 | conversation·term·term_rescue·out_of_scope | 5 | 3 |
| 품목 표현 | product_merge·product_multi·hs_lookup·numeric | 4 | 2 |
| 국가 오인 방지 | company_not_country·abbreviation_not_country·korea_partner | 5 | 2 |
| 거절 | too_complex | 1 | 2 |

숫자는 그 유형 태그가 하나라도 붙은 문항 수다(한 문항이 여러 유형에 걸칠 수 있다). holdout은 유형마다 2문항 이상이다.

### 5.4 문항 목록
| id | split | 질문 | 정답 intent | 확인점 |
|---|---|---|---|---|
| p001 | dev | 2025년 베트남 화장품 수출액 추이 보여줘 | STATISTICS | 연도 → 1~12월, 국가·품목·수출 |
| p002 | dev | 최근 6개월 미국 반도체 수출 추이 | STATISTICS | recent_months=6, period 없음 |
| p003 | dev | 2024년 중국 라면 수출액을 전년과 비교해줘 | STATISTICS | comparison_requested, compare_period는 null 또는 2023년 |
| p004 | dev | 2025년 한국의 수출액 상위 국가 5개 알려줘 | RANKING | COUNTRY·5, 한국은 상대국 아님(countries=[]) |
| p005 | dev | 한국 수출액 상위 10개국 보여줘 | RANKING | 정규식 처리(LLM 없음) |
| p006 | dev | 2025년 미국 화장품 수출에서 품목별 비중 보여줘 | MARKET_SHARE | HS6·KOREA_TRADE_PARTNER, custom_query에 "품목별"·"비중" |
| p007 | dev | 2025년 화장품 수출의 국가별 비중 | MARKET_SHARE | 분모 KOREA_TRADE_PARTNER |
| p008 | dev | 미국 수입시장에서 한국 화장품 점유율 알려줘 | MARKET_SHARE | FOREIGN_IMPORT_MARKET, 수입으로 오인 금지 |
| p009 | dev | 베트남 화장품 수출이 2025년에 왜 늘었어? | CAUSE | 원인 질문 |
| p010 | dev | 최근 인도 스마트폰 관련 기사 보여줘 | LOCAL_ISSUES | "최근"에 개월 수 넣지 않음 |
| p011 | dev | 미국 화장품 수출 리포트 만들어줘 | REPORT | |
| p012 | dev | 베트남에서 유망한 품목 8개 추천해줘 | PROMISING_MARKET | ranking_limit=8 |
| p013 | dev | 화장품 유망 국가 알려줘 | PROMISING_MARKET | 품목만 |
| p014 | dev | 미국 기초화장품 시장은 증가 예상 시장이야? | MARKET_TYPE | |
| p015 | dev | 면도기 HS코드 알려줘 | HS_LOOKUP | 품목 원문 |
| p016 | dev | 8510은 무슨 품목이야? | HS_LOOKUP | 코드 → 품목 |
| p017 | dev | HS코드가 뭐야? | CONVERSATION | 용어 질문 |
| p018 | dev | FTA가 뭐야? | CONVERSATION | 용어 질문 |
| p019 | dev | 오늘 서울 날씨 어때? | OUT_OF_SCOPE | |
| p020 | dev | hs코드 뜻 알려줘 | CONVERSATION | 소문자 용어, 범위 밖이면 안전망이 살림 |
| p021 | dev | 왜 증가했어? (이전: 베트남 기초화장품) | CAUSE | 슬롯 null로 이어받기, source_tasks=[] |
| p022 | dev | 미국은? (이전: 베트남 기초화장품) | STATISTICS | 국가만 바꾸고 나머지 null |
| p023 | dev | 이전 질문 뭐였더라? | CONVERSATION | 이전 대화 묻기 |
| p024 | dev | 아까 본 결과를 같은 조건으로 다시 조회해서 말로 설명해줘 | STATISTICS | answer_format=TEXT, 슬롯 null로 이어받기 |
| p025 | dev | 2025년 한국 수출 추이를 모든 상대국과 모든 품목을 합쳐서 보여줘 (이전: 베트남 기초화장품) | STATISTICS | countries=[]·products=[]로 초기화 |
| p026 | dev | 2025년 중국 화장품 수출 추이와 변화 원인 알려줘 | STATISTICS + CAUSE | 공통 조건을 두 하위 질문에 모두 명시 |
| p027 | dev | 2025년 화장품 수출 상위 국가 3곳을 뽑고, 그 3곳 각각에서 전체 품목 중 유망 품목을 알려줘 | RANKING + PROMISING_MARKET | source_tasks=[1], target_selections(COUNTRY·3·amount_usd), 2번 products=[]·countries=null |
| p028 | dev | 2025년 분기별 화장품 수출액 합계 보여줘 | STATISTICS | custom_query에 "분기별"·"합계" |
| p029 | dev | 2025년 월별 반도체 수출액과 전월 대비 증감률 | STATISTICS | custom_query에 "월별"·"수출액"·"전월"·"증감률" |
| p030 | dev | 2025년 한국의 대중국 화장품 수출 중량(kg) 알려줘 | STATISTICS | custom_query에 "중량/kg", 대중국 → 중국 |
| p031 | dev | 면도 및 탈취 관련 상품 2025년 수출 추이 | STATISTICS | 한 품목으로 유지(D-107) |
| p032 | dev | 화장품 및 세제 2025년 미국 수출 추이 | STATISTICS | 두 품목으로 분리 |
| p033 | dev | SK하이닉스는 참고로만 언급할게. 2025년 한국 전체 기업의 대미 반도체 수출 추이 보여줘 | STATISTICS | 기업 국적 추론 금지, 국가 표현은 원문 "대미" |
| p034 | dev | TV 수출 추이 2025년 | STATISTICS | TV는 국가 아님(countries=null) |
| p035 | dev | 2025년 3월부터 8월까지 일본 자동차 수입 추이 | STATISTICS | 월 범위·수입 |
| p036 | dev | 2025년 한국의 대미국 수출액 | STATISTICS | 대미국 → 미국 |
| p037 | dev | 2025년 품목별 수출 순위 상위 5개 | RANKING | HS6·5 |
| p038 | dev | 2025년 수출 순위 보여줘 | RANKING | 모호 → ranking_group=null |
| p039 | dev | 2025년 베트남 화장품 수출 추이, 변화 원인, 관련 기사를 보여주고 리포트까지 만들어줘 | (거절) | 하위 질문 4개 → QUESTION_TOO_COMPLEX |
| p040 | dev | 2025년 2분기 베트남 화장품 수출 추이 | STATISTICS | 분기 → 4~6월 |
| p041 | dev | 태국 의류 수출 추이를 조회해서 그 결과를 리포트로 남겨줘 | STATISTICS → REPORT | REPORT의 `source_tasks=[1]` |
| p042 | dev | 방금 그 표로 리포트 만들어줘 | REPORT | `previous_turn_result=true` |
| p043 | dev | 방금 그 조건으로 리포트 만들어줘 | REPORT | 조건 이어받기 |
| p044 | dev | 이전 미국 수입 조건에서 HS6 단위 코드 330499는? | STATISTICS | 품목 코드만 교체 |
| h001 | holdout | 2024년 일본 라면 수출 흐름 알려줘 | STATISTICS | 연도 |
| h002 | holdout | 최근 3개월 독일 자동차 부품 수입 추이 | STATISTICS | recent_months·수입 |
| h003 | holdout | 2025년 미국 반도체 수출액을 2024년 같은 기간과 비교해줘 | STATISTICS | 비교, compare_period는 null 또는 2024년 |
| h004 | holdout | 2025년 한국 수입액 상위 국가 7개 보여줘 | RANKING | 수입 순위 |
| h005 | holdout | 우리나라 수출액이 가장 큰 국가 5곳 알려줘 | RANKING | 정규식 처리 |
| h006 | holdout | 2025년 라면 수출의 상대국별 비중 알려줘 | MARKET_SHARE | KOREA_TRADE_PARTNER |
| h007 | holdout | 인도 스마트폰 수출이 최근 6개월 동안 줄어든 이유가 뭐야? | CAUSE | recent_months |
| h008 | holdout | 일본 화장품 관련 최신 뉴스 찾아줘 | LOCAL_ISSUES | |
| h009 | holdout | 중국 라면 수출 보고서 작성해줘 | REPORT | |
| h010 | holdout | 인도네시아 유망 품목 5개 보여줘 | PROMISING_MARKET | ranking_limit |
| h011 | holdout | 330499 설명해줘 | HS_LOOKUP | 6자리 코드 |
| h012 | holdout | 원산지 증명서가 뭔가요? | CONVERSATION | 용어 |
| h013 | holdout | 주말에 볼 영화 추천해줘 | OUT_OF_SCOPE | |
| h014 | holdout | 그럼 중국은 어때? (이전: 일본 라면) | STATISTICS | 국가 전환 |
| h015 | holdout | 방금 질문 다시 알려줘 | CONVERSATION | 이전 대화 묻기 |
| h016 | holdout | 2025년 베트남 수출 상위 품목 3개와, 베트남에서 그 3개 품목의 최신 예측 시장 유형을 알려줘 | RANKING + MARKET_TYPE | source_tasks=[1], target_selections(HS6·3·amount_usd), 2번 period=null |
| h017 | holdout | 2025년 반기별 일본 수출액 합계 | STATISTICS | custom_query에 "반기별"·"합계" |
| h018 | holdout | 의료 및 미용 관련 제품 2025년 수출 추이 | STATISTICS | 한 품목 유지 |
| h019 | holdout | 2025년 10월 미국 화장품 수출액 | STATISTICS | 한 달 기간 |
| h020 | holdout | 2024년 수출 순위 알려줘 | RANKING | 모호 → null |
| h021 | holdout | 2024년 일본 라면 수출 추이와 그 원인 알려줘 | STATISTICS + CAUSE | 공통 조건을 두 하위 질문에 모두 명시 |
| h022 | holdout | 2025년 월별 미국 화장품 수출액과 전월 대비 증감액 | STATISTICS | custom_query에 "월별"·"전월"·"증감액" |
| h023 | holdout | 미국 수입시장에서 한국 라면이 차지하는 점유율 알려줘 | MARKET_SHARE | FOREIGN_IMPORT_MARKET, 수입으로 오인 금지 |
| h024 | holdout | LG전자는 참고로만 언급할게. 2025년 한국 전체 기업의 대일본 TV 수출 추이 보여줘 | STATISTICS | 기업 국적 추론 금지, TV는 품목, 대일본 → 일본 |
| h025 | holdout | 2025년 우리나라의 대중국 수입액 알려줘 | STATISTICS | 대중국 → 중국, 우리나라는 상대국 아님, 수입 |
| h026 | holdout | 2025년 일본 라면 수출 추이, 수입 추이, 변화 원인, 관련 기사를 모두 알려줘 | (거절) | 하위 질문 4개 → QUESTION_TOO_COMPLEX |
| h027 | holdout | 2025년 중국 반도체 수출 상위 품목 5개, 중국 유망 품목, 중국 반도체 관련 기사, 중국 반도체 수출이 늘어난 원인을 알려줘 | (거절) | 서로 다른 분석 4개 → QUESTION_TOO_COMPLEX |
| h028 | holdout | 같은 기간을 모든 상대국과 모든 품목 합계로 다시 보여줘 (이전: 일본 라면 2024) | STATISTICS | countries=[]·products=[]로 초기화, 기간은 null로 이어받기 |
| h029 | holdout | 와인의 유망 수출국을 예측한 다음 그 예측 결과로 보고서 작성해 주세요 | PROMISING_MARKET → REPORT | REPORT의 `source_tasks=[1]` |
| h030 | holdout | 아까 조회한 결과표를 바탕으로 보고서 작성해 주세요 | REPORT | `previous_turn_result=true` |
| h031 | holdout | 같은 조건으로 보고서 만들어 주세요 | REPORT | 조건 이어받기 |

## 6. 실행 방법
```bash
.venv/Scripts/python.exe -m backend.evals.agent.run planner --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna --repeat 3
# 개선 완료 뒤 1회
.venv/Scripts/python.exe -m backend.evals.agent.run planner --split holdout --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna
```
세 모델을 한 명령으로 차례로 돌리고, 결과를 한 쌍의 파일로 남긴다: `output/agent_eval/planner_<split>_compare_<시각>.md`(모델별 지표·문항별 통과 비교 표)와 같은 이름의 `.json`(모델별 요약과 문항별 전체 결과).

## 7. 평가 지표
| 지표 | 정의 | 계산 | 목표(제안) |
|---|---|---|---|
| **문항 통과율** (주 지표) | 하위 질문 수와 모든 채점 칸이 맞은 실행 비율 | 통과 실행 / 전체 실행 (`pass_rate`) | dev ≥ 85%, holdout ≥ 80% |
| LLM 경로 통과율 | 정규식 처리가 아닌(LLM을 부른) 실행의 통과율 | `model_pass_rate` | 모델 비교에 쓴다 |
| 정규식 경로 통과율 | 정규식 처리(p005·h005) 실행의 통과율 | `regex_pass_rate` | 100% |
| intent 정확도 | 하위 질문별 intent 일치 | `field_accuracy.intent` | ≥ 95% |
| 칸별 정확도 | 슬롯 칸, custom_query 유무·내용(`custom_query_content`), source_tasks, target_selections, 국가 원문 표현(`country_mentions`), 계획 생성(`plan`) 각각 | `field_accuracy.<칸>` | 핵심 3칸(products·countries·period) ≥ 95%, 나머지 ≥ 90% |
| 하위 질문 수 정확도 | 분해한 수가 정답과 같은 비율 | 일치 / 전체 | ≥ 95% |
| 파싱 실패율 | 2회 모두 스키마 검증 실패·응답 미완료 | 실패 / 전체 | ≤ 2% |
| 반복 일관성 | 3회 실행 판정이 모두 같은 문항 비율 | 일관 문항 / 전체 문항 | ≥ 90% |
| 지연 p50·p95 | 정규식 처리~build_plan 소요 시간(ms) | 실행별 측정 | p95 ≤ 10,000ms |
| 유형별 통과율 | 태그별 문항 통과율 | `summary.by_tag` | 목표 없음. 약점 찾기용 |

**채점 규칙**
- **기본 칸**: products·countries·period·recent_months·trade_flow·answer_format·comparison_requested·compare_period·article_period. 정답에 없으면 null이 정답이다. 질문에 없는 조건을 지어내면 틀린다.
- **intent 전용 칸**: 그 intent에서만 기본 칸으로 본다. 다른 intent에서는 실행 결과가 같으므로 정답에 적힌 경우에만 본다.
  - `ranking_group`: RANKING·MARKET_SHARE
  - `share_basis`: MARKET_SHARE
  - `ranking_limit`: RANKING·PROMISING_MARKET·MARKET_TYPE
- CONVERSATION·OUT_OF_SCOPE·HS_LOOKUP은 정답에 적힌 슬롯만 본다.
- 품목은 띄어쓰기·대소문자를 정리해 순서 없이 비교하고, 국가는 정식 국가명 집합으로 비교한다.
- **custom_query**: 있는지 없는지를 항상 본다. `custom_query_patterns`가 있으면 문장에 패턴이 모두 있어야 한다.
- **source_tasks·target_selections**: 항상 본다(생략하면 `[]`). RANKING 결과를 가리키는 `country_rank`·`product_rank` 정렬은 실행과 같이 `amount_usd`로 바꿔 비교한다.
- **country_mentions**: 플래너가 적은 국가 mention이 질문 원문에 있어야 한다. 없으면 조건분석이 "국가 표현을 질문에서 확인하지 못했습니다"로 실패하기 때문이다.
- **plan**: `build_plan`이 계획을 만들고 NO_DATA로 끝난 하위 질문이 없어야 한다.

## 8. 판정 규칙
1. 모델별 dev 결과 → 실패 문항 원인 분류(프롬프트·안전망·정답 오류) → 개선
2. 개선 뒤 holdout 1회 → 문항 통과율이 가장 높은 모델을 고른다. 차이가 3%p 미만이면 지연 p95·비용이 낮은 쪽.
3. p95가 10초를 넘는 모델은 시간 상한 조정 비용(실행 예산 180초, D-85)과 함께 판단한다.
4. 표본 1건이 dev 2.5%p, holdout 약 3.6%p다. 그보다 작은 차이는 같은 수준으로 본다.

## 9. 위험·주의
- 추론 모델은 추론 토큰이 `max_output_tokens`(2000)에 포함된다. 잘리면 파싱 실패로 집계되니 실패율이 높으면 먼저 확인한다.
- holdout 실패 사례를 보고 프롬프트를 고치면 holdout을 새로 만든다.
- p021~p025·h014·h015는 맥락 처리도 함께 본다. 여러 턴 평가는 [CONTEXT_TEST_PLAN.md](CONTEXT_TEST_PLAN.md)다.

## 10. 결과 기록 양식
비율 칸은 %로 적는다. 결과 JSON의 비율은 0~1이다(1 = 100%). ↓ 표시는 낮을수록 좋은 지표이고, 나머지는 높을수록 좋다. 시간은 결과 JSON의 `latency_ms_*`(실행 1회)와 `elapsed_seconds`(평가 전체)를 옮긴다.

| 모델 | split | 통과율 | intent | products | countries | period | 하위 질문 수 | 파싱 실패 ↓ | 일관성 | p50(ms) ↓ | p95(ms) ↓ | 최대(ms) ↓ | 총 소요(초) | 비고 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | | | |
