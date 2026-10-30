"""질문 1건을 처리하는 에이전트 실행 상태.

LangGraph 그래프의 state이며, langgraph-checkpoint-redis가 Redis에 체크포인트로 저장한다(thread_id = job_id).
질문·답변·결과표·리포트의 원본은 MySQL이며, state에는 그 ID와 작은 요약만 담는다.
"""
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import (AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, field_validator,
                      model_validator)
from pydantic_core import to_json

from backend.app.schemas.rag import ArticlePeriod, CountryCode, HsProduct

SCHEMA_VERSION = 2
# 체크포인트 하나의 상한(D-62). 넘으면 원본을 state에 넣은 것이다.
# 대표적인 최대 크기 시나리오(약 260KB, Task.resolved 포함)가 상한 이내다(test_largest_allowed_state_fits).
# 칸을 더하면 그 테스트에도 넣는다.
MAX_STATE_BYTES = 320 * 1024
MAX_DIGEST_BYTES = 8 * 1024      # tool 결과 중 LLM에 넘기는 요약의 상한
HISTORY_TURNS = 5
HISTORY_QUESTION_CHARS = 200     # 5건 요약에 남기는 질문 길이. 원문은 MySQL에 있다
TASK_QUESTION_CHARS = 500        # 플래너가 나눈 하위 질문 문장 길이. 원 질문은 input.question에 있다
MAX_TASKS = 3                    # 복합 질문을 나눈 하위 질문 수
MAX_HS_ITEMS, MAX_COUNTRIES = 5, 20   # 질문 하나의 조회 조건(D-24)
MAX_SLOT_CHARS = 120             # 플래너가 옮긴 국가·품목 표현 하나(D-73)
# C-26 확정: 240개월 상한과 별도로 check_conditions가 실제 공개·검증 월 범위를 검사한다.
MAX_RECENT_MONTHS = 240          # "최근 N개월" 상한. 넘으면 안내한다(D-73). 날짜 계산(shift_month)이 깨지지 않게 둔다
MAX_CHART_LABELS = 60            # 차트 하나의 x축 값 수
MAX_CHART_SERIES = 5
# 칸 상한(D-62). 코드가 만드는 표시용 글자(HS4 품목명이 최대 347자)는 거부하지 않고 자른다.
# LLM이 쓰는 Draft 문장만 넘으면 거부해 재시도한다.
MAX_TEXT_CHARS = 200             # 문장 하나: 답변 문장, 되묻기 문구·선택지, 경고, 오류, 적용 조건
MAX_LABEL_CHARS = 40             # 이름 하나: 차트 제목·x축 값·시리즈, 자체 검증 이름, 출처, 품목 표현, 숫자
MAX_CONCLUSION_SENTENCES = 2 * MAX_TASKS   # 결론은 하위 질문마다 1~2문장(D-50)
MAX_EXPLANATION_SENTENCES = 4
MAX_LIST_ITEMS = 10              # 경고·자체 검증·적용 조건 목록
MAX_ARGS_BYTES = 512             # Step.args·Choice.patch JSON
MAX_RESOLVED_BYTES = 4 * 1024    # Task.resolved(되묻기로 확정한 답) 전체. 품목 5·국가 20개 답이 1KB 안팎이다
# 관심사는 저장 개수 제한이 없다(D-63). 조건 상한을 넘으면 이 안에서 골라 받는다(D-64). 고를 후보로 앞에서부터 남긴다.
MAX_INTEREST_HS, MAX_INTEREST_COUNTRIES = 100, 250

# (임시) Redis 키·TTL은 바뀔 수 있다(설계 §6). 직접 관리하는 키이며, 실행 state는 체크포인터가 관리한다.
# 실제 키는 기존 RedisState(api/redis_store.py)처럼 API_REDIS_PREFIX를 앞에 붙인다(D-01).
CONTEXT_KEY = 'chat:session:{session_id}:context'  # SessionContext JSON
ACTIVE_KEY = 'chat:session:{session_id}:active'    # 세션당 진행 중인 job_id 1개(SET NX)
CONTEXT_TTL_SECONDS = 7 * 24 * 3600  # 대화방을 열 때마다 연장
ACTIVE_TTL_SECONDS = 30 * 60
CHECKPOINT_TTL = {'default_ttl': 30, 'refresh_on_read': True}  # RedisSaver(ttl=...), 분 단위
WAITING_TTL_SECONDS = 30 * 60        # 되묻기 대기. 체크포인트 TTL은 읽을 때 연장되므로 pending.asked_at으로 따로 판정
RUN_TIMEOUT_SECONDS = 180            # 한 라운드(접수 또는 되묻기 답 이후) 실행 상한
MAX_WORKER_RECOVERIES = 2            # 최초 실행 외 장애 재개 횟수. API_CHAT_MAX_RECOVERIES로 설정(D-85)
TOO_MANY_TASKS_MESSAGE = '한 번에 3개 이하로 나눠 질문해 주세요.'
REPORT_WAIT_TIMEOUT_SECONDS: int | None = None  # TODO(C-24): 별도 대기 상한 미정. report의 실패·재시도 정책 적용
MAX_ASKS_PER_CONDITION = 2           # 조건 하나당 되묻기 횟수

ToolName = Literal['check_conditions', 'query_trade_data', 'search_articles', 'build_chart',
                   'generate_report', 'query_market_model']
Intent = Literal['STATISTICS', 'RANKING', 'MARKET_SHARE', 'CAUSE', 'LOCAL_ISSUES', 'REPORT',
                 'PROMISING_MARKET', 'MARKET_TYPE', 'OUT_OF_SCOPE']
