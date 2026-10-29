"""질문 1건을 처리하는 에이전트 실행 상태.

실행 중·되묻기 대기 동안만 Redis에 JSON으로 둔다(키·TTL은 아래 상수).
질문·답변·결과표·리포트의 원본은 MySQL이며, state에는 그 ID만 담는다.
"""
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import to_json

from backend.app.schemas.rag import CountryCode, HsProduct

SCHEMA_VERSION = 1
MAX_STATE_BYTES = 64 * 1024      # Redis 값 하나의 상한. 넘으면 원본을 state에 넣은 것이다.
MAX_DIGEST_BYTES = 8 * 1024      # tool 결과 중 LLM에 넘기는 요약의 상한
HISTORY_TURNS = 5

# Redis 키. 원본은 MySQL이라 키가 사라지면 MySQL에서 다시 만든다(context) 또는 처음부터 다시 실행한다(run).
CONTEXT_KEY = 'chat:session:{session_id}:context'  # SessionContext JSON
ACTIVE_KEY = 'chat:session:{session_id}:active'    # 세션당 진행 중인 job_id 1개(SET NX)
STATE_KEY = 'agent:run:{job_id}'                   # AgentState JSON
CONTEXT_TTL_SECONDS = 7 * 24 * 3600  # 대화방을 열 때마다 연장
RUNNING_TTL_SECONDS = 15 * 60        # 실행 중: 워커가 단계마다 연장
WAITING_TTL_SECONDS = 30 * 60        # 되묻기 대기: 지나면 EXPIRED, 질문을 다시 받는다

ToolName = Literal['check_conditions', 'query_trade_data', 'search_articles', 'build_chart', 'generate_report']
Intent = Literal['STATISTICS', 'RANKING', 'MARKET_SHARE', 'CAUSE', 'LOCAL_ISSUES', 'REPORT', 'OUT_OF_SCOPE']
RunStatus = Literal['QUEUED', 'RUNNING', 'NEEDS_INPUT', 'SUCCEEDED', 'FAILED', 'EXPIRED']
StepStatus = Literal['PENDING', 'RUNNING', 'DONE', 'FAILED', 'SKIPPED']


def _json_size(value) -> int:
    return len(to_json(value))


class Model(BaseModel):
    # 노드는 필드를 여러 개 바꾼 뒤 저장한다. 검증은 Redis에 쓰고 읽을 때 한 번(model_validate / model_validate_json).
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
    hs_items: list[HsProduct] = Field(default_factory=list, max_length=20)
    country_codes: list[CountryCode] = Field(default_factory=list, max_length=20)
    period: Period | None = None            # 수출입 기간(기본 최근 12개월)
    compare_period: Period | None = None    # 현재는 전년 동기만
    article_period: Period | None = None    # 기사 기간(기본 최근 3개월)
    trade_flow: Literal['EXPORT', 'IMPORT'] = 'EXPORT'
    ranking_group: Literal['COUNTRY', 'HS6'] | None = None


class Interests(Model):
    hs_items: list[HsProduct] = Field(default_factory=list)
    country_codes: list[CountryCode] = Field(default_factory=list)


class TurnSummary(Model):
    """답변 1건의 압축. LLM 요약 대신 저장된 값만 옮겨 숫자·조건이 바뀌지 않는다."""
    question: Annotated[str, Field(max_length=4000)]
    intent: Intent
    conditions: Conditions | None = None
    conclusion: Annotated[str, Field(max_length=300)] = ''   # 답변의 결론 문장
    answered_at: datetime


class SessionContext(Model):
    """대화방 컨텍스트(Redis CONTEXT_KEY). 답변이 끝날 때 갱신하고 MySQL에도 같은 값을 쓴다."""
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
    step_id: str
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)   # 조건 밖의 tool별 인자(지표, 차트 유형 등)
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = 'PENDING'
    attempts: int = Field(default=0, ge=0, le=3)


class Plan(Model):
    intent: Intent
    steps: list[Step] = Field(max_length=8)

    @model_validator(mode='after')
    def check_order(self):
        if self.intent == 'OUT_OF_SCOPE':
            if self.steps: raise ValueError('범위 밖 질문은 실행 단계가 없습니다')
            return self
        if not self.steps or self.steps[0].tool != 'check_conditions':
            raise ValueError('첫 단계는 check_conditions여야 합니다')
        seen = set()
        for step in self.steps:
            # 앞 단계만 의존하게 해 순환을 막는다.
            if step.step_id in seen or not set(step.depends_on) <= seen:
                raise ValueError(f'{step.step_id}: 중복 ID이거나 뒤 단계에 의존합니다')
            seen.add(step.step_id)
        return self


