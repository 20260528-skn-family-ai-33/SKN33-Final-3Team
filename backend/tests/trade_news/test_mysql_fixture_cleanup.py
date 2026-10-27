"""가짜 연결로 MySQL 시험 정리 계약만 확인합니다. 서버에 접속하지 않습니다."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from . import mysql_test_support as support


@pytest.mark.parametrize('fail_after_replace', [False, True])
def test_replaced_view_is_cleaned_once_even_after_failure(monkeypatch, fail_after_replace):
    calls=[]
    class Connection:
        def exec_driver_sql(self, statement, params=None):
            calls.append(statement)
            if statement=='SELECT VERSION()':return NS(scalar_one=lambda:'8.4.11')
            if statement.startswith('SELECT GET_LOCK'):return NS(scalar_one=lambda:1)
            if statement=='synthetic failure':raise RuntimeError('synthetic DDL failure')
            return NS(scalar_one=lambda:1)
        def rollback(self):pass
    class Engine:
        @contextmanager
        def connect(self):yield Connection()
        @contextmanager
        def begin(self):yield Connection()
        dispose=Mock()
    engine=Engine()
    monkeypatch.setattr(support,'create_engine',Mock(return_value=engine))
    monkeypatch.setattr(support,'inspect',lambda _:NS(get_table_names=lambda:[],get_view_names=lambda:[]))
    statements=['CREATE TABLE synthetic (id INT)',
                'CREATE VIEW v_synthetic AS SELECT id FROM synthetic',
                'CREATE OR REPLACE VIEW v_synthetic AS SELECT id FROM synthetic']
    if fail_after_replace:statements.append('synthetic failure')
    if fail_after_replace:
        with pytest.raises(RuntimeError,match='synthetic DDL failure'):
            with support.empty_schema(NS(database='test_synthetic'),statements):pass
    else:
        with support.empty_schema(NS(database='test_synthetic'),statements) as result:
            assert result is engine
    assert [x for x in calls if x.startswith('DROP ')]==['DROP VIEW `v_synthetic`','DROP TABLE `synthetic`']
    assert sum(x.startswith('SELECT RELEASE_LOCK') for x in calls)==1
    engine.dispose.assert_called_once()