# 실행 단계 없이 안내만 하는 intent. MARKET_TYPE은 B 모델 시장 유형 분류가 나오기 전까지 "지원 예정" 안내.
NO_STEP_INTENTS = frozenset({'OUT_OF_SCOPE', 'MARKET_TYPE'})
RunStatus = Literal['QUEUED', 'RUNNING', 'NEEDS_INPUT', 'WAITING_REPORT', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'CANCELLED']
AnswerStatus = Literal['ANSWERED', 'PARTIAL', 'NO_DATA', 'OUT_OF_SCOPE']
StepStatus = Literal['PENDING', 'RUNNING', 'DONE', 'FAILED', 'SKIPPED']


def _json_size(value) -> int:
    return len(to_json(value))


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f'{label} 중복은 허용하지 않습니다')


def _truncate(limit):
    """문자열·목록을 앞에서부터 limit개만 남긴다."""
    return BeforeValidator(lambda v: v[:limit] if isinstance(v, (str, list)) else v)


def shorten_name(name: str, head: int = 20, limit: int = MAX_LABEL_CHARS) -> str:
    """품목명 표시(E-04): 앞 head자 + '…' + 끝부분으로 limit자 안에 맞춘다. chart tool이 품목명 라벨에 쓴다."""
    if len(name) <= limit:
        return name
    head = min(head, limit - 1)   # 앞부분이 limit를 다 차지하면 끝부분 없이 '…'로 끝낸다
    return name[:head] + '…' + name[len(name) - (limit - head - 1):]


def shorten_names(names: list[str], hs_items: list[HsProduct]) -> list[str]:
    """한 차트의 품목명(D-68). x축 라벨과, 품목별 시리즈 이름(여러 품목 추이) 모두 이것으로 만든다.
    줄인 이름이 겹치면 '(HS 코드)'를 붙여 구분한다(가운데만 다른 품목명).
    같은 코드가 판본만 달리 함께 있으면 '(코드 판본)'을 붙인다. 예: '(8483 HS2017)'."""
    return _disambiguate(names, _hs_tags(hs_items))


def _hs_tags(hs_items: list[HsProduct | None]) -> list[str | None]:
    """구분용 HS 코드. 같은 코드가 다른 판본으로도 있을 때만 판본을 붙인다(같은 품목이 여러 국가에 있어도 코드만)."""
    versions = {}
    for p in hs_items:
        if p: versions.setdefault(p.hs_code, set()).add(p.hs_ver)
    return [None if p is None else p.hs_code if len(versions[p.hs_code]) == 1 else f'{p.hs_code} {p.hs_ver}'
            for p in hs_items]


def shorten_target_names(targets: list[tuple[str | None, str | None, HsProduct | None, str | None]]) -> list[str]:
    """여러 국가·품목 추이(D-89)의 대상 이름. 대상은 (국가명, 품목명, 품목, 국가코드)이고 없는 쪽은 None이다.
    '국가 · 품목' 전체를 shorten_name 규칙으로 40자 안에 줄인다. 국가명만 붙이고 품목명을 따로 줄이면 40자를 넘어
    뒤가 잘리고 라벨이 겹치기 때문이다. 겹치면 품목 코드(없으면 국가코드)를 붙여 구분한다."""
    names = [' · '.join(part for part in (country, product) if part) for country, product, _, _ in targets]
    hs_tags = _hs_tags([hs for _, _, hs, _ in targets])
    return _disambiguate(names, [tag or code for tag, (_, _, _, code) in zip(hs_tags, targets, strict=True)])


def _disambiguate(names: list[str], tags: list[str | None]) -> list[str]:
    """줄인 이름이 겹치는 것에만 '(tag)'를 붙인다."""
    short = [shorten_name(n) for n in names]
    return [shorten_name(n, limit=MAX_LABEL_CHARS - len(t) - 2) + f'({t})' if short.count(s) > 1 and t else s
            for n, t, s in zip(names, tags, short, strict=True)]


def _check_bytes(value):
    if _json_size(value) > MAX_ARGS_BYTES:
        raise ValueError('인자가 너무 큽니다')
    return value


def _chars(minimum: int, maximum: int | None = None):
    """LLM 출력 모델(PlannerOutput·Draft)의 글자 수 검사. OpenAI strict 스키마는 minLength·maxLength를 지원하지 않아
    Field(min_length/max_length) 대신 응답을 받은 뒤 검사한다(같은 거부 → 재시도 동작)."""
    def check(value: str) -> str:
        if len(value.strip()) < minimum or (maximum is not None and len(value) > maximum):
            raise ValueError(f'글자 수는 {minimum}~{maximum or "제한 없음"}자여야 합니다')
        return value
    return AfterValidator(check)


Sentence = Annotated[str, _chars(1, MAX_TEXT_CHARS)]   # LLM 문장. 넘으면 거부해 재시도
ShortText = Annotated[str, _truncate(MAX_TEXT_CHARS)]                     # 코드가 만드는 문구. 넘으면 자른다
Label = Annotated[str, _truncate(MAX_LABEL_CHARS)]
SmallJson = Annotated[dict[str, JsonValue], AfterValidator(_check_bytes)]


class Model(BaseModel):
    # 노드는 필드를 여러 개 바꾼 뒤 반환한다. 검증은 LangGraph가 노드 입력으로 state를 만들 때 돈다.
    # 체크포인터는 model_validate를 부르지 않으므로, 다음 노드가 없는 finalize는 저장 전에
    # AgentState.model_validate(state.model_dump())로 다시 검증한다. 인스턴스를 그대로 넘기면 제자리에서 바꾼 칸은 다시 보지 않는다.
    # NaN·inf는 JSON에서 null이 되어 재개 전후 값이 달라지므로 받지 않는다(rag.Contract와 같음).
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Period(Model):
    """월 단위 조회 기간. start·end 모두 1일, 양끝 포함."""
    start: date
    end: date

    @field_validator('end')
    @classmethod
    def month_end_to_first(cls, end: date) -> date:
        # 기존 채팅 코드(common.period)는 MySQL context_json에 end를 그달 말일로 저장한다.
        return end.replace(day=1) if (end + timedelta(days=1)).day == 1 else end

    @model_validator(mode='after')
    def check_months(self):
        if self.start.day != 1 or self.end.day != 1 or self.start > self.end:
            raise ValueError('기간은 월 1일 기준이고 start <= end여야 합니다')
        return self


