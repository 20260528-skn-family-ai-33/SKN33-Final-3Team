"""뉴스 적재의 DB 식별과 로컬 프로세스 간 직렬화. 자격 증명은 파일에 저장하지 않습니다."""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from weakref import WeakKeyDictionary

ROOT = Path(__file__).resolve().parents[3]
LOAD_LOCK_DIR = ROOT / "data/news/load_locks"


def database_identity(value):
    """접속 자격 증명을 제외한 논리 DB 식별값. URL·비밀번호는 상태 파일에 저장하지 않습니다."""
    from sqlalchemy.engine import make_url
    url = make_url(value)
    backend = url.get_backend_name()
    if backend == "sqlite":
        # 메모리 DB는 새 연결마다 다른 DB이므로 이전 실행의 완료 기록을 재사용하지 않습니다.
        database = (str(Path(url.database).expanduser().resolve())
                    if url.database and url.database != ":memory:" else uuid4().hex)
        identity = {"backend": backend, "database": database, "query": dict(url.query)}
    else:
        identity = {"backend": backend, "host": (url.host or "").lower(),
                    "port": url.port or (3306 if backend in ("mysql", "mariadb") else None),
                    "database": url.database, "socket": url.query.get("unix_socket")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def engine_identity(engine):
    """같은 메모리 DB Engine의 재실행은 식별하되 서로 다른 Engine은 구분합니다."""
    if engine.url.get_backend_name() == 'sqlite' and engine.url.database in (None, '', ':memory:'):
        if engine not in _MEMORY_IDENTITIES:
            _MEMORY_IDENTITIES[engine] = database_identity(engine.url)
        return _MEMORY_IDENTITIES[engine]
    return database_identity(engine.url)


_MEMORY_IDENTITIES = WeakKeyDictionary()


@contextmanager
def article_load_lock(engine):
    """같은 저장소·DB의 두 뉴스 적재기를 트랜잭션 시작 전부터 커밋/롤백까지 직렬화합니다."""
    from filelock import FileLock
    LOAD_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOAD_LOCK_DIR / (database_identity(engine.url) + ".lock")
    # 대기한 작업은 앞 작업의 커밋 이후에 새 트랜잭션/읽기 스냅샷을 시작합니다.
    with FileLock(str(path)):
        yield
