# 조건분석(check_conditions) 테스트 계획서 (이슈 86)

## 1. 목적
플래너가 옮긴 원문 슬롯을 조건분석 노드가 정확한 국가·HS 코드·기간으로 확정하는지 측정한다. 확정할 수 없을 때 되묻거나 실패로 끝내는지도 본다. 국가 유사도 임계값(C-28)을 정하고, 플래너 오류가 조건 확정까지 번지는 정도(`--chain`)도 잰다.

## 2. 범위
| 포함 | 제외 |
|---|---|
| 한국(기준 국가) 제외, 이전 조건 이어받기(D-22) | REPORT 미리보기(`confirm_report`) |
| 품목: 숫자 코드 검증, 품목명 검색(`_search_depth`), HS_ITEM 되묻기 | 선행 결과 연결(`resolve_bindings`, INPUT_SELECTION) |
| 국가: 정확·포함·유사도 매칭, 두 글자 코드, 국가 아닌 약어 | 되묻기 답 해석(`answer_patch`의 LLM) |
| 관심 범위 되묻기(INTEREST_CONTEXT/SELECT) | 유망시장 모델 예측 품질 |
| 기간(최근 N개월·공개 범위·전년 비교), 기사 기간 | 실제 SQL 조회 결과 |
| intent별 확인(RANKING_GROUP·SHARE_BASIS·MARKET_TYPE·수출 전용), SQL 템플릿 전환 | |
| 권한 범위 검사(`validate_scope`) | |

## 3. 평가 모드와 모델
| 모드 | 입력 슬롯 | LLM | 용도 |
|---|---|---|---|
| 기본 | 문항의 정답 슬롯 | 없음 | 노드만 따로 평가. 모델과 무관하고 비용 없음 |
| `--chain --model <모델>` | 플래너 출력 | 플래너 1회 | 실제 흐름에 가까운 평가. 기본 결과와의 차이가 플래너 영향. `unit_only` 문항은 뺀다 |
| `--country-threshold 0.5/0.6/0.7` | 정답 슬롯 | 없음 | C-28 임계값 비교 |

`--chain` 모델은 planner 계획서와 같다(기준선 `gpt-4.1-mini`, 후보 `gpt-5.6-luna`·`gpt-6-luna`).

**`unit_only` 문항**: 일부러 틀린 슬롯을 넣은 문항이다. 예를 들어 플래너가 다시 적은 이전 조건, 지어낸 국가·품목, 국가로 잘못 넣은 TV가 그렇다. 플래너 출력으로는 재현되지 않아 `--chain`에서 뺀다(dev c005·c017·c018·c019·c020, holdout c109·c110·c113·c114·c115). `--chain`은 dev 22문항·holdout 11문항으로 돈다.

## 4. 환경·전제
- `.env`의 RDS 접속 설정을 쓴다(`API_DB_TARGET`). 조회만 하고 질문마다 rollback한다.
- HS 벡터 검색은 환경 설정(`API_HS_VECTOR_ENABLED`)을 그대로 따른다. 운영과 같은 조건이어야 하니 그대로 둔다.
- 접수 시각은 `2026-11-09T09:00+09:00`으로 고정한다. 기사 기간 정답(2026-08-10~2026-11-09)은 이 시각 기준이다.
- `period_months` 정답은 RDS의 최신 공개 월을 끝으로 계산한다.
- 2024·2025년 절대 기간 문항은 그 기간의 데이터가 RDS에 있어야 DONE이다.
- **실행 전 검수**: 다음 정답 코드가 RDS `hs_code`(HS2022)에 있는지, 품목명이 문항 표현과 맞는지 확인한다.
  - 확정 정답(`hs_codes`): 330499·851010·190230, 숫자 문항 3304·1902·190230
  - 후보 정답(`hs_candidate_prefixes`): 8542·8703

## 5. 테스트셋
**파일**: `backend/evals/agent/cases/conditions.jsonl` (43문항: dev 27 / holdout 16)

