"""UN 수입 실적. 테이블은 SQL로 관리하며 앱에서 create_all을 호출하지 않습니다.
country 모델은 현재 SKN에 없어 운영 테이블을 그대로 참조합니다.
"""
from datetime import date
from decimal import Decimal
from sqlalchemy import CheckConstraint, Computed, Date, Enum, ForeignKeyConstraint, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base
from backend.app.models.hs_code import HsCodeMst  # register shared HS metadata


class UnData(Base):
    __tablename__ = 'un_data'
    __table_args__ = (
        ForeignKeyConstraint(['country_code'], ['country.country_code'], name='fk_un_data_country'),
        ForeignKeyConstraint(['hs6_code', 'hs_ver'], ['hs_code.hs_code', 'hs_code.hs_ver'], name='fk_un_data_hs'),
        CheckConstraint("freq_code IN ('M','A')", name='ck_un_data_frequency'),
        CheckConstraint('total_import_usd IS NOT NULL OR korea_import_usd IS NOT NULL', name='ck_un_data_has_value'),
        CheckConstraint('total_import_usd IS NULL OR total_import_usd >= 0', name='ck_un_data_world_nonnegative'),
        CheckConstraint('korea_import_usd IS NULL OR korea_import_usd >= 0', name='ck_un_data_korea_nonnegative'),
        CheckConstraint('total_import_usd IS NULL OR korea_import_usd IS NULL OR korea_import_usd <= total_import_usd', name='ck_un_data_subset'),
        CheckConstraint('LENGTH(hs6_code) = 6', name='ck_un_data_hs6_length'),
        Index('ix_un_data_country', 'country_code', 'hs6_code', 'hs_ver', 'freq_code', 'period_start'),
        Index('ix_un_data_hs', 'hs6_code', 'hs_ver', 'freq_code', 'period_start', 'country_code'),
    )
    freq_code: Mapped[str] = mapped_column(String(1), primary_key=True)
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(12), primary_key=True)
    hs6_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    hs_ver: Mapped[str] = mapped_column(String(10), primary_key=True)
    total_import_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 3))
    korea_import_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 3))
    korea_share_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), Computed(
        'CASE WHEN total_import_usd > 0 AND korea_import_usd IS NOT NULL '
        'THEN ROUND(CAST(korea_import_usd AS DECIMAL(36,12)) * 100 / total_import_usd, 6) ELSE NULL END', persisted=True))
    valuation_basis: Mapped[str] = mapped_column(Enum('CIF', 'FOB', name='valuation_basis'), nullable=False)