class Conditions(Model):
    """조건 확인 tool이 확정한 조회 조건. 빈 목록은 '전체'다."""
    hs_items: list[HsProduct] = Field(default_factory=list, max_length=MAX_HS_ITEMS)
    country_codes: list[CountryCode] = Field(default_factory=list, max_length=MAX_COUNTRIES)
    period: Period | None = None            # 수출입 기간(기본 최근 12개월)
    # None은 비교 없음. 비교를 요청했는데 기간이 없으면 check가 전년 동기를 계산해 넣는다(D-29, D-73)
    compare_period: Period | None = None
    article_period: ArticlePeriod | None = None   # 기사 발행일 기간, 일 단위(기본 최근 3개월)
    trade_flow: Literal['EXPORT', 'IMPORT'] = 'EXPORT'
    ranking_group: Literal['COUNTRY', 'HS6'] | None = None   # RANKING에서만(D-73)
    share_basis: Literal['KOREA_TRADE_PARTNER', 'FOREIGN_IMPORT_MARKET'] | None = None   # MARKET_SHARE에서만(D-87)

    @model_validator(mode='after')
    def check_duplicates(self):
        # RagRequest도 중복을 거부한다. 되묻기 답을 병합할 때 중복을 걸러야 한다.
        _unique([(p.hs_code, p.hs_ver) for p in self.hs_items], '품목·버전')
        _unique(self.country_codes, '국가')
        return self


class Interests(Model):
    hs_items: Annotated[list[HsProduct], _truncate(MAX_INTEREST_HS)] = Field(default_factory=list)
    country_codes: Annotated[list[CountryCode], _truncate(MAX_INTEREST_COUNTRIES)] = Field(default_factory=list)

    def fits_conditions(self) -> bool:
        """그대로 조건에 넣을 수 있으면 '관심 범위 적용'(INTEREST_CONTEXT), 아니면 골라 받는다(INTEREST_SELECT, D-64)."""
        return len(self.hs_items) <= MAX_HS_ITEMS and len(self.country_codes) <= MAX_COUNTRIES


class TurnSummary(Model):
    """답변 1건의 요약. summary는 compose가 쓴 문장, 조건은 주 하위 질문의 구조화된 값이다."""
    question: Annotated[str, _truncate(HISTORY_QUESTION_CHARS)]
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
    # 이 컨텍스트를 마지막으로 쓴 답변의 job_id. MySQL 성공 답변과 다르면 오래된 컨텍스트로 보고 조건만 복구한다(D-77)
    last_job_id: str | None = None

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
    # 접수 때 MySQL로 판정한 대화방 첫 질문 여부(그 방에 ASSISTANT 답변이 없음). 관심 되묻기(D-30)·제목(D-54) 기준.
    # Redis 컨텍스트가 비었는지로 판정하지 않는다(유실 뒤에도 비어 있음)
    first_question: bool = False
    received_at: datetime


class Step(Model):
    step_id: str            # 하위 질문 안에서만이 아니라 계획 전체에서 고유. 예: 't1.sql'
    tool: ToolName
    args: SmallJson = Field(default_factory=dict)   # 조건 밖의 tool별 인자(지표, 차트 유형 등)
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = 'PENDING'
    attempts: int = Field(default=0, ge=0, le=3)


class CountryMention(Model):
    """플래너가 옮긴 국가 표현 하나(§13.2, intent.py CountrySlot과 같은 모양).
    mention은 질문 원문 그대로, name은 플래너가 붙인 정식 한국어 국가명이다. 코드는 check_conditions가 확정한다."""
    mention: Annotated[str, _chars(1)]
    # 사용자가 쓴 값이 아니라 길다고 안내하지 않고 자른다. 가장 긴 국가명도 20자 안팎이다
    name: Annotated[str, _truncate(MAX_LABEL_CHARS), _chars(1)]


class PlannerSlots(Model):
    """플래너 LLM 출력의 미확정 표현. None은 생략(상속), 빈 목록은 명시적인 전체 범위다.
    국가·품목은 원문 표현만 받으며 코드는 check_conditions가 사전으로 확정한다.
    비교·순위·기사 기간은 새 요청에서 의미를 판단하며 무조건 상속하지 않는다.
    개수·길이 상한을 넘어도 받아서 limit_errors()로 안내한다(D-73). 확인 값은 칸이 없어 LLM이 넣으면 거부된다.
    """
    products: list[Annotated[str, _chars(1)]] | None = None
    countries: list[CountryMention] | None = None
    period: Period | None = None
    recent_months: int | None = Field(default=None, ge=1)
    trade_flow: Literal['EXPORT', 'IMPORT'] | None = None
    # None은 False와 같다. 기본값 False는 strict 스키마에 "default"로 실려 None으로 둔다(LLM 출력 모델)
    comparison_requested: bool | None = None
    compare_period: Period | None = None
    ranking_group: Literal['COUNTRY', 'HS6'] | None = None
    share_basis: Literal['KOREA_TRADE_PARTNER', 'FOREIGN_IMPORT_MARKET'] | None = None
    article_period: ArticlePeriod | None = None

    @model_validator(mode='after')
    def check_periods(self):
        if self.period is not None and self.recent_months is not None:
            raise ValueError('절대 기간과 최근 개월 수를 동시에 지정할 수 없습니다')
        if self.compare_period is not None and not self.comparison_requested:
            raise ValueError('비교 기간에는 비교 요청이 필요합니다')
        return self

    def limit_errors(self) -> list[str]:
        """TaskSlots 상한을 넘는 부분의 안내 문구. 있으면 플래너는 그 하위 질문을 단계 없이 NO_DATA로 끝낸다(D-73).
        C-26: 240개월은 확정 상한이며 실제 공개 범위는 conditions 노드가 별도로 검사한다."""
        errors = []
        if self.products and len(self.products) > MAX_HS_ITEMS:
            errors.append(f'품목은 질문 하나에 {MAX_HS_ITEMS}개까지 물어볼 수 있습니다.')
        if self.countries and len(self.countries) > MAX_COUNTRIES:
            errors.append(f'국가는 질문 하나에 {MAX_COUNTRIES}개까지 물어볼 수 있습니다.')
        mentions = [*(self.products or []), *(c.mention for c in self.countries or [])]
        if any(len(x) > MAX_SLOT_CHARS for x in mentions):
            errors.append('국가·품목 이름을 더 짧게 입력해 주세요.')
        if self.recent_months and self.recent_months > MAX_RECENT_MONTHS:
            errors.append(f'최근 {MAX_RECENT_MONTHS}개월까지 조회할 수 있습니다.')
        return errors


