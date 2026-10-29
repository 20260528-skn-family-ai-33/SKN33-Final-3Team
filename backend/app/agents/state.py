"""질문 1건을 처리하는 에이전트 실행 상태.

LangGraph 그래프의 state이며, langgraph-checkpoint-redis가 Redis에 체크포인트로 저장한다(thread_id = job_id).
질문·답변·결과표·리포트의 원본은 MySQL이며, state에는 그 ID와 작은 요약만 담는다.
"""
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_json

from backend.app.schemas.rag import CountryCode, HsProduct

SCHEMA_VERSION = 2
MAX_STATE_BYTES = 64 * 1024      # 체크포인트 하나의 상한. 넘으면 원본을 state에 넣은 것이다.
MAX_DIGEST_BYTES = 8 * 1024      # tool 결과 중 LLM에 넘기는 요약의 상한
HISTORY_TURNS = 5
MAX_TASKS = 3                    # 복합 질문을 나눈 하위 질문 수
MAX_CHART_LABELS = 60            # 차트 하나의 x축 값 수
MAX_CHART_SERIES = 5

# 직접 관리하는 Redis 키. 실행 state는 체크포인터가 관리한다.
CONTEXT_KEY = 'chat:session:{session_id}:context'  # SessionContext JSON
ACTIVE_KEY = 'chat:session:{session_id}:active'    # 세션당 진행 중인 job_id 1개(SET NX)
CONTEXT_TTL_SECONDS = 7 * 24 * 3600  # 대화방을 열 때마다 연장
ACTIVE_TTL_SECONDS = 30 * 60
CHECKPOINT_TTL = {'default_ttl': 30, 'refresh_on_read': True}  # RedisSaver(ttl=...), 분 단위
WAITING_TTL_SECONDS = 30 * 60        # 되묻기 대기. 체크포인트 TTL은 읽을 때 연장되므로 pending.asked_at으로 따로 판정
RUN_TIMEOUT_SECONDS = 180            # 한 라운드(접수 또는 되묻기 답 이후) 실행 상한
MAX_ASKS_PER_CONDITION = 2           # 조건 하나당 되묻기 횟수

ToolName = Literal['check_conditions', 'query_trade_data', 'search_articles', 'build_chart',
                   'generate_report', 'query_market_model']
Intent = Literal['STATISTICS', 'RANKING', 'MARKET_SHARE', 'CAUSE', 'LOCAL_ISSUES', 'REPORT',
                 'PROMISING_MARKET', 'MARKET_TYPE', 'OUT_OF_SCOPE']
# 실행 단계 없이 안내만 하는 intent. MARKET_TYPE은 B 모델 시장 유형 분류가 나오기 전까지 "지원 예정" 안내.
NO_STEP_INTENTS = frozenset({'OUT_OF_SCOPE', 'MARKET_TYPE'})
RunStatus = Literal['QUEUED', 'RUNNING', 'NEEDS_INPUT', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'CANCELLED']
StepStatus = Literal['PENDING', 'RUNNING', 'DONE', 'FAILED', 'SKIPPED']


def _json_size(value) -> int:
    return len(to_json(value))


class Model(BaseModel):
    # 노드는 필드를 여러 개 바꾼 뒤 반환한다. 검증은 체크포인트에 쓰고 읽을 때 한 번(model_validate / model_validate_json).
    model_config = ConfigDict(extra='forbid')


class Period(Model):
    """월 단위 조회 기간. start·end 모두 1일, 양끝 포함."""
    start: date
    end: date

    @model_validator(mode='after')
    def check_months(self):
        if self.start.day != 1 or self.end.day != 1 or self.start > self.end:
            raise ValueError('기간은 월 1일 기준이고 start <= end여야 합니다')
        return self


class Conditions(Model):
    """조건 확인 tool이 확정한 조회 조건. 빈 목록은 '전체'다."""
    hs_items: list[HsProduct] = Field(default_factory=list, max_length=5)
    country_codes: list[CountryCode] = Field(default_factory=list, max_length=20)
    period: Period | None = None            # 수출입 기간(기본 최근 12개월)
    compare_period: Period | None = None    # 미입력이면 전년 동기, 입력하면 그 기간
    article_period: Period | None = None    # 기사 기간(기본 최근 3개월)
    trade_flow: Literal['EXPORT', 'IMPORT'] = 'EXPORT'
    ranking_group: Literal['COUNTRY', 'HS6'] | None = None


class Interests(Model):
    hs_items: list[HsProduct] = Field(default_factory=list)
    country_codes: list[CountryCode] = Field(default_factory=list)


class TurnSummary(Model):
    """답변 1건의 요약. summary는 compose가 쓴 문장, 조건은 주 하위 질문의 구조화된 값이다."""
    question: Annotated[str, Field(max_length=4000)]
    intent: Intent
    conditions: Conditions | None = None
    summary: Annotated[str, Field(max_length=200)] = ''
    answered_at: datetime


