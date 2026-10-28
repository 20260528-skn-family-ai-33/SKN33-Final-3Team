"""호출별 전체 마감 시각과 원문 없는 실행 추적."""
from dataclasses import dataclass, field
import time
import math
from uuid import uuid4


class RagDeadlineExceeded(TimeoutError):
    """호출자가 공통 그래프 종료 정책으로 처리해야 하는 전체 상한 초과."""


@dataclass
class ExecutionContext:
    deadline: float
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    clock: object = time.monotonic
    started: float = field(init=False)
    events: list = field(default_factory=list)
    model_calls: int = 0
    tokens: int = 0

    def __post_init__(self):
        if not math.isfinite(self.deadline):
            raise ValueError('finite_deadline_required')
        self.started = self.clock()

    def remaining(self):
        value = self.deadline - self.clock()
        if value <= 0:
            raise RagDeadlineExceeded('global_deadline_exceeded')
        return value

    def event(self, stage, **safe_fields):
        self.events.append({'stage': stage, 'elapsed_ms': (self.clock()-self.started)*1000, **safe_fields})

    def trace(self):
        return {'trace_id': self.trace_id, 'elapsed_ms': (self.clock()-self.started)*1000,
                'model_calls': self.model_calls, 'tokens': self.tokens, 'events': self.events}
