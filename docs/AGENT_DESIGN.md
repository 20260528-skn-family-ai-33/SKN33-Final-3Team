# 채팅 에이전트 설계

기준 브랜치 `feat/50-agent-state` · 갱신 2026-10-30 · 그림: [무역통계 Agent State 설계](https://claude.ai/artifact/2SKYDkScxPRPjhxkxBabCb) (팀 공유 전에는 작성자만 열람) · API 변경: [AGENT_API_CHANGES.md](AGENT_API_CHANGES.md)

현재 `backend/app/api/chat.py`의 `execute`는 목업이다. LLM은 의도와 슬롯만 뽑고, 답변 문장은 고정 문구다. 이 문서는 그 목업을 대체할 실제 에이전트의 설계다. 에이전트·채팅·워커 코드를 고치기 전에 이 문서와 [state.py](../backend/app/agents/state.py)를 먼저 읽는다.

**원칙**
- 원본은 MySQL에 둔다. Redis에는 컨텍스트와 실행 중 상태만 둔다.
- state에는 ID와 작은 요약만 넣는다.
- LLM은 숫자를 계산하지 않고 HS 코드를 만들지 않는다.

## 1. 결정 사항

D-01~71은 별도 표기가 없으면 2026-10-29 결정이며, D-72~88은 2026-10-30 사용자 확인을 반영했다. 괄호 안 번호는 질문 목록의 번호다. **(임시)** 표시는 Redis 관련이라 바뀔 수 있는 결정이다. 구현할 때 값은 설정이나 상수로 빼서 바꾸기 쉽게 둔다.

### 저장·세션
| ID | 결정 |
|---|---|
| D-01 | **(임시)** Redis에는 대화방 컨텍스트(최근 검색 조건 + 최근 5건 요약), 세션 lock, LangGraph 체크포인트(실행 state)를 둔다. 로그인 세션은 따로 결정한다(현재 MySQL `api_auth_session` 유지). (Q-02) |
| D-02 | **(임시)** 컨텍스트는 Redis에만 둔다. MySQL `chat_session.context_json`에는 검색 조건만 저장한다. Redis 키가 사라지면 조건만 MySQL에서 복구하고 5건 요약은 비운 채 다시 쌓는다. (Q-03) |
| D-03 | **(임시)** 컨텍스트 TTL은 7일, 대화방을 열 때 연장한다. (Q-01) |
| D-04 | 되묻기 대기는 30분. 지나면 EXPIRED 처리하고 질문을 다시 받는다. |
| D-05 | 실행 상태는 `state.py`의 `AgentState`를 따른다. |
| D-06 | **(임시)** Redis 장애 시 채팅 접수를 막고 "잠시 후 다시 시도해 주세요"를 안내한다. (Q-50) |
| D-07 | **(임시)** Redis는 EC2 Docker로 띄운다. 체크포인터가 RedisJSON·RediSearch를 쓰므로 Redis 8 이상 이미지를 쓴다. AOF를 켜고(`redis.conf`의 `appendonly yes`·`appendfsync everysec`, `/data` 볼륨) 배포할 때 Redis 컨테이너는 내리지 않는다. Redis 설정은 `redis.conf` 한 곳에만 두고 실행 명령에는 설정 파일 경로만 넘긴다. 설정 파일은 `deploy/redis/docker-compose.yml`과 `deploy/redis/redis.conf`이다: `redis:8.2`, 메모리 상한 512MB(`noeviction`, 가득 차면 쓰기가 실패해 채팅 접수가 막힘), 컨테이너 1280MB(AOF 재작성 fork가 copy-on-write로 데이터의 최대 약 2배를 쓰므로 512MB의 2.5배), `127.0.0.1:6379`만 열고, `restart: unless-stopped`. 비밀번호는 실행 명령에 넣지 않고(`docker inspect`·`ps`에 보임) git에 올리지 않는 `deploy/redis/redis-auth.conf`(`requirepass` 한 줄, uid 999만 읽기)를 compose secret으로 넘겨 `include`한다. 비밀번호는 영문·숫자로만 만든다(공백·따옴표·`#`는 설정을 깨뜨림). API·워커는 루트 `.env`의 `API_REDIS_URL`(`redis://:<비밀번호>@127.0.0.1:6379/0`)로 접속하며, 같은 비밀번호를 두 곳에 둔다. 컨테이너 1280MB가 같은 EC2의 API·워커와 함께 들어가는지는 C-23. (Q-49, C-03, C-16, E-08) |
| D-08 | 채팅 질문 요청 제한은 두지 않는다. 기존 `api_rate_limit`은 로그인·HS 검색에만 적용된 현행을 유지한다. (Q-08, C-01) |

### 하네스
| ID | 결정 |
|---|---|
| D-09 | 에이전트는 LangGraph 그래프로 연결한다. **(임시)** 실행 state는 `langgraph-checkpoint-redis`의 `RedisSaver`로 저장한다. `thread_id`는 `job_id`, TTL은 `state.CHECKPOINT_TTL`(30분, 읽을 때 연장)이다. 그래프 구조는 §5. (C-03) |
| D-62 | state 상한은 256KB, digest는 8KB다. 칸 상한은 문장·문구 200자, 이름(차트 제목·x축 값·시리즈, 자체 검증, 출처, 숫자) 40자, 목록 10개, `args`·`patch` 512바이트, 관심사 품목 100개·국가 250개다. 코드가 만드는 표시용 글자·목록은 넘으면 자르고, compose가 쓰는 `Draft` 문장은 거부해 재시도한다. 대표적인 최대 크기 시나리오는 상한 이내다(`test_largest_allowed_state_fits`). 처음 정한 64KB는 하위 질문 3개의 digest·차트만으로 넘을 수 있어 바꿨다. (C-21) |

### 접수·되묻기
| ID | 결정 |
|---|---|
| D-10 | 질문은 4,000자 이하, 기존 `mask_text`로 마스킹한다. (Q-04) |
| D-11 | 같은 대화방에서 실행 중이면 409 `JOB_IN_PROGRESS`를 반환한다. 프론트는 생성 중에 입력창·전송 버튼을 비활성화한다. (Q-05) |
| D-12 | 되묻기 대기 중 입력은 되묻기 답(직접 입력)으로 처리한다. 해석할 수 없거나 무관하면 다시 되묻는다(D-15에 포함). 작은 글씨로 "다른 질문은 새 채팅을 열거나 이 질문을 중단하세요"를 표시하고, 중단 API를 둔다. (Q-06) |
| D-13 | 사용자당 동시 실행 수는 제한하지 않는다. (Q-07) |
| D-14 | 관심사는 질문 시점 값으로 고정한다. 되묻기 중 사용자가 바꿔 달라고 하면 "○○로 바꿀까요?"를 확인(`INTEREST_CHANGE`)한 뒤 **이번 질문의 조건만** 바꾼다. 저장된 관심사는 관심 추가 버튼으로만 바꾼다. (Q-11, C-11) |
| D-15 | 되묻기는 조건 하나(`Pending.condition_key`)당 2회까지다. 넘으면 해당 하위 질문만 NO_DATA로 끝내고 나머지는 계속한다(D-72). (Q-30) |
| D-16 | 되묻기 선택지는 최대 5개이고 "분류에서 직접 고르기" 링크를 함께 준다. (Q-29) |
| D-63 | 관심사 저장 개수는 제한하지 않는다. state(`AgentInput.interests`)에는 앞에서부터 품목 100개·국가 250개만 넣는다(D-62). 이 목록은 조건이 아니라 관심 적용 되묻기의 후보이며, 조건 상한을 넘으면 그 안에서 골라 받는다(D-64). (C-22) |

### 플래너·조건
| ID | 결정 |
|---|---|
| D-20 | 모델명은 환경변수로 바꿀 수 있게 한다. 플래너는 `API_INTENT_MODEL`, compose는 `API_COMPOSE_MODEL`이며 기본값은 둘 다 `gpt-4.1-mini`. (Q-15) |
| D-21 | LLM 대기 시간은 10초, 실패하면 1번 재시도한다. (Q-16) |
| D-22 | 후속 질문의 기본 조건은 생략하면 이어받고, 명시적인 전체 범위는 해제한다. 비교·순위·기사 기간은 D-73의 별도 규칙을 따른다. (Q-18) |
| D-23 | ML intent는 두 가지다. `PROMISING_MARKET`(유망시장)은 국가·품목 중 1개 이상이 필요하다. `MARKET_TYPE`(시장 유형)은 국가·품목이 모두 필요하지만, B 모델 시장 유형 분류가 나오기 전까지는 단계 없이 "지원 예정" 안내만 한다. 추가 예정 내용은 `backend/app/ml/ML_USAGE.md`에 적었다. (Q-13, C-05) |
| D-24 | 질문 하나에 품목은 5개, 국가는 20개까지다. (Q-20) |
| D-25 | 국가명이 모호하면 비슷한 국가로 "○○가 맞나요?"를 되묻는다(`COUNTRY`). 후보가 없으면 전체 국가명 재입력을 요청한다. (Q-21) |
| D-26 | 권역 표현(동남아, EU)은 미지원 안내만 한다. (Q-22) |
| D-27 | 기간을 말하지 않으면 수출입은 최근 12개월, 기사는 최근 3개월이다. (Q-23) |
| D-28 | 미공개 월을 물으면 범위 밖으로 처리하고 최신 공개 월을 안내한다. (Q-24) |
| D-29 | 비교 기간을 말하지 않으면 전년 동기, 지정하면 그 기간을 쓴다. 지정 기간용 SQL 템플릿을 보완한다(현재는 전년 동기 열만 있음). (Q-25, C-13) |
| D-30 | 관심 적용 되묻기는 대화방 첫 질문이면서 품목·국가가 둘 다 없을 때만 한다. "전체 범위"를 고르면 그 방에서 다시 묻지 않는다. (Q-26) |
| D-64 | 관심사가 조건 상한(품목 5개·국가 20개, D-24)을 넘으면 "관심 범위 적용" 대신 `INTEREST_SELECT`로 묻는다. 질문 시점 관심사(D-14, `input.interests`) 중에서 품목 5개·국가 20개까지 체크박스로 고르게 하고, "전체 범위" 선택지를 함께 준다. 상한 안이면 기존처럼 `INTEREST_CONTEXT`(적용/전체 범위)로 묻는다. 판단은 `Interests.fits_conditions()`다. |
| D-31 | 수출/수입을 말하지 않으면 수출로 보고, 답변에 "수출"을 명시한다. (Q-27) |
| D-32 | 순위 기준이 모호하면 `RANKING_GROUP` 선택지로 되묻는다. (Q-28) |
| D-33 | HS 품목은 SQL 정확 일치(등록 품목명·코드)일 때만 바로 확정한다. 임베딩 결과는 자동 확정하지 않고, 항상 상위 5 HS6 선택지 + 분류 직접 선택으로 되묻는다. 근거는 2026-10-29 시험(일상어 79개): 1위 정답 59%, 상위 5 안 75%, 1·2위 거리 차이 중앙값이 맞은 경우 0.010 / 틀린 경우 0.012로 구분되지 않음. (Q-19, C-04) |
| D-34 | 복합 질문은 플래너가 하위 질문(`Task`) 최대 3개로 나눠 한 답변으로 처리한다. 대상만 여러 개인 질문("베트남·미국 추이")은 하위 질문 하나에 조건을 여러 개 넣는다. 하위 질문은 순서대로 실행하고, 되묻기는 한 번에 하나씩 한다. 최근 검색 조건과 5건 요약의 조건은 첫 번째 하위 질문(주 의도) 기준이다. (Q-12, Q-14, C-02) |

### tool
| ID | 결정 |
|---|---|
| D-40 | 템플릿에 없는 질문은 LLM이 SQL을 만들고, 검증 실패 시 최대 2회 다시 만든다. (Q-31) |
| D-41 | 개발 중 LLM SQL은 `.env`에 등록된 관리자 DB 계정으로 실행한다. 대신 세션을 `READ ONLY` 트랜잭션으로 열고, sqlglot으로 SELECT만·허용 테이블만(D-65)·LIMIT 강제를 검사하고, 쿼리마다 최대 30초(`MAX_EXECUTION_TIME`)를 건다. 배포 전에 읽기 전용 계정으로 바꾼다(`TODO(D-41)`). (C-10, E-02) |
| D-65 | LLM SQL은 개인정보·시스템 테이블을 뺀 모든 테이블을 쓸 수 있다. 구현은 **허용 목록**으로 한다. 목록에 없는 테이블(새로 생긴 테이블, RDS에만 있는 테이블 포함)은 기본 차단이고, 분류한 뒤 목록에 더한다. 허용: `export_monthly`, `export_summary`, `import_monthly`, `import_summary`, `country`, `market_region`, `currency`, `fx_daily`, `hs_code`, `hs_code_correlation`, `un_data`, `keyword_category`, `keyword_topic`, `news_keyword_daily`, `news_product_daily`, 그리고 기사 계열 `article`, `article_tag`, `article_classification`(숨김 기사·`admin_meta_json`도 별도 제한 없이 허용, 2026-10-30 F-01). 단 기사 본문 `article.body_masked`는 sqlglot 검사에서 막는다. 결과표가 digest를 거쳐 LLM 입력이 되므로 §8의 "LLM 입력에 기사 본문 금지"를 지키기 위해서다. `SELECT *`·`article.*`처럼 본문이 딸려 오는 조회도 막는다(2026-10-30). 차단(개인정보): `users`, `api_user_profile`, `api_auth_session`, `user_consent`, `user_interest`, `user_alert`, `notification_setting`, `alert_event`, `chat_session`, `chat_message`, `report`, `report_subscription`, `api_keyword_block`, `api_report_file`, `api_snapshot`. 차단(시스템): `service_option`, `batch_run`, `async_job`, `api_audit_event`, `api_rate_limit`, `ml_model_version`, `model_run`. (E-01) |
| D-42 | 결과표는 1,000행, 순위는 기본 상위 10이다. (Q-32) |
| D-43 | 기사 검색 tool은 RAG 담당자가 제공할 `search_news(RagRequest) -> RagResponse`를 부른다. 지금은 자리만 있는 함수(`agents/tools.py`)가 `None`을 돌려준다. 그러면 tool은 NO_DATA와 함께 "기사 검색은 지원 예정입니다." 문구만 남긴다. 기사가 필요한 하위 질문(LOCAL_ISSUES)은 이 문구로 답하고, CAUSE는 통계만 답한 뒤 문구를 덧붙여 PARTIAL로 둔다. `blocked_topics`는 알림 차단용이라 채팅에서는 빈 값이다. (Q-33, C-15) |
| D-44 | RAG가 연결되면 기사는 확정된 품목명·국가명을 키워드로, CAUSE는 통계 기간 앞뒤 1개월, LOCAL_ISSUES는 명시한 기사 기간 또는 최근 3개월로 vector DB에서 검색한다(D-75). 급증 월은 쓰지 않는다. (Q-34, C-14) |
| D-48 | ML 유망시장은 워커가 서버에 둔 모델 파일(v1·v3 앙상블 joblib)과 피처 스냅샷으로 요청 때마다 예측한다. 전체 예측 모집단은 전용 SQL 테이블에 적재하지 않는다. 사용자에게 반환한 조회 결과는 기존 MySQL 결과표 저장소에 보관한다(D-88). 스냅샷은 최신 기준월만 담은 파일(`features_snapshot_py311_latest*.pkl`, 약 22MB)을 쓴다. 전체 스냅샷(약 750MB)은 쓰지 않는다. tool은 최신 스냅샷의 전체 예측 대상에서 `two_stage.predict` → `combine`(앙상블) → 순위를 계산한 뒤 요청한 국가·품목을 필터링한다(D-78). `two_stage.query`는 출력만 하므로 tool은 D-70의 함수를 쓴다. 모델과 스냅샷은 워커 메모리에 한 번 올려 재사용하고, 경로는 환경변수로 받는다. 워커에는 LightGBM·`libgomp1`이 필요하고, 패키지 버전은 모델 `runtime`(ML_USAGE.md)과 맞춘다. 예측 값은 스냅샷이나 모델 파일을 바꿀 때만 달라진다. (C-05, C-19) |
| D-45 | 재시도는 SQL 2회, 기사 1회, 차트·리포트·ML 0회다. (Q-36) |
| D-46 | tool 일부가 실패하면 나머지 결과로 PARTIAL 답변을 만들고 경고를 붙인다. (Q-37) |
| D-70 | ML tool용 함수 이름·형식(구현 전 지정, 구현할 때 이대로 만든다). `two_stage.load_market_bundle(model_paths: list[str], snapshot_path: str) -> dict`는 앙상블 모델과 최신 기준월 스냅샷을 읽어 워커가 한 번만 부른다. `two_stage.predict_markets(bundle, *, country_code: str \| None = None, hs6: str \| None = None, top: int = 10) -> pandas.DataFrame`은 `query`와 같은 규칙으로 예측한다: 국가만 → 유망 품목(`order` 순), 품목만 → 유망 국가(`product` 순), 둘 다 → 그 쌍 한 행. 열은 `anchor`, `country_code`, `hs6`, `growth_pct`, `exp_12m`, `country_rank`, `product_rank`이고, 예측 대상(최근 12개월 수출 $100K 이상)이 아니면 빈 표다. `query`는 이 함수를 불러 출력만 하도록 바꾼다. 두 함수는 ML 담당자가 구현하고, 에이전트 ML tool은 이 함수만 부른다. (E-06, F-03) |
| D-47 | 단계는 순차 실행한다. 단계별 소요 시간을 로그로 남기고, CAUSE 질문의 90%가 30초 안에 끝나지 않으면서 SQL과 기사 대기가 그 시간의 절반 이상이면 병렬로 바꾼다. (Q-38, C-08) |

### 답변·마무리·진행 표시
| ID | 결정 |
|---|---|
| D-50 | 결론은 하위 질문마다 1~2문장, 설명은 전체 4문장 이하로, 합니다체로 쓴다. compose는 하위 질문별 `Draft.conclusion[{task_id, sentences}]`과 `explanation` 문장 목록(문장마다 200자)으로 낸다(D-80). 기사는 문장 끝 [번호]와 기사 카드로 인용한다. (Q-39~41) |
| D-67 | compose가 재시도(D-21) 후에도 `Draft` 규칙(문장 200자, 결론 하위 질문마다 2문장, 설명 4문장)을 어기면, 표·차트만 담아 `answer_status=PARTIAL`로 답하고 경고 "요약 문장을 만들지 못했습니다."를 붙인다. 작업은 실패로 끝내지 않는다. (E-03) |
| D-69 | 고정 안내 문구. 시장 유형: "시장 유형 분석은 지원 예정입니다. 대신 이 국가·품목의 유망시장 순위를 볼 수 있습니다." 범위 밖: "수출입 통계·시장 비중·유망시장에 관한 질문을 입력해 주세요." 기사: "기사 검색은 지원 예정입니다."(`tools.NEWS_PENDING_MESSAGE`). (E-05) |
| D-51 | 숫자 대조는 화면 표시 단위의 반올림 차이만 허용한다. 대조 결과는 문장 번호(`Draft.sentences()` 순서)로 남긴다. 재생성 후에도 틀린 문장은 "확인 필요"로 표시한다. (Q-42, 43) |
| D-52 | 5건 요약은 compose가 같은 LLM 호출에서 `Draft.turn_summary`로 쓴다. 200자 이하이고 양식은 `{조건 요약} {질문 요지} → {핵심 결과 1개}`다. 숫자는 답변에 나온 것만 쓰고, 되묻기 과정은 넣지 않는다. 정상·부분 답변만 넣는다. (Q-09, 10, C-06) |
| D-53 | 최근 검색 조건은 정상·부분 답변일 때만 갱신한다. (Q-44) |
| D-54 | 대화방 제목은 첫 답변에서만 compose가 검색 조건을 넣어 20자 이하로 쓴다. (Q-45, C-12) |
| D-55 | answer actions는 유지한다. 리포트 저장은 PDF/DOCX를 고르게 한다(기존 `formats`). 관심 추가는 현재 조건의 국가·품목을 체크박스로 골라 추가한다(기존 `POST /users/me/interests/items`). (Q-46) |
| D-56 | 2초마다 폴링한다. 단계별 문구, 진행률 바, 경과 초를 보여 주고, 되묻기에 답하면 경과 초를 0부터 다시 센다(`round_started_at`). (Q-47) |
| D-57 | 한 라운드의 실제 에이전트 실행 상한은 180초(`RUN_TIMEOUT_SECONDS`)다. 리포트·큐·장애 복구 대기는 제외하며 이미 사용한 실행 예산은 유지한다(D-85). (Q-48) |
| D-58 | 비동기 구조를 유지한다. API가 등록하고, 워커가 실행하고, 프론트가 폴링한다. 기존 answer 필드는 유지하고 추가만 한다(§7). |
| D-59 | 차트는 `state.ChartSpec`이다. 유형 4개(`line`·`bar`·`grouped_bar`·`donut`), x축 값 60개, 시리즈 5개 이하다. 제목·x축 값·시리즈 이름은 40자 이하다. 품목명은 x축 값이든 품목별 시리즈 이름이든 `state.shorten_names`로 앞 20자 + "…" + 끝부분을 이어 40자 안에 맞춘다(HS4 품목명은 최대 347자, D-68). 한 차트 안에서 x축 값과 시리즈 이름은 겹치지 않아야 하며 `ChartSpec`이 검사한다. 그 밖의 글자는 40자에서 자른다. donut은 시리즈 1개, grouped_bar는 2개. `charts.jsx` props와는 모양이 달라 프론트가 변환한다(LineChart는 `yTicks`·색 추가, Donut은 `[label, 비율, 색]`, 값 없는 칸은 선을 끊음). (Q-35, C-07) |
| D-68 | 품목명 표시: 40자를 넘으면 앞 20자 + "…" + 끝부분으로 40자를 채운다. 예: "전동축[캠샤프트(cam shaft)와…ersal joint)를 포함한다]". 한 차트에서 줄인 이름이 겹치면(가운데만 다른 품목명) 이름을 더 줄이고 "(HS 코드)"를 붙여 구분한다. 같은 코드가 판본만 달리 함께 있으면 "(8483 HS2017)"처럼 판본까지 붙인다(`state.shorten_names`). 마우스를 올리면 전체 이름을 보여 준다. 전체 이름은 state에 넣지 않는다(품목명 최대 347자 × 60개 × 3차트면 state 상한을 넘음). finalize가 answer를 만들 때 `result_id`의 결과표에서 읽어 `charts[].full_labels`로 붙인다. (E-04, F-02) |
| D-60 | API 변경은 [AGENT_API_CHANGES.md](AGENT_API_CHANGES.md)를 따른다: 되묻기 직접 입력, 중단 API, 진행률, 하위 질문. (C-09) |
| D-61 | 골든셋 평가: 질문 파일은 `backend/tests/golden/chat_golden.jsonl`이고 나중에 LLM으로 만든다. 평가 스크립트 결과는 `outputs/eval/<날짜>/runs.jsonl` + `summary.md`. LLM 원문은 평가 실행에서만 남긴다. 운영 답변에는 `result_json.trace`(intent, 계획, 단계별 시간, 모델명, 프롬프트 버전)만 남긴다. 채점 기준은 4개: intent 일치, 조건(HS·국가·기간·수출입) 일치, 숫자 일치(D-51 허용 오차), 되묻기 종류 일치. (Q-51, C-17, C-18) |

### 구현 순서
| ID | 결정 |
|---|---|
| D-71 | ① Redis 컨텍스트·lock(`context.py`) ② 그래프 뼈대 planner → dispatch → check → compose → finalize(메모리 체크포인터로 테스트) ③ SQL tool ④ 되묻기(ask_user) ⑤ 차트 ⑥ ML ⑦ 리포트. 기존 목업 `chat.execute`는 그래프가 붙을 때까지 그대로 둔다. 로그인 세션 Redis 이전(C-20)은 채팅이 다 붙은 뒤 결정한다. (E-07, E-09) |

## 2. 구성 요소

| 구성 요소 | 하는 일 | 위치 |
|---|---|---|
| API 서버 | 질문 접수, 작업 등록, 폴링 응답, 되묻기 답·중단 접수 | `backend/app/api/chat.py`, `routes.py` |
| 워커 | 작업을 claim해 그래프를 실행·재개한다. lease 10분 | `backend/app/api/worker.py`, `jobs.py` |
| 에이전트 그래프 | LangGraph. 플래너 → (하위 질문별) 조건 확인 → tool → compose → verify → finalize | `backend/app/agents/` |
| Redis 8 **(임시)** | 컨텍스트, 세션 lock, 체크포인트 (§6) | 신규. EC2 Docker, `deploy/redis/docker-compose.yml` |
| MySQL | 질문·답변·작업 상태(`_execution`)·AgentInput·검색 조건·결과표·리포트·HS 사전·적재 로그 | 기존 |
| Chroma | 기사 벡터, HS 품목명 벡터 | 기존 |
| ML | 유망시장 조회. 서버에 배포한 모델 파일을 워커가 읽음 | `backend/app/ml/two_stage.py` |

새 의존성: `langgraph`, `langgraph-checkpoint-redis`(→ `redis`, `redisvl`), `sqlglot`, 워커에 `lightgbm`·`joblib`(모델 `runtime` 버전과 맞춤).

## 3. 실행 흐름

### A. 질문 접수 (API 서버)
1. `POST /chat/sessions/{sid}/messages`로 질문이 들어온다. Redis에 연결되지 않으면 503 `CHAT_UNAVAILABLE`을 반환한다.
2. Redis `GET chat:session:{sid}:active`로 진행 중인 작업을 확인한다.
   - 동일 멱등키 재전송은 먼저 기존 작업을 반환한다(D-77). 그 밖의 RUNNING·QUEUED·WAITING_REPORT 작업이 있으면 409를 반환한다.
   - NEEDS_INPUT 작업이 있으면 이 글을 그 작업의 직접 입력 답으로 넘기고 C-17로 간다.
3. 멱등키를 확인하고 USER 메시지를 `_execution.status=QUEUED`로 저장한다. `SET active <job_id> NX EX 1800`.
4. Redis `GET chat:session:{sid}:context`로 컨텍스트를 읽는다. 없으면 MySQL `context_json.conditions`로 조건만 채운다.
5. `AgentInput`(질문, 컨텍스트, 관심사, 접수 시각)을 만들어 MySQL `_execution.payload_json.input`에 저장한다. 체크포인트가 사라져도 여기서 다시 시작할 수 있다.
6. `job_id`를 반환한다. 프론트는 입력창을 잠근다.

### B. 워커 실행 (그래프)
7. claim하면 `RUNNING`이 된다. 재개 방식:
   - 체크포인트(`thread_id=job_id`)가 있으면 이어서 실행한다. 되묻기 답이면 `Command(resume=답)`, 워커가 죽은 뒤면 `None`을 넣는다.
   - 체크포인트가 없으면 `AgentState(input=…)`로 처음 실행한다.
8. `round_started_at`이 비어 있으면 지금 시각을 넣는다. 180초 실행 상한은 벽시계 차이가 아니라 D-85의 누적 예산으로 판정한다. 노드를 시작할 때마다 MySQL 작업 상태가 CANCELLED인지 확인하고, 중단됐으면 멈춘다.
9. **planner 노드**: LLM 1회로 질문을 하위 질문 1~3개로 나눈다. 하위 질문마다 intent와 슬롯을 뽑고, 단계 표(§5.1)로 `plan.tasks`를 만든다.
10. **dispatch 노드**: 먼저 `steps_finished()`를 부른다. 이 함수는 실패에 막힌 단계를 SKIPPED로 정리한 뒤 판단한다. True면 compose로 가고, 아니면 `ready_steps()`의 첫 단계를 골라 그 tool 노드로 보낸다. `ready_steps()`가 비었다고 끝난 것이 아니다. 리포트·되묻기 대기 중이거나 PENDING·RUNNING 단계가 남으면 compose로 가지 않는다(D-76).
11. **check_conditions 노드**(하위 질문별): 슬롯을 HS 사전·국가 표·적재 로그와 대조해 `task.conditions`·`applied_defaults`와 공통 `data_meta`를 채운다.
    - 품목은 SQL 정확 일치만 확정하고, 그 밖은 상위 5로 되묻는다(D-33).
    - 국가는 비슷한 국가로 되묻고, 후보가 없으면 재입력을 요청한다(D-25).
    - ML 필수 조건을 확인한다(D-23). 유망시장 질문의 품목이 HS4면 그 아래 HS6로 되묻는다(D-74).
    - 빈 조건은 최근 조건 → (첫 질문이면) 관심 되묻기 → 기본값 순서로 채운다. 관심사가 품목 5개·국가 20개를 넘으면 관심 되묻기는 골라 받는 `INTEREST_SELECT`다(D-64).
    - 되묻기가 필요하면 `pending`을 채우고 `ask_counts`를 올린 뒤 `status=NEEDS_INPUT`으로 바꾼다. 그 조건을 이미 2회 물었으면 `ask_counts`를 올리지 않고 해당 하위 질문만 NO_DATA로 끝내며 dispatch로 간다(D-72). 더 물을 수 있을 때만 **ask_user 노드**로 간다.
    - 조건을 확정하지 못해 check 단계를 FAILED로 두면 `skip_blocked()`를 불러 그 하위 질문의 뒤 단계를 SKIPPED로 바꾼다.
12. **ask_user 노드**: `interrupt(state.pending)`로 멈춘다. 체크포인트에는 11번이 쓴 `pending`이 남는다. 워커는 MySQL `_execution`을 NEEDS_INPUT + clarification으로 바꾸고, `active` TTL을 30분으로 둔 뒤 끝낸다.
    재개되면 이 노드가 답을 받아 그 하위 질문의 조건에 병합한다. 그리고 `pending=None`, `status=RUNNING`, `round_started_at=지금`으로 바꾸고, 그 하위 질문의 check 단계를 PENDING으로 되돌린 뒤 dispatch로 간다. 직접 입력이면 `pending.message` 맥락으로 해석하고, 해석에 실패하면 check가 다시 묻는다.
13. **tool 노드**(sql, news, chart, report, ml): report는 D-76에 따라 완료까지 WAITING_REPORT로 대기하고 재개한다. 나머지는 실행 후 `results[step_id]`와 단계 상태를 쓴다. MySQL `_execution`의 step·progress를 갱신하고 dispatch로 돌아간다. 실패하면 재시도 한도까지 다시 하고, 그래도 실패하면 FAILED로 두고 `skip_blocked()`를 불러, 그 단계에 직접·간접으로 의존하는 단계를 SKIPPED로 바꾼다. `ready_steps()`는 의존 단계가 모두 DONE인 단계만 돌려준다.
14. **compose 노드**: LLM 1회로 `Draft`(결론 문장 목록, 설명 문장 목록, `turn_summary`, 첫 답변이면 `title`)를 쓴다. 문장은 LLM이 나눠 준다. 숫자는 digest 값만 쓴다. `MARKET_TYPE`·`OUT_OF_SCOPE` 하위 질문은 고정 안내 문구를 쓴다.
15. **verify 노드**: `Draft.sentences()`(결론 다음 설명)의 문장마다 숫자를 결과표와 대조해 문장 번호(`SentenceCheck.index`)로 남기고, 1회 재생성한 뒤 그래도 틀리면 "확인 필요"를 붙인다.
16. **finalize 노드**:
    - answer JSON을 저장한다.
    - D-72 조건을 만족할 때만 MySQL `context_json`에 주 하위 질문의 conditions를 쓴다.
    - D-72 조건을 만족할 때만 Redis context에 conditions와 검증된 `TurnSummary`를 쓴다(첫 하위 질문 기준).
    - 첫 답변이고 유효한 제목이 있을 때만 제목을 갱신한다.
    - 같은 MySQL 트랜잭션에서 `_execution=SUCCEEDED`를 확정한다. Redis 정리와 체크포인트 삭제는 그래프 실행 반환 후 수행한다(D-77).

### C. 되묻기 답·중단 (API 서버)
17. 선택지·직접 입력(API-38 또는 A-2)이 들어오면:
    - 체크포인트의 `pending`을 `graph.get_state`로 읽는다. 없거나 `asked_at`에서 30분이 지났으면 409 `CLARIFICATION_EXPIRED`를 반환하고 EXPIRED로 처리한다.
    - `clarification_id`를 비교하고, 답을 `_execution.payload_json.resume`에 저장한 뒤 QUEUED로 바꾼다. 워커가 7번에서 `Command(resume=답)`으로 재개한다.
18. 중단(API-131)이 들어오면 `_execution=CANCELLED`로 바꾸고 해당 job의 active만 해제한다. 워커는 다음 노드 진입 및 저장 직전에 중단·소유권을 확인한다. 체크포인트는 실행 종료 후 정리한다(D-77). 리포트 대기 중 중단은 D-76을 따른다.

### 장애
- **워커가 죽었을 때**: lease 10분이 지나면 다른 워커가 claim한다. D-85의 복구 횟수·남은 예산을 확인하고 7번에서 `None`으로 재개한다. 10분의 복구 대기를 실행 시간에 더하지 않는다.
- **Redis 데이터가 사라졌을 때**:
  - 실행 중이던 작업은 MySQL의 `AgentInput`으로 처음부터 다시 실행한다.
  - 되묻기 대기 중이던 작업은 만료로 처리한다.
  - 컨텍스트는 조건만 복구된다.
- **Redis에 연결되지 않을 때**: 채팅 접수를 막는다(D-06).

## 4. state

원본은 [state.py](../backend/app/agents/state.py)다. 필드를 바꾸면 `backend/tests/test_agent_state.py`도 함께 고친다. LangGraph state 스키마로 이 `AgentState`(Pydantic)를 그대로 쓴다. 검증은 LangGraph가 노드 입력을 만들 때 돌고 체크포인터는 검증하지 않으므로, finalize는 저장 전에 `AgentState.model_validate(state.model_dump())`로 다시 검증한다. 인스턴스를 그대로 넘기면 제자리에서 바꾼 안쪽 칸은 다시 보지 않는다.

| 칸 | 쓰는 노드 | 읽는 노드 |
|---|---|---|
| `input` (질문·컨텍스트·관심사) | 최초 실행 | planner, check, compose, finalize |
| `status` | 워커 시작, check(NEEDS_INPUT), ask_user, finalize | 전부 |
| `plan.tasks[]` (하위 질문: intent·질문·슬롯·단계·조건·기본값·결과 상태) | planner(슬롯은 `PlannerSlots`에서 옮김, D-73). 조건은 check, 단계 상태는 tool 노드, `answer_status`는 그 Task를 끝낸 노드, `slots.latest_prediction_confirmed`는 ask_user | dispatch, tool, compose, finalize || `data_meta` | check | tool, compose |
| `pending`, `ask_counts` | check (ask_user가 비움) | ask_user, API(되묻기 답 검증) |
| `round_started_at` | 워커 시작, ask_user | 라운드 경과 시간 표시. 대기·장애 복구로 바꾸지 않음 |
| `run_budget` | 워커 실행 경계 | D-85 실행 예산·장애 복구 횟수. 복구 시 MySQL 실행 메타데이터 우선 |
| `report_wait` | generate_report(WAITING_REPORT로 바꿀 때), 리포트 재개 노드가 비움 | 완료 확인기, 리포트 재개 노드 (D-76) |
| `results` (ID + digest + ChartSpec + ChartSelector) | tool | compose(digest), verify(result_id), finalize(chart_selectors, D-82) |
| `draft` (문장·요약·제목) | compose | verify, finalize |
| `verification` | verify | finalize |
| `warnings` | check, tool | finalize |

모델이 강제하는 규칙
- NEEDS_INPUT이면 `pending`이 반드시 있고, 아니면 없어야 한다. `pending.task_id`는 계획에 있어야 한다.
- 하위 질문은 1~3개다. 각 하위 질문은 `check_conditions`로 시작하고, 같은 하위 질문의 앞 단계에만 의존할 수 있다. `OUT_OF_SCOPE`·`MARKET_TYPE`은 단계가 없다.
- `step_id`는 계획 전체에서 고유하다(예: `t1.sql`). 계획에 없는 단계의 결과는 넣을 수 없고, FAILED에는 `error`가 필요하다.
- state 256KB, digest 8KB 이하. 대표적인 최대 크기 시나리오가 상한 이내다(`test_largest_allowed_state_fits`). 상한을 바꾸거나 칸을 더하면 이 테스트로 확인한다.
- 글자 칸은 모두 길이 상한이 있다(D-62). 문장·문구 200자, 이름(차트 제목·x축 값·시리즈, 자체 검증, 출처, 숫자) 40자, 목록 10개, `args`·`patch` 512바이트, 관심사 품목 100개·국가 250개.
  - 코드가 만드는 표시용 글자·목록(되묻기 문구·선택지·품목 표현, 경고, 오류, 적용 조건, 차트 글자, 관심사)은 넘으면 앞에서부터 자른다. HS4 품목명이 최대 347자이고 관심사는 저장 개수 제한이 없기 때문이다(D-63).
  - compose가 쓰는 `Draft` 문장은 자르지 않고 거부한다. 결론은 하위 질문마다 2문장까지(최대 6문장), 설명은 4문장, 문장마다 200자다. 넘으면 compose가 재시도(D-21)한다.
- digest·`args`·`patch`에는 JSON 값만 넣는다. Decimal·date·numpy 값은 tool이 숫자·문자열로 바꾸고(numpy는 `.item()`·`tolist()`), NaN·inf는 받지 않는다. 조건 하나당 되묻기 2회까지.
- 품목 5개, 국가 20개, 중복 불가. 기사 기간(`Conditions.article_period`, `ToolResult.article_period`)은 일 단위, 나머지 기간은 월 단위다. 두 종류를 한 칸에 섞지 않는다(1일~1일 기사 기간이 JSON 왕복에서 월 기간으로 바뀜). 월 단위 기간의 end는 1일이며, 기존 채팅 코드가 MySQL에 저장한 말일 값은 1일로 바꿔 읽는다.
- 설명 4문장, 요약 200자, 제목 20자 이하. 5건 요약의 질문은 200자, 하위 질문 문장은 500자로 자른다(원문은 MySQL과 `input.question`에 있다). 문장 대조는 문장 원문 대신 `Draft.sentences()`의 번호를 남긴다.
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
                            ├→ sql / news / chart / ml → dispatch
                            ├→ report → report_ready ⏸ 정적 중단점 → (완료 후 재개) → dispatch
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
| ML 유망시장 | `ml/two_stage.py` `load_market_bundle`, `predict_markets` (D-70, 구현 예정) |
| 품목명 줄이기 | `agents/state.py` `shorten_names`(차트 하나의 라벨 목록), `shorten_name`(이름 하나) |
| 작업 claim·완료·재큐 | `api/jobs.py` `claim`, `finish`, `requeue_chat`, `_save_chat` |
| 답변 저장·조건 갱신 | `api/chat.py` `save_answer` |
| 프론트 차트 | `frontend/src/components/charts.jsx` `LineChart`, `Donut` (bar·grouped_bar는 추가 필요) |

## 6. Redis (임시)

이 절의 키 이름·TTL·용량은 모두 임시값이며 바뀔 수 있다. 값은 `state.py` 상수와 `deploy/redis/docker-compose.yml`에서 바꾼다.

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

- `text`는 finalize가 `Draft.sentences()`를 순서대로 이어 만든다.
- `charts[].full_labels`는 finalize가 결과표에서 채우는 줄이기 전 이름이다(D-68). state의 `ChartSpec`에는 없다.
- `verification.sentence_checks`는 `[{index, sentence, numbers, status}]`다. state에는 문장 번호만 있으므로 finalize가 `sentence`를 draft에서 채운다.

## 8. 구현 규칙

**반드시**
- 숫자(합계·증감률·비중·순위)는 SQL이나 코드가 계산한다. compose는 digest의 값만 옮긴다.
- HS·국가 코드는 사전·국가 표에 있는 값만 쓴다. LLM 출력은 질문 속 표현을 그대로 옮긴 것만 받는다.
- LLM SQL은 READ ONLY 세션 + sqlglot 검사(SELECT·허용 테이블·`article.body_masked` 차단·LIMIT) + 시간 상한으로 실행한다(D-41, D-65).
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
| C-20 | 로그인 세션의 Redis 이전 여부. 채팅이 다 붙은 뒤 결정(D-71) | 현재 MySQL 유지 |
| C-24 | 리포트 대기의 별도 전체 시간 상한 | 별도 상한 없음. 리포트의 기존 재시도·실패 정책과 사용자 중단 적용(D-76). 추가 상한 도입은 별도 결정 |
| C-23 | EC2 메모리 배분. Redis 컨테이너 1280MB와 같은 서버의 API·워커(ML 모델·스냅샷을 메모리에 올림, D-48)가 인스턴스 메모리 안에 들어가는지 | Redis 1280MB (`deploy/redis/docker-compose.yml` `TODO(C-23)`) |
| C-26 | "최근 N개월" 상한과 슬롯 상한 초과 안내 문구(D-73). 적재 데이터 첫 달과 맞는지 | 240개월, `PlannerSlots.limit_errors()` 문구 (`state.py` `TODO(C-26)`) |


## 10. 보완 결정 (2026-10-30)

### D-72. 복합 질문의 부분 성공과 컨텍스트
- 조건 확인 실패·되묻기 한도 초과는 해당 Task만 `answer_status=NO_DATA`로 끝내고 check를 FAILED로 둔다. `skip_blocked()` 후 다음 Task를 처리한다. 다른 Task의 결과를 버리지 않는다.
- Task.answer_status는 None(진행 중) 또는 ANSWERED/PARTIAL/NO_DATA/OUT_OF_SCOPE다. 전체는 모두 정상일 때 ANSWERED, 사용 가능한 결과와 실패·미지원이 섞이면 PARTIAL, 사용 가능한 결과가 없으면 NO_DATA다. 모두 범위 밖이면 OUT_OF_SCOPE다. 시스템 장애로 답변 자체를 저장할 수 없을 때 실행 상태 FAILED로 끝낸다. 계획 전체가 제한을 초과해 시작할 수 없는 경우도 D-86의 명시적 오류로 종료한다.
- 사용 가능한 결과가 없는 compose 실패는 D-67의 PARTIAL 예외가 아니라 NO_DATA다.
- 첫 Task가 ANSWERED/PARTIAL이고 확정 조건이 있을 때만 최근 조건과 5건 요약을 갱신한다. 첫 Task 실패 시 두 번째 Task의 조건으로 대체하지 않는다. 제목·고정 안내만 있는 답변도 이전 조건을 지우지 않는다.
- compose는 각 Task의 결과와 실패 안내를 빠짐없이 포함한다. 최종 API tasks[]에 answer_status를 더한다.
- Task.answer_status는 그 Task에 PENDING·RUNNING 단계가 없을 때만 정한다. 실행 상태가 SUCCEEDED면 모든 Task에 answer_status가 있어야 한다. 둘 다 `state.py`가 검사한다.

### D-73. 미확정 슬롯과 상속
- Task.slots(TaskSlots)는 플래너의 원문 표현을 보관한다. products/countries의 None은 생략, []는 명시적인 전체 범위, 값이 있는 목록은 교체다. 질문에 없는 코드·품목을 추론해서 넣지 않는다. 확정된 값은 Task.conditions에만 넣는다.
- 품목·국가·수출입 방향·조회 기간은 생략하면 input.context에서 이어받는다. 복합 질문의 각 Task는 동일 접수 시점 컨텍스트를 기준으로 하며 앞 Task의 처리 결과를 암묵적으로 상속하지 않는다. 플래너는 질문 속 공통 조건을 관련 Task에 명시한다.
- 비교 요청은 comparison_requested로 구분한다. 비교 요청이 없으면 이전 compare_period를 제거한다. 비교 요청이 있으면 명시한 기간, 명시적인 이전 비교 지칭이면 이전 비교 기간, 그 밖에는 전년 동기를 쓴다. 이전 비교 지칭도 플래너가 해당 기간을 슬롯에 넣어 명시한다.
- ranking_group은 RANKING/MARKET_SHARE에서만 사용한다. 이전 순위 기준을 무조건 상속하지 않고 이번 질문으로 결정하며 모호하면 RANKING_GROUP으로 묻는다. article_period는 D-75를 따른다.
- 원문 표현은 각각 120자 이하이며 초과 시 조용히 잘라 검색하지 않고 조건을 더 짧게 입력하도록 안내한다. 품목·국가 개수 초과도 일부만 골라 실행하지 않는다. 절대 기간과 recent_months는 동시에 받지 않는다. 비교 기간은 comparison_requested=True일 때만 받는다.
- recent_months는 240개월까지다(`MAX_RECENT_MONTHS`, 임시값 C-26). 넘는 값은 날짜 계산이 깨지므로 받지 않고 안내한다.
- 플래너는 LLM 출력을 `PlannerSlots`로 받는다. 이 모델은 개수·길이 상한을 넘어도 받고, `limit_errors()`가 안내 문구를 돌려준다. 문구가 있으면 그 Task는 단계 없이 `answer_status=NO_DATA`로 두고 문구를 `warnings`에 넣으며, 나머지 Task는 그대로 실행한다. 문구가 없을 때만 상한을 거는 `TaskSlots`로 옮겨 저장한다. 계획 전체를 검증 오류로 실패시키지 않기 위해서다.

### D-74. 유망시장 지원 범위
- 현재 모델은 수출·HS6·최신 스냅샷 기준이다. IMPORT는 미지원으로 안내하며 수출로 자동 변경하지 않는다. HS4는 하위 HS6를 선택하도록 HS_ITEM으로 묻는다.
  - 선택지는 그 HS4 아래 예측 대상 HS6(최근 12개월 수출 $100K 이상, D-70) 중 최근 12개월 수출액이 큰 5개이고, D-16처럼 "분류에서 직접 고르기"를 함께 준다. `condition_key` 예: `t1:ml_hs6:3304`.
  - 예측 대상 HS6가 하나뿐이면 묻지 않고 그 HS6로 정한 뒤 `applied_defaults`에 적는다. 하나도 없으면 묻지 않고 "이 품목은 유망시장 예측 대상이 아닙니다."로 해당 Task를 NO_DATA로 끝낸다.
- 사용자가 과거 기간을 명시하면 MODEL_PERIOD로 최신 기준 예측을 볼지 확인한다. 거절하면 해당 Task는 NO_DATA, 동의하면 slots.latest_prediction_confirmed=True를 기록한다. 플래너는 이 값을 True로 생성할 수 없고 ask_user만 사용자 답에 따라 바꾼다. 플래너 출력 모델 `PlannerSlots`에는 이 칸이 없어서, LLM이 넣으면 검증에서 거부된다. 단순 상속 기간 때문에 매번 확인하지 않는다.
- 결과에 스냅샷 기준월·예측 대상 기간·모델 버전을 표시한다. exp_12m은 과거 12개월 수출액이며 미래 예측액으로 표시하지 않는다. 모델에서 제공하는 예측 기간 정의는 ML 담당자와 함수 계약에 명시한다.
- 복수 입력은 국가만이면 국가별, 품목만이면 HS6별, 둘 다면 요청된 국가×품목 조합별로 predict_markets를 호출한다(최대 100쌍). 결과를 국가·품목별로 구분하고 임의로 순위를 합치지 않는다. 동일 bundle을 재사용하고 전체 실행 시간 제한을 지킨다. 일부 조합의 데이터 없음·실패는 PARTIAL로 처리한다.
  - 국가만이면 국가마다 유망 품목 상위 10, 품목만이면 품목마다 유망 국가 상위 10이다. 결과는 한 결과표에 `country_code`·`hs6`로 대상을 구분해 담고, `anchor`는 기준월로만 사용한다(D-79)(최대 200행, D-42 안). 예측 대상이 아닌 대상은 경고 "○○은 예측 대상이 아닙니다."를 붙인다. 대상이 여러 개일 때는 D-82의 대상 선택 차트를 쓴다(C-25 결정 완료).

### D-75. 기사 기간
- CAUSE는 확정된 통계 기간 시작월의 전월 1일부터 종료월의 다음 달 말일까지다. 통계 결과와 기사 시점의 연관성을 설명하되 원인으로 확정하지 않는다.
- LOCAL_ISSUES는 사용자가 지정한 기사 기간을 우선하고, 없으면 접수 날짜 기준 최근 3개월(종료일 포함)이다. 달 이동 시 해당 일이 없으면 그달 말일을 쓴다. 시간대는 Asia/Seoul이다.
- Conditions.article_period와 ToolResult.article_period에 실제 검색 기간을 남긴다. 적재 최신일은 별도로 표시하고 데이터 지연을 숨기기 위해 요청 기간을 조용히 바꾸지 않는다.

### D-76. 리포트가 완성된 뒤 채팅 완료
- generate_report는 D-81의 조건·형식 확인을 마친 뒤 report 작업을 멱등하게 접수하고 ReportWait(step_id, report_id, started_at)를 기록한다. 해당 Step은 RUNNING, AgentState와 MySQL 실행 상태는 WAITING_REPORT다. 이 단계에서는 compose/finalize로 진행하거나 성공 답변을 반환하지 않는다.
- 워커를 점유한 채 대기하지 않는다. report 완료 확인 직전의 전용 재개 지점에 체크포인트를 남기고 실행권을 반환한다. graph 구현은 이 지점에 정적 중단점(동적 interrupt()는 여전히 ask_user 전용)을 두고, 완료 감지 후 해당 체크포인트를 None으로 재개하는 통합 테스트를 작성한다. 일반 QUEUED claim과 분리하여 단일 워커에서도 report 작업을 처리할 수 있어야 한다.
- 완료 확인기는 MySQL report가 READY면 채팅을 재개 가능하게 하고, 재개 노드가 report_wait를 비우고 결과를 저장한 뒤 Step을 DONE으로 만든다. FAILED면 해당 Step을 FAILED로 두고 D-72에 따라 나머지 질문을 처리한다. report의 DELAYED/QUEUED/RUNNING은 완료가 아니다. 채팅 노드에서 report 생성 재시도를 중복 수행하지 않는다.
- WAITING_REPORT 동안 같은 대화방은 잠겨 있고 프론트는 2초 폴링과 '리포트 만드는 중' 표시를 유지한다. 진행률은 생성 완료 전 100%로 만들지 않는다. 대기 확인기는 job 소유권을 확인하며 active·체크포인트 TTL을 연장한다. 확인기 중단이나 Redis 유실 시에도 MySQL의 job+step에 연결한 report_id로 기존 리포트를 찾아 재사용한다.
- 리포트 대기는 실행 예산에서 제외한다. 재개 시 round_started_at을 이동하지 않고 run_budget의 기존 차감 시간을 유지한다(D-85). 화면의 전체 경과 시간은 MySQL 라운드 접수 시각으로 계산한다. 리포트 대기의 별도 전체 상한은 TODO(C-24), state.REPORT_WAIT_TIMEOUT_SECONDS는 임시로 None이며 기존 리포트 재시도 한도·실패 상태를 따른다.
- 사용자가 채팅을 중단하면 대기를 끝내고 답변을 저장하지 않는다. 이미 접수한 리포트는 기존 리포트 작업 정책으로 처리한다. 리포트 자체의 취소는 현재 지원 범위 밖이며 API 확인 문구로 이를 알린다.

### D-77. 중복·장애·검증의 구현 계약
- API는 소유권 확인 후 멱등키를 먼저 확인한다. 같은 키·같은 내용은 기존 job, 다른 내용은 IDEMPOTENCY_CONFLICT다. 그 뒤 새 요청의 실행 중 여부를 판단한다. API-36 직접 답변도 client_message_id로, API-38은 clarification_id와 정규화된 답으로 중복을 판정한다. 이미 수락한 동일 답은 기존 job, 다른 답은 CLARIFICATION_CONFLICT다. 새 되묻기에 이전 답을 다시 적용하지 않는다.
- MySQL의 세션 잠금과 작업 상태를 접수의 최종 기준으로 삼고 AgentInput과 QUEUED 등록을 한 트랜잭션에 저장한다. Redis SET NX 실패나 접수 중 쓰기 실패 시 등록을 롤백한다. Redis 장애는 503이며 성공 응답 후 중복 job을 만들지 않는다. Redis 고아 lock은 가리키는 MySQL job 상태를 확인해 복구한다.
- 워커는 외부 호출 중 MySQL 트랜잭션·행 잠금을 유지하지 않는다. 짧은 트랜잭션으로 상태를 갱신하고 매번 claim_token 및 CANCELLED 여부를 확인한다. 잠금 삭제·TTL 연장은 active 값이 자기 job_id일 때만 원자적으로 수행한다.
- finalize는 답변·조건·제목·SUCCEEDED를 같은 MySQL 트랜잭션에 멱등 저장한다. job_id 기준 답변 중복을 방지하고 결과표·리포트는 job_id+step_id 기준으로 재사용한다. Redis 갱신 실패로 MySQL 성공 답변을 실패 처리하거나 다시 생성하지 않는다. 다음 접근에서 성공 여부를 대조해 오래된 Redis 컨텍스트를 무효화하고 조건만 복구한다.
- 체크포인트 삭제는 finalize 노드 안에서 하지 않고 그래프 호출이 반환된 뒤 수행한다. 취소 중 실행이 계속되면 체크포인트가 다시 생길 수 있으므로 중단된 워커가 반환된 뒤 정리한다. 모든 재개는 MySQL의 최종 상태를 먼저 확인한다.
- verify는 숫자의 존재 여부뿐 아니라 국가·품목·기간·지표·단위를 함께 대조한다. 퍼센트와 퍼센트포인트, 0과 미집계 null을 구분한다. turn_summary의 숫자도 검증하며 검증 실패한 숫자는 이력에 남기지 않는다. 재생성 후 불일치 문장은 NEEDS_REVIEW로 표시한다.
- 후속 구현 테스트: 중복 질문/직접 답, 취소와 저장 경쟁, 오래된 claim의 저장, SQL 성공 후 워커 중단, report 생성 후 체크포인트 유실, MySQL 성공 후 Redis 실패, WAITING_REPORT 단일 워커 재개를 외부 연결 없이 검증한다.


### D-78. ML 순위의 모집단 (2026-10-30 사용자 승인)
- 최신 스냅샷의 전체 예측 대상 국가×HS6를 모델별로 예측하고 같은 행 키(anchor, country_code, hs6)로 정렬·대조한 뒤 combine한다. 국가·품목 필터는 앙상블과 전체 모집단 순위 계산 이후에 적용한다.
- combine의 백분위는 입력 집합에 따라 달라진다. 요청한 한 쌍만 예측·앙상블하거나 조회 대상별로 모집단을 줄이지 않는다. 국가 내 품목 순위와 품목 내 국가 순위도 전체 대상 기준이다.
- 한 복합 요청의 여러 조회는 동일한 전체 예측 결과를 재사용한다. 모델 버전·스냅샷 기준월이 같으면 단건/복수 조회의 같은 쌍은 같은 점수와 순위를 반환해야 한다. ML 담당 함수 계약과 회귀 테스트에 포함한다.

### D-79. ML 결과 식별 (2026-10-30 사용자 승인)
- anchor는 예측 기준월이며 조회 대상 ID가 아니다. 저장 결과표의 각 행은 anchor, country_code, hs6로 식별한다. HS 판본은 결과표 메타데이터에 명시한다.
- 국가별 유망 품목 표는 country_code로, 품목별 유망 국가 표는 hs6로, 쌍 조회는 두 코드로 구분한다. 화면의 한국어 이름은 사전에서 붙이며 이름만으로 행을 연결하지 않는다.

### D-80. 하위 질문과 결론 연결 (2026-10-30 사용자 승인)
- Draft.conclusion은 `[{task_id, sentences: [문장 1, 문장 2]}]`다. Task마다 1~2문장이고 계획 순서대로 정확히 한 번씩 등장해야 한다. 실패·미지원 Task도 안내 문장을 포함한다.
- TaskConclusion은 문장 수·길이를 검사하고 AgentState는 누락·중복·미등록 task_id와 순서 오류를 거부한다. compose 실패 시 기존 D-67의 표·차트 대체 응답을 쓰며 그 경우 draft=None을 허용한다.
- Draft.sentences()는 하위 질문별 결론을 펼친 다음 explanation을 잇는다. 숫자 검증의 문장 번호와 API text는 이 순서를 유지한다. API tasks[].conclusion에는 그 Task의 문장 목록을 붙인다.

### D-81. 채팅 리포트 조건·형식 확인 (2026-10-30 사용자 승인)
- REPORT는 현재 수출 분석만 지원한다. IMPORT는 NO_DATA와 미지원 안내로 끝내고 수출로 자동 변경하지 않는다.
- check_conditions에서 기존 reports.preview로 최종 조건을 검증·정규화한 뒤 REPORT_CONFIRM을 묻는다. 화면에는 품목·국가·기간·비교 기간·수출 방향·조정 사항과 PDF/DOCX 선택을 함께 보여 준다. 기존 기본 형식 PDF를 표시하되 생성은 사용자 확인 후에만 한다.
- Task.report_request(ReportRequest)는 preview_id, expires_at, conditions, formats, confirmed를 담는다. confirmed는 플래너가 만들지 않고 ask_user가 명시적 확인을 받은 뒤 기록한다. Task.conditions와 미리보기 조건은 같아야 한다. formats는 PDF/DOCX 중 한 개 이상, 중복 없이 선택한다.
- 사용자가 확인하면 미리보기 소유권·유효기간·조건을 재검증하고 기존 reports.create의 confirm_conditions=True로 접수한다. 조건·형식 수정 또는 미리보기 만료 시 재확인한다. 만료된 미리보기를 새 ID로 바꿔 동의 없이 생성하지 않는다. 거절은 해당 Task만 NO_DATA다. 리포트 확인도 D-15의 조건별 되묻기 한도를 따른다.
- 비교 기간이 다른 길이인 리포트도 D-84를 따른다. 기존 리포트 비교 검증·템플릿이 이를 지원하도록 후속 구현에서 함께 보완한다. 채팅 그래프만 연결한 채 서로 다른 비교 결과를 내보내지 않는다.

### D-82. 여러 유망시장 대상의 차트 (C-25 확정, 2026-10-30 사용자 승인)
- 모든 대상의 결과표를 제공하고 대상 선택 시 그 대상 차트를 보여 준다. 서로 다른 국가·품목의 순위를 한 시리즈로 합치지 않는다.
- ToolResult.chart는 최초 표시용 한 차트로 유지하고 chart_selector(ChartSelector)에 result_id, group_by(COUNTRY/HS6/PAIR), 국가·품목 후보를 둔다. 후보는 실제 결과가 있는 대상만 포함한다. 품목 후보는 6자리 HS6만 받는다. ML 결과표에는 HS6 행만 있으므로 HS4는 거부한다(HS4는 check가 HS6로 되물어 정한 뒤에만 온다, D-74). PAIR의 유효 조합은 결과표에서 검증한다. 모든 차트를 state에 미리 넣지 않는다.
- 최초 대상은 요청 순서에서 결과가 있는 첫 대상이다. finalize는 API answer.chart_selectors[]에 task_id와 선택 메타데이터를 붙인다. 선택 이후에는 저장 결과표로 차트를 생성하며 ML을 다시 실행하지 않는다.
- 신규 읽기 API `GET /results/{result_id}/chart`는 저장 결과표의 소유권과 대상 범위를 검증한다. COUNTRY는 country_code, HS6는 hs_code+hs_ver, PAIR는 둘 다 요구한다. 결과표에서 허용하지 않은 선택은 400, 없는 표는 404, 만료된 표는 기존 results.read_snapshot과 같은 410 RESULT_EXPIRED다. 반환은 ChartSpec와 full_labels이며 UI에서 해당 차트를 교체한다. 이 API와 프론트는 후속 구현 범위다.

### D-83. 긴 기간의 차트 집계 (2026-10-30 사용자 승인)
- 표에는 요청 기간의 월별 데이터를 유지한다. 차트가 60개 월을 넘으면 달력 분기, 분기도 60개를 넘으면 달력 연도로 집계한다. ChartSpec.granularity(MONTH/QUARTER/YEAR)와 제목에 집계 단위를 표시한다.
- 요청 범위 바깥 월은 넣지 않는다. 첫·끝 분기/연도가 일부 기간이면 실제 포함 범위를 표시한다. 연도로도 60개를 넘는 경우 원본 표를 유지하고 차트는 범위를 줄여 달라는 안내로 생략한다. 표의 기존 1,000행 제한도 표시하며 잘린 표를 전체 데이터라고 안내하지 않는다.
- 수출입 금액은 합계로, 증감률·비중은 대응하는 집계 금액으로 다시 계산한다. 월별 비율을 평균하지 않는다. 집계에 필요한 월이 미검증이면 해당 집계 값은 null이며 누락을 0으로 바꾸지 않는다.

### D-84. 길이가 다른 기간 비교 (2026-10-30 사용자 승인)
- 서로 다른 개월 수의 비교를 허용한다. 두 기간의 실제 범위와 개월 수, '기간 길이가 다릅니다' 안내를 표시하고 총액과 월평균을 함께 제공한다.
- 월평균은 검증된 월의 금액 합계 / 검증된 월 수다. 검증된 무거래 월의 0은 분모에 포함하고 미집계 월은 포함하지 않는다. 검증된 월이 0개면 월평균은 null이다.
- 요청 기간에 미집계 월이 있으면 전체 기간 총액·전체 기간 증감률은 null로 두고, 관측된 금액은 '검증된 월 소계'로 구분한다. 월평균 옆에는 실제 분모(검증 월 수/요청 월 수)를 표시하여 기간 전체의 완전한 비교로 오해하지 않게 한다.
- 비교값이 0이면 증감률은 null이고 금액 차이만 표시한다. 총액 비교와 월평균 비교의 지표·단위를 구분한다. 통계 SQL·리포트·digest·차트·verify에 같은 정의를 적용한다.

### 후속 구현 검증 항목
- ML: 필터 적용 전후 모집단, 단건/복수 요청 동일 쌍의 순위 동일성, 기준월이 같은 여러 대상의 구분.
- 리포트: 확인 전 미접수, PDF/DOCX 선택 보존, 수입 거부, 만료/조건 변경 재확인, 생성 완료 후 답변.
- 차트: 대상 선택 시 모델 미호출·소유권 검사, 월 60/61개 경계, 분기 60/61개 경계, 일부 분기/미집계 월.
- 비교: 3개월 대 12개월, 0 기준값, 미집계 월, 검증된 무거래 월, 월평균 분모와 표시 일치.


### D-85. 실제 실행 예산과 장애 복구 (2026-10-30 사용자 승인)
- 한 라운드의 실행 예산은 180초다. LLM·SQL·ML 등 실행 중 소요 시간은 포함하고, 큐·되묻기·리포트 생성 대기·장애 복구 대기는 제외한다. 정상 재시도에 든 시간도 포함한다.
- state.run_budget의 charged_seconds는 이미 차감한 실행 시간, recovery_count는 장애로 소유권을 다시 받은 횟수다. 남은 시간은 max(0, 180 - charged_seconds)다. round_started_at은 표시용이며 대기·재개 때 시간을 이동하거나 새로 설정하지 않는다. 사용자의 새 되묻기 답으로 새 라운드를 시작할 때만 예산을 초기화한다.
- 워커는 MySQL _execution에 라운드 ID·차감 시간·복구 횟수·진행 중 호출의 예약 시간을 실행 메타데이터로 기록한다. 전체 AgentState를 별도로 복제하지 않는다. Redis 체크포인트가 사라져도 이 예산은 유지하고, 복구 시 체크포인트 사본보다 MySQL 값을 우선한다.
- 호출 전에 남은 예산 안에서 해당 노드의 최대 실행 시간을 원자적으로 예약하고 실제 호출 timeout에도 적용한다. 정상 종료하면 단조 시계로 잰 실제 소요 시간으로 정산하고 남은 예약분을 돌려준다. 예약 후 워커가 죽으면 사용 시간을 정확히 알 수 없으므로 예약분은 차감된 채 유지한다. 리포트 생성 자체는 대기 작업이므로 채팅에서는 접수·결과 확인 호출 시간만 예약한다.
- 예약·정산은 라운드 ID+claim_token+호출 ID 기준으로 멱등 처리한다. 오래된 워커는 정산·반환·답변 저장을 할 수 없다. 재시도 호출도 별도 예약하므로 180초를 새로 부여하지 않는다.
- 장애 재개는 라운드당 기본 2회(최초 실행 포함 최대 3회), 설정 API_CHAT_MAX_RECOVERIES로 조정한다. state.MAX_WORKER_RECOVERIES는 기본값이다. LLM/SQL tool의 재시도 횟수, 정상 되묻기 답, 정상 리포트 완료 재개는 이 횟수에 포함하지 않는다.
- 예산 소진은 RUN_TIMEOUT, 장애 복구 한도 초과는 RECOVERY_EXHAUSTED로 종료하고 잠금을 해제한다. 데이터가 저장돼 있고 안전하게 최종 답변을 만들 수 있으면 D-72 부분 결과 정책을 적용한다. 그렇지 않으면 FAILED와 재질문 안내를 반환하며 자동 재큐하지 않는다.

### D-86. 하위 질문 3개 초과 (2026-10-30 사용자 승인)
- 플래너는 먼저 같은 분석의 여러 대상을 한 Task로 묶는다. 다른 분석을 억지로 한 Task로 합치거나 질문 일부를 버리지 않는다.
- 묶은 뒤에도 3개를 초과하면 조회·리포트 생성 등 tool을 전혀 실행하지 않고 '한 번에 3개 이하로 나눠 질문해 주세요.'라고 안내한다. 이전 검색 조건과 대화 요약은 유지한다.
- LLM 출력 단계에서 전체 하위 질문 개수를 확인한 뒤 state.task_count_error를 호출한다. 저장용 Plan의 3개 제한에 걸린 예외로 처리하거나 tasks[:3]으로 자르지 않는다. LLM 출력이 토큰 제한으로 잘린 경우도 완전한 계획으로 간주하지 않는다.
- 이 경우 job은 FAILED, error.code=QUESTION_TOO_COMPLEX, retryable=false, error.message는 위 안내 문구다. 사용자가 질문을 나눠 새 요청을 제출한다. Plan·Draft를 억지로 만들어 일반 답변처럼 표시하지 않는다.

### D-87. 시장 점유율의 분모 (2026-10-30 사용자 승인)
- 현재 MARKET_SHARE가 지원하는 국가별 비중은 '한국의 해당 품목 전체 수출 중 각 대상국 비중'이다. 수입 방향이면 '한국의 해당 품목 전체 수입 중 각 상대국 비중'이다. 선택 국가로 분모를 좁히지 않는다.
- PlannerSlots.share_basis는 KOREA_TRADE_PARTNER(위 정의), FOREIGN_IMPORT_MARKET(해외 수입시장에서 한국 제품 비중), None(모호함)이다. 명확한 요청만 바로 처리하고 '베트남 시장 점유율'처럼 분모가 불명확하면 SHARE_BASIS로 되묻는다. RANKING_GROUP은 국가/품목 구분이며 분모 확인을 대신하지 않는다.
- SHARE_BASIS는 위 두 의미를 선택지로 표시하고 직접 입력도 받는다. condition_key는 예를 들어 t1:share_basis다. 동일 조건의 2회 제한을 따른다.
- FOREIGN_IMPORT_MARKET은 데이터·계산 계약이 연결되기 전까지 '해외 수입시장에서 한국 제품의 점유율은 아직 지원하지 않습니다.'로 해당 Task를 NO_DATA 처리한다. LLM SQL로 다른 의미의 비중을 대체 계산하지 않는다.
- 확정한 분모 의미·실제 분모 금액·기간·품목·수출입 방향은 결과표 메타데이터와 digest·답변에 표시한다. 모호한 질문은 과거 share_basis를 자동 상속해 확정하지 않는다.

### D-88. 결과표 저장과 차트 참조 (2026-10-30 사용자 승인)
- ML 전체 예측 모집단은 모델·스냅샷 기반으로 계산하고 전용 예측 테이블에 적재하지 않는다. 사용자가 조회한 결과 행은 기존 MySQL 결과표 저장소에 소유권·조건·기준월·모델/스냅샷 버전과 함께 저장한다.
- 최초 차트, 대상 전환 차트, 표 보기·다운로드는 이 저장 결과를 재사용한다. 저장 결과가 만료되면 기존 results.read_snapshot의 410 RESULT_EXPIRED 계약을 따르고 최신 모델로 몰래 재계산하지 않는다.
- 동일 ToolResult 안의 result_id, chart.result_id, chart_selector.result_id는 존재하는 값끼리 모두 같아야 한다. state 검증에서 불일치를 거부한다. 단독 필드는 기존대로 허용한다.
- 후속 구현 테스트에 10분 장애 대기 후 남은 예산 유지, 예약 후 프로세스 종료·재정산, 복구 횟수 한도, 4개 질문의 무실행 안내, 모호한 점유율 재질문·해외 점유율 미지원, 차트 결과표 불일치를 포함한다.


### 추가 리뷰 결과 (2026-10-30, D-85~88 반영 후)
- 기존 results.read_snapshot은 저장 결과 만료 시 410 RESULT_EXPIRED, 없는 결과는 404를 사용한다. 차트 조회에도 같은 구분을 적용하도록 D-82/API 문서의 만료 404 설명을 정정했다.
- 이번 범위에서 새로 사용자 결정을 받아야 하는 제품 정책은 추가하지 않았다. C-20(로그인 세션), C-23(EC2 메모리), C-24(리포트 대기 상한), C-26(최근 개월 수 상한)은 기존 임시값을 유지한다.
- state 계약 검증과 실제 실행 보장은 구분한다. claim 예약·정산, 원자적 접수, 재개·취소 경쟁, 차트 읽기 API와 SQL 집계는 후속 구현 및 통합 테스트가 필요하다. 이 브랜치의 state 단위 테스트 통과로 실행 계층의 완료를 주장하지 않는다.