class TaskSlots(PlannerSlots):
    """Task에 저장하는 슬롯. 플래너는 PlannerSlots로 받고 limit_errors()가 없을 때만 이것으로 옮긴다.
    상한 검사는 limit_errors() 하나로 한다(개수·길이·최근 개월 수가 두 모델에서 어긋나지 않게)."""
    latest_prediction_confirmed: bool = False  # MODEL_PERIOD에 사용자가 동의했을 때 ask_user만 True로 바꾼다(D-74)

    @model_validator(mode='after')
    def check_limits(self):
        if errors := self.limit_errors():
            raise ValueError(' '.join(errors))
        return self


def task_count_error(count: int) -> str | None:
    """동일 분석 병합 후 호출. 초과 질문을 자르거나 일부만 Plan으로 만들지 않는다(D-86)."""
    return TOO_MANY_TASKS_MESSAGE if count > MAX_TASKS else None


class TargetSelection(Model):
    """선행 표에서 다음 분석 대상을 고르는 규칙. null인 기준은 INPUT_SELECTION으로 확인한다(D-95)."""
    dimension: Literal['COUNTRY', 'HS6'] | None = None
    limit: int | None = Field(default=None, ge=1)
    order_by: Literal['country_rank', 'product_rank', 'amount_usd'] | None = None


class PlannedTargetSelection(TargetSelection):
    source_task: int = Field(ge=1)


class PlannedTask(Model):
    """플래너 LLM이 내는 하위 질문 하나(설계 §11.1). 단계·조건·결과는 코드가 채운다."""
    intent: Intent
    question: Annotated[str, _chars(1)]   # Task로 옮길 때 TASK_QUESTION_CHARS로 자른다
    slots: PlannerSlots = Field(default_factory=PlannerSlots)
    # 템플릿(월별 추이·순위·비중)으로 답할 수 없을 때만 필요한 조회를 한국어로 적는다. 있으면 LLM SQL로 간다(§11.5)
    # 설명일 뿐이라 길면 거부하지 않고 자른다. 거부하면 플래너 출력 전체가 실패한다
    custom_query: Annotated[str, _truncate(MAX_TEXT_CHARS), _chars(1)] | None = None
    source_tasks: list[int] = Field(default_factory=list)  # 1부터 시작하는 선행 질문 번호. ID는 서버가 만든다.
    target_selections: list[PlannedTargetSelection] = Field(default_factory=list)


class PlannerOutput(Model):
    """플래너 LLM 출력 전체(responses.parse의 text_format). 개수 상한을 두지 않고 task_count_error()로 판단한다(D-86)."""
    tasks: list[PlannedTask] = Field(min_length=1)

    @model_validator(mode='after')
    def check_sources(self):
        for index, task in enumerate(self.tasks, 1):
            _unique(task.source_tasks, '선행 질문')
            if any(source < 1 or source >= index for source in task.source_tasks):
                raise ValueError('선행 질문은 현재 질문보다 앞에 있어야 합니다')
            selected = [s.source_task for s in task.target_selections]
            _unique(selected, '대상 선택의 선행 질문')
            if not set(selected) <= set(task.source_tasks):
                raise ValueError('대상 선택은 연결된 선행 질문을 가리켜야 합니다')
        return self


class ResultBinding(Model):
    """선행 질문의 저장 결과 참조. 실제 result_id와 조건은 서버가 results에서 읽는다(D-90)."""
    source_task_id: str
    source_step_id: str
    selection: TargetSelection | None = None  # None은 저장 결과 자체 전달, 객체는 국가·품목 추출


class ReportRequest(Model):
    """REPORT_CONFIRM으로 확인할 미리보기. 조건 변경·만료 시 새 미리보기로 다시 확인한다."""
    preview_id: str
    expires_at: datetime
    conditions: Conditions
    formats: list[Literal['PDF', 'DOCX']] = Field(min_length=1, max_length=2)
    confirmed: bool = False  # ask_user만 사용자의 확인을 기록한다

    @model_validator(mode='after')
    def check_request(self):
        _unique(self.formats, '리포트 형식')
        if self.conditions.trade_flow != 'EXPORT':
            raise ValueError('리포트는 수출만 지원합니다')
        return self


