"""호출별 전체 마감 시각과 원문 없는 실행 추적."""
from dataclasses import dataclass, field
import time
import math
from uuid import uuid4
from threading import Event
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from functools import lru_cache
from threading import BoundedSemaphore, Lock

_QUERY_LOCK = Lock()
_QUERY_POOL = None


@lru_cache(maxsize=1)
def query_limits():
    """(검색 임베딩 호출 상한 초, 동시 호출 수). 프로세스에서 한 번만 읽는다.
    TODO(C-28): 검색 후 본문 검수 시간을 남기는 임시 상한. 운영 지연·워커 수로 조정한다."""
    from backend.app.api.config import Settings
    settings = Settings()
    return (max(0.1, float(settings.get('API_RAG_QUERY_EMBED_SECONDS', 10))),
            max(1, int(settings.get('API_RAG_QUERY_SLOTS', 3))))


def _query_pool():
    global _QUERY_POOL
    with _QUERY_LOCK:
        if _QUERY_POOL is None:
            slots = query_limits()[1]
            _QUERY_POOL = (ThreadPoolExecutor(max_workers=slots, thread_name_prefix='rag-query'), BoundedSemaphore(slots))
        return _QUERY_POOL


def query_call(work, seconds):
    """진행 중인 외부 질의는 제한된 스레드에서 끝내고 늦은 결과를 호출자에게 돌려주지 않는다.
    자리가 모두 차 있으면 같은 시간 상한 안에서 기다린다. 기다리다 상한을 넘기면 시간 초과로 처리한다."""
    executor, slots = _query_pool()
    started = time.monotonic()
    if not slots.acquire(timeout=max(0.0, seconds)):
        raise RagDeadlineExceeded('query_embedding_timeout')
    remaining = seconds - (time.monotonic() - started)
    if remaining <= 0:   # 자리를 기다리다 시간을 다 썼으면 버려질 외부 호출을 보내지 않는다.
        slots.release()
        raise RagDeadlineExceeded('query_embedding_timeout')
    try:
        future = executor.submit(work)
    except BaseException:
        slots.release()
        raise
    future.add_done_callback(lambda _: slots.release())
    try:
        return future.result(timeout=max(0.0, seconds - (time.monotonic() - started)))
    except FutureTimeout as exc:
        future.cancel()
        raise RagDeadlineExceeded('query_embedding_timeout') from exc


class RagDeadlineExceeded(TimeoutError):
    """호출자가 공통 그래프 종료 정책으로 처리해야 하는 전체 상한 초과."""


class RagInferenceUnavailable(RuntimeError):
    """Caller cannot perform uncached inference; preserve unreviewed status."""


class RagReviewCancelled(RuntimeError):
    """A stopped article worker must not begin another inference or publish a result."""


@dataclass
class ExecutionContext:
    deadline: float
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    clock: object = time.monotonic
    started: float = field(init=False)
    events: list = field(default_factory=list)
    model_calls: int = 0
    tokens: int = 0
    finish_reserve: float = 0.0
    cancelled: Event = field(default_factory=Event, repr=False)
    event_fields: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if not math.isfinite(self.deadline):
            raise ValueError('finite_deadline_required')
        if not math.isfinite(self.finish_reserve) or self.finish_reserve < 0:
            raise ValueError('invalid_finish_reserve')
        self.started = self.clock()

    @property
    def work_deadline(self):
        return self.deadline - self.finish_reserve

    def remaining(self, *, finalizing=False):
        if self.cancelled.is_set():
            raise RagReviewCancelled('article_review_cancelled')
        value = (self.deadline if finalizing else self.work_deadline) - self.clock()
        if value <= 0:
            raise RagDeadlineExceeded('global_deadline_exceeded')
        return value

    def event(self, stage, **safe_fields):
        if not self.cancelled.is_set():
            self.events.append({'stage': stage, 'elapsed_ms': (self.clock()-self.started)*1000,
                                **self.event_fields, **safe_fields})

    def child(self, **event_fields):
        child = ExecutionContext(self.deadline, clock=self.clock, finish_reserve=self.finish_reserve,
                                 event_fields=event_fields)
        child.started = self.started
        return child

    def absorb(self, child):
        self.events.extend(list(child.events))
        self.model_calls += child.model_calls
        self.tokens += child.tokens

    def trace(self):
        return {'trace_id': self.trace_id, 'elapsed_ms': (self.clock()-self.started)*1000,
                'model_calls': self.model_calls, 'tokens': self.tokens, 'events': self.events}
