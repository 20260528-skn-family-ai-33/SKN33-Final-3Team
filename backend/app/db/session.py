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
        raise ValueError(f"{env_path}에 {', '.join(missing)} 값이 없습니다.")
    query = {"charset": "utf8mb4"}
    if values.get("MYSQL_SSL_CA"):
        ca = Path(values["MYSQL_SSL_CA"]).expanduser()
        if not ca.is_absolute():
            ca = Path(env_path).resolve().parent / ca
        if not ca.is_file():
            raise ValueError("MYSQL_SSL_CA 인증서 파일을 찾을 수 없습니다")
        # SQLAlchemy가 PyMySQL ssl 옵션으로 변환합니다. CA와 호스트 이름을 모두 검증합니다.
        query.update(ssl_ca=str(ca.resolve()), ssl_check_hostname="true")
    return URL.create(
        "mysql+pymysql",
        username=values["MYSQL_USER"], password=values["MYSQL_PASSWORD"],  # 특수문자 비밀번호도 안전하게 처리
        host=values["MYSQL_HOST"], port=int(values.get("MYSQL_PORT") or 3306),
        database=values["MYSQL_DATABASE"], query=query,
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