class Task(Model):
    """복합 질문을 나눈 하위 질문 하나. 첫 번째가 주 의도이며 최근 검색 조건·요약의 기준이다."""
    task_id: str
    intent: Intent
    slots: TaskSlots = Field(default_factory=TaskSlots)
    custom_query: Annotated[str, _truncate(MAX_TEXT_CHARS), _chars(1)] | None = None
    input_bindings: list[ResultBinding] = Field(default_factory=list, max_length=2)
    answer_status: AnswerStatus | None = None  # 해당 하위 질문의 최종 결과. None은 미완료
    question: Annotated[str, _truncate(TASK_QUESTION_CHARS), Field(min_length=1)]   # 플래너가 나눈 하위 질문 문장
    steps: list[Step] = Field(default_factory=list, max_length=6)
    conditions: Conditions | None = None        # check_conditions가 채움
    report_request: ReportRequest | None = None
    applied_defaults: Annotated[list[ShortText], _truncate(MAX_LIST_ITEMS)] = Field(default_factory=list)   # "적용한 조건" 표시용
    # 되묻기로 확정한 답(condition_key → 병합할 값). check는 재실행될 때 이것부터 적용하고 이미 확정한 표현은 다시
    # 찾거나 묻지 않는다. 예: 't1:hs:화장품' → {'hs_items': [...]}, 't1:interest' → {'interest': 'GLOBAL'}
    resolved: dict[Annotated[str, _truncate(MAX_TEXT_CHARS)], SmallJson] = Field(default_factory=dict, max_length=32)

    @model_validator(mode='after')
    def check_outcome(self):
        if _json_size(self.resolved) > MAX_RESOLVED_BYTES:
            raise ValueError('확정한 되묻기 답이 너무 큽니다')
        if self.report_request and (self.intent != 'REPORT' or self.conditions != self.report_request.conditions):
            raise ValueError('리포트 미리보기는 해당 REPORT의 확정 조건과 같아야 합니다')
        # 결과는 단계가 모두 끝난 뒤에만 정한다. 남은 단계가 있으면 전체 결과(D-72)를 미완료 값으로 집계하게 된다.
        if self.answer_status is not None and any(s.status in ('PENDING', 'RUNNING') for s in self.steps):
            raise ValueError(f'{self.task_id}: 끝나지 않은 단계가 있는데 결과가 정해졌습니다')
        return self


class Plan(Model):
    tasks: list[Task] = Field(min_length=1, max_length=MAX_TASKS)

    @model_validator(mode='after')
    def check_order(self):
        task_ids, step_ids = set(), set()
        producers = {}
        for task in self.tasks:
            if task.task_id in task_ids: raise ValueError(f'{task.task_id}: 중복 하위 질문 ID')
            task_ids.add(task.task_id)
            bound = [b.source_step_id for b in task.input_bindings]
            _unique(bound, '입력 결과')
            for binding in task.input_bindings:
                if producers.get(binding.source_step_id) != binding.source_task_id:
                    raise ValueError('입력 결과는 앞 하위 질문의 SQL 또는 ML 단계여야 합니다')
            if task.intent in NO_STEP_INTENTS:
                if task.steps or task.input_bindings: raise ValueError(f'{task.intent}는 실행 단계가 없습니다')
                continue
            if not task.steps and task.answer_status == 'NO_DATA':
                continue   # 플래너가 상한 초과로 실행 없이 끝낸 하위 질문(D-73)
            if not task.steps or task.steps[0].tool != 'check_conditions':
                raise ValueError(f'{task.task_id}: 첫 단계는 check_conditions여야 합니다')
            if not set(bound) <= set(task.steps[0].depends_on):
                raise ValueError('조건 확인은 모든 입력 결과를 기다려야 합니다')
            seen = set(bound)
            for step in task.steps:
                # 같은 질문의 앞 단계 또는 명시적으로 연결한 선행 결과만 허용한다.
                if step.step_id in step_ids or not set(step.depends_on) <= seen:
                    raise ValueError(f'{step.step_id}: 중복 ID이거나 앞 단계가 아닌 곳에 의존합니다')
                seen.add(step.step_id); step_ids.add(step.step_id)
            producers.update({s.step_id: task.task_id for s in task.steps
                              if s.tool in ('query_trade_data', 'query_market_model')})
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
    label: ShortText
    patch: SmallJson   # 서버가 만드는 선택 값. 조건 또는 MODEL_PERIOD 확인으로 종류별 검증 후 적용


class Pending(Model):
    """되묻기 대기. 답(선택지·직접 입력)을 조건에 병합하고 그 하위 질문의 check_conditions부터 다시 돈다."""
    clarification_id: str
    task_id: str
    # INTEREST_SELECT: 관심사가 조건 상한을 넘을 때 input.interests 안에서 품목 5개·국가 20개까지 골라 받는다(D-64).
    # 후보는 state에 이미 있으므로 choices에는 '전체 범위'만 둔다.
    kind: Literal['HS_ITEM', 'COUNTRY', 'INTEREST_CONTEXT', 'INTEREST_SELECT', 'INTEREST_CHANGE', 'RANKING_GROUP', 'MODEL_PERIOD', 'REPORT_CONFIRM', 'SHARE_BASIS', 'INPUT_SELECTION']
    condition_key: ShortText   # 되묻기 횟수를 세는 단위. 예: 't1:hs:화장품', 't1:country:콩고'
    message: ShortText
    choices: list[Choice] = Field(max_length=5)
    # 표시용(되묻기 문구의 "남은 품목 n개"). 확정한 값의 원본은 Task.resolved이고, check는 그것으로 재실행한다(§3-12)
    remaining: list[ShortText] = Field(default_factory=list, max_length=MAX_HS_ITEMS)   # 아직 못 정한 품목 표현
    selected: list[HsProduct] = Field(default_factory=list, max_length=MAX_HS_ITEMS)  # 앞 표현에서 고른 품목
    asked_at: datetime


class ChartSeries(Model):
    name: Label   # 품목명이면 HS4 이름이 길 수 있어 자른다
    values: list[float | None]   # 값이 없는 칸은 None. 결과표에서 코드로 채운다