### 5.1 문항 형식
| 칸 | 내용 |
|---|---|
| `question` | 원문 질문. 슬롯 표현이 원문에 있는지 검사하는 데 쓴다 |
| `intent` | 하위 질문 intent |
| `slots` | 플래너 출력이라고 가정한 슬롯(`countries`는 mention·name 쌍). 일부러 이전 조건을 다시 적거나 지어낸 슬롯도 넣었다 |
| `context` | (선택) 이전 조건·최근 대화 |
| `first_question`, `interests` | (선택) 관심 범위 되묻기 문항 |
| `unit_only` | (선택) `true`면 `--chain`에서 뺀다 |
| `expect` | 아래 표 |

`expect` 칸은 실제 상태에 따라 나눠 채점한다. 한 문항에 DONE용 칸과 NEEDS_INPUT용 칸을 함께 적을 수 있다. 예를 들어 품목명이 정확히 하나만 일치하면 확정, 아니면 후보로 되묻는 문항이다.

| 언제 보나 | expect 칸 | 판정 |
|---|---|---|
| 항상 | `status` / `status_in` | DONE·NEEDS_INPUT·FAILED 중 허용 값 |
| NEEDS_INPUT일 때 | `pending_kind` | 되묻기 종류 |
| NEEDS_INPUT일 때 | `choices_include` | 선택지에 들어 있어야 할 값 |
| NEEDS_INPUT일 때 | `hs_candidate_prefixes` | HS_ITEM 후보 상위 5개에 정답 접두가 모두 있음 |
| 항상(지표만) | `country_candidate` | COUNTRY 되묻기 후보 상위 5개에 정답 국가가 있음. 통과 판정에는 넣지 않는다 |
| DONE일 때 | `country_codes`, `hs_codes` | 확정 코드 집합 일치 |
| DONE일 때 | `period`, `trade_flow`, `article_period`, `ranking_group`, `ranking_limit`, `share_basis`, `sql_template` | 값 일치 |
| DONE일 때 | `period_months` | 개월 수 + 끝이 최신 공개 월 |
| DONE일 때 | `compare_period` | `null`(비교 없음), 기간 값, 또는 `"previous_year"`(확정 기간의 12개월 전) |
| FAILED일 때 | `error_contains` | 실패 문구에 들어 있어야 할 말 |

### 5.2 유형 분포
| 유형 | 태그 | dev | holdout |
|---|---|---|---|
| 국가 매칭 | country_exact·country_code·country_fuzzy(c28)·mismatch·country_multi | 5 | 4 |
| 국가 아님·기준 국가 | abbreviation_not_country·korea_reporting | 2 | 2 |
| 품목 확정 | hs_search·numeric_hs·invalid | 5 | 2 |
| 이어받기 | inherit_echo·d22·new_country | 2 | 2 |
| 지어낸 슬롯 거부 | hallucinated_slot | 2 | 2 |
| 되묻기 | ranking_group·share_basis·interest | 5 | 2 |
| 미지원·필수 조건 누락 | unsupported·missing_slot·market·market_type | 4 | 2 |
| 기간 | recent_months·period_unavailable·compare·article_period | 4 | 2 |

숫자는 그 유형 태그가 하나라도 붙은 문항 수다(한 문항이 여러 유형에 걸칠 수 있다). holdout은 유형마다 2문항 이상이다.

