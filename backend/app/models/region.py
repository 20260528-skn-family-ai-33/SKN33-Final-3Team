"""대륙·하위 지역(market_region)과 국가(country). 실제 MySQL 테이블에 맞춘 정의로, 테이블은 DB에서 관리합니다.

country.currency_code의 currency FK는 currency 모델이 아직 없어 옮기지 않았습니다 (MySQL에는 있음)."""
from sqlalchemy import CheckConstraint, Enum, ForeignKey, SmallInteger, String
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class MarketRegion(Base):
    __tablename__ = "market_region"
    __table_args__ = (
        CheckConstraint("(region_level = 1 AND parent_id IS NULL) OR "
                        "(region_level = 2 AND parent_id IS NOT NULL AND parent_id <> region_id)"),
    )

    region_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False,
                                           comment="대륙 또는 하위 지역 번호 (UN M49 코드)")
    parent_id: Mapped[int | None] = mapped_column(SmallInteger, ForeignKey("market_region.region_id"),
                                                  comment="하위 지역이 속한 대륙의 region_id")
    region_level: Mapped[int] = mapped_column(SmallInteger().with_variant(TINYINT(unsigned=True), "mysql"),
                                              nullable=False, comment="1 대륙, 2 하위 지역")
    name_ko: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, comment="화면 표시용 지역 이름")


class Country(Base):
    __tablename__ = "country"

    country_code: Mapped[str] = mapped_column(String(12), primary_key=True, comment="관세청 기준 국가 식별 코드")
    name_ko: Mapped[str] = mapped_column(String(120), nullable=False, comment="한국어 국가 이름")
    country_role: Mapped[str] = mapped_column(Enum("COUNTRY", "SPECIAL", "TOTAL"), nullable=False, default="COUNTRY")
    region_id: Mapped[int | None] = mapped_column(SmallInteger, ForeignKey("market_region.region_id"),
                                                  comment="속한 하위 지역 번호. 미매핑이면 NULL")
    currency_code: Mapped[str | None] = mapped_column(String(12), comment="대표 통화 코드")