class ChartSpec(Model):
    """answer.charts[] 형식. charts.jsx props와는 다르므로 프론트가 바꿔 그린다
    (LineChart는 yTicks·색 추가, Donut은 [label, 비율, 색], None 칸은 선을 끊음)."""
    chart_id: str
    task_id: str
    type: Literal['line', 'bar', 'grouped_bar', 'donut']   # 추이 / 순위 / 두 시점 비교 / 비중
    title: Label
    unit: Annotated[str, Field(max_length=20)]
    period: Period | None = None
    granularity: Literal['MONTH', 'QUARTER', 'YEAR'] | None = None  # 시계열 집계 단위(D-83)
    source: Label
    result_id: str
    labels: list[Label] = Field(min_length=1, max_length=MAX_CHART_LABELS)
    series: list[ChartSeries] = Field(min_length=1, max_length=MAX_CHART_SERIES)

    @model_validator(mode='after')
    def check_shape(self):
        if any(len(s.values) != len(self.labels) for s in self.series):
            raise ValueError('시리즈 값 수가 labels 수와 다릅니다')
        # 40자에서 자른 뒤에 검사한다. 품목명은 shorten_names로 만들면 겹치지 않는다.
        _unique(self.labels, '차트 x축 값')
        _unique([s.name for s in self.series], '시리즈 이름')
        if self.type == 'donut' and len(self.series) != 1:
            raise ValueError('donut은 시리즈 1개입니다')
        if self.type == 'grouped_bar' and len(self.series) != 2:
            raise ValueError('grouped_bar는 두 시점, 시리즈 2개입니다')
        return self


class ChartSelector(Model):
    """모든 대상의 차트를 state에 넣지 않고 저장 결과표에서 선택 시 생성한다(D-82)."""
    result_id: str
    group_by: Literal['COUNTRY', 'HS6', 'PAIR']
    country_codes: list[CountryCode] = Field(default_factory=list, max_length=MAX_COUNTRIES)
    hs_items: list[HsProduct] = Field(default_factory=list, max_length=MAX_HS_ITEMS)

    @model_validator(mode='after')
    def check_targets(self):
        _unique(self.country_codes, '차트 선택 국가')
        _unique([(p.hs_code, p.hs_ver) for p in self.hs_items], '차트 선택 품목')
        if ((self.group_by == 'COUNTRY' and (not self.country_codes or self.hs_items))
                or (self.group_by == 'HS6' and (not self.hs_items or self.country_codes))
                or (self.group_by == 'PAIR' and (not self.country_codes or not self.hs_items))):
            raise ValueError('차트 선택 기준과 대상 목록이 맞지 않습니다')
        # ML 결과표에는 HS6 행만 있다(D-74). HS4는 check가 HS6로 되물어 정한 뒤에만 여기에 온다.
        if any(len(p.hs_code) != 6 for p in self.hs_items):
            raise ValueError('차트 선택 품목은 HS6여야 합니다')
        return self


class ToolResult(Model):
    """tool 결과 봉투. 원본은 MySQL·Chroma에 있고 여기엔 참조와 작은 요약만 둔다."""
    status: Literal['OK', 'NO_DATA', 'FAILED']
    completeness: Literal['COMPLETE', 'PARTIAL'] = 'COMPLETE'
    # 누락 대상·근거 부족 등은 코드로 기록한다. 경고 문구를 파싱해 결과 상태를 정하지 않는다.
    limitations: list[ShortText] = Field(default_factory=list, max_length=100)
    result_id: str | None = None                  # query_trade_data·query_market_model 결과표
    article_ids: list[int] = Field(default_factory=list, max_length=20)
    chart: ChartSpec | None = None                # build_chart
    chart_selector: ChartSelector | None = None   # 복수 ML 대상. 원본 행·모든 차트 대신 선택 메타데이터
    report_id: str | None = None                  # generate_report 비동기 작업
    period: Period | None = None                  # 수출입·ML 기준 기간(월 단위)
    article_period: ArticlePeriod | None = None   # search_articles 기준 기간(일 단위). 월 기간과 섞으면 JSON 왕복에서 종류가 바뀐다
    source: Label | None = None                   # 출처
    checks: Annotated[list[Label], _truncate(MAX_LIST_ITEMS)] = Field(default_factory=list)   # 통과한 자체 검증 이름
    # LLM 입력용: 합계·상위 행 등. Decimal·date·numpy 값은 tool이 int·float·str로 바꿔 넣는다(numpy는 .item()·tolist())
    digest: dict[str, JsonValue] | None = None
    error: ShortText | None = None                # 사용자에게 보여도 되는 사유

    @model_validator(mode='after')
    def check_digest(self):
        if _json_size(self.limitations) > 1024:
            raise ValueError('누락 사유는 1KB 이내로 요약하고 전체 대상 목록은 결과표 meta에 저장하세요')
        if self.completeness == 'PARTIAL' and (self.status != 'OK' or not self.limitations):
            raise ValueError('부분 결과는 OK 상태와 누락 사유가 필요합니다')
        if self.completeness == 'COMPLETE' and self.limitations:
            raise ValueError('누락 사유가 있으면 PARTIAL이어야 합니다')
        references = [ref for ref in (self.result_id,
                      self.chart.result_id if self.chart else None,
                      self.chart_selector.result_id if self.chart_selector else None) if ref is not None]
        if len(set(references)) > 1:
            raise ValueError('결과·차트·차트 선택은 같은 결과표를 참조해야 합니다')
        if self.digest is not None and _json_size(self.digest) > MAX_DIGEST_BYTES:
            raise ValueError('digest가 너무 큽니다. 행 원본은 결과표에 두세요')
        if self.status == 'FAILED' and not self.error:
            raise ValueError('실패에는 사유가 필요합니다')
        return self


class TaskConclusion(Model):
    task_id: str
    sentences: list[Sentence] = Field(min_length=1, max_length=2)

    @classmethod
    def report_completed(cls, task_id: str) -> 'TaskConclusion':
        """워커가 READY를 확인한 뒤 compose가 사용하는 고정 결론(D-93)."""
        return cls(task_id=task_id, sentences=[
            '요청하신 리포트가 완성되었습니다. 아래에서 확인하거나 다운로드할 수 있습니다.'])


