"""Short claim-checked transactions around graph I/O (D-77, D-85).

This adapter does not enable the new graph on legacy chat jobs. Admission adds
payload_json.input only after the complete service adapter is wired.
"""
from copy import deepcopy
from datetime import datetime
from time import monotonic
from uuid import uuid4

from langgraph.types import Command

from backend.app.agents import budget
from backend.app.agents.graph import graph_config
from backend.app.agents.state import AgentInput, AgentState, RunBudget
from backend.app.api import jobs
from backend.app.api.common import require
from backend.app.api.database import Store


class ExecutionStore:
    def __init__(self, database, job):
        self.database, self.job = database, deepcopy(job)

    def transaction(self, operation):
        with self.database.engine.begin() as connection:
            store = Store(self.database, connection)
            current = jobs.locked_current(store, self.job)
            require(current and current['status'] == 'RUNNING'
                    and current['claim_token'] == self.job['claim_token'],
                    '작업이 중단되었거나 다른 실행기로 이전되었습니다.', 'JOB_CLAIM_LOST', 409)
            result = operation(store, current)
            jobs._save_chat(store, current)
            return result

    def read(self):
        with self.database.engine.begin() as connection:
            current=jobs.locked_current(Store(self.database,connection),self.job)
            require(current and current['status']=='RUNNING' and current['claim_token']==self.job['claim_token'],
                    '작업이 중단되었거나 다른 실행기로 이전되었습니다.','JOB_CLAIM_LOST',409)
            return deepcopy(current)

    def transition(self, transform):
        def update(store, current):
            current['agent'] = transform(deepcopy(current['agent']))
            return deepcopy(current['agent'])
        return self.transaction(update)

    def sync_budget(self, state):
        ledger = self.read()['agent']
        state.run_budget = RunBudget(charged_seconds=ledger['charged_seconds'],
                                    recovery_count=ledger['recovery_count'])
        state.round_started_at = datetime.fromisoformat(ledger['round_received_at'])

    def call(self, node, timeout, callback, *, finish_reserve=0, clock=monotonic):
        ledger = self.read()['agent']
        identity = dict(round_id=ledger['round_id'], claim_token=self.job['claim_token'], call_id=uuid4().hex)
        self.transition(lambda value: budget.reserve(value, **identity, node=node,
                            timeout=timeout, finish_reserve=finish_reserve))
        started = clock()
        try:
            # There is no transaction open while external work runs.
            return callback(timeout)
        finally:
            elapsed = clock() - started
            def record(value):
                settled=budget.settle(value,**identity,elapsed=elapsed)
                timings=settled.setdefault('timings',{})
                timings[node]=round(timings.get(node,0)+elapsed,4)
                return settled
            self.transition(record)

    def clear_resume(self, expected):
        def clear(store, current):
            require(current['payload_json'].get('resume') == expected,
                    '되묻기 답이 변경되었습니다.', 'JOB_CLAIM_LOST', 409)
            current['payload_json'].pop('resume', None)
        self.transaction(clear)


def invoke_graph(graph, execution):
    """Invoke outside DB transactions; clear consumed resume only after success.

If a process dies before clearing resume, the next checkpoint no longer has the
ask_user interrupt, so the stale answer is ignored rather than delivered twice.
"""
    job = execution.read()
    config = graph_config(job['job_id'])
    snapshot = graph.get_state(config)
    resume = job['payload_json'].get('resume')
    has_interrupt = any(task.interrupts for task in snapshot.tasks)
    if snapshot.values:
        # A crash after consuming an answer may leave the previous payload in
        # MySQL while the graph already waits for a different clarification.
        pending=snapshot.values.get('pending') or {}
        if has_interrupt and resume is not None and resume.get('clarification_id')!=pending.get('clarification_id'):
            execution.clear_resume(resume)
            return snapshot.values
        value = Command(resume=resume) if has_interrupt and resume is not None else None
        require(not has_interrupt or resume is not None,
                '되묻기 답을 기다리고 있습니다.', 'CLARIFICATION_REQUIRED', 409)
    else:
        state = AgentState(input=AgentInput.model_validate(job['payload_json']['input']))
        execution.sync_budget(state)
        value = state.model_dump()
    result = graph.invoke(value, config)
    if resume is not None:
        # Finalize may already have set SUCCEEDED; terminal cleanup uses a
        # separate claim-checked transaction rather than the RUNNING-only guard.
        with execution.database.engine.begin() as connection:
            store = Store(execution.database, connection)
            current = jobs.locked_current(store, execution.job)
            if (current and current['claim_token'] == execution.job['claim_token']
                    and current['payload_json'].get('resume') == resume):
                current['payload_json'].pop('resume', None)
                jobs._save_chat(store, current)
    return result
