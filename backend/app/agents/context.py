"""D-01/02/03/77: small Redis context; MySQL is the admission authority.

Admission callers must hold the MySQL chat_session row lock from the active-job
lookup through enqueue, Redis SET NX and commit. No MySQL access is hidden here.
"""
from collections.abc import Callable
from pydantic import ValidationError

from backend.app.agents.state import Conditions, SessionContext, TurnSummary, MAX_HS_ITEMS, MAX_COUNTRIES
from backend.app.api.common import ApiError
from backend.app.api.common import now
from backend.app.api.redis_store import RedisState

ACTIVE_STATUSES = frozenset({'QUEUED', 'RUNNING', 'NEEDS_INPUT', 'WAITING_REPORT'})


def completed_context(state):
    context = state.input.context.model_copy(deep=True)
    primary = state.plan.tasks[0]
    if primary.answer_status in ('PARTIAL', 'ANSWERED') and primary.conditions:
        for decision in primary.resolved.values():
            if decision.get('interest') in ('APPLIED', 'GLOBAL'):
                context.interest_action = 'APPLIED' if decision['interest']=='APPLIED' else 'DECLINED'
        # Keep confirmed question/conditions even if verifier removed its summary.
        context = context.remember(TurnSummary(question=state.input.question, intent=primary.intent,
            conditions=primary.conditions, summary=state.draft.turn_summary, answered_at=now()))
    return context


def restore_conditions(context_json: dict) -> Conditions | None:
    """Read the legacy API shape without carrying compare/metric into state."""
    raw = context_json.get('conditions') or {}
    if not raw:
        return None
    values = {k: v for k, v in raw.items() if k in Conditions.model_fields}
    values['hs_items'] = (values.get('hs_items') or [])[:MAX_HS_ITEMS]
    values['country_codes'] = (values.get('country_codes') or [])[:MAX_COUNTRIES]
    if raw.get('compare') == 'NONE':
        values['compare_period'] = None
    return Conditions.model_validate(values)


class ChatContext:
    def __init__(self, redis: RedisState):
        self.redis = redis

    def load(self, session_id: str, context_json: dict, last_job_id: str | None,
             *, opened: bool = False) -> SessionContext:
        """Ownership and last successful answer are read by the caller in MySQL.

        A stale/corrupt cache is ignored, not deleted or overwritten: a concurrent
        finalize may already be publishing a newer context. Redis errors propagate.
        """
        session_id = str(session_id)
        last_job_id = str(last_job_id) if last_job_id is not None else None
        raw = self.redis.chat_context(session_id, refresh=opened)
        # 조건 초기화(API-39) 뒤 새 답변이 없으면, 그 앞 답변이 늦게 올린 캐시는 초기화 이전 조건이다.
        if raw and 'cleared_after_job_id' in context_json and context_json['cleared_after_job_id'] == last_job_id:
            raw = None
        if raw:
            try:
                cached = SessionContext.model_validate_json(raw)
                if cached.session_id == session_id and cached.last_job_id == last_job_id:
                    return cached
            except (ValidationError, ValueError):
                pass
        return SessionContext(session_id=session_id, conditions=restore_conditions(context_json),
            interest_action=context_json.get('interest_action', 'NONE'), last_job_id=last_job_id)

    def acquire_locked(self, session_id: str, job_id: str,
                       current_job: dict | None, lookup_job: Callable[[str], dict | None]) -> None:
        """Call under the MySQL session lock, after idempotency/clarification handling.

        current_job is authoritative even if Redis lost its active key. lookup_job
        must be scoped to this session; a different session's job is not an owner.
        """
        if current_job and current_job['status'] in ACTIVE_STATUSES:
            raise ApiError(409, 'JOB_IN_PROGRESS', '이 대화의 작업이 진행 중입니다.')
        owner = self.redis.chat_active(session_id)
        if owner:
            old = lookup_job(owner)
            if old and old['status'] in ACTIVE_STATUSES:
                raise ApiError(409, 'JOB_IN_PROGRESS', '이 대화의 작업이 진행 중입니다.')
            self.redis.release_chat(session_id, owner)
        if not self.redis.acquire_chat(session_id, job_id):
            raise ApiError(409, 'JOB_IN_PROGRESS', '이 대화의 작업이 진행 중입니다.')

    def publish(self, context: SessionContext, job_id: str) -> bool:
        """Only after durable success; the worker handles Redis failure as cleanup.

        Revalidate mutated models and stamp the answer ID before the atomic write.
        A subsequent admission can only see MySQL's committed conditions.
        """
        payload = context.model_dump()
        payload['last_job_id'] = str(job_id)
        checked = SessionContext.model_validate(payload)
        return self.redis.save_chat_context(checked.session_id, job_id, checked.model_dump_json())
