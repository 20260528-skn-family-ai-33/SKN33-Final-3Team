"""통화 사전·일별 환율 테이블. db/mysql/init/01_schema.sql의 currency·fx_daily 정의와 같습니다.

테이블은 DB에서 관리하고 이 모델로 만들지 않습니다 (테스트용 SQLite에서만 create_all).
통화 사전은 db/mysql/init/08_currency_seed.sql로 채우고, fx_daily는 fx_daily_load 작업이 채웁니다.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class Currency(Base):
    """통화 사전. ecos_item_code가 NULL이면 ECOS 환율 수집 대상에서 빠집니다."""

    __tablename__ = "currency"
    __table_args__ = (CheckConstraint("quote_unit > 0", name="currency_chk_1"),)

    currency_code: Mapped[str] = mapped_column(String(12), primary_key=True, comment="USD·JPY 같은 통화 코드")
    name_ko: Mapped[str] = mapped_column(String(80), nullable=False, comment="통화 한글 이름")
    ecos_item_code: Mapped[str | None] = mapped_column(String(20), unique=True, comment="ECOS 731Y001 항목 코드")
    quote_unit: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="환율 기준 외화 수량 (엔은 100)")


class FxDaily(Base):
    """일별 환율. rate_krw는 quote_unit만큼의 외화를 원화로 표시한 매매기준율입니다 (JPY는 100엔당 원)."""

    __tablename__ = "fx_daily"
    __table_args__ = (CheckConstraint("rate_krw > 0", name="fx_daily_chk_1"),)

    base_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="환율 원천의 기준 영업일")
    currency_code: Mapped[str] = mapped_column(String(12), ForeignKey("currency.currency_code"), primary_key=True)
    rate_krw: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, comment="매매기준율 (원)")