### 5.3 문항 목록
| id | split | 질문 | intent | 기대 결과 | 확인점 |
|---|---|---|---|---|---|
| c001 | dev | 2025년 미국 기타 메이크업 및 기초화장품 수출액 | STATISTICS | DONE이면 US·330499·2025, 되물으면 HS_ITEM 후보에 330499 | DB 품목명과 같은 표현의 검색 |
| c002 | dev | 2025년 미구 수출액 | STATISTICS | COUNTRY 되묻기(후보 US는 지표) | 오타 국가명(C-28) |
| c003 | dev | 2025년 US 수출액 | STATISTICS | DONE, US | 두 글자 코드 |
| c004 | dev | 2025년 UK 수출액 | STATISTICS | COUNTRY 되묻기, 후보에 GB | 코드와 국가명 불일치 |
| c005 | dev | 2025년 TV 수출액 | STATISTICS | DONE, 국가 없음 | 국가 아닌 약어 무시 (unit_only) |
| c006 | dev | 2025년 한국의 모든 상대국 대상 HS 3304 수출액 (이전: US) | STATISTICS | DONE, 국가 []·3304 | 한국 제외 + 이전 국가 해제 |
| c007 | dev | 최근 3개월 수출액 | STATISTICS | DONE, 3개월·최신 월 | recent_months |
| c008 | dev | 1990년 미국 수출액 | STATISTICS | FAILED "최초 공개 월" | 공개 범위 밖 기간 |
| c009 | dev | 2025년 수출 순위 | RANKING | RANKING_GROUP 되묻기(COUNTRY·HS6) | 순위 기준 없음 |
| c010 | dev | 2025년 수출 상위 국가 | RANKING | DONE, ranking_group=COUNTRY | 순위 기준 있음 |
| c011 | dev | 2025년 HS 3304 수출 비중 | MARKET_SHARE | SHARE_BASIS 되묻기(두 기준) | 비중 분모 없음 |
| c012 | dev | 2025년 미국 수입시장 한국 점유율 | MARKET_SHARE | FAILED "아직 지원하지" | 해외 시장 미지원 |
| c013 | dev | 미국 수입 리포트 만들어줘 | REPORT | FAILED "수출만" | 리포트 수입 미지원 |
| c014 | dev | 미국 시장 유형 알려줘 | MARKET_TYPE | FAILED "국가와 품목을 함께" | 품목 누락 |
| c015 | dev | 유망시장 알려줘 | PROMISING_MARKET | FAILED "국가 또는 품목" | 둘 다 누락 |
| c016 | dev | 2025년 미국 수출액 전년 비교 | STATISTICS | DONE, 비교 2024년 | 전년 동기 자동 계산 |
| c017 | dev | 왜 증가했어? (이전: VN·330499, 슬롯에 다시 적음) | CAUSE | DONE, VN·330499·2025 | 다시 적은 이전 조건 이어받기(D-22) (unit_only) |
| c018 | dev | 미국은 왜 증가했어? (이전: VN, 슬롯에 둘 다) | CAUSE | DONE, US·330499 | 새 국가가 있으면 이전 국가 버림 (unit_only) |
| c019 | dev | 왜 줄었어? (슬롯에 일본) | CAUSE | FAILED "국가 표현을 질문에서" | 지어낸 국가 거부 (unit_only) |
| c020 | dev | 2025년 미국 수출 추이 (슬롯에 화장품) | STATISTICS | FAILED "질문에서 품목 표현을 확인하지 못했습니다" | 지어낸 품목 거부 (unit_only) |
| c021 | dev | 2025년 3304 미국 수출 | STATISTICS | DONE, US·3304 | 숫자 HS4 |
| c022 | dev | 2025년 9999 수출 추이 | STATISTICS | FAILED "HS 사전에 없습니다" | 없는 코드 |
| c023 | dev | 수출 추이 보여줘 (첫 질문, 관심 US·330499) | STATISTICS | INTEREST_CONTEXT 되묻기(APPLY·GLOBAL) | 관심 범위 적용 확인 |
| c024 | dev | 최근 베트남 HS 3304 관련 기사 | LOCAL_ISSUES | DONE, VN·3304, 기사 기간 2026-08-10~11-09 | 기사 기간 계산 |
| c025 | dev | 2025년 미국, 중국, 일본 수출 추이 | STATISTICS | DONE, US·CN·JP | 여러 국가 |
| c026 | dev | 2025년 베트남 전자집적회로 수출 | STATISTICS | HS_ITEM 되묻기, 후보에 8542 | 품목명 검색 후보 |
| c027 | dev | 2025년 전동 면도기 수출 | STATISTICS | DONE이면 851010, 되물으면 후보에 851010 | 품목명 검색 |
| c101 | holdout | 2025년 일본 즉석 라면 수출 | STATISTICS | DONE이면 JP·190230, 되물으면 후보에 190230 | 품목명 검색 |
| c102 | holdout | 2025년 DE 승용차 수입 | STATISTICS | HS_ITEM 되묻기, 후보에 8703 | 코드 국가 + 품목 후보 |
| c103 | holdout | 2025년 인도네시어 수출액 | STATISTICS | COUNTRY 되묻기(후보 ID는 지표) | 오타 국가명(C-28) |
| c104 | holdout | 2025년 베트남과 태국 수출 추이 | STATISTICS | DONE, VN·TH | 여러 국가 |
| c105 | holdout | 최근 6개월 품목별 수출 순위 | RANKING | DONE, 6개월·HS6 | recent_months + 순위 기준 |
| c106 | holdout | 2025년 미국 수출 품목별 비중 | MARKET_SHARE | DONE, US·HS6·KOREA_TRADE_PARTNER·LLM_SQL | 품목별 비중 → LLM SQL 전환 |
| c107 | holdout | 2025년 우리나라의 모든 상대국 대상 수출 추이 (이전: US) | STATISTICS | DONE, 국가 [] | 우리나라 = 기준 국가 + 이전 국가 해제 |
| c108 | holdout | 2025년 JP 수출액 | STATISTICS | DONE, JP | 두 글자 코드 |
| c109 | holdout | 중국은? (이전: JP·1902, 슬롯에 둘 다) | STATISTICS | DONE, CN·1902·2024 | 새 국가 + 품목 이어받기 (unit_only) |
| c110 | holdout | 2025년 중국 수출 추이 (슬롯에 라면) | STATISTICS | FAILED "질문에서 품목 표현을 확인하지 못했습니다" | 지어낸 품목 거부 (unit_only) |
| c111 | holdout | 최근 12개월 중국 수출 전년 대비 | STATISTICS | DONE, CN·12개월·비교 12개월 전 | 최근 N개월 + 비교 |
| c112 | holdout | HS 190230 시장 유형 | MARKET_TYPE | FAILED "국가와 품목을 함께" | HS6 숫자 + 국가 누락 |
| c113 | holdout | 2025년 IT 기기 수출액 (슬롯에 IT=정보기술) | STATISTICS | DONE, 국가 없음 | 국가 코드(이탈리아)와 같은 약어지만 국가명이 아님 (unit_only) |
| c114 | holdout | 그럼 2024년은? (이전: US·3304·2025, 슬롯에 다시 적음) | STATISTICS | DONE, US·3304·2024 | 다시 적은 이전 조건 이어받기 + 기간만 변경 (unit_only) |
| c115 | holdout | 최근 6개월 수출 추이 (슬롯에 베트남) | STATISTICS | FAILED "국가 표현을 질문에서" | 지어낸 국가 거부 (unit_only) |
| c116 | holdout | 중국 HS 3304 수입 유망시장 알려줘 | PROMISING_MARKET | FAILED "수출만" | 유망시장 수입 미지원 |

