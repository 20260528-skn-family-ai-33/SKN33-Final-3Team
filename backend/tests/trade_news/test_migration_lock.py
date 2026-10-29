"""언론사 적재·배치도 관리자 ERD 잠금 이후 구조를 조회하는지 확인합니다."""
import json
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import text
from backend.app.db import news_lock
from backend.app.services import news_schema
from news.mysql_store import MySQLArticleStore
from news.batch_history import BatchHistory
from .helpers import prepared
from .test_batch_history import database


@pytest.mark.parametrize('target', ['article', 'batch'])
def test_schema_changed_before_lock_is_used_without_duplicates(tmp_path, monkeypatch, target):
    db = database(tmp_path / 'db.sqlite3')
    monkeypatch.setattr(news_schema, 'SNAPSHOT_DIR', tmp_path / 'snapshots')
    calls = []

    @contextmanager
    def migration_completed(engine):
        calls.append(target)
        if len(calls) == 1:
            with engine.begin() as conn:
                if target == 'article':
                    conn.exec_driver_sql("ALTER TABLE article ADD COLUMN visibility_status TEXT NOT NULL DEFAULT 'VISIBLE'")
                    conn.exec_driver_sql('ALTER TABLE article ADD COLUMN first_ingested_at DATETIME NULL')
                    conn.exec_driver_sql('ALTER TABLE article ADD COLUMN admin_meta_json JSON NOT NULL')
                else:
                    conn.exec_driver_sql('ALTER TABLE batch_run ADD COLUMN detail_json JSON NOT NULL')
        yield

    monkeypatch.setattr(news_lock, 'article_load_lock', migration_completed)
    try:
        if target == 'article':
            with db.connect() as conn:
                store = MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'})
                first = store.save(prepared(), return_result=True)
                second = store.save(prepared(), return_result=True)
                assert first['article_id'] == second['article_id']
                assert (first['status'], second['status']) == ('inserted', 'existing')
                assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar_one() == 1
                meta = json.loads(conn.execute(text('SELECT admin_meta_json FROM article')).scalar_one())
                assert meta['classification_input']['sha256']
        else:
            history = BatchHistory(tmp_path, 'LOAD', ['hankyung'], date(2026, 10, 22), date(2026, 10, 22))
            history.engine = db
            history._write_rows(history.record)
            history._write_rows(history.record)
            with db.connect() as conn:
                assert conn.execute(text('SELECT COUNT(*) FROM batch_run')).scalar_one() == 1
                assert json.loads(conn.execute(text('SELECT detail_json FROM batch_run')).scalar_one()) == {'schema_version': 1}
        assert len(calls) == 2
    finally:
        db.dispose()
