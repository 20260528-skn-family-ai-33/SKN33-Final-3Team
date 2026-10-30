# 채팅 API 변경 정의 (에이전트 반영분)

API 정의서 v0.18(2026-10-23)의 채팅 API(API-36~38)에 반영할 변경과 새 API 1개를 적었습니다. 근거가 되는 설계 결정은 [채팅 에이전트 설계](AGENT_DESIGN.md) §1에 있습니다. 공통 규칙(Base `/api/v1`, snake_case, `bs_session` 쿠키 + `X-CSRF-Token`, 오류 형식)은 기존과 같습니다. 이 API를 쓰는 화면 구현 명세는 [설계 §12](AGENT_DESIGN.md), 서버 쪽 구현 명세는 [설계 §11](AGENT_DESIGN.md)에 있습니다.

## 바뀌는 것

| API | 구분 | 바뀌는 점 | 근거 |
|---|---|---|---|
| API-36 질문 보내기 | 동작 변경 | 되묻기 대기 중에 보낸 질문은 새 작업이 아니라 그 작업의 되묻기 답(직접 입력)으로 처리 | D-12 |
| API-36 | 오류 | Redis 장애는 기존 503 `REDIS_UNAVAILABLE`(인증과 같은 코드) | D-06 |
| API-37 작업 조회 | 응답 추가 | `progress`, `round_started_at`, `clarification.allow_text`, `clarification.hint`, 새 되묻기 종류 | D-12, D-25, D-56 |
| API-37 | 상태 추가 | `status`에 `EXPIRED`, `CANCELLED`, `WAITING_REPORT` | D-04, D-12 |
| API-37 | answer 추가 | `answer.tasks[]`(복합 질문), `charts[]` 형식 확정 | D-59, D-60 |
| API-37 | 응답 추가 | 되묻기 종류 `INTEREST_SELECT`와 `clarification.interests`(고를 관심사 후보) | D-64 |
| API-38 되묻기 답 | 요청 추가 | `text`(직접 입력), `interest_selection`(관심사 골라 받기) | D-12, D-64 |
| API-38 | 오류 추가 | 409 `CLARIFICATION_EXPIRED` (30분 경과) | D-04 |
| API-131 (신규) 질문 중단 | 신설 | `POST /chat/jobs/{job_id}/cancel` | D-12 |

번호 131은 제안입니다. 106~116은 삭제·예약이라 피했습니다. 정의서 담당자가 확정합니다.

---

## API-36 `POST /chat/sessions/{session_id}/messages`

요청 본문은 그대로입니다(`question` 1~4,000자, `client_message_id`). 처리만 다음처럼 바뀝니다.

| 대화방 상태 | 응답 |
|---|---|
| 진행 중인 작업 없음 | 202, 새 작업 (기존과 같음) |
| 작업 실행 중(QUEUED·RUNNING·WAITING_REPORT) | 409 `JOB_IN_PROGRESS` (기존). 프론트는 생성 중에 입력창·전송 버튼을 비활성화 |
| 되묻기 대기 중(NEEDS_INPUT), 30분 안 | 202, **기존 작업**을 반환. 보낸 글은 API-38 `text`와 같이 처리하고, 작업 상태는 QUEUED. 같은 `client_message_id`로 다시 보내면 같은 작업을 돌려줌. 글은 대화 기록(USER 메시지)에 남음 |
| Redis 연결 불가 | 503 `REDIS_UNAVAILABLE`, `retryable: true`(기존 인증 저장소 오류와 같음). 프론트 안내 "잠시 후 다시 시도해 주세요." |
| 되묻기 대기 중이지만 30분이 지남 | 그 작업을 `EXPIRED`로 바꾸고 이 글을 **새 질문**으로 접수한다(202, 새 작업) |

## API-37 `GET /chat/jobs/{job_id}`

기존 필드(`job_id`, `session_id`, `status`, `step`, `clarification`, `answer`, `error`, `retry_after_seconds`)에 다음을 더합니다.

