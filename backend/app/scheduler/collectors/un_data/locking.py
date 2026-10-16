"""UN 전용 잠금. 잠금 소유 연결에서만 적재 트랜잭션을 수행합니다."""
import logging
import sys
from contextlib import contextmanager
from sqlalchemy import text
from backend.app.db.session import session_scope

log = logging.getLogger(__name__)
LOCK_NAME = 'un_data_monthly_refresh'


# MySQL 잠금의 소유 연결을 보관하고, 소유권 확인과 같은 연결의 적재 트랜잭션을 제공합니다.
class Guard:
    def __init__(self, engine, connection=None, owner=None):
        self.engine, self.connection, self.owner = engine, connection, owner

    # 잠금을 얻었던 연결이 살아 있고 현재도 같은 잠금의 소유자인지 확인합니다.
    def check(self):
        if self.connection is None:
            return
        if self.connection.closed or self.connection.invalidated:
            raise RuntimeError('UN DB 잠금 연결 유실: 자동 재연결 금지')
        # SQLAlchemy의 연결 자동 복구로 잠금 없이 진행하지 않도록 연결 ID까지 확인합니다.
        row = self.connection.execute(text(
            'SELECT CONNECTION_ID(), IS_USED_LOCK(:name)'), {'name': LOCK_NAME}).one()
        if row[0] != self.owner or row[1] != self.owner:
            raise RuntimeError('UN DB 잠금 소유권 유실: 적재 및 점검일 갱신 중단')

    # 잠금 소유 연결에서 작업을 실행하고, 성공 시 commit·실패 시 rollback합니다.
    @contextmanager
    def transaction(self):
        self.check()
        if self.connection is None:
            with session_scope(self.engine) as session:
                yield session
            return
        # GET_LOCK/소유권 SELECT의 자동 시작 트랜잭션만 종료. advisory lock은 유지됩니다.
        self.connection.rollback()
        with self.connection.begin():
            with session_scope(self.connection) as session:
                try:
                    yield session
                    session.flush()
                    self.check()
                except BaseException:
                    # rollback 실패는 보조 오류로 기록하고 원래 작업 예외를 유지합니다.
                    try:
                        self.connection.rollback()
                    except Exception:
                        log.exception('UN rollback 실패 (원래 작업 오류 유지)')
                        try:
                            self.connection.invalidate()
                        except Exception:
                            log.exception('UN 실패 트랜잭션 연결 폐기 오류')
                    raise
            # 실제 commit은 같은 연결의 외부 트랜잭션이 수행합니다.

    # API 요청 전후에 잠금 상태를 확인하도록 기존 클라이언트를 감쌉니다.
    def watch(self, client):
        guard = self

        class WatchedClient:
            def __getattr__(self, name):
                return getattr(client, name)

            def get(self, *args, **kwargs):
                guard.check()
                result = client.get(*args, **kwargs)
                guard.check()
                return result

        return WatchedClient()


# MySQL 실행 잠금을 획득해 Guard를 전달하고, 작업 종료 시 잠금과 연결을 정리합니다.
@contextmanager
def db_lock(engine):
    if engine.dialect.name != 'mysql':
        yield Guard(engine)
        return
    connection = engine.connect()
    acquired = False
    try:
        owner = connection.scalar(text('SELECT CONNECTION_ID()'))
        acquired = connection.scalar(text('SELECT GET_LOCK(:name, 0)'), {'name': LOCK_NAME}) == 1
        if not acquired:
            raise RuntimeError('다른 UN 갱신이 실행 중입니다')
        guard = Guard(engine, connection, owner)
        guard.check()
        yield guard
    finally:
        failed = sys.exc_info()[0] is not None
        cleanup_error = None
        try:
            if acquired and not connection.invalidated and not connection.closed:
                result = connection.scalar(text('SELECT RELEASE_LOCK(:name)'), {'name': LOCK_NAME})
                if result != 1:
                    raise RuntimeError('UN DB 잠금 해제 시 소유권 유실')
        except Exception as error:
            cleanup_error = error
            try:
                connection.invalidate()
            except Exception:
                log.exception('UN 잠금 연결 폐기 실패')
            log.exception('UN 잠금 정리 실패')
        try:
            connection.close()
        except Exception as error:
            cleanup_error = cleanup_error or error
            log.exception('UN 잠금 연결 종료 실패')
        if cleanup_error is not None and not failed:
            raise cleanup_error
