"""A·B 모델별 버전 이력. 운영 테이블은 10_ml_model_version.sql로 준비한다."""
from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, Date, DateTime, Index, String, Text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class MlModelVersion(Base):
    __tablename__ = "ml_model_version"
    __table_args__ = (
        CheckConstraint("model_key IN ('model_a','model_b')", name="ck_ml_version_model"),
        CheckConstraint("status IN ('READY','SKIPPED')", name="ck_ml_version_status"),
        CheckConstraint("train_rows >= 0", name="ck_ml_version_rows"),
        CheckConstraint("(status='READY' AND artifact_path IS NOT NULL AND artifact_sha256 IS NOT NULL) "
                        "OR (status='SKIPPED' AND artifact_path IS NULL AND artifact_sha256 IS NULL)",
                        name="ck_ml_version_artifact"),
        Index("ix_ml_version_created", "model_key", "created_at"),
    )

    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(16), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    parent_version_id: Mapped[str | None] = mapped_column(String(64))
    training_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    train_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data_info: Mapped[dict] = mapped_column(JSON, nullable=False)
    evaluation: Mapped[dict] = mapped_column(JSON, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text)