| 필드 | 형식 | 설명 |
|---|---|---|
| `status` | string | 기존 값 + `EXPIRED`(되묻기 30분 경과) + `CANCELLED`(사용자 중단) + `WAITING_REPORT`(리포트 완료 대기) |
| `progress` | object \| null | 실행 중일 때 `{done, total, label}`. 완료 단계 수, 전체 단계 수, 현재 단계 문구(예: "데이터 조회 중"). 진행률 바에 사용 |
| `round_started_at` | string(ISO 8601) \| null | 경과 초 기준 시각. 되묻기에 답하면 새 시각으로 바뀜. 프론트는 현재 시각 − 이 값으로 초를 표시 |
| `clarification.kind` | string | 기존 `HS_ITEM`, `INTEREST_CONTEXT` + `COUNTRY`(비슷한 국가 확인), `INTEREST_SELECT`(관심사 골라 받기), `INTEREST_CHANGE`(관심사 변경 확인), `RANKING_GROUP`(국가/품목 순위 선택), `MODEL_PERIOD`(최신 기준 예측 확인), `REPORT_CONFIRM`(리포트 조건·형식 확인), `SHARE_BASIS`(점유율 분모 선택). 뒤 세 개는 아래 보완 절 참고 |
| `clarification.report_preview` | object \| null | `REPORT_CONFIRM`일 때만. `{preview_id, expires_at, conditions, adjustments, formats}` |
| `clarification.interests` | object \| null | `INTEREST_SELECT`일 때만. `{hs_items: HsItem[], country_codes: string[]}`, 질문 시점 관심사(품목 최대 100개·국가 최대 250개). 프론트는 체크박스로 보여 주고 품목 5개·국가 20개까지 고르게 한다. 선택지(`choices`)에는 "전체 범위"만 있다 |
| `clarification.allow_text` | boolean | 직접 입력 답을 받는지. 기본 true |
| `clarification.hint` | string | 작은 글씨 안내. "다른 질문은 새 채팅을 열거나 이 질문을 중단하세요." |
| `clarification.expires_at` | string(ISO 8601) | 되묻기 만료 시각(물은 시각 + 30분) |
| `clarification.message`, 선택지 `label` | string | (기존) 200자에서 자름. HS4 품목명은 최대 347자 (설계 D-62) |

`progress.label` 값: `조건 확인 중`, `데이터 조회 중`, `기사 찾는 중`, `차트 만드는 중`, `리포트 만드는 중`, `시장 예측 조회 중`, `답변 작성 중`, `숫자 확인 중`.

### answer에 더하는 것

기존 answer 필드(`intent`, `answer_status`, `text`, `conditions`, `tables`, `charts`, `articles`, `verification`, `actions`, `report_id`, `warnings`)는 유지합니다.

| 필드 | 형식 | 설명 |
|---|---|---|
| `tasks` | array | 복합 질문을 나눈 하위 질문. `[{task_id, intent, question, conditions, answer_status, conclusion}]`. 단일 질문도 1개. `conclusion`은 그 Task의 결론 문장 목록 |
| `chart_selectors` | array | 대상이 여러 개인 유망시장 결과의 대상 선택. `[{task_id, result_id, group_by, country_codes, hs_items}]`(아래 D-78~84 절) |
| `intent`, `conditions` | (기존) | 첫 번째 하위 질문(주 의도)의 값. 기존 화면과 호환 |
| `tables[].task_id`, `charts[].task_id` | string | 어느 하위 질문의 표·차트인지 |
| `charts[]` | ChartSpec | 아래 형식 |
| `text` | (기존) | 결론 문장(하위 질문마다 1~2문장, 최대 6문장) 다음 설명 문장(최대 4문장)을 이은 것. 문장마다 200자 이하 |
| `verification.sentence_checks` | array | 형식 확정. `[{index, sentence, numbers, status}]`. `index`는 `text`를 이루는 문장 순서(0부터), `status`는 `PASS`·`MISMATCH`. `MISMATCH` 문장에 "확인 필요"를 표시 |
| `warnings` | (기존) | 최대 10개, 각 200자에서 자름 |

