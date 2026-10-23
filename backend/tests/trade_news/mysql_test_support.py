"""로컬 MySQL 시험 공통 도구. 운영 설정·서버 설치·예약 실행을 사용하지 않습니다."""
import hashlib
import os
import re
import socket
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
URL_ENV = 'TRADE_NEWS_TEST_MYSQL_URL'


def test_url():
    """명시한 빈 test_ DB만 받습니다. .env 또는 MYSQL_*로 대체하지 않습니다."""
    value = os.environ.get(URL_ENV)
    if not value:
        raise ValueError(f'{URL_ENV}에 로컬 시험 DB URL을 설정하세요')
    try:
        url = make_url(value)
        port = url.port or 3306
    except Exception:
        raise ValueError('시험 DB URL 형식 오류') from None
    if (url.drivername != 'mysql+pymysql'
            or not re.fullmatch(r'test_[A-Za-z0-9_]+', url.database or '')
            or url.host not in ('localhost', '127.0.0.1', '::1')
            or not url.username or not 1 <= port <= 65535
            or set(url.query) - {'unix_socket', 'charset'}
            or any(not isinstance(v, str) for v in url.query.values())):
        raise ValueError('mysql+pymysql·로컬 주소·test_ DB·시험 계정만 허용합니다')
    path = url.query.get('unix_socket')
    if path is not None and (not path or not Path(path).is_absolute()):
        raise ValueError('시험 Unix 소켓은 절대 경로여야 합니다')
    # localhost를 IP로 바꿔 외부 DNS를 조회하지 않습니다.
    return url.set(host='127.0.0.1' if url.host == 'localhost' else url.host)


def block_external_io(monkeypatch, url=None):
    """지정 MySQL 주소·포트 또는 Unix 소켓만 허용하며 예약 시작도 차단합니다."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    getaddrinfo = socket.getaddrinfo
    unix = url.query.get('unix_socket') if url is not None else None
    host, port = (url.host, url.port or 3306) if url is not None else (None, None)

    def allowed(sock, address):
        if unix:
            return sock.family == getattr(socket, 'AF_UNIX', None) and address == unix
        return (host is not None and sock.family in (socket.AF_INET, socket.AF_INET6)
                and isinstance(address, tuple) and address[:2] == (host, port))

    def deny(*args, **kwargs):
        raise AssertionError('지정 로컬 MySQL 외 연결·예약 시작 금지')

    def checked_connect(sock, address):
        if not allowed(sock, address):
            deny()
        return connect(sock, address)

    def checked_connect_ex(sock, address):
        if not allowed(sock, address):
            deny()
        return connect_ex(sock, address)

    def checked_lookup(target, service, *args, **kwargs):
        if unix or host is None or target != host or service != port:
            deny()
        return getaddrinfo(target, service, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', checked_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', checked_connect_ex)
    monkeypatch.setattr(socket, 'getaddrinfo', checked_lookup)
    from apscheduler.schedulers.base import BaseScheduler
    monkeypatch.setattr(BaseScheduler, 'start', deny)


def sql_statements(source):
    """저장소 시험 SQL의 DELIMITER를 처리해 트리거 내부 세미콜론을 보존합니다."""
    delimiter, lines = ';', []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('--'):
            continue
        if stripped.upper().startswith('DELIMITER '):
            if lines:
                raise ValueError('미완성 SQL 뒤 DELIMITER 변경')
            delimiter = stripped.split()[1]
            continue
        lines.append(line)
        if stripped.endswith(delimiter):
            yield '\n'.join(lines).rstrip()[:-len(delimiter)]
            lines = []
    if lines:
        raise ValueError('시험 SQL 종료 구분자 누락')


@contextmanager
def empty_schema(url, statements):
    """빈 DB에서 이번 시험이 만든 테이블·뷰만 정리합니다. DB 자체는 삭제하지 않습니다."""
    engine = create_engine(url, pool_pre_ping=True)
    created, locked = [], False
    try:
        # 같은 시험 DB를 동시에 실행해 서로의 테이블을 삭제하지 않도록 합니다.
        with engine.connect() as control:
            # DB 이름 길이와 관계없이 63자로 고정해 MySQL 잠금 이름 제한(64자)을 지킵니다.
            lock = 'trade_news_test:' + hashlib.sha256(url.database.encode('utf-8')).hexdigest()[:48]
            try:
                version = control.exec_driver_sql('SELECT VERSION()').scalar_one()
                if not re.match(r'^8\.4\.', version):
                    raise ValueError('이 회귀 시험은 MySQL 8.4 전용입니다')
                locked = control.exec_driver_sql('SELECT GET_LOCK(%s, 0)', (lock,)).scalar_one() == 1
                if not locked:
                    raise ValueError('같은 시험 DB가 사용 중입니다. 직렬로 실행하세요')
                inspector = inspect(control)
                if inspector.get_table_names() or inspector.get_view_names():
                    raise ValueError('기존 테이블·뷰가 있는 DB는 수정하지 않습니다. 빈 시험 DB가 필요합니다')
                control.rollback()
                with engine.begin() as conn:
                    for statement in statements:
                        conn.exec_driver_sql(statement)
                        match = re.match(r'CREATE(?: OR REPLACE)? (TABLE|VIEW) (\w+)\b', statement, re.I)
                        if match:
                            created.append(match.groups())
                yield engine
            finally:
                try:
                    # 트리거는 소유 테이블과 함께 제거됩니다. 생성 성공한 객체만 역순 정리합니다.
                    if created:
                        with engine.begin() as conn:
                            for kind, name in reversed(created):
                                conn.exec_driver_sql(f'DROP {kind} `{name}`')
                finally:
                    if locked:
                        control.exec_driver_sql('SELECT RELEASE_LOCK(%s)', (lock,))
    finally:
        engine.dispose()


def crash_after_commit(root):
    """시험 자식만 첫 article 커밋 직후 종료합니다. 운영 적재기를 수정하지 않습니다."""
    from datetime import date
    from types import SimpleNamespace
    from pytest import MonkeyPatch

    sys.path[:0] = [str(ROOT), str(ROOT / 'backend/app/scheduler/collectors/trade_news')]
    url = test_url()
    with MonkeyPatch.context() as patch:
        block_external_io(patch, url)
        from backend.app.services import news_schema
        from news.mysql_store import MySQLArticleStore
        import load_project

        news_schema.ROOT = root / 'policy'
        news_schema.SNAPSHOT_DIR = root / 'snapshots'
        original = MySQLArticleStore.save

        def crash(self, *args, **kwargs):
            original(self, *args, **kwargs)
            os._exit(77)

        patch.setattr(MySQLArticleStore, 'save', crash)
        args = SimpleNamespace(data_dir=root / 'data', source_map=root / 'map.json',
            target_date=date(2026, 10, 21), sites=['hankyung'], apply=True, include_tags=False,
            record_batch_run=True, result_file=root / 'result.json')
        result = load_project.execute(args, lambda: create_engine(url))
        raise AssertionError('첫 커밋 강제 종료 지점에 도달하지 못했습니다: ' + result['status'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='로컬 MySQL 커밋 직후 종료 시험 자식')
    parser.add_argument('--crash-after-commit', type=Path, required=True)
    crash_after_commit(parser.parse_args().crash_after_commit.resolve())
