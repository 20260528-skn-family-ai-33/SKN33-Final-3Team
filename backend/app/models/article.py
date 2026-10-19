"""기사·기사 태그 테이블. 실제 MySQL article·article_tag 정의(SHOW CREATE TABLE)에 맞춘 모델입니다.
테이블은 DB에서 관리하고 이 모델로 만들지 않습니다 (테스트용 SQLite에서만 create_all).
article_tag.country_code의 country FK는 country 모델이 아직 없어 옮기지 않았습니다 (MySQL에는 있음)."""
from datetime import date

from sqlalchemy import (BigInteger, CheckConstraint, Date, ForeignKey, ForeignKeyConstraint, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

# SQLite는 INTEGER PK만 자동 증가하므로 테스트용으로만 Integer로 바꿉니다.
_ID = BigInteger().with_variant(Integer, "sqlite")


class Article(Base):
    __tablename__ = "article"
    __table_args__ = (UniqueConstraint("source_code", "external_id", name="uq_article_source_id"),
                      Index("ix_article_date", "source_code", "published_date"))

    article_id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(String(40), ForeignKey("service_option.option_code"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, comment="원천 API 기사 ID")
    title: Mapped[str] = mapped_column(String(600), nullable=False)
    published_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    body_masked: Mapped[str] = mapped_column(Text, nullable=False, comment="정제·개인정보 마스킹한 본문")
    category: Mapped[str | None] = mapped_column(String(120))


class ArticleTag(Base):
    """기사 하나에 국가·HS 코드·키워드 중 정확히 하나를 연결하는 행."""

    __tablename__ = "article_tag"
    __table_args__ = (
        UniqueConstraint("article_id", "country_code", name="uq_tag_country"),
        UniqueConstraint("article_id", "hs_code", "hs_ver", name="uq_tag_hs"),
        UniqueConstraint("article_id", "keyword_text", name="uq_tag_keyword"),
        ForeignKeyConstraint(["hs_code", "hs_ver"], ["hs_code.hs_code", "hs_code.hs_ver"]),
        CheckConstraint("(country_code IS NOT NULL) + (hs_code IS NOT NULL) + (keyword_text IS NOT NULL) = 1",
                        name="article_tag_chk_1"),
        CheckConstraint("keyword_text IS NULL OR LENGTH(TRIM(keyword_text)) > 0", name="article_tag_chk_2"),
        CheckConstraint("(hs_code IS NULL AND hs_ver IS NULL) OR (hs_code IS NOT NULL AND hs_ver IS NOT NULL)",
                        name="article_tag_chk_3"),
    )

    tag_id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(_ID, ForeignKey("article.article_id", ondelete="CASCADE"), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(12))
    hs_code: Mapped[str | None] = mapped_column(String(10))
    hs_ver: Mapped[str | None] = mapped_column(String(10))
    keyword_text: Mapped[str | None] = mapped_column(String(120))