### ChartSpec

`state.ChartSpec` 형식입니다. 프론트 `frontend/src/components/charts.jsx`의 props와는 모양이 달라 프론트가 변환합니다(`LineChart`는 `yTicks`·시리즈 색 추가, `Donut`은 `[label, 비율, 색]`, `null` 칸은 선을 끊어 그림).

```json
{"chart_id":"c1","task_id":"t1","type":"line","title":"베트남 화장품 수출 추이","unit":"USD",
 "period":{"start":"2024-10-01","end":"2025-09-01"},"source":"관세청","result_id":"r1",
 "labels":["2024-10","2024-11"],"series":[{"name":"수출액","values":[35100000,36200000]}]}
```

| 필드 | 규칙 |
|---|---|
| `type` | `line`(추이), `bar`(순위·유망시장), `grouped_bar`(두 기간 비교: 길이가 같으면 합계, 다르면 월평균. 여러 대상이거나 길이가 다른 비교일 때), `donut`(비중). 서버가 결과를 보고 정한다 |
| `granularity` | `MONTH`·`QUARTER`·`YEAR` 또는 null. 60개월을 넘는 추이는 분기·연도로 묶음 |
| `title` | 40자에서 자름 |
| `labels` | 1~60개 (x축 값, 또는 donut 조각 이름). 각 40자 이하. 품목명은 앞 20자 + "…" + 끝부분으로 40자에 맞추고, 줄인 이름이 겹치면 끝에 "(HS 코드)"를 붙여 구분함. 같은 코드가 판본만 달리 함께 있으면 "(8483 HS2017)"처럼 판본까지 붙임(설계 D-68). 그 밖은 40자에서 자름. 한 차트 안에서 라벨은 겹치지 않음 |
| `full_labels` | `labels`와 같은 개수. 줄이기 전 전체 이름. 프론트는 마우스를 올렸을 때 보여 줌 |
| `series` | 1~5개. 각 `values` 개수 = `labels` 개수. 값이 없으면 `null`(NaN·무한대는 보내지 않음). `name`은 40자 이하이고 한 차트 안에서 겹치지 않음. 여러 대상 추이의 시리즈 `name`은 "국가 · 품목" 전체를 품목명과 같은 규칙으로 줄이고, 겹치는 이름에만 코드를 붙임(설계 D-89). 대상이 5개를 넘으면 금액 상위 5개만 그리고 `warnings`에 안내 |
| `unit` | 20자 이하 |
| `source` | 40자에서 자름 |
| `donut` | 시리즈 1개 |
| `grouped_bar` | 시리즈 2개 (비교 기간, 조회 기간). 기간 길이가 다르면 값은 월평균이고 시리즈 이름에 "월평균"을 붙임 |
| `result_id` | 같은 값의 표 보기·엑셀 다운로드에 사용 |

`bar`, `grouped_bar` 컴포넌트는 프론트에 아직 없어 추가가 필요합니다.

## API-38 `POST /chat/jobs/{job_id}/clarifications`

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `clarification_id` | string | 필수 | 기존 |
| `choice_ids` | string[] | 다섯 중 하나 | 기존 |
| `hs_item` | HsItem | 다섯 중 하나 | 기존 |
| `text` | string | 다섯 중 하나 | **신규.** 1~4,000자, 질문과 같은 마스킹. 되묻기 맥락으로 해석하고, 해석할 수 없으면 다시 되묻기 |
| `report_confirmation` | object | 다섯 중 하나 | **신규.** REPORT_CONFIRM 전용. `{preview_id, confirm: boolean, formats: [PDF 또는 DOCX]}`. 형식은 1개 이상, 중복 불가 |
| `interest_selection` | object | 다섯 중 하나 | **신규.** `INTEREST_SELECT`의 답. `{hs_items: HsItem[], country_codes: string[]}`, 품목 0~5개·국가 0~20개, 둘 다 비면 안 됨. 모두 `clarification.interests` 안의 값이어야 함 |

