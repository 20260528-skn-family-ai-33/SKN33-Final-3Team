"""키워드 사전 테이블 (분류 틀·주제어). 실제 MySQL 정의는 db/mysql/init/10_keyword_dictionary.sql입니다.
테이블은 DB에서 관리하고 이 모델로 만들지 않습니다 (테스트용 SQLite에서만 create_all).
행은 csv와 맞추는 keyword_dict.py가 채웁니다. updated_at이 synced_at보다 나중이면 csv에 아직 반영하지 않은 DB 변경입니다."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

# PENDING 검수 대기(관리자 분류 대상) · CLASSIFIED 분야 있음 · UNCLASSIFIED 분야 없음으로 확정 · SYNONYM 다른 말의 동의어
TOPIC_STATUSES = ("PENDING", "CLASSIFIED", "UNCLASSIFIED", "SYNONYM")


class KeywordCategory(Base):
    """분류 틀: 대분류 > 중분류 한 쌍."""

    __tablename__ = "keyword_category"
    __table_args__ = (UniqueConstraint("major_name", "minor_name", name="uq_keyword_category"),)

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    major_name: Mapped[str] = mapped_column(String(40), nullable=False)
    minor_name: Mapped[str] = mapped_column(String(60), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)


class KeywordTopic(Base):
    """주제어 하나 (article_tag.keyword_text와 같은 표기)와 그 분야·동의어 상태."""

    __tablename__ = "keyword_topic"
    __table_args__ = (
        CheckConstraint("(status = 'SYNONYM') = (canonical_text IS NOT NULL)", name="keyword_topic_chk_1"),
        CheckConstraint("(status = 'CLASSIFIED') = (category_id IS NOT NULL)", name="keyword_topic_chk_2"),
        Index("ix_keyword_topic_status", "status"),
    )

    keyword_text: Mapped[str] = mapped_column(String(120), primary_key=True)
    canonical_text: Mapped[str | None] = mapped_column(String(120), comment="동의어면 대표어")
    category_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("keyword_category.category_id"))
    status: Mapped[str] = mapped_column(Enum(*TOPIC_STATUSES, name="topic_status"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)
