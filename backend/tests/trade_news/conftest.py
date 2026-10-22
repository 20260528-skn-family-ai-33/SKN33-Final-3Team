"""기본 시험은 외부 네트워크를 차단합니다. MySQL은 명시적 전용 시험만 허용합니다."""
import socket, sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'backend/app/scheduler/collectors/trade_news'))

def pytest_addoption(parser):
    parser.addoption('--trade-news-mysql',action='store_true',default=False,
                     help='전용 빈 MySQL test_ DB의 통합 회귀 테스트를 명시적으로 실행')

@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    if request.node.get_closest_marker('mysql_integration') and request.config.getoption('--trade-news-mysql'):
        return
    def deny(*a,**kw):raise AssertionError('외부 네트워크 금지')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)
    monkeypatch.setattr(socket,'getaddrinfo',deny)

def pytest_configure(config):
    config.addinivalue_line('markers','mysql_integration: 명시 옵션이 있어야 실행되는 전용 MySQL 회귀 시험')