추가 오류

| 코드 | HTTP | 경우 |
|---|---|---|
| `CLARIFICATION_EXPIRED` | 409 | 물은 지 30분이 지났거나 실행 상태가 사라짐. 안내 "시간이 지나 다시 질문해 주세요." |
| `CLARIFICATION_CONFLICT` | 409 | 기존. `clarification_id` 불일치 |
| `VALIDATION_ERROR` | 400 | `choice_ids`·`hs_item`·`text`·`interest_selection`·`report_confirmation` 중 0개 또는 2개 이상. `interest_selection`이 품목 5개·국가 20개를 넘거나, 둘 다 비었거나, 후보에 없는 값을 담음 |

같은 조건에 대한 되묻기는 2회까지입니다. 넘으면 해당 하위 질문만 NO_DATA로 끝내고 나머지는 계속합니다. 다른 하위 질문에 사용 가능한 결과가 있으면 전체는 PARTIAL, 없으면 NO_DATA입니다. 실행은 답변 저장 후 SUCCEEDED로 끝납니다(설계 D-72).

## API-131 (신규) `POST /chat/jobs/{job_id}/cancel`

되묻기 대기 중이거나 실행 중인 질문을 중단합니다.

| 항목 | 내용 |
|---|---|
| 인증 | 필요 (본인 작업만) |
| 요청 본문 | 없음 |
| 성공 | 200 `{job}`, `status: CANCELLED`. 대화방 잠금이 풀려 바로 새 질문 가능 |
| 실행 중 중단 | 진행 중인 단계가 끝나는 시점에 멈추고, 답변은 저장하지 않음 |
| 이미 끝난 작업 | 409 `JOB_ALREADY_FINISHED` |
| 없는 작업 | 404 `NOT_FOUND` |

중단은 되돌릴 수 없으므로, 프론트는 확인 문구("이 질문을 중단할까요?")를 한 번 보여 줍니다.


## 2026-10-30 보완 계약 (D-72~77)

- API-36은 같은 client_message_id의 동일 요청을 먼저 찾아 기존 job을 반환한다. 이 동작은 RUNNING/WAITING_REPORT/NEEDS_INPUT에서도 동일하다. 되묻기 직접 답도 멱등키로 구분한다. 다른 내용으로 키를 재사용하면 409 IDEMPOTENCY_CONFLICT다.
- API-38은 이미 수락한 clarification_id와 동일 답을 재전송하면 기존 job을 반환한다. 다른 답으로 재사용하면 409 CLARIFICATION_CONFLICT다.
- API-37의 status에 WAITING_REPORT를 추가한다. 리포트 생성 완료 전에는 answer가 없고 retry_after_seconds=2다. 입력은 잠기며 '리포트 만드는 중'을 표시한다. READY 이후에만 채팅 답변에 완성된 리포트를 포함한다. FAILED면 해당 하위 질문 실패를 반영한다.
- WAITING_REPORT도 progress는 완료 전 100%가 아니다. API의 round_started_at은 사용자 화면의 전체 경과 시간 기준으로 유지한다. state.round_started_at도 대기·재개 때 이동하지 않는다. 실행 상한은 별도 run_budget으로 관리한다(D-85).
- answer.tasks[]에 answer_status(ANSWERED/PARTIAL/NO_DATA/OUT_OF_SCOPE)를 추가한다. 전체 answer_status와 개별 결과는 D-72로 집계한다. 첫 Task 실패 시 이전 대화 조건을 유지한다.
- clarification.kind에 MODEL_PERIOD를 추가한다. '지정한 과거 기간은 예측할 수 없습니다. 최신 기준 예측을 볼까요?'에 대해 choice_ids로 동의/거절하거나 text로 답한다. 기준월은 실제 스냅샷 값을 표시한다. hs_item/interest_selection은 이 종류에 허용하지 않는다.
- API-131은 WAITING_REPORT에서도 채팅 대기를 중단한다. 이때 확인 문구는 '채팅의 리포트 대기를 중단할까요? 이미 접수한 리포트 생성은 계속될 수 있습니다.'다. 취소된 채팅에 나중에 완료 답변을 붙이지 않는다.
- 새 state 계약과 API 필드는 구현 예정이다. 이 브랜치는 실제 routes/worker 동작을 변경하지 않는다.


