# 채팅 API 변경 정의 (에이전트 반영분)

API 정의서 v0.18(2026-10-23)의 채팅 API(API-36~38)에 반영할 변경과 새 API 1개를 적었습니다. 근거가 되는 설계 결정은 [채팅 에이전트 설계](AGENT_DESIGN.md) §1에 있습니다. 공통 규칙(Base `/api/v1`, snake_case, `bs_session` 쿠키 + `X-CSRF-Token`, 오류 형식)은 기존과 같습니다.

## 바뀌는 것

| API | 구분 | 바뀌는 점 | 근거 |
|---|---|---|---|
| API-36 질문 보내기 | 동작 변경 | 되묻기 대기 중에 보낸 질문은 새 작업이 아니라 그 작업의 되묻기 답(직접 입력)으로 처리 | D-12 |
| API-36 | 오류 추가 | 503 `CHAT_UNAVAILABLE` (Redis 장애) | D-06 |
| API-37 작업 조회 | 응답 추가 | `progress`, `round_started_at`, `clarification.allow_text`, `clarification.hint`, 새 되묻기 종류 | D-12, D-25, D-56 |
| API-37 | 상태 추가 | `status`에 `EXPIRED`, `CANCELLED` | D-04, D-12 |
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
| 작업 실행 중(QUEUED·RUNNING) | 409 `JOB_IN_PROGRESS` (기존). 프론트는 생성 중에 입력창·전송 버튼을 비활성화 |
| 되묻기 대기 중(NEEDS_INPUT) | 202, **기존 작업**을 반환. 보낸 글은 API-38 `text`와 같이 처리하고, 작업 상태는 QUEUED |
| Redis 연결 불가 | 503 `CHAT_UNAVAILABLE`, `retryable: true`, 안내 "잠시 후 다시 시도해 주세요." |

## API-37 `GET /chat/jobs/{job_id}`

기존 필드(`job_id`, `session_id`, `status`, `step`, `clarification`, `answer`, `error`, `retry_after_seconds`)에 다음을 더합니다.

| 필드 | 형식 | 설명 |
|---|---|---|
| `status` | string | 기존 값 + `EXPIRED`(되묻기 30분 경과) + `CANCELLED`(사용자 중단) |
| `progress` | object \| null | 실행 중일 때 `{done, total, label}`. 완료 단계 수, 전체 단계 수, 현재 단계 문구(예: "데이터 조회 중"). 진행률 바에 사용 |
| `round_started_at` | string(ISO 8601) \| null | 경과 초 기준 시각. 되묻기에 답하면 새 시각으로 바뀜. 프론트는 현재 시각 − 이 값으로 초를 표시 |
| `clarification.kind` | string | 기존 `HS_ITEM`, `INTEREST_CONTEXT` + `COUNTRY`(비슷한 국가 확인), `INTEREST_SELECT`(관심사 골라 받기), `INTEREST_CHANGE`(관심사 변경 확인), `RANKING_GROUP`(국가/품목 순위 선택) |
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
| `tasks` | array | 복합 질문을 나눈 하위 질문. `[{task_id, intent, question, conditions}]`. 단일 질문도 1개 |
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
| `type` | `line`(추이), `bar`(순위), `grouped_bar`(두 시점 비교), `donut`(비중) |
| `title` | 40자에서 자름 |
| `labels` | 1~60개 (x축 값, 또는 donut 조각 이름). 각 40자 이하. 품목명은 앞 20자 + "…" + 끝부분으로 40자에 맞춤(설계 D-68), 그 밖은 40자에서 자름 |
| `full_labels` | `labels`와 같은 개수. 줄이기 전 전체 이름. 프론트는 마우스를 올렸을 때 보여 줌 |
| `series` | 1~5개. 각 `values` 개수 = `labels` 개수. 값이 없으면 `null`(NaN·무한대는 보내지 않음). `name`은 40자에서 자름 |
| `unit` | 20자 이하 |
| `source` | 40자에서 자름 |
| `donut` | 시리즈 1개 |
| `grouped_bar` | 시리즈 2개 (비교 기간, 조회 기간) |
| `result_id` | 같은 값의 표 보기·엑셀 다운로드에 사용 |

`bar`, `grouped_bar` 컴포넌트는 프론트에 아직 없어 추가가 필요합니다.

## API-38 `POST /chat/jobs/{job_id}/clarifications`

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `clarification_id` | string | 필수 | 기존 |
| `choice_ids` | string[] | 넷 중 하나 | 기존 |
| `hs_item` | HsItem | 넷 중 하나 | 기존 |
| `text` | string | 넷 중 하나 | **신규.** 1~4,000자, 질문과 같은 마스킹. 되묻기 맥락으로 해석하고, 해석할 수 없으면 다시 되묻기 |
| `interest_selection` | object | 넷 중 하나 | **신규.** `INTEREST_SELECT`의 답. `{hs_items: HsItem[], country_codes: string[]}`, 품목 0~5개·국가 0~20개, 둘 다 비면 안 됨. 모두 `clarification.interests` 안의 값이어야 함 |

추가 오류

| 코드 | HTTP | 경우 |
|---|---|---|
| `CLARIFICATION_EXPIRED` | 409 | 물은 지 30분이 지났거나 실행 상태가 사라짐. 안내 "시간이 지나 다시 질문해 주세요." |
| `CLARIFICATION_CONFLICT` | 409 | 기존. `clarification_id` 불일치 |
| `VALIDATION_ERROR` | 400 | `choice_ids`·`hs_item`·`text`·`interest_selection` 중 0개 또는 2개 이상. `interest_selection`이 품목 5개·국가 20개를 넘거나, 둘 다 비었거나, 후보에 없는 값을 담음 |

같은 조건에 대한 되묻기는 2회까지입니다. 넘으면 작업이 `SUCCEEDED`로 끝나고, answer는 `answer_status: NO_DATA`와 "조건을 확인해 다시 질문해 주세요."를 담습니다.

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
