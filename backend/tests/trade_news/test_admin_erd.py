"""관리자 ERD의 필수값·수동 설정 보존·복구를 합성 기사와 임시 SQLite로 검증합니다."""
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.app.services import news_schema
from backend.tests.news_erd_fixture import install_trigger
from news.mysql_store import ArticleConflict, MySQLArticleStore
from .helpers import prepared
from .test_batch_history import start, rows
from .test_daily_scheduler import local, fixtures, offline_runner, job, NOW


@pytest.fixture
def admin_db(local, monkeypatch):
    with local.db.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE article ADD COLUMN visibility_status VARCHAR(10) NOT NULL DEFAULT 'VISIBLE'")
        conn.exec_driver_sql("ALTER TABLE article ADD COLUMN first_ingested_at DATETIME NULL")
        conn.exec_driver_sql("ALTER TABLE article ADD COLUMN admin_meta_json JSON NOT NULL")
        conn.exec_driver_sql("ALTER TABLE batch_run ADD COLUMN detail_json JSON NOT NULL")
        conn.exec_driver_sql("""CREATE TRIGGER check_batch_detail BEFORE INSERT ON batch_run
            WHEN json_extract(NEW.detail_json, '$.schema_version') IS NOT 1
            BEGIN SELECT RAISE(ABORT, 'detail_json schema_version required'); END""")
    install_trigger(local.db)
    policy = local.tmp / 'policy/backend/app/services/news_keywords/rules.py'
    policy.parent.mkdir(parents=True)
    policy.write_text('POLICY = 1\n')
    monkeypatch.setattr(news_schema, 'ROOT', local.tmp / 'policy')
    monkeypatch.setattr(news_schema, 'SNAPSHOT_DIR', local.tmp / 'snapshots')
    return local.db


def article_rows(db):
    table = news_schema.reflect(db, 'article')
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(select(table).order_by(table.c.article_id)).mappings()]


def verify_snapshot(row):
    metadata = row['admin_meta_json']
    snapshot = metadata['classification_input']
    payload = Path(snapshot['snapshot_ref']).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == snapshot['sha256']
    document = json.loads(payload)
    assert document['schema_version'] == 1
    assert document['files']['backend/app/services/news_keywords/rules.py'] in ('POLICY = 1\n', 'POLICY = 2\n')
    assert 'body' not in document and 'body_masked' not in document
    return snapshot


def test_article_required_metadata_and_content(admin_db):
    p = prepared()
    with admin_db.connect() as conn:
        loader = MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'})
        assert loader.save(p, return_result=True)['status'] == 'inserted'
        assert not conn.in_transaction()
        assert loader.save(p, return_result=True)['status'] == 'existing'
    saved = article_rows(admin_db)
    assert len(saved) == 1 and saved[0]['body_masked'] == p['body_masked']
    assert saved[0]['visibility_status'] == 'VISIBLE' and saved[0]['first_ingested_at'] is not None
    snapshot = verify_snapshot(saved[0])
    assert saved[0]['admin_meta_json'] == news_schema.admin_defaults(snapshot)


@pytest.mark.parametrize('country_manual,hs_manual', [(True, True), (True, False), (False, True)])
def test_existing_admin_settings_and_manual_tags_survive(admin_db, monkeypatch, country_manual, hs_manual):
    p = prepared()
    with admin_db.connect() as conn:
        MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'}).save(p)
    table = news_schema.reflect(admin_db, 'article')
    before = article_rows(admin_db)[0]
    meta = copy.deepcopy(before['admin_meta_json'])
    meta.update(country_tags_manual=country_manual, hs_tags_manual=hs_manual)
    meta['classification_state'] = {'status': 'READY', 'revision': 3}
    first = datetime(2025, 1, 1)
    with admin_db.begin() as conn:
        conn.execute(table.update().values(visibility_status='HIDDEN', admin_meta_json=meta, first_ingested_at=first))
        conn.execute(text("INSERT INTO article_tag(article_id,country_code) VALUES (:id,'KR')"), {'id': before['article_id']})
        conn.execute(text("INSERT INTO article_tag(article_id,hs_code,hs_ver) VALUES (:id,'100100','HS2022')"), {'id': before['article_id']})
    monkeypatch.setattr(news_schema, 'classification_snapshot', Mock(side_effect=AssertionError('기존 기사 정책 변경 금지')))
    p['tagging']['tags'] = [{'country_code': 'US'}, {'hs_code': '854200', 'hs_ver': 'HS2022'}, {'keyword_text': '수출'}]
    with admin_db.connect() as conn:
        loader = MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'}, include_tags=True)
        loader.save(p); loader.save(p)
        bad = copy.deepcopy(p); bad['body_masked'] += ' 변경'
        with pytest.raises(ArticleConflict): loader.save(bad)
        assert not conn.in_transaction()
        tags = list(conn.execute(text('SELECT country_code,hs_code,keyword_text FROM article_tag')))
    after = article_rows(admin_db)[0]
    assert after['admin_meta_json'] == meta and after['visibility_status'] == 'HIDDEN'
    assert after['first_ingested_at'] == first and after['body_masked'] == before['body_masked']
    assert ('KR', None, None) in tags and (None, '100100', None) in tags
    assert (('US', None, None) in tags) is (not country_manual)
    assert ((None, '854200', None) in tags) is (not hs_manual)
    assert tags.count((None, None, '수출')) == 1