## 2026-10-30 추가 확정 (D-78~84)

- API-37의 clarification.kind에 REPORT_CONFIRM을 추가한다. clarification.report_preview는 `{preview_id, expires_at, conditions, adjustments, formats}`이며 PDF/DOCX 체크박스와 확인/거절 버튼을 보여 준다. 원본은 Task.report_request와 MySQL 미리보기다.
- API-38의 report_confirmation은 최신 clarification_id와 preview_id를 함께 검증한다. confirm=true일 때 선택 형식을 저장하고 생성한다. confirm=false면 해당 Task는 NO_DATA다. 이 종류에서는 choice_ids/hs_item/interest_selection으로 생성을 승인할 수 없으며 text는 조건 수정·재확인용으로만 쓴다. 명시적 확인 버튼 없이 리포트를 접수하지 않는다.
- 미리보기가 만료되면 새 미리보기를 제시하고 다시 확인받는다. 오래된 ID 제출은 409 CLARIFICATION_CONFLICT다. 반복 만료·조건 변경도 D-15의 동일 조건 확인 횟수 제한을 적용한다. 수입 리포트는 미지원 안내 후 NO_DATA다.
- answer.tasks[].conclusion은 하위 질문별 문장 목록이다. answer.text와 verification.sentence_checks는 Draft.sentences()의 순서로 구성한다. compose 대체 응답(draft=None)이면 해당 목록은 []다.
- answer.chart_selectors[]는 `{task_id, result_id, group_by, country_codes, hs_items}`다. hs_items는 6자리 HS6 코드만 담는다. 최초 차트는 요청 순서에서 결과가 있는 첫 대상을 표시하고, 선택 변경 시 신규 `GET /results/{result_id}/chart`로 읽는다. 대상 선택 후보와 저장 결과표는 같은 분석 결과를 사용한다.
- 차트 조회 파라미터: COUNTRY는 country_code, HS6는 hs_code와 hs_ver, PAIR는 세 값 모두다. group_by는 저장된 선택 메타데이터에서 읽는다. 인증·소유권과 실제 결과 행을 확인하며, 유효하지 않은 선택은 400 VALIDATION_ERROR, 없는 결과는 404 NOT_FOUND, 만료된 결과는 기존 결과 조회와 같은 410 RESULT_EXPIRED다. 반환은 `{chart: ChartSpec + full_labels}`다. task_id는 저장 결과의 소속이다. 모델·LLM 재호출은 없다.
- ChartSpec.granularity는 MONTH/QUARTER/YEAR 또는 null이다. 60개월 초과 차트는 분기, 분기도 60개 초과면 연도로 집계하며 표는 월별이다. 일부 분기/연도는 실제 포함 범위를 표시하고 null을 0으로 바꾸지 않는다.
- 길이가 다른 기간의 표·답변은 기간별 총액·월평균·요청 월 수·검증 월 수와 기간 길이 차이 안내를 포함한다. 미검증 월이 있으면 전체 총액은 null, 확인된 금액은 '검증된 월 소계'로 표시한다. 비율은 집계 금액으로 재계산한다(D-84).
- 이 절의 API·프론트·SQL·리포트 연결은 후속 구현 항목이며 이 브랜치에서는 설계와 state 계약만 구현한다.


## 2026-10-30 추가 확정 (D-85~88)