## 6. 실행 방법
```bash
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev --country-threshold 0.5
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev --country-threshold 0.7
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split dev --chain --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna
# 개선 완료 뒤
.venv/Scripts/python.exe -m backend.evals.agent.run conditions --split holdout
```
결과는 `output/agent_eval/conditions_<split>_<nollm|compare>[_chain]_<시각>.md`(비교 표)와 같은 이름의 `.json`에 저장된다. 정답 슬롯 모드는 모델과 무관해 `nollm` 한 열이고, `--chain`은 세 모델을 한 리포트로 비교한다.

## 7. 평가 지표
| 지표 | 정의 | 계산 | 목표(제안) |
|---|---|---|---|
| **잘못 확정률** (필수) | DONE으로 끝났는데 판정 칸이 하나라도 틀린 실행. DONE이 허용되지 않은 문항에서 DONE이 나온 경우도 포함. chain에서는 intent·하위 질문 수 오류도 포함 | `wrong_confirm_rate` | **0건** |
| **문항 통과율** | 실제 상태에 해당하는 칸이 모두 맞은 실행 비율 | `pass_rate` | ≥ 95% |
| 불필요 되묻기율 | 기대 상태에 NEEDS_INPUT이 없는데 되물은 실행. 종류만 틀린 되묻기는 `pending_kind` 정확도로 본다 | `unnecessary_ask_rate` | ≤ 5% |
| HS 후보 적중률 (recall@5) | HS_ITEM으로 되물은 실행 중 후보 상위 5개에 정답 접두가 모두 있는 비율 | `hs_candidate_recall_at_5` | ≥ 80% (참고: 임베딩 상위 5개 일치 72%) |
| 국가 후보 적중률 (recall@5) | `country_candidate` 문항에서 COUNTRY 되묻기 후보 상위 5개에 정답 국가가 있는 비율 | `country_candidate_recall_at_5` | C-28 비교용 |
| 칸별 정확도 | status·pending_kind·choices_include·hs_candidates·country_codes·hs_codes·period·period_months·compare_period·trade_flow·article_period·ranking_group·ranking_limit·share_basis·sql_template·error_contains | `field_accuracy` | 각 ≥ 95% |
| 지연 p50·p95 | 조건분석 1회 소요 시간(ms) | 실행별 측정 | p95 ≤ 15,000ms (check 예산 `API_CHAT_CHECK_SECONDS`) |
| `--chain` 통과율 차이 | 기본 통과율 - chain 통과율(같은 문항 기준) | 두 결과 비교 | 목표 없음. 플래너 영향 측정용 |
| 유형별 통과율 | 태그별 | `summary.by_tag` | 약점 찾기용 |

