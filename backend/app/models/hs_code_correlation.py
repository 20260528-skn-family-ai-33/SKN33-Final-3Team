from sqlalchemy import CheckConstraint, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

RELATIONS = ("1:1", "1:n", "n:1", "n:n")


class HsCodeCorrelation(Base):
    """HS 판 간 6단위 대응표. db/mysql/init/02_hs_code_correlation.sql과 같은 정의입니다.

    테이블은 DB에서 관리하고 이 모델로 만들지 않습니다. CHECK는 테스트(SQLite)에서도 지키도록 숫자 길이 검사로 옮겼습니다.
    hs_code를 FK로 참조하지 않습니다: 이전 판 코드는 hs_code에 일부만 있어도(예: 실적에 남은 HS2017) 대응표는 전체가 필요합니다.
    """

    __tablename__ = "hs_code_correlation"
    __table_args__ = (
        CheckConstraint("LENGTH(new_code) = 6 AND LENGTH(old_code) = 6 AND LENGTH(group_code) = 6",
                        name="hs_code_correlation_chk_1"),
        CheckConstraint("new_ver <> old_ver", name="hs_code_correlation_chk_2"),
        Index("ix_corr_old", "old_ver", "old_code", "new_ver"),
        Index("ix_corr_group", "new_ver", "old_ver", "group_code"),
    )

    new_ver: Mapped[str] = mapped_column(String(10), primary_key=True, comment="새 HS 판 (예: HS2022)")
    new_code: Mapped[str] = mapped_column(String(6), primary_key=True, comment="새 판의 HS 6단위 코드")
    old_ver: Mapped[str] = mapped_column(String(10), primary_key=True, comment="이전 HS 판 (예: HS2017)")
    old_code: Mapped[str] = mapped_column(String(6), primary_key=True, comment="이전 판의 HS 6단위 코드")
    relation: Mapped[str] = mapped_column(Enum(*RELATIONS, name="hs_relation"), nullable=False,
                                          comment="새 판:이전 판 대응 관계")
    group_code: Mapped[str] = mapped_column(String(6), nullable=False, comment="비교 그룹 대표 코드 (묶음 안 가장 작은 new_code)")
    source_edition: Mapped[str] = mapped_column(String(80), nullable=False, comment="대응표 원천 파일")