def test_policy_change_only_affects_new_articles(admin_db):
    with admin_db.connect() as conn:
        loader = MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'})
        loader.save(prepared())
        first = article_rows(admin_db)[0]
        (news_schema.ROOT / 'backend/app/services/news_keywords/rules.py').write_text('POLICY = 2\n')
        loader.save(prepared()); loader.save(prepared(2))
    saved = article_rows(admin_db)
    assert saved[0] == first
    assert verify_snapshot(saved[0]) != verify_snapshot(saved[1])


@pytest.mark.parametrize('failure', ['write_error', 'corrupt_snapshot'])
def test_snapshot_failure_rolls_back_and_preserves_saved_articles(admin_db, monkeypatch, failure):
    with admin_db.connect() as conn:
        loader = MySQLArticleStore(conn, {'hankyung': 'HANKYUNG'})
        loader.save(prepared())
        before = article_rows(admin_db)
        if failure == 'write_error':
            monkeypatch.setattr(news_schema, 'classification_snapshot', Mock(side_effect=OSError('시험 쓰기 오류')))
        else:
            path = Path(before[0]['admin_meta_json']['classification_input']['snapshot_ref'])
            path.write_text('corrupt')
        with pytest.raises((OSError, ValueError)): loader.save(prepared(2))
        assert not conn.in_transaction()
    assert article_rows(admin_db) == before
    if failure == 'corrupt_snapshot': assert path.read_text() == 'corrupt'


def test_admin_constraints_reject_incomplete_rows(admin_db):
    from news.article_schema import article_values
    p = prepared()
    article = news_schema.reflect(admin_db, 'article')
    values = dict(zip(('title', 'published_date', 'source_url', 'body_masked', 'category'), article_values(p)))
    with pytest.raises(IntegrityError), admin_db.begin() as conn:
        conn.execute(article.insert().values(**values, source_code='HANKYUNG', external_id=p['external_id'],
            admin_meta_json=news_schema.admin_defaults(None), first_ingested_at=news_schema.now()))
    assert article_rows(admin_db) == []


def test_batch_detail_required_and_preserved_during_recovery(admin_db, local):
    _, first = start(local.root, admin_db)
    assert not first.record['sync_pending']
    table = news_schema.reflect(admin_db, 'batch_run')
    with admin_db.begin() as conn:
        details = conn.execute(select(table.c.detail_json)).scalars().all()
        assert details == [{'schema_version': 1}] * 2
        conn.execute(table.update().values(detail_json={'schema_version': 1, 'note': '보존'}))
    # DB 커밋 직후 로컬 응답을 못 받은 상황도 동일 실행 행을 찾아 종료합니다.
    first.record['sync_pending'] = True
    first.persist()
    _, recovered = start(local.root, admin_db)
    result = recovered.finish()
    assert not result['sync_pending'] and len(rows(admin_db)) == 4
    assert all(row['status'] == 'FAILED' for row in rows(admin_db))
    with admin_db.connect() as conn:
        details = conn.execute(select(table.c.detail_json).order_by(table.c.run_id)).scalars().all()
    assert details == [{'schema_version': 1, 'note': '보존'}] * 2 + [{'schema_version': 1}] * 2


def test_missing_source_rolls_back_all_admin_batch_rows(admin_db, local):
    with admin_db.begin() as conn:
        conn.execute(text("DELETE FROM service_option WHERE option_code='FNNEWS'"))
    _, history = start(local.root, admin_db)
    assert history.finish()['sync_pending'] and rows(admin_db) == []
    assert all(row['run_id'] is None for row in history.record['sources'].values())


@pytest.mark.parametrize('crash', [False, True])
def test_daily_chain_admin_schema_and_recovery(admin_db, local, crash):
    fixtures(local)
    result = job.run(local.root, now=NOW, env_path=local.env,
        runner=offline_runner(local, [], fault='after_article_commit' if crash else None))
    assert result['status'] == ('failed' if crash else 'complete'), result
    before = article_rows(admin_db)
    assert len(before) == (1 if crash else 2)
    for row in before: verify_snapshot(row)
    if crash: assert sum(row['status'] == 'RUNNING' for row in rows(admin_db)) == 2
    recovered = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, []))
    assert recovered['status'] == 'complete', recovered
    assert recovered['mysql']['inserted'] == (1 if crash else 0)
    assert recovered['mysql']['existing'] == (1 if crash else 2)
    saved = article_rows(admin_db)
    assert len(saved) == 2 and saved[:len(before)] == before
    assert len(rows(admin_db)) == 8 and all(row['status'] != 'RUNNING' for row in rows(admin_db))
    assert sum(row['status'] == 'FAILED' for row in rows(admin_db)) == (2 if crash else 0)
    table = news_schema.reflect(admin_db, 'batch_run')
    with admin_db.connect() as conn:
        assert all(value == {'schema_version': 1} for value in conn.execute(select(table.c.detail_json)).scalars())
    loads = [json.loads(line)['url'] for line in (local.tmp/'downloads.jsonl').read_text().splitlines()]
    assert sum('/article/' in url or '/news/2026' in url for url in loads) == 2
    assert not (local.tmp/'network_attempts.log').exists()