class SessionContext(Model):
    """대화방 컨텍스트(Redis CONTEXT_KEY, Redis에만 둔다).
    MySQL chat_session.context_json에는 conditions만 저장하므로 키가 사라지면 recent는 비어서 다시 시작한다."""
    session_id: str
    conditions: Conditions | None = None   # 최근 검색 조건
    recent: list[TurnSummary] = Field(default_factory=list, max_length=HISTORY_TURNS)  # 오래된 것부터
    interest_action: Literal['NONE', 'APPLIED', 'DECLINED', 'CLEARED'] = 'NONE'

    def remember(self, turn: TurnSummary) -> 'SessionContext':
        return self.model_copy(update={'conditions': turn.conditions or self.conditions,
                                       'recent': [*self.recent, turn][-HISTORY_TURNS:]})


class AgentInput(Model):
    """백엔드가 접수 시점에 한 번 만드는 스냅샷. 재실행·재개해도 바뀌지 않는다."""
    job_id: str
    session_id: str
    user_id: int
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    context: SessionContext
    interests: Interests = Field(default_factory=Interests)
    received_at: datetime


class Step(Model):
    step_id: str            # 하위 질문 안에서만이 아니라 계획 전체에서 고유. 예: 't1.sql'
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)   # 조건 밖의 tool별 인자(지표, 차트 유형 등)
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = 'PENDING'
    attempts: int = Field(default=0, ge=0, le=3)


class Task(Model):
    """복합 질문을 나눈 하위 질문 하나. 첫 번째가 주 의도이며 최근 검색 조건·요약의 기준이다."""
    task_id: str
    intent: Intent
    question: Annotated[str, Field(min_length=1, max_length=4000)]   # 플래너가 나눈 하위 질문 문장
    steps: list[Step] = Field(default_factory=list, max_length=6)
    conditions: Conditions | None = None        # check_conditions가 채움
    applied_defaults: list[str] = Field(default_factory=list)   # "적용한 조건" 표시용


class Plan(Model):
    tasks: list[Task] = Field(min_length=1, max_length=MAX_TASKS)

    @model_validator(mode='after')
    def check_order(self):
        task_ids, step_ids = set(), set()
        for task in self.tasks:
            if task.task_id in task_ids: raise ValueError(f'{task.task_id}: 중복 하위 질문 ID')
            task_ids.add(task.task_id)
            if task.intent in NO_STEP_INTENTS:
                if task.steps: raise ValueError(f'{task.intent}는 실행 단계가 없습니다')
                continue
            if not task.steps or task.steps[0].tool != 'check_conditions':
                raise ValueError(f'{task.task_id}: 첫 단계는 check_conditions여야 합니다')
            seen = set()
            for step in task.steps:
                # 같은 하위 질문의 앞 단계만 의존하게 해 순환과 하위 질문 간 얽힘을 막는다.
                if step.step_id in step_ids or not set(step.depends_on) <= seen:
                    raise ValueError(f'{step.step_id}: 중복 ID이거나 앞 단계가 아닌 곳에 의존합니다')
                seen.add(step.step_id); step_ids.add(step.step_id)
        return self

    def steps(self) -> list[Step]:
        return [s for t in self.tasks for s in t.steps]


class DataMeta(Model):
    """적재 로그에서 읽은 소스별 마지막 검증 완료 시점."""
    trade_first_month: date | None = None
    trade_latest_month: date | None = None
    article_latest_date: date | None = None


class Choice(Model):
    choice_id: str
    label: str
    patch: dict[str, Any]   # 고르면 해당 하위 질문의 Conditions에 병합할 값


class Pending(Model):
    """되묻기 대기. 답(선택지·직접 입력)을 조건에 병합하고 그 하위 질문의 check_conditions부터 다시 돈다."""
    clarification_id: str
    task_id: str
    kind: Literal['HS_ITEM', 'COUNTRY', 'INTEREST_CONTEXT', 'INTEREST_CHANGE', 'RANKING_GROUP']
    condition_key: str      # 되묻기 횟수를 세는 단위. 예: 't1:hs:화장품', 't1:country:콩고'
    message: str
    choices: list[Choice] = Field(max_length=5)
    remaining: list[str] = Field(default_factory=list)       # 아직 못 정한 품목 표현
    selected: list[HsProduct] = Field(default_factory=list)  # 앞 표현에서 고른 품목
    asked_at: datetime


class ChartSeries(Model):
    name: Annotated[str, Field(max_length=40)]
    values: list[float | None]   # 값이 없는 칸은 None. 결과표에서 코드로 채운다


class ChartSpec(Model):
    """프론트 charts.jsx(LineChart·Donut 등) props와 같은 모양으로 보낸다."""
    chart_id: str
    type: Literal['line', 'bar', 'grouped_bar', 'donut']   # 추이 / 순위 / 두 시점 비교 / 비중
    title: Annotated[str, Field(max_length=40)]
    unit: str
    period: Period | None = None
    source: str
    result_id: str
    labels: list[str] = Field(min_length=1, max_length=MAX_CHART_LABELS)
    series: list[ChartSeries] = Field(min_length=1, max_length=MAX_CHART_SERIES)

    @model_validator(mode='after')
    def check_shape(self):
        if any(len(s.values) != len(self.labels) for s in self.series):
            raise ValueError('시리즈 값 수가 labels 수와 다릅니다')
        if self.type == 'donut' and len(self.series) != 1:
            raise ValueError('donut은 시리즈 1개입니다')
        if self.type == 'grouped_bar' and len(self.series) != 2:
            raise ValueError('grouped_bar는 두 시점, 시리즈 2개입니다')
        return self


