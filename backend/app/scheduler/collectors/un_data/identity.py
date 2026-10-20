"""접속 주소 별칭과 실제 DB를 구분한다. 구형 상태는 기존 주소로 확인 후에만 연결한다."""
import hashlib
from sqlalchemy import text
from backend.app.scheduler.collectors.un_data.pipeline import require


def url_hash(engine):
    return hashlib.sha256(engine.url.render_as_string(hide_password=True).encode()).hexdigest()


# MySQL 서버가 직접 제공하는 식별자를 사용하며 조회 실패 시 URL 판정으로 후퇴하지 않습니다.
def database_identity(engine, connection=None):
    if engine.dialect.name != 'mysql':
        return None
    if connection is None:
        with engine.connect() as conn:
            return database_identity(engine, conn)
    server, database = connection.execute(text('SELECT @@server_uuid, DATABASE()')).one()
    require(isinstance(server, str) and bool(server.strip()) and
            isinstance(database, str) and bool(database.strip()), 'MySQL 서버 UUID/DB 이름 확인 실패')
    return {'version': 1, 'server_uuid': server, 'database': database}


# target_sha256은 기존 pending/cache와의 연결 키로 유지하고 실제 동일 DB 여부는 별도로 확인합니다.
def bind_target(engine, checkpoint=None, connection=None):
    identity = database_identity(engine, connection)
    if checkpoint is not None:
        saved = checkpoint.get('database_identity')
        if saved is not None:
            require(identity is not None and saved == identity, '다른 MySQL 서버/DB의 체크포인트 사용 금지')
        else:
            require(checkpoint['target_sha256'] == url_hash(engine),
                    '구형 체크포인트: 기존 접속 설정으로 먼저 DB 식별을 연결하세요')
        result = {'target_sha256': checkpoint['target_sha256']}
    else:
        result = {'target_sha256': url_hash(engine)}
    if identity is not None:
        result['database_identity'] = identity
    return result
