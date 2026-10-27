"""스케줄 작업 실행 기록 (batch_run). 모든 수집·적재 작업이 같은 방식으로 씁니다.

    SOURCE = Source("KCS_HS_NAMES", "관세청 HS부호 단위별 품목명", {"url": "..."})
    with track_batch(SOURCE, "HS_CODE_REFRESH", "hs_code", start, end) as run:
        run.request_count += 4          # API 호출 수 (재시도 포함)
        run.received_count = 17072      # 원천에서 받은 행 수
        if 문제가_있으면:
            run.fail()                  # 예외 없이 실패로 끝낼 때

- 시작할 때 RUNNING 행을 넣고 바로 커밋, 끝나면 SUCCESS/FAILED와 finished_at·건수를 갱신합니다.
  with 블록에서 예외가 나면 FAILED로 기록한 뒤 예외를 그대로 다시 올립니다.
- 출처(source)가 service_option에 없으면 SOURCE 행으로 추가합니다. 이미 있으면 바꾸지 않습니다.
- 기록용 DB에 연결하지 못해도 작업 자체는 멈추지 않습니다. 경고 로그만 남기고 계속합니다.
"""
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import MetaData, Table

from backend.app.db.session import make_engine, session_scope
from backend.app.db.news_lock import article_load_lock
from backend.app.models.batch import BatchRun, ServiceOption

log = logging.getLogger("batch_log")


@dataclass(frozen=True)
class Source:
    code: str
    label_ko: str
    value: dict = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: int | None = None
    request_count: int = 0
    received_count: int = 0
    failed: bool = False

    def fail(self):
        self.failed = True


def ensure_source(session, source):
    if session.get(ServiceOption, source.code) is None:
        session.add(ServiceOption(option_code=source.code, option_type="SOURCE", label_ko=source.label_ko,
                                  value_json=source.value))
        session.flush()


def _now():
    return datetime.now().replace(microsecond=0)  # DB 세션 시간대(KST)와 같은 로컬 시각, 초 단위


@contextmanager
def track_batch(source, job_name, target_table, period_start, period_end, engine=None, logger=None):
    """logger를 주면 그 작업 로그 파일에 batch_run 기록 결과도 함께 남깁니다."""
    out = logger or log
    record = RunRecord()
    own_engine = engine is None
    try:
        engine = engine or make_engine()
        with article_load_lock(engine), session_scope(engine) as s:
            ensure_source(s, source)
            table = Table("batch_run", MetaData(), autoload_with=s.connection(), resolve_fks=False)
            values = dict(source_code=source.code, job_name=job_name, target_table=target_table,
                          period_start=period_start, period_end=period_end, status="RUNNING", started_at=_now(),
                          request_count=0, received_count=0)
            if "detail_json" in table.c:
                values["detail_json"] = {"schema_version": 1}
            record.run_id = s.execute(table.insert().values(**values)).inserted_primary_key[0]
        out.info(f"batch_run {record.run_id} 시작 | {job_name} → {target_table} {period_start}~{period_end}")
    except Exception as e:  # 기록 실패가 작업을 막지 않게
        out.warning(f"batch_run 기록 시작 실패 (작업은 계속): {type(e).__name__}: {e}")

    try:
        yield record
    except BaseException:
        record.failed = True
        raise
    finally:
        _finish(engine, record, out)
        if own_engine and engine is not None:
            engine.dispose()


def _finish(engine, record, out):
    if record.run_id is None:
        return
    status = "FAILED" if record.failed else "SUCCESS"
    try:
        with article_load_lock(engine), session_scope(engine) as s:
            run = s.get(BatchRun, record.run_id)
            run.status, run.finished_at = status, _now()
            run.request_count, run.received_count = record.request_count, record.received_count
        out.info(f"batch_run {record.run_id} {status} | 요청 {record.request_count:,} | 수신 {record.received_count:,}")
    except Exception as e:
        out.warning(f"batch_run {record.run_id} 종료 기록 실패 (RUNNING으로 남음): {type(e).__name__}: {e}")
