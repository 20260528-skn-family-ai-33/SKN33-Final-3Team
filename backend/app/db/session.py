"""MySQL 연결과 세션. 접속 정보는 저장소 루트 .env의 MYSQL_* 값을 씁니다 (환경변수가 있으면 우선)."""
import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def mysql_url(env_path=ENV_PATH):
    values = {**dotenv_values(env_path), **os.environ}
    missing = [k for k in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE") if not values.get(k)]
    if missing:
        raise SystemExit(f"{env_path}에 {', '.join(missing)} 값이 없습니다.")
    return URL.create(
        "mysql+pymysql",
        username=values["MYSQL_USER"], password=values["MYSQL_PASSWORD"],  # 특수문자 비밀번호도 안전하게 처리
        host=values["MYSQL_HOST"], port=int(values.get("MYSQL_PORT") or 3306),
        database=values["MYSQL_DATABASE"], query={"charset": "utf8mb4"},
    )


def make_engine(url=None):
    """url을 주지 않으면 .env의 MySQL에 연결합니다. 테스트에서는 'sqlite://'를 넘깁니다."""
    return create_engine(url or mysql_url(), pool_pre_ping=True)


@contextmanager
def session_scope(engine):
    """with 블록이 끝나면 커밋하고, 예외가 나면 롤백합니다."""
    session = sessionmaker(bind=engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
