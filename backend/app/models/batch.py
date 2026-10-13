"""배치 실행 기록 테이블. 실제 MySQL service_option·batch_run 정의(SHOW CREATE TABLE)에 맞춘 모델입니다.
테이블은 DB에서 관리하고 이 모델로 만들지 않습니다 (테스트용 SQLite에서만 create_all)."""
from datetime import date, datetime

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer,
                        SmallInteger, String, func)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

OPTION_TYPES = ("SOURCE", "REPORT_PERIOD", "REPORT_TYPE", "RULE", "NOTIFICATION_TERMS")
TARGET_TABLES = ("hs_code", "export_monthly", "import_monthly", "export_summary", "import_summary", "fx_daily",
                 "article", "article_tag", "news_product_daily", "news_keyword_daily", "model_run", "report")
RUN_STATUSES = ("RUNNING", "SUCCESS", "FAILED")


class ServiceOption(Base):
    """서비스 설정 마스터. 배치 출처는 option_type='SOURCE' 행입니다."""

    __tablename__ = "service_option"
    __table_args__ = (Index("ix_option_selection", "option_type", "enabled", "sort_order", "option_code"),)

    option_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    option_type: Mapped[str] = mapped_column(Enum(*OPTION_TYPES, name="option_type"), nullable=False)
    label_ko: Mapped[str] = mapped_column(String(120), nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BatchRun(Base):
    """데이터 작업 실행 한 건의 기록."""

    __tablename__ = "batch_run"
    __table_args__ = (
        CheckConstraint("period_end >= period_start", name="batch_run_chk_1"),
        Index("ix_batch_source", "source_code", "started_at"),
        Index("ix_run_target_period", "target_table", "period_start", "period_end", "status"),
    )

    # SQLite는 INTEGER PK만 자동 증가하므로 테스트용으로만 Integer로 바꿉니다.
    run_id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(String(40), ForeignKey("service_option.option_code"), nullable=False)
    job_name: Mapped[str] = mapped_column(String(60), nullable=False)
    target_table: Mapped[str] = mapped_column(Enum(*TARGET_TABLES, name="target_table"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Enum(*RUN_STATUSES, name="run_status"), nullable=False, default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
