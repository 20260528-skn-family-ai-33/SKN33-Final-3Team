"""호출별 전체 마감 시각과 원문 없는 실행 추적."""
from dataclasses import dataclass, field
import time
import math
from uuid import uuid4
from threading import Event


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
