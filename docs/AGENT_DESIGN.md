# 채팅 에이전트 설계

기준 브랜치 `feat/50-agent-state` · 갱신 2026-10-29 · 그림: [무역통계 Agent State 설계](https://claude.ai/artifact/2SKYDkScxPRPjhxkxBabCb) (팀 공유 전에는 작성자만 열람) · API 변경: [AGENT_API_CHANGES.md](AGENT_API_CHANGES.md)

현재 `backend/app/api/chat.py`의 `execute`는 목업이다. LLM은 의도와 슬롯만 뽑고, 답변 문장은 고정 문구다. 이 문서는 그 목업을 대체할 실제 에이전트의 설계다. 에이전트·채팅·워커 코드를 고치기 전에 이 문서와 [state.py](../backend/app/agents/state.py)를 먼저 읽는다.

**원칙**
- 원본은 MySQL에 둔다. Redis에는 컨텍스트와 실행 중 상태만 둔다.
- state에는 ID와 작은 요약만 넣는다.
- LLM은 숫자를 계산하지 않고 HS 코드를 만들지 않는다.

## 1. 결정 사항

결정한 날짜는 모두 2026-10-29다. 괄호 안 번호는 질문 목록의 번호다.

### 저장·세션
| ID | 결정 |
|---|---|
| D-01 | Redis에는 대화방 컨텍스트(최근 검색 조건 + 최근 5건 요약), 세션 lock, LangGraph 체크포인트(실행 state)를 둔다. 로그인 세션은 따로 결정한다(현재 MySQL `api_auth_session` 유지). (Q-02) |
| D-02 | 컨텍스트는 Redis에만 둔다. MySQL `chat_session.context_json`에는 검색 조건만 저장한다. Redis 키가 사라지면 조건만 MySQL에서 복구하고 5건 요약은 비운 채 다시 쌓는다. (Q-03) |
| D-03 | 컨텍스트 TTL은 7일, 대화방을 열 때 연장한다. (Q-01) |
| D-04 | 되묻기 대기는 30분. 지나면 EXPIRED 처리하고 질문을 다시 받는다. |
| D-05 | 실행 상태는 `state.py`의 `AgentState`를 따른다. |
| D-06 | Redis 장애 시 채팅 접수를 막고 "잠시 후 다시 시도해 주세요"를 안내한다. (Q-50) |
| D-07 | Redis는 EC2 Docker로 띄운다. 체크포인터가 RedisJSON·RediSearch를 쓰므로 Redis 8 이상 이미지를 쓴다. AOF를 켜고(`--appendonly yes --appendfsync everysec`, `/data` 볼륨) 배포할 때 Redis 컨테이너는 내리지 않는다. (Q-49, C-03, C-16) |
| D-08 | 채팅 질문 요청 제한은 두지 않는다. 기존 `api_rate_limit`은 로그인·HS 검색에만 적용된 현행을 유지한다. (Q-08, C-01) |

### 하네스
| ID | 결정 |
|---|---|
| D-09 | 에이전트는 LangGraph 그래프로 연결하고, 실행 state는 `langgraph-checkpoint-redis`의 `RedisSaver`로 저장한다. `thread_id`는 `job_id`, TTL은 `state.CHECKPOINT_TTL`(30분, 읽을 때 연장)이다. 그래프 구조는 §5. (C-03) |

### 접수·되묻기
| ID | 결정 |
|---|---|
| D-10 | 질문은 4,000자 이하, 기존 `mask_text`로 마스킹한다. (Q-04) |
| D-11 | 같은 대화방에서 실행 중이면 409 `JOB_IN_PROGRESS`를 반환한다. 프론트는 생성 중에 입력창·전송 버튼을 비활성화한다. (Q-05) |
| D-12 | 되묻기 대기 중 입력은 되묻기 답(직접 입력)으로 처리한다. 해석할 수 없거나 무관하면 다시 되묻는다(D-15에 포함). 작은 글씨로 "다른 질문은 새 채팅을 열거나 이 질문을 중단하세요"를 표시하고, 중단 API를 둔다. (Q-06) |
| D-13 | 사용자당 동시 실행 수는 제한하지 않는다. (Q-07) |
| D-14 | 관심사는 질문 시점 값으로 고정한다. 되묻기 중 사용자가 바꿔 달라고 하면 "○○로 바꿀까요?"를 확인(`INTEREST_CHANGE`)한 뒤 **이번 질문의 조건만** 바꾼다. 저장된 관심사는 관심 추가 버튼으로만 바꾼다. (Q-11, C-11) |
| D-15 | 되묻기는 조건 하나(`Pending.condition_key`)당 2회까지다. 넘으면 "조건을 확인해 다시 질문해 주세요"로 끝낸다. (Q-30) |
| D-16 | 되묻기 선택지는 최대 5개이고 "분류에서 직접 고르기" 링크를 함께 준다. (Q-29) |

### 플래너·조건
| ID | 결정 |
|---|---|
| D-20 | 모델명은 환경변수로 바꿀 수 있게 한다. 플래너는 `API_INTENT_MODEL`, compose는 `API_COMPOSE_MODEL`이며 기본값은 둘 다 `gpt-4.1-mini`. (Q-15) |
| D-21 | LLM 대기 시간은 10초, 실패하면 1번 재시도한다. (Q-16) |
| D-22 | 후속 질문은 질문에 나온 슬롯만 바꾸고 나머지를 이어받는다. (Q-18) |
| D-23 | ML intent는 두 가지다. `PROMISING_MARKET`(유망시장)은 국가·품목 중 1개 이상이 필요하다. `MARKET_TYPE`(시장 유형)은 국가·품목이 모두 필요하지만, B 모델 시장 유형 분류가 나오기 전까지는 단계 없이 "지원 예정" 안내만 한다. 추가 예정 내용은 `backend/app/ml/ML_USAGE.md`에 적었다. (Q-13, C-05) |
| D-24 | 질문 하나에 품목은 5개, 국가는 20개까지다. (Q-20) |
| D-25 | 국가명이 모호하면 비슷한 국가로 "○○가 맞나요?"를 되묻는다(`COUNTRY`). 후보가 없으면 전체 국가명 재입력을 요청한다. (Q-21) |
| D-26 | 권역 표현(동남아, EU)은 미지원 안내만 한다. (Q-22) |
| D-27 | 기간을 말하지 않으면 수출입은 최근 12개월, 기사는 최근 3개월이다. (Q-23) |
| D-28 | 미공개 월을 물으면 범위 밖으로 처리하고 최신 공개 월을 안내한다. (Q-24) |
| D-29 | 비교 기간을 말하지 않으면 전년 동기, 지정하면 그 기간을 쓴다. 지정 기간용 SQL 템플릿을 보완한다(현재는 전년 동기 열만 있음). (Q-25, C-13) |
| D-30 | 관심 적용 되묻기는 대화방 첫 질문이면서 품목·국가가 둘 다 없을 때만 한다. "전체 범위"를 고르면 그 방에서 다시 묻지 않는다. (Q-26) |
| D-31 | 수출/수입을 말하지 않으면 수출로 보고, 답변에 "수출"을 명시한다. (Q-27) |
| D-32 | 순위 기준이 모호하면 `RANKING_GROUP` 선택지로 되묻는다. (Q-28) |
| D-33 | HS 품목은 SQL 정확 일치(등록 품목명·코드)일 때만 바로 확정한다. 임베딩 결과는 자동 확정하지 않고, 항상 상위 5 HS6 선택지 + 분류 직접 선택으로 되묻는다. 근거는 2026-10-29 시험(일상어 79개): 1위 정답 59%, 상위 5 안 75%, 1·2위 거리 차이 중앙값이 맞은 경우 0.010 / 틀린 경우 0.012로 구분되지 않음. (Q-19, C-04) |
| D-34 | 복합 질문은 플래너가 하위 질문(`Task`) 최대 3개로 나눠 한 답변으로 처리한다. 대상만 여러 개인 질문("베트남·미국 추이")은 하위 질문 하나에 조건을 여러 개 넣는다. 하위 질문은 순서대로 실행하고, 되묻기는 한 번에 하나씩 한다. 최근 검색 조건과 5건 요약의 조건은 첫 번째 하위 질문(주 의도) 기준이다. (Q-12, Q-14, C-02) |

### tool
| ID | 결정 |
|---|---|
| D-40 | 템플릿에 없는 질문은 LLM이 SQL을 만들고, 검증 실패 시 최대 2회 다시 만든다. (Q-31) |
| D-41 | 개발 중 LLM SQL은 `.env`에 등록된 관리자 DB 계정으로 실행한다. 대신 세션을 `READ ONLY` 트랜잭션으로 열고, sqlglot으로 SELECT만·허용 테이블만·LIMIT 강제를 검사하고, 시간 상한을 둔다. 배포 전에 읽기 전용 계정으로 바꾼다(`TODO(D-41)`). (C-10) |
| D-42 | 결과표는 1,000행, 순위는 기본 상위 10이다. (Q-32) |
| D-43 | 기사 검색 tool은 RAG 담당자가 제공할 `search_news(RagRequest) -> RagResponse`를 부른다. 지금은 자리만 있는 함수(`agents/tools.py`)가 `None`을 돌려준다. 그러면 tool은 NO_DATA와 함께 "기사 검색은 지원 예정입니다." 문구만 남긴다. 기사가 필요한 하위 질문(LOCAL_ISSUES)은 이 문구로 답하고, CAUSE는 통계만 답한 뒤 문구를 덧붙여 PARTIAL로 둔다. `blocked_topics`는 알림 차단용이라 채팅에서는 빈 값이다. (Q-33, C-15) |
| D-44 | RAG가 연결되면 기사는 확정된 품목명·국가명을 키워드로, 조회 기간 앞뒤 1개월까지 vector DB에서 검색한다. 급증 월은 쓰지 않는다. (Q-34, C-14) |
| D-48 | ML 유망시장은 워커가 서버에 둔 모델 파일(v1·v3 앙상블 joblib)과 피처 스냅샷으로 요청 때마다 예측한다. 결과는 SQL에 저장하지 않는다. 스냅샷은 최신 기준월만 담은 파일(`features_snapshot_py311_latest*.pkl`, 약 22MB)을 쓴다. 전체 스냅샷(약 750MB)은 쓰지 않는다. tool은 스냅샷에서 국가·품목 행을 골라 `two_stage.predict` → `combine`(앙상블) → 순위·`exp_12m`을 계산한다. `two_stage.query`는 출력만 하므로 tool용으로는 값을 돌려주는 함수가 필요하다. 모델과 스냅샷은 워커 메모리에 한 번 올려 재사용하고, 경로는 환경변수로 받는다. 워커에는 LightGBM·`libgomp1`이 필요하고, 패키지 버전은 모델 `runtime`(ML_USAGE.md)과 맞춘다. 예측 값은 스냅샷이나 모델 파일을 바꿀 때만 달라진다. (C-05, C-19) |
| D-45 | 재시도는 SQL 2회, 기사 1회, 차트·리포트·ML 0회다. (Q-36) |
| D-46 | tool 일부가 실패하면 나머지 결과로 PARTIAL 답변을 만들고 경고를 붙인다. (Q-37) |
| D-47 | 단계는 순차 실행한다. 단계별 소요 시간을 로그로 남기고, CAUSE 질문의 90%가 30초 안에 끝나지 않으면서 SQL과 기사 대기가 그 시간의 절반 이상이면 병렬로 바꾼다. (Q-38, C-08) |

### 답변·마무리·진행 표시
| ID | 결정 |
|---|---|
| D-50 | 결론은 하위 질문마다 1~2문장, 설명은 전체 4문장 이하로, 합니다체로 쓴다. 기사는 문장 끝 [번호]와 기사 카드로 인용한다. (Q-39~41) |
| D-51 | 숫자 대조는 화면 표시 단위의 반올림 차이만 허용한다. 재생성 후에도 틀린 문장은 "확인 필요"로 표시한다. (Q-42, 43) |
| D-52 | 5건 요약은 compose가 같은 LLM 호출에서 `Draft.turn_summary`로 쓴다. 200자 이하이고 양식은 `{조건 요약} {질문 요지} → {핵심 결과 1개}`다. 숫자는 답변에 나온 것만 쓰고, 되묻기 과정은 넣지 않는다. 정상·부분 답변만 넣는다. (Q-09, 10, C-06) |
| D-53 | 최근 검색 조건은 정상·부분 답변일 때만 갱신한다. (Q-44) |
| D-54 | 대화방 제목은 첫 답변에서만 compose가 검색 조건을 넣어 20자 이하로 쓴다. (Q-45, C-12) |
| D-55 | answer actions는 유지한다. 리포트 저장은 PDF/DOCX를 고르게 한다(기존 `formats`). 관심 추가는 현재 조건의 국가·품목을 체크박스로 골라 추가한다(기존 `POST /users/me/interests/items`). (Q-46) |
| D-56 | 2초마다 폴링한다. 단계별 문구, 진행률 바, 경과 초를 보여 주고, 되묻기에 답하면 경과 초를 0부터 다시 센다(`round_started_at`). (Q-47) |
| D-57 | 한 라운드 실행 상한은 180초(`RUN_TIMEOUT_SECONDS`)다. (Q-48) |
| D-58 | 비동기 구조를 유지한다. API가 등록하고, 워커가 실행하고, 프론트가 폴링한다. 기존 answer 필드는 유지하고 추가만 한다(§7). |
| D-59 | 차트는 `state.ChartSpec`이다. 유형 4개(`line`·`bar`·`grouped_bar`·`donut`), 제목 40자, x축 값 60개, 시리즈 5개 이하다. donut은 시리즈 1개, grouped_bar는 2개. `charts.jsx` props와는 모양이 달라 프론트가 변환한다(LineChart는 `yTicks`·색 추가, Donut은 `[label, 비율, 색]`, 값 없는 칸은 선을 끊음). (Q-35, C-07) |
| D-60 | API 변경은 [AGENT_API_CHANGES.md](AGENT_API_CHANGES.md)를 따른다: 되묻기 직접 입력, 중단 API, 진행률, 하위 질문. (C-09) |
| D-61 | 골든셋 평가: 질문 파일은 `backend/tests/golden/chat_golden.jsonl`이고 나중에 LLM으로 만든다. 평가 스크립트 결과는 `outputs/eval/<날짜>/runs.jsonl` + `summary.md`. LLM 원문은 평가 실행에서만 남긴다. 운영 답변에는 `result_json.trace`(intent, 계획, 단계별 시간, 모델명, 프롬프트 버전)만 남긴다. 채점 기준은 4개: intent 일치, 조건(HS·국가·기간·수출입) 일치, 숫자 일치(D-51 허용 오차), 되묻기 종류 일치. (Q-51, C-17, C-18) |

## 2. 구성 요소

| 구성 요소 | 하는 일 | 위치 |
|---|---|---|
| API 서버 | 질문 접수, 작업 등록, 폴링 응답, 되묻기 답·중단 접수 | `backend/app/api/chat.py`, `routes.py` |
| 워커 | 작업을 claim해 그래프를 실행·재개한다. lease 10분 | `backend/app/api/worker.py`, `jobs.py` |
| 에이전트 그래프 | LangGraph. 플래너 → (하위 질문별) 조건 확인 → tool → compose → verify → finalize | `backend/app/agents/` |
| Redis 8 | 컨텍스트, 세션 lock, 체크포인트 (§6) | 신규. EC2 Docker |
| MySQL | 질문·답변·작업 상태(`_execution`)·AgentInput·검색 조건·결과표·리포트·HS 사전·적재 로그 | 기존 |
| Chroma | 기사 벡터, HS 품목명 벡터 | 기존 |
| ML | 유망시장 조회. 서버에 배포한 모델 파일을 워커가 읽음 | `backend/app/ml/two_stage.py` |

새 의존성: `langgraph`, `langgraph-checkpoint-redis`(→ `redis`, `redisvl`), `sqlglot`, 워커에 `lightgbm`·`joblib`(모델 `runtime` 버전과 맞춤).

## 3. 실행 흐름

### A. 질문 접수 (API 서버)
1. `POST /chat/sessions/{sid}/messages`로 질문이 들어온다. Redis에 연결되지 않으면 503 `CHAT_UNAVAILABLE`을 반환한다.
2. Redis `GET chat:session:{sid}:active`로 진행 중인 작업을 확인한다.
   - RUNNING·QUEUED 작업이 있으면 409를 반환한다.
   - NEEDS_INPUT 작업이 있으면 이 글을 그 작업의 직접 입력 답으로 넘기고 C-17로 간다.
3. 멱등키를 확인하고 USER 메시지를 `_execution.status=QUEUED`로 저장한다. `SET active <job_id> NX EX 1800`.
4. Redis `GET chat:session:{sid}:context`로 컨텍스트를 읽는다. 없으면 MySQL `context_json.conditions`로 조건만 채운다.
5. `AgentInput`(질문, 컨텍스트, 관심사, 접수 시각)을 만들어 MySQL `_execution.payload_json.input`에 저장한다. 체크포인트가 사라져도 여기서 다시 시작할 수 있다.
6. `job_id`를 반환한다. 프론트는 입력창을 잠근다.

### B. 워커 실행 (그래프)
7. claim하면 `RUNNING`이 된다. 재개 방식:
   - 체크포인트(`thread_id=job_id`)가 있으면 이어서 실행한다. 되묻기 답이면 `Command(resume=답)`, 워커가 죽은 뒤면 `None`을 넣는다.
   - 체크포인트가 없으면 `AgentState(input=…)`로 처음 실행한다.
8. `round_started_at`이 비어 있으면 지금 시각을 넣는다. 180초가 넘으면 실패로 처리한다. 노드를 시작할 때마다 MySQL 작업 상태가 CANCELLED인지 확인하고, 중단됐으면 멈춘다.
9. **planner 노드**: LLM 1회로 질문을 하위 질문 1~3개로 나눈다. 하위 질문마다 intent와 슬롯을 뽑고, 단계 표(§5.1)로 `plan.tasks`를 만든다.
10. **dispatch 노드**: `ready_steps()`의 첫 단계를 골라 그 tool 노드로 보낸다. 남은 단계가 없으면 compose로 간다.
11. **check_conditions 노드**(하위 질문별): 슬롯을 HS 사전·국가 표·적재 로그와 대조해 `task.conditions`·`applied_defaults`와 공통 `data_meta`를 채운다.
    - 품목은 SQL 정확 일치만 확정하고, 그 밖은 상위 5로 되묻는다(D-33).
    - 국가는 비슷한 국가로 되묻고, 후보가 없으면 재입력을 요청한다(D-25).
    - ML 필수 조건을 확인한다(D-23).
    - 빈 조건은 최근 조건 → (첫 질문이면) 관심 되묻기 → 기본값 순서로 채운다.
    - 되묻기가 필요하면 `pending`을 채우고 `ask_counts`를 올린 뒤 `status=NEEDS_INPUT`으로 바꾼다. 그 조건을 이미 2회 물었으면 `ask_counts`를 올리지 않고 종료한다. 그다음 **ask_user 노드**로 간다.
    - 조건을 확정하지 못해 check 단계를 FAILED로 두면 `skip_blocked()`를 불러 그 하위 질문의 뒤 단계를 SKIPPED로 바꾼다.
12. **ask_user 노드**: `interrupt(state.pending)`로 멈춘다. 체크포인트에는 11번이 쓴 `pending`이 남는다. 워커는 MySQL `_execution`을 NEEDS_INPUT + clarification으로 바꾸고, `active` TTL을 30분으로 둔 뒤 끝낸다.
    재개되면 이 노드가 답을 받아 그 하위 질문의 조건에 병합한다. 그리고 `pending=None`, `status=RUNNING`, `round_started_at=지금`으로 바꾸고, 그 하위 질문의 check 단계를 PENDING으로 되돌린 뒤 dispatch로 간다. 직접 입력이면 `pending.message` 맥락으로 해석하고, 해석에 실패하면 check가 다시 묻는다.
13. **tool 노드**(sql, news, chart, report, ml): 실행 후 `results[step_id]`와 단계 상태를 쓴다. MySQL `_execution`의 step·progress를 갱신하고 dispatch로 돌아간다. 실패하면 재시도 한도까지 다시 하고, 그래도 실패하면 FAILED로 두고 `skip_blocked()`를 불러, 그 단계에 직접·간접으로 의존하는 단계를 SKIPPED로 바꾼다. `ready_steps()`는 의존 단계가 모두 DONE인 단계만 돌려준다.
14. **compose 노드**: LLM 1회로 `Draft`(결론, 설명, `turn_summary`, 첫 답변이면 `title`)를 쓴다. 숫자는 digest 값만 쓴다. `MARKET_TYPE`·`OUT_OF_SCOPE` 하위 질문은 고정 안내 문구를 쓴다.
15. **verify 노드**: 문장 속 숫자를 결과표와 대조하고, 1회 재생성한 뒤 그래도 틀리면 "확인 필요"를 붙인다.
16. **finalize 노드**:
    - answer JSON을 저장한다.
    - MySQL `context_json`에 conditions를 쓴다.
    - Redis context에 conditions와 `TurnSummary`를 쓴다(첫 하위 질문 기준).
    - 제목을 갱신한다.
    - `_execution=SUCCEEDED`로 바꾸고, `active`와 체크포인트 thread를 지운다(`delete_thread`).

### C. 되묻기 답·중단 (API 서버)
17. 선택지·직접 입력(API-38 또는 A-2)이 들어오면:
    - 체크포인트의 `pending`을 `graph.get_state`로 읽는다. 없거나 `asked_at`에서 30분이 지났으면 409 `CLARIFICATION_EXPIRED`를 반환하고 EXPIRED로 처리한다.
    - `clarification_id`를 비교하고, 답을 `_execution.payload_json.resume`에 저장한 뒤 QUEUED로 바꾼다. 워커가 7번에서 `Command(resume=답)`으로 재개한다.
18. 중단(API-131)이 들어오면 `_execution=CANCELLED`로 바꾸고, `active`와 체크포인트 thread를 지운다. 실행 중이던 워커는 다음 노드를 시작할 때 멈춘다.

### 장애
- **워커가 죽었을 때**: lease 10분이 지나면 다른 워커가 claim한다. 7번에서 `None`으로 재개해 마지막 체크포인트 다음 노드부터 실행한다.
- **Redis 데이터가 사라졌을 때**:
  - 실행 중이던 작업은 MySQL의 `AgentInput`으로 처음부터 다시 실행한다.
  - 되묻기 대기 중이던 작업은 만료로 처리한다.
  - 컨텍스트는 조건만 복구된다.
- **Redis에 연결되지 않을 때**: 채팅 접수를 막는다(D-06).

## 4. state

원본은 [state.py](../backend/app/agents/state.py)다. 필드를 바꾸면 `backend/tests/test_agent_state.py`도 함께 고친다. LangGraph state 스키마로 이 `AgentState`(Pydantic)를 그대로 쓴다. 검증은 LangGraph가 노드 입력을 만들 때 돌고 체크포인터는 검증하지 않으므로, finalize는 저장 전에 `model_validate`한다.

| 칸 | 쓰는 노드 | 읽는 노드 |
|---|---|---|
| `input` (질문·컨텍스트·관심사) | 최초 실행 | planner, check, compose, finalize |
| `status` | 워커 시작, check(NEEDS_INPUT), ask_user, finalize | 전부 |
| `plan.tasks[]` (하위 질문: intent·질문·단계·조건·기본값) | planner. 조건은 check, 단계 상태는 tool 노드 | dispatch, tool, compose, finalize |
| `data_meta` | check | tool, compose |
| `pending`, `ask_counts` | check (ask_user가 비움) | ask_user, API(되묻기 답 검증) |
| `round_started_at` | 워커 시작, ask_user | 폴링 응답, 시간 상한 |
| `results` (ID + digest + ChartSpec) | tool | compose(digest), verify(result_id), finalize |
| `draft` (문장·요약·제목) | compose | verify, finalize |
| `verification` | verify | finalize |
| `warnings` | check, tool | finalize |

모델이 강제하는 규칙
- NEEDS_INPUT이면 `pending`이 반드시 있고, 아니면 없어야 한다. `pending.task_id`는 계획에 있어야 한다.
- 하위 질문은 1~3개다. 각 하위 질문은 `check_conditions`로 시작하고, 같은 하위 질문의 앞 단계에만 의존할 수 있다. `OUT_OF_SCOPE`·`MARKET_TYPE`은 단계가 없다.
- `step_id`는 계획 전체에서 고유하다(예: `t1.sql`). 계획에 없는 단계의 결과는 넣을 수 없고, FAILED에는 `error`가 필요하다.
- state 128KB, digest 8KB 이하. 모든 칸을 허용 최대로 채운 state가 약 95KB다(`test_largest_allowed_state_fits`). 상한을 바꾸거나 칸을 더하면 이 테스트로 확인한다.
- digest·`args`·`patch`에는 JSON 값만 넣는다. Decimal·date·numpy 값은 tool이 숫자·문자열로 바꾸고(numpy는 `.item()`·`tolist()`), NaN·inf는 받지 않는다. 조건 하나당 되묻기 2회까지.
- 품목 5개, 국가 20개, 중복 불가. 기사 기간(`article_period`, 기사 결과의 `period`)은 일 단위, 나머지 기간은 월 단위다. 월 단위 기간의 end는 1일이며, 기존 채팅 코드가 MySQL에 저장한 말일 값은 1일로 바꿔 읽는다.
- 설명 4문장, 요약 200자, 제목 20자 이하. 5건 요약의 질문은 200자, 하위 질문 문장은 500자로 자른다(원문은 MySQL과 `input.question`에 있다).
- 차트의 `task_id`는 그 단계가 속한 하위 질문과 같아야 한다.
- 차트 모양: x축 값 수와 시리즈 값 수가 같아야 하고, donut은 시리즈 1개, grouped_bar는 2개다.

## 5. 모듈 배치

```
backend/app/agents/
  state.py        완료. 모델, Redis 키·TTL·상한 상수
  context.py      Redis 컨텍스트·세션 lock 읽기·쓰기
  graph.py        StateGraph 구성 + RedisSaver(ttl=CHECKPOINT_TTL) 연결, 워커에서 invoke/resume
  planner.py      하위 질문 분해 + intent·슬롯 추출(LLM 1회) + STEPS 표
  conditions.py   check_conditions, ask_user
  tools.py        search_news(자리만, RAG 담당 구현), query_trade_data, search_articles, build_chart, generate_report, query_market_model
  compose.py      Draft 작성(LLM 1회)
  verify.py       숫자 대조
  finalize.py     answer JSON·컨텍스트·제목 저장, 체크포인트 정리
```

그래프 노드와 연결

```
START → planner → dispatch ─┬→ check_conditions ─┬→ dispatch
                            │                    └→ ask_user ⏸ interrupt → (재개) → dispatch
                            ├→ sql / news / chart / report / ml → dispatch
                            └→ (남은 단계 없음) compose → verify ─┬→ compose (재생성 1회)
                                                               └→ finalize → END
```

`interrupt()`는 ask_user 노드에서만 쓴다. 노드는 재개되면 처음부터 다시 실행되므로, 되묻기 내용은 앞 노드(check)가 state에 써 두고 ask_user는 멈추고 병합하는 일만 한다.

### 5.1 플래너 단계 표 (하위 질문 하나 기준, step_id 앞에 `t1.` 같은 접두사를 붙인다)

| intent | 단계 (괄호는 depends_on) |
|---|---|
| STATISTICS · RANKING · MARKET_SHARE | check → sql(check) → chart(sql) |
| CAUSE | check → sql(check) → news(check) → chart(sql) |
| LOCAL_ISSUES | check → news(check). RAG 연결 전에는 news가 "지원 예정" 문구만 남김(D-43) |
| REPORT | check → report(check) |
| PROMISING_MARKET | check → ml(check) → chart(ml) |
| MARKET_TYPE · OUT_OF_SCOPE | 없음 (안내 문구) |

### 5.2 재사용할 기존 코드

| 필요 | 기존 코드 |
|---|---|
| intent·슬롯 추출 프롬프트 규칙 | `api/intent.py` `PROMPT`, `IntentSlots` |
| 국가·품목·기간 검증 | `api/chat_slots.py` `resolve`, `resolve_country`, `numeric_product` |
| HS 정확 일치·후보 | `api/reference.py` `_hs_search`, `chat_hs_search`, `api/hs_vectors.py` `candidates` |
| 관심사, 범위 검증, 관심 추가 | `api/accounts.py` `interests`, `validate_scope`, `save_interests` |
| 수출입 SQL 템플릿 | `api/trade.py` `trends`, `rankings`, `market_shares`, `bounds`, `resolve_period` |
| 결과표 | `api/results.py` `get_table` |
| 기사 RAG 계약·호출 자리 | `schemas/rag.py` `RagRequest`, `RagResponse`, `agents/tools.py` `search_news` |
| 리포트(PDF/DOCX) | `api/reports.py`, `api/files.py` `report_pdf`, `report_docx` |
| ML 유망시장 | `ml/two_stage.py` `query` |
| 작업 claim·완료·재큐 | `api/jobs.py` `claim`, `finish`, `requeue_chat`, `_save_chat` |
| 답변 저장·조건 갱신 | `api/chat.py` `save_answer` |
| 프론트 차트 | `frontend/src/components/charts.jsx` `LineChart`, `Donut` (bar·grouped_bar는 추가 필요) |

## 6. Redis

| 키 | 값 | TTL | 사라지면 |
|---|---|---|---|
| `chat:session:{sid}:context` | `SessionContext` | 7일, 대화방을 열 때 연장 | 조건만 MySQL에서 복구, 5건 요약은 비움 |
| `chat:session:{sid}:active` | 진행 중인 job_id | 30분 | lock이 풀림. 접수 때 MySQL 작업 상태도 확인 |
| 체크포인터 키 (라이브러리가 관리) | `AgentState` 체크포인트 | 30분, 읽을 때 연장 | 실행 중이면 MySQL `AgentInput`으로 처음부터, 되묻기 중이면 만료 |

- 이미지는 Redis 8 이상을 쓴다. RedisJSON·RediSearch가 기본 포함돼 있다.
- 로그인 세션 키는 로그인 Redis 이전을 결정할 때 추가한다.
- Redis에 두지 않는 것: 결과표 행, 기사 본문, 채팅 원문 전체, LLM 프롬프트, 작업 큐(MySQL claim 유지).

## 7. 프론트에 주는 answer JSON

기존 필드는 유지하고, 하위 질문(`tasks[]`, `tables[].task_id`, `charts[].task_id`)과 ChartSpec을 더한다. 자세한 내용은 [AGENT_API_CHANGES.md](AGENT_API_CHANGES.md)에 있다.

```
intent, answer_status(ANSWERED|PARTIAL|NO_DATA|OUT_OF_SCOPE), text, conditions,
tasks[], tables[], charts[], articles[], verification{status, sentence_checks, regeneration_count},
actions[], report_id, warnings[]
```

## 8. 구현 규칙

**반드시**
- 숫자(합계·증감률·비중·순위)는 SQL이나 코드가 계산한다. compose는 digest의 값만 옮긴다.
- HS·국가 코드는 사전·국가 표에 있는 값만 쓴다. LLM 출력은 질문 속 표현을 그대로 옮긴 것만 받는다.
- LLM SQL은 READ ONLY 세션 + sqlglot 검사(SELECT·허용 테이블·LIMIT) + 시간 상한으로 실행한다(D-41).
- 질문·이전 대화·직접 입력 답은 신뢰할 수 없는 입력이다. 프롬프트에 데이터로 넣고 지시로 따르지 않는다.
- tool 노드마다 MySQL `_execution`의 step·progress를 갱신한다.
- 외부 호출(LLM, Redis, Chroma, ML 파일)에는 타임아웃을 건다. 테스트는 외부 연결 없이 돌아야 한다. 그래프 테스트는 LangGraph의 메모리 체크포인터로 한다.

**금지**
- state·체크포인트·LLM 입력에 결과표 행 원본이나 기사 본문을 넣는 것
- 체크포인터 말고 실행 state를 따로 저장하는 것
- ask_user 말고 다른 노드에서 `interrupt()`를 부르는 것
- §9 미정 사항을 추측해서 구현하는 것. 임시값을 설정으로 빼고 `TODO(C-xx)`를 남긴다.

## 9. 미정·확인 필요

| ID | 내용 | 임시값 |
|---|---|---|
| C-20 | 로그인 세션의 Redis 이전 여부 | 현재 MySQL 유지 |