class Draft(Model):
    """compose의 LLM 호출 1번으로 답변 문장, 대화 요약, (첫 답변이면) 대화방 제목을 같이 받는다.
    결론은 하위 질문마다 1~2문장, 설명은 전체 4문장 이하. LLM이 문장을 나눠 주므로 verify는 문장 번호로 대조한다."""
    conclusion: list[TaskConclusion] = Field(min_length=1, max_length=MAX_TASKS)
    explanation: list[Sentence] = Field(default_factory=list, max_length=MAX_EXPLANATION_SENTENCES)
    turn_summary: Annotated[str, _chars(0, 200)]
    title: Annotated[str, _chars(1, 20)] | None = None

    def sentences(self) -> list[str]:
        """verify가 대조하는 순서: 결론 다음 설명."""
        return [sentence for item in self.conclusion for sentence in item.sentences] + self.explanation


class SentenceCheck(Model):
    index: int = Field(ge=0)   # Draft.sentences()의 번호. 문장 원문은 draft에 있다
    numbers: list[Label] = Field(default_factory=list)   # 문장에서 찾은 숫자. 문장 200자 안이라 개수도 그 안에 묶인다
    status: Literal['PASS', 'MISMATCH']


class Verification(Model):
    status: Literal['PENDING', 'PASS', 'NEEDS_REVIEW'] = 'PENDING'
    checks: list[SentenceCheck] = Field(default_factory=list, max_length=MAX_CONCLUSION_SENTENCES + MAX_EXPLANATION_SENTENCES)
    regeneration_count: int = Field(default=0, ge=0, le=1)


class ReportWait(Model):
    """리포트 완료 대기 참조. 원본 작업 상태·재시도는 MySQL report가 관리한다."""
    step_id: str
    report_id: str
    started_at: datetime


class RunBudget(Model):
    """라운드별 실행 예산의 체크포인트 사본. 장애 복구 시 MySQL 실행 메타데이터가 우선한다(D-85)."""
    charged_seconds: float = Field(default=0, ge=0)  # 확정 소요 시간 + 미완료 호출의 예약 시간
    recovery_count: int = Field(default=0, ge=0)

    def remaining_seconds(self) -> float:
        return max(0.0, RUN_TIMEOUT_SECONDS - self.charged_seconds)


