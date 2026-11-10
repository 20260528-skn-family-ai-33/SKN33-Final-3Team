"""LangGraph orchestration; service I/O is supplied by the worker adapter.

Checkpoints contain only AgentState. Callers use job_id as thread_id and perform
cleanup after invoke returns, never inside finalize. Tests use InMemorySaver.
"""
from typing import Protocol, Any
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from backend.app.agents.state import AgentState, Step, ToolResult, ReportWait, is_text_task


class NodeServices(Protocol):
    def guard(self, state: AgentState, node: str) -> None: ...
    def planner(self, state: AgentState) -> None: ...
    def check(self, state: AgentState, step: Step) -> None: ...
    def tool(self, state: AgentState, step: Step) -> ToolResult | ReportWait: ...
    def resume(self, state: AgentState, answer: Any) -> None: ...
    def report_ready(self, state: AgentState) -> ToolResult: ...
    def compose(self, state: AgentState) -> None: ...
    def compose_text(self, state: AgentState) -> None: ...
    def verify(self, state: AgentState) -> None: ...
    def finalize(self, state: AgentState) -> None: ...


def checked(state: AgentState) -> dict:
    return AgentState.model_validate(state.model_dump()).model_dump()


def compose_node(state: AgentState) -> str:
    """문장형 하위 질문(D-98)이 있으면 문장 답변 노드로 간다. 예산·진행 표시는 compose와 같이 쓴다."""
    return 'compose_text' if state.plan and any(map(is_text_task, state.plan.tasks)) else 'compose'


def next_step(state: AgentState) -> Step:
    steps = state.ready_steps()
    if not steps:
        raise ValueError('미완료 계획에 실행 가능한 단계가 없습니다')
    return steps[0]


def build_graph(services: NodeServices, checkpointer, *, regeneration_seconds=35):
    """Compile with explicit checkpointer; importing this module makes no connections."""
    builder = StateGraph(AgentState)

    def planner(state):
        services.guard(state, 'planner')
        state.status = 'RUNNING'
        services.planner(state)
        return checked(state)

    def dispatch(state):
        services.guard(state, 'dispatch')
        state.skip_blocked()
        return checked(state)

    def route(state):
        if state.steps_finished():
            return compose_node(state)
        return next_step(state).tool

    def check(state):
        services.guard(state, 'check_conditions')
        step = next_step(state)
        services.check(state, step)
        if step.status == 'FAILED': state.skip_blocked()
        if state.status != 'NEEDS_INPUT' and step.status not in ('DONE', 'FAILED'):
            raise ValueError('조건 확인은 완료·실패 또는 되묻기로 끝나야 합니다')
        return checked(state)

    def ask_user(state):
        # No I/O or counters before interrupt: this node reruns from the start.
        answer = interrupt(state.pending.model_dump(mode='json'))
        services.guard(state, 'ask_user')
        services.resume(state, answer)
        return checked(state)

    def tool(state):
        step = next_step(state)
        services.guard(state, step.tool)
        step.status = 'RUNNING'
        result = services.tool(state, step)
        if isinstance(result, ReportWait):
            if step.tool != 'generate_report' or result.step_id != step.step_id:
                raise ValueError('리포트 대기 참조가 실행 단계와 다릅니다')
            state.status, state.report_wait = 'WAITING_REPORT', result
        else:
            # Persistence adapters also call this BEFORE saving result rows (D-96).
            state.results[step.step_id] = state.result_for_storage(step.step_id, result)
            step.status = 'DONE' if result.status == 'OK' else 'FAILED'
            state.skip_blocked()
        return checked(state)

    def report_ready(state):
        services.guard(state, 'report_ready')
        wait = state.report_wait
        if wait is None:
            raise ValueError('리포트 대기 정보가 없습니다')
        result = services.report_ready(state)
        state.results[wait.step_id] = state.result_for_storage(wait.step_id, result)
        step = next(s for s in state.plan.steps() if s.step_id == wait.step_id)
        step.status = 'DONE' if result.status == 'OK' else 'FAILED'
        state.report_wait, state.status = None, 'RUNNING'
        state.skip_blocked()
        return checked(state)

    def write_answer(state, write):
        services.guard(state, 'compose')
        if not state.steps_finished():
            raise ValueError('단계가 끝나기 전에 답변을 만들 수 없습니다')
        for task in state.plan.tasks:
            task.answer_status = state.task_outcome(task.task_id)
        write(state)
        # A rewritten draft can have fewer sentences. The old indexes were
        # consumed by compose; only verify may attach checks to this draft.
        state.verification.checks = []
        state.verification.feedback = []
        state.verification.failed_summary = ''
        state.verification.summary_status = None
        return checked(state)

    def compose(state):
        return write_answer(state, services.compose)

    def compose_text(state):
        return write_answer(state, services.compose_text)

    def verify(state):
        services.guard(state, 'verify')
        services.verify(state)
        if (state.verification.status == 'NEEDS_REVIEW'
                and state.verification.regeneration_count == 0
                and state.run_budget.remaining_seconds() >= regeneration_seconds):
            if state.verification.failed_summary:
                state.draft.turn_summary = state.verification.failed_summary
            state.verification.regeneration_count = 1
            state.verification.status = 'PENDING'
        return checked(state)

    def finalize(state):
        services.guard(state, 'finalize')
        state.status = 'SUCCEEDED'
        # Revalidate mutated nested models before the durable transaction.
        state = AgentState.model_validate(state.model_dump())
        services.finalize(state)
        return checked(state)

    for name, node in [('planner', planner), ('dispatch', dispatch), ('check_conditions', check),
                       ('ask_user', ask_user), ('compose', compose), ('compose_text', compose_text), ('verify', verify),
                       ('finalize', finalize), ('report_ready', report_ready)]:
        builder.add_node(name, node)
    for name in ('query_trade_data', 'search_articles', 'build_chart', 'query_market_model', 'generate_report', 'lookup_hs'):
        builder.add_node(name, tool)
        if name == 'generate_report':
            builder.add_conditional_edges(name, lambda s: 'report_ready' if s.report_wait else 'dispatch',
                                          ['report_ready', 'dispatch'])
        else:
            builder.add_edge(name, 'dispatch')
    builder.add_edge(START, 'planner')
    builder.add_edge('planner', 'dispatch')
    builder.add_conditional_edges('dispatch', route, ['compose', 'compose_text', 'check_conditions', 'query_trade_data',
        'search_articles', 'build_chart', 'query_market_model', 'generate_report', 'lookup_hs'])
    builder.add_conditional_edges('check_conditions',
        lambda s: 'ask_user' if s.status == 'NEEDS_INPUT' else 'dispatch', ['ask_user', 'dispatch'])
    builder.add_edge('ask_user', 'dispatch')
    builder.add_edge('report_ready', 'dispatch')
    builder.add_edge('compose', 'verify')
    builder.add_edge('compose_text', 'verify')
    builder.add_conditional_edges('verify',
        lambda s: compose_node(s) if s.verification.status == 'PENDING' else 'finalize',
        ['compose', 'compose_text', 'finalize'])
    builder.add_edge('finalize', END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=['report_ready'])


def graph_config(job_id: str) -> dict:
    # Three tasks, repeated clarifications, tool dispatch and one regeneration
    # exceed LangGraph's default 25 even for valid plans. Time is bounded by D-85.
    return {'configurable': {'thread_id': str(job_id)}, 'recursion_limit': 200}