- API-37의 라운드 경과 시간에는 큐·리포트·장애 복구 대기가 보일 수 있지만, 180초 실행 제한은 별도 누적 예산으로 계산한다. 복구했다고 새 180초를 주지 않는다.
- 실행 예산/복구 한도 소진 시 안전한 부분 답변이 없으면 FAILED와 RUN_TIMEOUT 또는 RECOVERY_EXHAUSTED, retryable=false로 끝낸다. 복구 한도 기본값은 라운드당 2회다.
- 하위 질문을 합친 뒤에도 3개를 초과하면 FAILED, error.code=QUESTION_TOO_COMPLEX, retryable=false, message='한 번에 3개 이하로 나눠 질문해 주세요.'를 반환한다. tool은 실행하지 않고 기존 대화 조건은 유지한다.
- clarification.kind에 SHARE_BASIS를 추가한다. choice_ids 또는 text 중 하나로 답하며 조건별 2회 제한을 따른다. 선택지는 '한국 수출입의 상대국별 비중'과 '해외 수입시장에서 한국 제품의 점유율(지원 예정)'이다. 무관한 hs_item/interest_selection/report_confirmation은 거부한다.
- 현재 지원하는 분모·수출입 방향·기간을 표와 답변에 명시한다. 해외시장 내 한국 점유율을 선택하면 해당 Task는 NO_DATA와 미지원 문구로 끝낸다.
- chart와 chart_selector는 같은 result_id를 사용한다. 저장된 ML 조회 결과는 표·차트·다운로드에 재사용하며 만료 시 기존 410 RESULT_EXPIRED 응답을 따른다.


## 2026-10-30 리뷰 후 확정 (D-90~94)

- answer.tasks[]의 answer_status는 자체 tool과 선행 입력의 completeness를 반영한다. 일부 대상 누락·기사 근거 부족은 PARTIAL이며 경고 문구로 판정하지 않는다. 내부 completeness/limitations를 사용자 결과 상태와 warnings로 변환한다.
- answer.articles[]에 evidence_sufficient(boolean)를 추가한다. false면 제목·발행일·원문 링크와 근거 부족 안내만 표시하고 summary는 비운다. 원인 설명·사실 주장의 인용 대상으로 쓰지 않는다. 기사 번호는 전체 카드 순서를 유지한다.
- clarification.report_preview에 source_results([{task_id, step_id, result_id, conditions, completeness, limitations}])를 추가한다. 선행 분석의 조건·누락 사항을 확인 화면에 표시한다. 서버 내부 입력 사본은 노출하지 않는다.
- 리포트 확인의 text는 생성·취소 승인이 아니다. '네/생성/취소'에는 버튼을 안내하며 기존 asked_at을 유지하고 확인 횟수를 추가 차감하지 않는다. 실제 승인·거절은 report_confirmation으로만 한다. 독립 리포트 조건 변경은 새 미리보기로 재확인하며, 선행 결과와 다른 조건은 새 분석을 안내한다.
- 기존 reports.preview(result_id)는 조건만 복사한다. 후속 구현은 선행 결과를 서버 PREVIEW에 복사한 뒤 승인된 동일 사본을 리포트 작업으로 전달한다(D-90). 생성 중 원본 결과표가 만료되어도 승인된 사본으로 완성한다. 누락 섹션을 새 조회로 몰래 채우지 않는다.
- 리포트 READY 후 해당 Task의 고정 완료 결론과 보기·다운로드 actions를 반환한다. 부분 입력이면 PARTIAL을 유지한다. 생성 전/실패 상태에는 완료 문구를 반환하지 않는다.
- 이 절은 구현 계약이다. 실제 API·프론트·리포트 워커 변경은 후속 구현이며 현재 routes 동작은 그대로다.


### D-95: 선행 결과 대상 선택
- clarification.kind에 INPUT_SELECTION을 추가한다. 선택지와 직접 입력을 지원하며 API-38은 choice_ids 또는 text만 허용한다. hs_item/interest_selection/report_confirmation은 거부한다. 조건별 2회 제한이다.
- 사용자가 명시한 개수·기준이면 자동으로 후속 분석한다. 모호한 개수·기준·그룹만 확인한다. applied_defaults/warnings에 실제 선택 기준과 대상 부족을 표시한다. 리포트의 REPORT_CONFIRM은 생략하지 않는다.