class AgentState(Model):
    schema_version: Literal[2] = SCHEMA_VERSION
    input: AgentInput
    status: RunStatus = 'QUEUED'
    plan: Plan | None = None
    data_meta: DataMeta | None = None
    pending: Pending | None = None
    report_wait: ReportWait | None = None
    # 이미 MAX_ASKS_PER_CONDITION번 물은 조건은 올리지 말고 "조건을 확인해 다시 질문해 주세요"로 끝낸다
    ask_counts: dict[str, Annotated[int, Field(ge=0, le=MAX_ASKS_PER_CONDITION)]] = Field(default_factory=dict)
    round_started_at: datetime | None = None   # 라운드 시작 표시용. 대기·복구 때 이동/초기화하지 않는다(D-85)
    run_budget: RunBudget = Field(default_factory=RunBudget)
    results: dict[str, ToolResult] = Field(default_factory=dict)  # step_id → 결과
    draft: Draft | None = None
    verification: Verification = Field(default_factory=Verification)
    warnings: Annotated[list[ShortText], _truncate(MAX_LIST_ITEMS)] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_consistency(self):
        if (self.status == 'NEEDS_INPUT') != (self.pending is not None):
            raise ValueError('NEEDS_INPUT일 때만 pending이 있습니다')
        if (self.status == 'WAITING_REPORT') != (self.report_wait is not None):
            raise ValueError('WAITING_REPORT일 때만 report_wait가 있습니다')
        if self.report_wait:
            step = next((s for s in self.plan.steps() if s.step_id == self.report_wait.step_id), None) if self.plan else None
            if not step or step.tool != 'generate_report' or step.status != 'RUNNING':
                raise ValueError('리포트 대기는 실행 중인 generate_report 단계를 가리켜야 합니다')
        owner = {s.step_id: t.task_id for t in self.plan.tasks for s in t.steps} if self.plan else {}
        if not set(self.results) <= set(owner):
            raise ValueError('계획에 없는 단계의 결과입니다')
        if any(r.chart and r.chart.task_id != owner[step_id] for step_id, r in self.results.items()):
            raise ValueError('차트의 task_id가 그 단계의 하위 질문과 다릅니다')
        if self.pending and (not self.plan or self.pending.task_id not in {t.task_id for t in self.plan.tasks}):
            raise ValueError('되묻기가 계획에 없는 하위 질문을 가리킵니다')
        if self.status == 'SUCCEEDED' and self.plan and any(t.answer_status is None for t in self.plan.tasks):
            raise ValueError('끝난 작업은 모든 하위 질문의 결과가 있어야 합니다')
        if self.draft and (not self.plan or [c.task_id for c in self.draft.conclusion] != [t.task_id for t in self.plan.tasks]):
            raise ValueError('결론은 모든 하위 질문을 계획 순서대로 한 번씩 포함해야 합니다')
        indexes = [c.index for c in self.verification.checks]
        if indexes and (not self.draft or len(set(indexes)) != len(indexes) or max(indexes) >= len(self.draft.sentences())):
            raise ValueError('문장 대조가 답변에 없는 문장을 가리킵니다')
        if _json_size(self) > MAX_STATE_BYTES:
            raise ValueError('state가 체크포인트 상한을 넘습니다')
        return self

    def steps_finished(self) -> bool:
        """compose로 넘어가도 되는지. dispatch는 ready_steps()보다 먼저 부르고, False면 compose로 가지 않는다.
        리포트·되묻기 대기 중이거나 PENDING·RUNNING 단계가 남아 있으면 False다(D-76).
        먼저 skip_blocked()로 실패에 막힌 단계를 SKIPPED로 정리한다. 실패를 둔 노드가 정리를 빠뜨려도
        '실행할 단계도 없고 끝나지도 않은' 상태로 멈추지 않게 하기 위해서다."""
        if not self.plan or self.status in ('WAITING_REPORT', 'NEEDS_INPUT'):
            return False
        self.skip_blocked()
        return all(s.status in ('DONE', 'FAILED', 'SKIPPED') for s in self.plan.steps())

    def ready_steps(self) -> list[Step]:
        """의존 단계가 모두 DONE인 대기 단계. 하위 질문 순서대로 나온다. 비었다고 끝난 것은 아니다(steps_finished)."""
        if self.status in ('WAITING_REPORT', 'NEEDS_INPUT', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'CANCELLED'): return []
        if not self.plan: return []
        steps = self.plan.steps()
        done = {s.step_id for s in steps if s.status == 'DONE'}
        return [s for s in steps if s.status == 'PENDING' and set(s.depends_on) <= done]

    def task_outcome(self, task_id: str) -> AnswerStatus:
        """종료한 Task의 결과를 계산한다. compose 전 호출하고 반환값을 answer_status에 쓴다(§14.9)."""
        task = next((t for t in self.plan.tasks if t.task_id == task_id), None) if self.plan else None
        if task is None:
            raise ValueError('계획에 없는 하위 질문입니다')
        if any(s.status in ('PENDING', 'RUNNING') for s in task.steps):
            raise ValueError('진행 중인 하위 질문은 결과를 집계할 수 없습니다')
        if task.intent == 'OUT_OF_SCOPE':
            return 'OUT_OF_SCOPE'
        if task.intent == 'MARKET_TYPE' or not task.steps:
            return 'NO_DATA'
        if task.steps[0].status != 'DONE':
            return 'NO_DATA'
        core_tools = {'query_trade_data', 'query_market_model', 'generate_report'}
        if task.intent == 'LOCAL_ISSUES':
            core_tools.add('search_articles')
        core = [s for s in task.steps if s.tool in core_tools]
        if not core or any(s.status != 'DONE' or s.step_id not in self.results
                           or self.results[s.step_id].status != 'OK' for s in core):
            return 'NO_DATA'
        relevant = [self.results[s.step_id] for s in task.steps if s.step_id in self.results]
        for binding in task.input_bindings:
            result = self.results.get(binding.source_step_id)
            if result is None or result.status != 'OK':
                return 'NO_DATA'
            relevant.append(result)
        # 저장 경계에서 전파하지 않은 이전 체크포인트도 간접 선행 결과를 확인한다.
        for step in core:
            relevant.append(self.result_for_storage(step.step_id, self.results[step.step_id]))
        if (any(s.status != 'DONE' for s in task.steps)
                or any(r.status != 'OK' or r.completeness == 'PARTIAL' for r in relevant)):
            return 'PARTIAL'
        return 'ANSWERED'

    def result_for_storage(self, step_id: str, result: ToolResult) -> ToolResult:
        """MySQL 결과 저장 전에 호출한다. 실제 실행 의존 경로의 누락 사유를 전이 병합한다(D-96).

        원본 결과/state는 바꾸지 않는다. 반환값을 결과표 meta와 state.results 양쪽에 저장해야 한다.
        """
        steps = {s.step_id: s for s in self.plan.steps()} if self.plan else {}
        if step_id not in steps:
            raise ValueError('계획에 없는 단계입니다')
        if result.status != 'OK':
            return ToolResult.model_validate(result.model_dump())
        reasons = list(result.limitations)
        visited = {step_id}

        def collect(current):
            for dependency in steps[current].depends_on:
                if dependency in visited:
                    continue
                visited.add(dependency)
                if dependency not in steps or steps[dependency].status != 'DONE':
                    raise ValueError('완료되지 않은 입력으로 성공 결과를 저장할 수 없습니다')
                upstream = self.results.get(dependency)
                if upstream is not None:
                    if upstream.status != 'OK':
                        raise ValueError('실패한 입력으로 성공 결과를 저장할 수 없습니다')
                    reasons.extend(upstream.limitations)
                elif steps[dependency].tool in ('query_trade_data', 'query_market_model'):
                    raise ValueError('선행 분석 결과가 없습니다')
                collect(dependency)

        collect(step_id)
        reasons = list(dict.fromkeys(reasons))
        # 상세 원인은 선행 결과 meta에 유지한다. state의 요약 상한 때문에 실행을 실패시키지 않는다.
        summary = []
        omitted = '추가 누락 사유는 선행 결과의 상세 정보를 확인하세요.'
        for reason in reasons:
            if len(summary) >= 99 or _json_size([*summary, reason, omitted]) > 1024:
                summary.append(omitted)
                break
            summary.append(reason)
        data = result.model_dump()
        data.update(completeness='PARTIAL' if reasons else 'COMPLETE', limitations=summary)
        return ToolResult.model_validate(data)

    def skip_blocked(self) -> list[str]:
        """FAILED·SKIPPED 단계에 의존하는 대기 단계를 SKIPPED로 바꾼다. 단계를 FAILED로 둔 노드(check_conditions·tool)가 부른다.
        단계는 계획 순서상 앞 단계에만 의존하므로 한 번 훑으면 하위 질문 간 전이 의존까지 처리된다."""
        if not self.plan: return []
        stopped = {s.step_id for s in self.plan.steps() if s.status in ('FAILED', 'SKIPPED')}
        skipped = []
        for s in self.plan.steps():
            if s.status == 'PENDING' and stopped & set(s.depends_on):
                s.status = 'SKIPPED'; stopped.add(s.step_id); skipped.append(s.step_id)
        return skipped
