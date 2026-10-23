"""키워드 분류 테이블 (keyword_category · keyword_topic). 실제 MySQL 정의(SHOW CREATE TABLE)에 맞춘 모델입니다.

테이블은 DB에서 관리하고 이 모델로 만들지 않습니다 (테스트용 SQLite에서만 create_all).
CHECK는 테스트(SQLite)에서도 같은 규칙을 지키도록 옮겨 둔 것입니다.
updated_at은 행을 바꾼 시각, synced_at은 csv와 맞춘 시각입니다. synced_at이 NULL이거나 updated_at보다 이르면
관리자가 DB에서 바꾼 뒤 csv에 아직 반영하지 않은 행입니다.
"""
from datetime import datetime

from sqlalchemy import (CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

TOPIC_STATUSES = ("PENDING", "CLASSIFIED", "UNCLASSIFIED", "SYNONYM")
NOW6 = DateTime(timezone=False).with_variant(DateTime(), "sqlite")


class KeywordCategory(Base):
    """분류 틀 한 쌍 (대분류 > 중분류)."""

    __tablename__ = "keyword_category"
    __table_args__ = (UniqueConstraint("major_name", "minor_name", name="uq_keyword_category"),)

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    major_name: Mapped[str] = mapped_column(String(40), nullable=False)
    minor_name: Mapped[str] = mapped_column(String(60), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(NOW6, nullable=False, server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(NOW6)


class KeywordTopic(Base):
    """주제어 한 개의 분류 상태. SYNONYM이면 canonical_text(대표어·상위어)로 바뀌는 말입니다."""

    __tablename__ = "keyword_topic"
    __table_args__ = (
        Index("ix_keyword_topic_status", "status"),
        CheckConstraint("(status = 'SYNONYM') = (canonical_text IS NOT NULL)", name="keyword_topic_chk_1"),
        CheckConstraint("(status = 'CLASSIFIED') = (category_id IS NOT NULL)", name="keyword_topic_chk_2"),
    )

    keyword_text: Mapped[str] = mapped_column(String(120), primary_key=True)
    canonical_text: Mapped[str | None] = mapped_column(String(120))
    category_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("keyword_category.category_id"))
    status: Mapped[str] = mapped_column(Enum(*TOPIC_STATUSES, name="keyword_topic_status"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(NOW6, nullable=False, server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(NOW6)