class ToolResult(Model):
    """tool 결과 봉투. 원본은 MySQL·Chroma에 있고 여기엔 참조와 작은 요약만 둔다."""
    status: Literal['OK', 'NO_DATA', 'FAILED']
    result_id: str | None = None                  # query_trade_data·query_market_model 결과표
    article_ids: list[int] = Field(default_factory=list, max_length=20)
    chart: ChartSpec | None = None                # build_chart
    report_id: str | None = None                  # generate_report 비동기 작업
    period: Period | None = None                  # 기준 기간
    source: str | None = None                     # 출처
    checks: list[str] = Field(default_factory=list)   # 통과한 자체 검증
    digest: dict[str, Any] | None = None          # LLM 입력용: 합계·상위 행 등
    error: str | None = None                      # 사용자에게 보여도 되는 사유

    @model_validator(mode='after')
    def check_digest(self):
        if self.digest is not None and _json_size(self.digest) > MAX_DIGEST_BYTES:
            raise ValueError('digest가 너무 큽니다. 행 원본은 결과표에 두세요')
        if self.status == 'FAILED' and not self.error:
            raise ValueError('실패에는 사유가 필요합니다')
        return self


class Draft(Model):
    """compose의 LLM 호출 1번으로 답변 문장, 대화 요약, (첫 답변이면) 대화방 제목을 같이 받는다.
    결론은 하위 질문마다 1~2문장, 설명은 전체 4문장 이하."""
    conclusion: str
    explanation: list[str] = Field(default_factory=list, max_length=4)
    turn_summary: Annotated[str, Field(max_length=200)]
    title: Annotated[str, Field(max_length=20)] | None = None


class SentenceCheck(Model):
    sentence: str
    numbers: list[str] = Field(default_factory=list)
    status: Literal['PASS', 'MISMATCH']


class Verification(Model):
    status: Literal['PENDING', 'PASS', 'NEEDS_REVIEW'] = 'PENDING'
    checks: list[SentenceCheck] = Field(default_factory=list)
    regeneration_count: int = Field(default=0, ge=0, le=1)


class AgentState(Model):
    schema_version: Literal[2] = SCHEMA_VERSION
    input: AgentInput
    status: RunStatus = 'QUEUED'
    plan: Plan | None = None
    data_meta: DataMeta | None = None
    pending: Pending | None = None
    ask_counts: dict[str, Annotated[int, Field(ge=0, le=MAX_ASKS_PER_CONDITION)]] = Field(default_factory=dict)
    round_started_at: datetime | None = None   # 진행 시간 표시 기준. 워커 시작·되묻기 답 때 다시 잰다
    results: dict[str, ToolResult] = Field(default_factory=dict)  # step_id → 결과
    draft: Draft | None = None
    verification: Verification = Field(default_factory=Verification)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_consistency(self):
        if (self.status == 'NEEDS_INPUT') != (self.pending is not None):
            raise ValueError('NEEDS_INPUT일 때만 pending이 있습니다')
        steps = {s.step_id for s in self.plan.steps()} if self.plan else set()
        if not set(self.results) <= steps:
            raise ValueError('계획에 없는 단계의 결과입니다')
        if self.pending and (not self.plan or self.pending.task_id not in {t.task_id for t in self.plan.tasks}):
            raise ValueError('되묻기가 계획에 없는 하위 질문을 가리킵니다')
        if _json_size(self.model_dump(mode='json')) > MAX_STATE_BYTES:
            raise ValueError('state가 체크포인트 상한을 넘습니다')
        return self

    def ready_steps(self) -> list[Step]:
        """의존 단계가 모두 DONE인 대기 단계. 하위 질문 순서대로 나온다."""
        if not self.plan: return []
        steps = self.plan.steps()
        done = {s.step_id for s in steps if s.status == 'DONE'}
        return [s for s in steps if s.status == 'PENDING' and set(s.depends_on) <= done]

    def skip_blocked(self) -> list[str]:
        """FAILED·SKIPPED 단계에 의존하는 대기 단계를 SKIPPED로 바꾼다. tool 노드가 단계를 FAILED로 둔 뒤 부른다.
        단계는 같은 하위 질문의 앞 단계에만 의존하므로 한 번 훑으면 전이 의존까지 처리된다."""
        if not self.plan: return []
        stopped = {s.step_id for s in self.plan.steps() if s.status in ('FAILED', 'SKIPPED')}
        skipped = []
        for s in self.plan.steps():
            if s.status == 'PENDING' and stopped & set(s.depends_on):
                s.status = 'SKIPPED'; stopped.add(s.step_id); skipped.append(s.step_id)
        return skipped
