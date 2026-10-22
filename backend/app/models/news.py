"""해외시장뉴스 기사·태그·일별 집계 테이블. db/mysql/init/01_schema.sql의 정의에 맞춘 모델입니다.

테이블은 DB에서 관리하고 이 모델로 만들지 않습니다 (테스트용 SQLite에서만 create_all).
CHECK는 테스트(SQLite)에서도 같은 규칙을 지키도록 옮겨 둔 것으로, MySQL의 CHAR_LENGTH 대신 LENGTH를 씁니다.
country_key는 DB가 COALESCE(country_code,'*')로 계산하는 컬럼이라 값을 넣지 않습니다.
"""
from datetime import date

from sqlalchemy import (BigInteger, CheckConstraint, Column, Computed, Date, ForeignKey, ForeignKeyConstraint, Index,
                        Integer, String, Table, Text, UniqueConstraint)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models import batch, hs_code  # noqa: F401  FK 대상 테이블(service_option·hs_code) 등록
from backend.app.models.base import Base

# country 모델은 아직 없어 FK 확인용 국가 코드만 등록합니다 (backend/tests/__init__.py와 같은 정의).
if "country" not in Base.metadata.tables:
    Table("country", Base.metadata, Column("country_code", String(12), primary_key=True))
COUNTRY = Base.metadata.tables["country"]

# SQLite는 INTEGER PK만 자동 증가하므로 테스트용으로만 Integer로 바꿉니다.
AUTO_ID = BigInteger().with_variant(Integer, "sqlite")


class Article(Base):
    """기사 한 건. (source_code, external_id)가 원천 기사 하나입니다."""

    __tablename__ = "article"
    __table_args__ = (
        UniqueConstraint("source_code", "external_id", name="uq_article_source_id"),
        Index("ix_article_date", "source_code", "published_date"),
    )

    article_id: Mapped[int] = mapped_column(AUTO_ID, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(String(40), ForeignKey("service_option.option_code"), nullable=False,
                                             comment="시장뉴스(KOTRA_MARKET) 또는 단신속보를 구분하는 출처 코드")
    external_id: Mapped[str] = mapped_column(String(100), nullable=False, comment="원천 API의 기사 ID (bbstxSn)")
    title: Mapped[str] = mapped_column(String(600), nullable=False)
    published_date: Mapped[date] = mapped_column(Date, nullable=False, comment="원천 기사 발행일 (수집일 아님)")
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    body_masked: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), nullable=False,
                                             comment="HTML을 벗기고 이메일·전화번호를 가린 본문")
    category: Mapped[str | None] = mapped_column(String(120), comment="원천 기사 분류 (infoCl)")


class ArticleTag(Base):
    """기사와 국가·품목·키워드 하나의 연결. 한 행에 셋 중 하나만 들어갑니다."""

    __tablename__ = "article_tag"
    __table_args__ = (
        UniqueConstraint("article_id", "country_code", name="uq_tag_country"),
        UniqueConstraint("article_id", "hs_code", "hs_ver", name="uq_tag_hs"),
        UniqueConstraint("article_id", "keyword_text", name="uq_tag_keyword"),
        ForeignKeyConstraint(["hs_code", "hs_ver"], ["hs_code.hs_code", "hs_code.hs_ver"]),
        CheckConstraint("(country_code IS NOT NULL)+(hs_code IS NOT NULL)+(keyword_text IS NOT NULL)=1",
                        name="article_tag_chk_1"),
        CheckConstraint("keyword_text IS NULL OR LENGTH(TRIM(keyword_text))>0", name="article_tag_chk_2"),
        CheckConstraint("(hs_code IS NULL AND hs_ver IS NULL) OR (hs_code IS NOT NULL AND hs_ver IS NOT NULL)",
                        name="article_tag_chk_3"),
    )

    tag_id: Mapped[int] = mapped_column(AUTO_ID, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(AUTO_ID, ForeignKey("article.article_id", ondelete="CASCADE"),
                                            nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(12), ForeignKey("country.country_code"))
    hs_code: Mapped[str | None] = mapped_column(String(10))
    hs_ver: Mapped[str | None] = mapped_column(String(10))
    keyword_text: Mapped[str | None] = mapped_column(String(120))


class NewsKeywordDaily(Base):
    """날짜 × 키워드 × 국가 고유 기사 수. country_code NULL은 국가 조건 없는 전체 집계입니다."""

    __tablename__ = "news_keyword_daily"
    __table_args__ = (
        UniqueConstraint("metric_date", "keyword_text", "country_key", name="uq_news_keyword"),
        CheckConstraint("LENGTH(TRIM(keyword_text))>0", name="news_keyword_daily_chk_1"),
    )

    metric_id: Mapped[int] = mapped_column(AUTO_ID, primary_key=True, autoincrement=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    keyword_text: Mapped[str] = mapped_column(String(120), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(12), ForeignKey("country.country_code"))
    country_key: Mapped[str | None] = mapped_column(String(12), Computed("COALESCE(country_code,'*')", persisted=True))
    day_count: Mapped[int] = mapped_column(Integer, nullable=False)


class NewsProductDaily(Base):
    """날짜 × 품목(HS4·HS6) × 국가 고유 기사 수. country_code NULL은 전체 집계입니다."""

    __tablename__ = "news_product_daily"
    __table_args__ = (
        UniqueConstraint("metric_date", "hs_code", "hs_ver", "country_key", name="uq_news_product"),
        ForeignKeyConstraint(["hs_code", "hs_ver"], ["hs_code.hs_code", "hs_code.hs_ver"]),
    )

    metric_id: Mapped[int] = mapped_column(AUTO_ID, primary_key=True, autoincrement=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    hs_code: Mapped[str] = mapped_column(String(10), nullable=False)
    hs_ver: Mapped[str] = mapped_column(String(10), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(12), ForeignKey("country.country_code"))
    country_key: Mapped[str | None] = mapped_column(String(12), Computed("COALESCE(country_code,'*')", persisted=True))
    day_count: Mapped[int] = mapped_column(Integer, nullable=False)
