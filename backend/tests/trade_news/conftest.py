"""기본 시험은 외부 연결 금지. 명시한 MySQL 시험도 지정 로컬 DB만 연결합니다."""
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'backend/app/scheduler/collectors/trade_news'))


def pytest_addoption(parser):
    parser.addoption('--trade-news-mysql', action='store_true', default=False,
                     help='로컬 MySQL 8.4 빈 test_ DB의 통합 회귀 시험 실행')


@pytest.fixture
def mysql_test_url(request):
    # 옵션이 없으면 환경변수를 읽거나 접속하기 전에 건너뜁니다.
    if not request.config.getoption('--trade-news-mysql'):
        pytest.skip('로컬 MySQL 시험: --trade-news-mysql 필요')
    from .mysql_test_support import test_url
    try:
        return test_url()
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    from .mysql_test_support import block_external_io
    url = None
    if request.node.get_closest_marker('mysql_integration'):
        url = request.getfixturevalue('mysql_test_url')
    block_external_io(monkeypatch, url)


@pytest.fixture(autouse=True)
def isolated_article_locks(monkeypatch, tmp_path):
    # 합성 SQLite 시험은 저장소의 업무용 data/ 아래에 잠금 파일도 남기지 않습니다.
    from backend.app.db import news_lock
    monkeypatch.setattr(news_lock, 'LOAD_LOCK_DIR', tmp_path / 'article_load_locks')


def pytest_configure(config):
    config.addinivalue_line('markers', 'mysql_integration: 명시 옵션과 로컬 빈 시험 DB가 필요한 회귀 시험')
