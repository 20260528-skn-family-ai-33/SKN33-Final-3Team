from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 테이블 모델의 부모. Base.metadata.create_all(engine)로 테이블을 만듭니다."""