class DataMeta(Model):
    """적재 로그에서 읽은 소스별 마지막 검증 완료 시점."""
    trade_first_month: date | None = None
    trade_latest_month: date | None = None
    article_latest_date: date | None = None


class Choice(Model):
    choice_id: str
    label: str
    patch: dict[str, Any]   # 고르면 Conditions에 병합할 값


class Pending(Model):
    """되묻기 대기. 답이 오면 조건에 병합하고 check_conditions부터 다시 돈다."""
    clarification_id: str
    kind: Literal['HS_ITEM', 'INTEREST_CONTEXT', 'RANKING_GROUP']
    message: str
    choices: list[Choice] = Field(max_length=5)
    remaining: list[str] = Field(default_factory=list)       # 아직 못 정한 품목 표현
    selected: list[HsProduct] = Field(default_factory=list)  # 앞 표현에서 고른 품목
    asked_at: datetime


class ToolResult(Model):
    """tool 결과 봉투. 원본은 MySQL·Chroma에 있고 여기엔 참조와 작은 요약만 둔다."""
    status: Literal['OK', 'NO_DATA', 'FAILED']
    result_id: str | None = None                  # query_trade_data 결과표
    article_ids: list[int] = Field(default_factory=list, max_length=20)
    chart: dict[str, Any] | None = None           # build_chart 스펙(값은 결과표에서 코드로 채움)
    report_id: str | None = None                  # generate_report 비동기 작업
    period: Period | None = None                  # 기준 기간
    source: str | None = None                     # 출처
    checks: list[str] = Field(default_factory=list)   # 통과한 자체 검증
    digest: dict[str, Any] | None = None          # LLM 입력용: 합계·상위 행·변동 월 등
    error: str | None = None                      # 사용자에게 보여도 되는 사유

    @model_validator(mode='after')
    def check_digest(self):
        if self.digest is not None and _json_size(self.digest) > MAX_DIGEST_BYTES:
            raise ValueError('digest가 너무 큽니다. 행 원본은 결과표에 두세요')
        if self.status == 'FAILED' and not self.error:
            raise ValueError('실패에는 사유가 필요합니다')
        return self


class Draft(Model):
    conclusion: str
    explanation: list[str] = Field(default_factory=list)


class SentenceCheck(Model):
    sentence: str
    numbers: list[str] = Field(default_factory=list)
    status: Literal['PASS', 'MISMATCH']


class Verification(Model):
    status: Literal['PENDING', 'PASS', 'NEEDS_REVIEW'] = 'PENDING'
    checks: list[SentenceCheck] = Field(default_factory=list)
    regeneration_count: int = Field(default=0, ge=0, le=1)


class AgentState(Model):
    schema_version: Literal[1] = SCHEMA_VERSION
    input: AgentInput
    status: RunStatus = 'QUEUED'
    plan: Plan | None = None
    conditions: Conditions = Field(default_factory=Conditions)
    applied_defaults: list[str] = Field(default_factory=list)   # "적용한 조건" 표시용
    data_meta: DataMeta | None = None
    pending: Pending | None = None
    results: dict[str, ToolResult] = Field(default_factory=dict)  # step_id → 결과
    draft: Draft | None = None
    verification: Verification = Field(default_factory=Verification)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_consistency(self):
        if (self.status == 'NEEDS_INPUT') != (self.pending is not None):
            raise ValueError('NEEDS_INPUT일 때만 pending이 있습니다')
        steps = {s.step_id for s in self.plan.steps} if self.plan else set()
        if not set(self.results) <= steps:
            raise ValueError('계획에 없는 단계의 결과입니다')
        if _json_size(self.model_dump(mode='json')) > MAX_STATE_BYTES:
            raise ValueError('state가 Redis 상한을 넘습니다')
        return self

    def ready_steps(self) -> list[Step]:
        """의존 단계가 모두 끝난 대기 단계. 여러 개면 병렬로 실행해도 된다."""
        if not self.plan: return []
        done = {s.step_id for s in self.plan.steps if s.status in ('DONE', 'SKIPPED')}
        return [s for s in self.plan.steps if s.status == 'PENDING' and set(s.depends_on) <= done]
