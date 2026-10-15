from sqlalchemy import CheckConstraint, Computed, Index, SmallInteger, String, Text
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class HsCodeMst(Base):
    """HS 코드 마스터 (2·4·6·10단위). 실제 MySQL hs_code 테이블(SHOW CREATE TABLE)에 맞춘 정의입니다.

    테이블은 DB에서 관리하고 이 모델로 만들지 않습니다. CHECK는 테스트(SQLite)에서도 같은 규칙을 지키도록 옮겨 둔 것으로,
    MySQL의 CHAR_LENGTH·REGEXP_LIKE 대신 숫자 코드에서 결과가 같은 LENGTH를 씁니다.
    """

    __tablename__ = "hs_code"
    __table_args__ = (
        CheckConstraint("LENGTH(hs_code) IN (2, 4, 6, 10)", name="hs_code_chk_1"),
        # 6단위만 원문 필수, 나머지 단위는 원문 NULL
        CheckConstraint("(LENGTH(hs_code) = 6 AND original_name_ko IS NOT NULL AND LENGTH(TRIM(original_name_ko)) > 0)"
                        " OR (LENGTH(hs_code) <> 6 AND original_name_ko IS NULL)", name="hs_code_chk_2"),
        Index("ix_hs_depth", "depth", "hs_ver", "hs_code"),
    )

    hs_code: Mapped[str] = mapped_column(String(10), primary_key=True, comment="품목을 구분하는 HS 문자열 (2·4·6·10자리)")
    hs_ver: Mapped[str] = mapped_column(String(10), primary_key=True, comment="코드의 공통 사전 판본 식별값 (예: HS2022)")
    depth: Mapped[int] = mapped_column(SmallInteger().with_variant(TINYINT(unsigned=True), "mysql"),
                                       Computed("LENGTH(hs_code)", persisted=True),
                                       comment="HS 코드 자릿수 (DB 자동 계산)")
    name_ko: Mapped[str] = mapped_column(String(500), nullable=False, comment="화면 표시·검색용 한국어 품목명 (6단위는 요약명)")
    original_name_ko: Mapped[str | None] = mapped_column(Text, comment="공식 원천의 HS6 한국어 품목명 원문 (6단위만)")
    source_edition: Mapped[str] = mapped_column(String(80), nullable=False, comment="품목명을 확인한 공식 사전의 판본·기준")
