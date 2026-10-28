"""실제 MySQL 8.4 관리자 ERD 14개 회귀 시험. 기본 실행은 DB 연결 없이 skip합니다."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.app.services import news_schema
from . import test_admin_erd as cases
from .test_loader_views import setup
from .helpers import prepared
from .test_batch_history import rows
from .mysql_test_support import ROOT, empty_schema, sql_statements
from news.article_schema import article_values
import load_project

pytestmark = pytest.mark.mysql_integration


@pytest.fixture
def local_db(mysql_test_url, tmp_path, monkeypatch):
    schema = Path(__file__).with_name('fixtures') / 'mysql_admin_schema.sql'
    migration = ROOT / 'db/mysql/migrations/20261022_trade_news_sources.sql'
    final_views = ROOT / 'db/mysql/migrations/20261025_news_source_views.sql'
    statements = [*sql_statements(schema.read_text(encoding='utf-8')),
                  *sql_statements(migration.read_text(encoding='utf-8')),
                  *sql_statements(final_views.read_text(encoding='utf-8'))]
    with empty_schema(mysql_test_url, statements) as db:
        with db.begin() as conn:
            for country in ('KR', 'US'):
                conn.execute(text("INSERT INTO country(country_code,name_ko,country_role) VALUES (:c,'시험 국가','COUNTRY')"), {'c':country})
            for hs in ('100100', '854200'):
                conn.execute(text("INSERT INTO hs_code(hs_code,hs_ver,name_ko,original_name_ko,source_edition) VALUES (:h,'HS2022','시험 품목','시험 원품목','test')"), {'h':hs})
        policy = tmp_path/'policy/backend/app/services/news_keywords/rules.py'
        policy.parent.mkdir(parents=True); policy.write_text('POLICY = 1\n', encoding='utf-8')
        monkeypatch.setattr(news_schema, 'ROOT', tmp_path/'policy')
        monkeypatch.setattr(news_schema, 'SNAPSHOT_DIR', tmp_path/'snapshots')
        yield SimpleNamespace(db=db, root=tmp_path/'data', tmp=tmp_path)


def test_admin_insert_repeat_and_required_values(local_db):
    cases.test_article_required_metadata_and_content(local_db.db)


@pytest.mark.parametrize('country_manual,hs_manual', [(True,True),(True,False),(False,True)])
def test_manual_admin_settings_survive(local_db, monkeypatch, country_manual, hs_manual):
    cases.test_existing_admin_settings_and_manual_tags_survive(local_db.db, monkeypatch, country_manual, hs_manual)


def test_policy_change_new_articles_only(local_db):
    cases.test_policy_change_only_affects_new_articles(local_db.db)


@pytest.mark.parametrize('failure', ['write_error','corrupt_snapshot'])
def test_snapshot_failure_rolls_back(local_db, monkeypatch, failure):
    cases.test_snapshot_failure_rolls_back_and_preserves_saved_articles(local_db.db, monkeypatch, failure)


@pytest.mark.parametrize('invalid,errno', [('missing_time',1644),('missing_snapshot',1644),('bad_schema_version',3819)])
def test_real_json_check_and_insert_trigger(local_db, invalid, errno):
    article = news_schema.reflect(local_db.db, 'article')
    p = prepared()
    value = dict(zip(('title','published_date','source_url','body_masked','category'),article_values(p)))
    meta = news_schema.admin_defaults({'snapshot_ref':'test.json','sha256':'a'*64})
    if invalid == 'missing_snapshot':meta['classification_input'] = None
    if invalid == 'bad_schema_version':meta['schema_version'] = 2
    with pytest.raises(DBAPIError) as caught, local_db.db.begin() as conn:
        conn.execute(article.insert().values(**value, source_code='HANKYUNG',external_id=p['external_id'],
            first_ingested_at=None if invalid=='missing_time' else news_schema.now(),admin_meta_json=meta))
    assert caught.value.orig.args[0] == errno
    assert cases.article_rows(local_db.db) == []


def test_batch_json_and_recovery(local_db):
    cases.test_batch_detail_required_and_preserved_during_recovery(local_db.db, local_db)


def test_batch_missing_source_atomic_rollback(local_db):
    cases.test_missing_source_rolls_back_all_admin_batch_rows(local_db.db, local_db)


def test_load_project_report_views_and_repeat(local_db):
    args = setup(local_db.tmp)
    args.apply = args.record_batch_run = True
    source_bytes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.data_dir.glob('*.sqlite3')}
    first = load_project.execute(args, lambda:local_db.db)
    assert first['status']=='complete' and first['inserted']==2 and not first['batch_run']['sync_pending'], first
    second = load_project.execute(args, lambda:local_db.db)
    assert second['existing']==2 and second['inserted']==0 and not second['batch_run']['sync_pending'], second
    with local_db.db.connect() as conn:
        # 합성 두 기사는 HANKYUNG입니다. 시장뉴스·KOTRA 통합 화면에는 포함하지 않습니다.
        assert conn.execute(text('SELECT COALESCE(SUM(article_count),0) FROM v_article_day')).scalar_one()==0
        assert conn.execute(text('SELECT COALESCE(SUM(article_count),0) FROM v_article_integrated_day')).scalar_one()==0
        assert conn.execute(text('SELECT source_code,article_count FROM v_article_source_day')).all()==[('HANKYUNG',2)]
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar_one()==2
    assert [r['status'] for r in rows(local_db.db)] == ['SUCCESS','SUCCESS']
    assert source_bytes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in args.data_dir.glob('*.sqlite3')}
    assert json.loads(args.result_file.read_text())==second


def test_hard_exit_after_mysql_commit_and_resume(local_db):
    args = setup(local_db.tmp)
    args.apply = args.record_batch_run = True
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT), str(ROOT/'backend/app/scheduler/collectors/trade_news'), env.get('PYTHONPATH', '')])
    child = subprocess.run([sys.executable, '-m', 'backend.tests.trade_news.mysql_test_support',
        '--crash-after-commit', str(local_db.tmp)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert child.returncode==77,child.stderr
    before = cases.article_rows(local_db.db)
    assert len(before)==1 and rows(local_db.db)[0]['status']=='RUNNING'
    result = load_project.execute(args,lambda:local_db.db)
    assert result['status']=='complete' and result['existing']==result['inserted']==1, result
    assert not result['batch_run']['sync_pending']
    after=cases.article_rows(local_db.db)
    assert len(after)==2 and after[0]==before[0]
    assert [r['status'] for r in rows(local_db.db)]==['FAILED','SUCCESS']
