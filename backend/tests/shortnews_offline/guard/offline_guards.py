import pytest
from pathlib import Path
@pytest.fixture(autouse=True)
def guarded_paths(monkeypatch,tmp_path):
    from backend.app.db import news_lock
    from backend.app.scheduler.jobs import news_load
    from backend.app.services import news_schema
    from local_io_guard import block_external_io
    news_lock_path=tmp_path/'load_locks'
    monkeypatch.setattr(news_lock,'LOAD_LOCK_DIR',news_lock_path)
    monkeypatch.setattr(news_load,'RAW_DIR',tmp_path/'no_raw_autodiscovery')
    block_external_io(monkeypatch)

    policy=tmp_path/'_isolated_policy'
    policy_file=policy/'backend/app/services/news_keywords/category_rules.py'
    policy_file.parent.mkdir(parents=True,exist_ok=True)
    policy_file.write_text('# synthetic policy fixture\n')
    monkeypatch.setattr(news_schema,'ROOT',policy)
    monkeypatch.setattr(news_schema,'SNAPSHOT_DIR',tmp_path/'snapshots')
