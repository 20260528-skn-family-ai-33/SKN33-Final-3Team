from sqlalchemy import CheckConstraint, Computed, SmallInteger, String
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class HsCodeMst(Base):
    """HS 코드 마스터 (2·4·6·10단위). 6단위 name_ko는 '요약|6단위 원본' 형식입니다."""

    __tablename__ = "hs_code_mst"
    __table_args__ = (
        CheckConstraint("LENGTH(hs_code) IN (2, 4, 6, 10)", name="ck_hs_code_mst_length"),
        {"comment": "HS 코드 마스터"},
    )

    hs_code: Mapped[str] = mapped_column(String(10), primary_key=True,
                                         comment="품목을 구분하는 HS 문자열 (2·4·6·10자리)")
    # 숫자 코드라 LENGTH(바이트)와 글자 수가 같아 MySQL·SQLite 모두에서 같은 값입니다.
    depth: Mapped[int] = mapped_column(SmallInteger().with_variant(TINYINT(unsigned=True), "mysql"),
                                       Computed("LENGTH(hs_code)", persisted=True),
                                       comment="HS 코드 자릿수 (DB 자동 계산)")
    name_ko: Mapped[str] = mapped_column(String(500), nullable=False, comment="공식 한국어 품목명")
    source_edition: Mapped[str | None] = mapped_column(String(80), comment="품목명을 확인한 공식 사전의 판본·기준")
    hs_ver: Mapped[str | None] = mapped_column(String(10), comment="HS 코드 갱신 버전")
