"""분류 쓰기를 같은 MySQL 연결의 이름 잠금으로 직렬화한다. DDL 없음."""
from contextlib import contextmanager
import hashlib
import logging
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from backend.app.api.common import ApiError

log = logging.getLogger(__name__)


@contextmanager
def category_transaction(db, timeout=2):
    engine = db.engine
    if engine.dialect.name != 'mysql':
        with engine.begin() as connection:
            yield connection
        return
    # 이름은 서버 전체에서 공유되므로 DB를 구분하고 MySQL의 64자 제한을 지킨다.
    name = 'keyword_major:' + hashlib.sha256((engine.url.database or '').encode()).hexdigest()[:48]
    connection = engine.connect()
    acquired = False
    try:
        # 획득 응답 유실/해제 실패 시에도 잠근 물리 연결이 풀로 돌아가지 않는다.
        connection.detach()
        try:
            result = connection.scalar(text('SELECT GET_LOCK(:name, :timeout)'), {'name': name, 'timeout': timeout})
        except SQLAlchemyError as error:
            raise ApiError(503, 'CATEGORY_LOCK_ERROR', '분류 저장 잠금을 얻지 못했습니다.', retryable=True) from error
        if result == 0:
            raise ApiError(409, 'CATEGORY_BUSY', '다른 관리자가 분류를 저장 중입니다. 다시 시도하세요.', retryable=True)
        if result != 1:
            raise ApiError(503, 'CATEGORY_LOCK_ERROR', '분류 저장 잠금을 얻지 못했습니다.', retryable=True)
        acquired = True
        connection.rollback()  # GET_LOCK의 autobegin만 종료. 이름 잠금은 유지한다.
        with connection.begin():
            yield connection  # 로그인 조회도 이 새 트랜잭션 안에서 시작한다.
    finally:
        try:
            if acquired and not connection.closed and not connection.invalidated:
                result = connection.scalar(text('SELECT RELEASE_LOCK(:name)'), {'name': name})
                if result != 1:
                    raise RuntimeError('Category lock release failed')
        except Exception:
            log.exception('분류 잠금 해제 실패; 연결 폐기')
            try:
                connection.invalidate()
            except Exception:
                log.exception('분류 잠금 연결 폐기 실패')
        finally:
            # 커밋 성공 뒤 정리 오류는 성공 응답을 바꾸지 않는다. 본문 오류는 그대로 전파한다.
            try:
                connection.close()
            except Exception:
                log.exception('분류 잠금 연결 종료 실패')
                try:
                    connection.invalidate()
                    connection.close()
                except Exception:
                    log.exception('분류 잠금 연결 종료 재시도 실패')