## 8. 판정 규칙
1. **잘못 확정이 1건이라도 있으면 불합격**이다. 원인(매칭 규칙·이어받기·정답 오류)을 고친 뒤 다시 돌린다.
2. **C-28 임계값**: 0.5·0.6·0.7 중 잘못 확정 0건을 지키는 값 가운데 다음 순서로 고른다.
   - 불필요 되묻기가 가장 적은 값
   - 같으면 국가 후보 적중률이 높은 값
   - 그래도 같으면 현재값 0.6을 유지한다
3. **`--chain` 모델 비교**: 잘못 확정이 나오는 모델은 플래너 후보에서 뺀다.
4. 실패 문항은 먼저 정답 오류(특히 HS 정답·RDS 기간)인지 확인하고, 정답 오류면 테스트셋을 고친다.

## 9. 위험·주의
- 품목명 검색 결과는 RDS 품목명과 벡터 색인 상태에 따라 바뀐다. 결과 파일에 색인 상태를 함께 적는다.
- c026·c102는 되묻기만 정답으로 둔다. 품목명이 DB와 정확히 하나만 일치해 맞는 코드로 확정되면 잘못 확정으로 집계되니, 이 경우는 정답을 고친다.
- 국가 되묻기는 질문 하나에 한 번만 한다(`ask`). 국가가 여러 개인 오타 문항은 넣지 않았다.

## 10. 결과 기록 양식
비율 칸은 %로 적는다. 결과 JSON의 비율은 0~1이다(1 = 100%). ↓ 표시는 낮을수록 좋은 지표이고, 나머지는 높을수록 좋다. 시간은 결과 JSON의 `latency_ms_*`(실행 1회)와 `elapsed_seconds`(평가 전체)를 옮긴다.

| 모드 | 임계값 | 모델 | split | 통과율 | 잘못 확정 ↓ | 불필요 되묻기 ↓ | HS recall@5 | 국가 recall@5 | country | period | p50(ms) ↓ | p95(ms) ↓ | 최대(ms) ↓ | 총 소요(초) | 비고 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | | | | | |
