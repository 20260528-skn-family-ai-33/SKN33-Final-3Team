"""실제 공통 모델 + 임시 SQLite. 원격 DB/실제 기사/예약 실행은 사용하지 않습니다."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from backend.app.models.batch import BatchRun, ServiceOption
from news.batch_history import BatchHistory
from news.run_lifecycle import begin_run, new_run, publish_run
from .helpers import DDL
from .test_loader_views import setup as setup_articles

ROOT = Path(__file__).resolve().parents[3]
CRAWLER = ROOT / 'backend/app/scheduler/collectors/trade_news'
DAY = date(2026, 10, 21)
START = '2026-10-22T03:00:00+09:00'


def database(path):
    engine = create_engine('sqlite:///' + str(path))
    @event.listens_for(engine, 'connect')
    def fk(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    ServiceOption.__table__.create(engine)
    BatchRun.__table__.create(engine)
    with Session(engine) as session, session.begin():
        for code in ('HANKYUNG', 'FNNEWS'):
            session.add(ServiceOption(option_code=code, option_type='SOURCE', label_ko=code, value_json={}))
    with engine.begin() as connection:
        for sql in DDL[1:]:
            connection.execute(text(sql))
    return engine


def rows(engine):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(BatchRun.__table__).order_by(BatchRun.run_id)).mappings()]


def start(root, engine, sites=('hankyung', 'fnnews')):
    run = new_run(DAY.isoformat(), sites, START)
    begin_run(root, run)
    history = BatchHistory(root, 'COLLECT', sites, DAY, DAY, attempt=run['attempt_id'],
                           started_at=START, engine_factory=lambda: engine).start()
    return run, history


def test_counts_status_dates_and_new_attempt(tmp_path):
    db = database(tmp_path / 'batch.sqlite3')
    run, history = start(tmp_path, db)
    assert all(row['status'] == 'RUNNING' and row['finished_at'] is None for row in rows(db))
    for site in ('hankyung', 'fnnews'):
        for _ in range(3):
            history.event({'event': 'request', 'site': site})
        history.event({'event': 'article_parsed', 'site': site})
        history.event({'event': 'filtered', 'site': site})  # 판정 결과는 수신 건수와 별개
    publish_run(tmp_path, run, {'execution_healthy': True, 'sites': {}})
    status = history.finish()
    assert not status['sync_pending']
    first = rows(db)
    assert {x['source_code'] for x in first} == {'HANKYUNG', 'FNNEWS'}
    assert all(x['status'] == 'SUCCESS' and x['request_count'] == 3 and x['received_count'] == 1 for x in first)
    assert all(x['period_start'] == x['period_end'] == DAY and x['target_table'] == 'article' for x in first)
    assert all(x['started_at'].hour == 3 and x['finished_at'] is not None and len(x['job_name']) <= 60 for x in first)
    second, h2 = start(tmp_path, db)
    publish_run(tmp_path, second, {'execution_healthy': True})
    h2.finish()
    assert len(rows(db)) == 4 and rows(db)[:2] == first
    assert all(x['received_count'] == 0 and x['status'] == 'SUCCESS' for x in rows(db)[2:])


@pytest.mark.parametrize('confirmed', [False, True])
def test_previous_interrupted_or_confirmed_recovery(tmp_path, confirmed):
    db = database(tmp_path / 'batch.sqlite3')
    old, history = start(tmp_path, db, ['hankyung'])
    history.event({'event': 'request', 'site': 'hankyung'})
    if confirmed:
        publish_run(tmp_path, old, {'execution_healthy': True})
    # finally가 실행되지 않은 강제 종료 상태를 재현
    newer, h2 = start(tmp_path, db, ['hankyung'])
    before = rows(db)
    assert len(before) == 2
    assert before[0]['status'] == ('SUCCESS' if confirmed else 'FAILED')
    assert before[0]['request_count'] == 1 and before[1]['status'] == 'RUNNING'
    h2.finish()
    assert rows(db)[1]['status'] == 'FAILED'


def test_db_failure_keeps_pending_and_replays_idempotently(tmp_path, capsys):
    db = database(tmp_path / 'batch.sqlite3')
    run = new_run(DAY.isoformat(), ['hankyung'], START)
    begin_run(tmp_path, run)
    bad = Mock(side_effect=RuntimeError('password=SECRET'))
    history = BatchHistory(tmp_path, 'COLLECT', ['hankyung'], DAY, DAY,
                           attempt=run['attempt_id'], engine_factory=bad).start()
    history.event({'event': 'request', 'site': 'hankyung'})
    publish_run(tmp_path, run, {'execution_healthy': True})
    result = history.finish()
    assert bad.call_count == 1 and result['sync_pending'] and result['status'] == 'SUCCESS'
    assert 'SECRET' not in history.path.read_text() + capsys.readouterr().err
    newer, h2 = start(tmp_path, db, ['hankyung'])
    assert len(rows(db)) == 2 and rows(db)[0]['status'] == 'SUCCESS' and rows(db)[0]['request_count'] == 1
    h2.finish()
    third, h3 = start(tmp_path, db, ['hankyung'])
    assert len(rows(db)) == 3  # 과거 동일 실행을 중복 등록하지 않음
    h3.finish()


def test_commit_before_local_ack_does_not_duplicate(tmp_path):
    db = database(tmp_path / 'batch.sqlite3')
    run, history = start(tmp_path, db, ['hankyung'])
    stale = json.loads(history.path.read_text())
    stale['sync_pending'] = True
    stale['sources']['hankyung']['run_id'] = None
    history.path.write_text(json.dumps(stale))
    new, h2 = start(tmp_path, db, ['hankyung'])
    assert len(rows(db)) == 2 and rows(db)[0]['status'] == 'FAILED'
    h2.finish()


def test_missing_source_is_not_seeded(tmp_path):
    db = database(tmp_path / 'batch.sqlite3')
    with db.begin() as c:
        c.execute(text("DELETE FROM service_option WHERE option_code='FNNEWS'"))
    run, history = start(tmp_path, db)
    publish_run(tmp_path, run, {'execution_healthy': True})
    assert history.finish()['sync_pending']
    assert rows(db) == []  # 두 출처의 이력은 한 트랜잭션으로 기록
    assert all(row['run_id'] is None for row in history.record['sources'].values())
    with db.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM service_option WHERE option_code='FNNEWS'")).scalar() == 0


def test_different_destination_not_replayed(tmp_path):
    db = database(tmp_path / 'one.sqlite3')
    old, history = start(tmp_path, db, ['hankyung'])
    other = database(tmp_path / 'two.sqlite3')
    new, h2 = start(tmp_path, other, ['hankyung'])
    assert rows(db)[0]['status'] == 'RUNNING'
    assert len(rows(other)) == 1
    assert rows(other)[0]['job_name'] == h2.record['job_name']
    h2.finish()


def test_load_plan_success_repeat_conflict(tmp_path):
    import load_project
    args = setup_articles(tmp_path)
    args.record_batch_run = True
    no_db = Mock(side_effect=AssertionError('no DB'))
    assert load_project.execute(args, no_db)['status'] == 'plan_only'
    no_db.assert_not_called()
    assert not (args.data_dir / 'batch_runs').exists()
    db = database(tmp_path / 'batch.sqlite3')
    args.apply = True
    first = load_project.execute(args, lambda: db)
    assert first['inserted'] == 2 and not first['batch_run']['sync_pending']
    repeated = load_project.execute(args, lambda: db)
    assert repeated['existing'] == 2 and len(rows(db)) == 2
    with db.begin() as c:
        c.execute(text("UPDATE article SET body_masked='기존 값 보존' WHERE article_id=2"))
    failed = load_project.execute(args, lambda: db)
    assert failed['status'] == 'failed' and failed['existing'] == failed['failed'] == 1
    assert json.loads(args.result_file.read_text()) == failed
    assert [r['status'] for r in rows(db)] == ['SUCCESS', 'SUCCESS', 'FAILED']
    assert all(r['request_count'] == 0 and r['received_count'] == 2 for r in rows(db))
    with db.connect() as c:
        assert c.execute(text('SELECT body_masked FROM article WHERE article_id=2')).scalar() == '기존 값 보존'


def test_load_history_failure_does_not_prevent_article_storage(tmp_path, monkeypatch):
    import load_project
    args = setup_articles(tmp_path)
    args.apply = args.record_batch_run = True
    db = database(tmp_path / 'batch.sqlite3')
    monkeypatch.setattr(BatchHistory, '_write_rows', Mock(side_effect=RuntimeError('SECRET')))
    result = load_project.execute(args, lambda: db)
    assert result['status'] == 'complete' and result['inserted'] == 2
    assert result['batch_run']['sync_pending'] and 'SECRET' not in args.result_file.read_text()


def test_daily_flag_forward_and_no_implicit_connection(tmp_path):
    from daily_collect import arguments
    from collect import command
    args = arguments(['--data-dir', str(tmp_path), '--target-date', DAY.isoformat()])
    assert '--record-batch-run' not in args
    enabled = arguments(['--data-dir', str(tmp_path), '--record-batch-run'])
    assert '--record-batch-run' in command(enabled)


def offline(tmp, mode=None, entry='daily_collect'):
    article_url = 'https://www.hankyung.com/article/2026102100001'
    feed = 'https://www.hankyung.com/sitemap/latest-article.xml'
    spec = {
        'https://www.hankyung.com/robots.txt': {'body': 'User-agent: *\nAllow: /'},
        feed: {'body': f'<urlset xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"><url><loc>{article_url}</loc><news:news><news:title>반도체 수출 계약</news:title><news:publication_date>2026-10-21</news:publication_date></news:news></url></urlset>'},
        article_url: {'body': '<meta property="article:published_time" content="2026-10-21"><h1 class="headline">반도체 수출 계약</h1><div id="articletxt">한국 시험기업은 미국에 반도체 수출 100억원 계약을 체결했다.<br>공유하기<br>연락 sample@example.test</div>'},
    }
    (tmp / 'responses.json').write_text(json.dumps(spec))
    env = {**os.environ, 'OFFLINE_DIR': str(tmp), 'OFFLINE_ENTRYPOINT': entry,
           'OFFLINE_BATCH_DB': str(tmp / 'batch.sqlite3'), 'OFFLINE_VIRTUAL_CLOCK': '1',
           'OFFLINE_RUN_AT': '2026-10-22T03:00:00+09:00',
           'PYTHONPATH': os.pathsep.join([str(ROOT), str(Path(__file__).parent), str(CRAWLER), os.environ.get('PYTHONPATH', '')])}
    env.pop('OFFLINE_FAULT_MODE', None)
    if mode:
        env['OFFLINE_FAULT_MODE'] = mode
    cmd = [sys.executable, str(Path(__file__).with_name('offline_runner.py')),
           '--data-dir', str(tmp / 'data'), '--sites', 'hankyung', '--record-batch-run']
    if entry == 'collect':
        cmd += ['--discover', '--daily-target', DAY.isoformat()]
    return subprocess.run(cmd, cwd=CRAWLER, env=env, capture_output=True, text=True, timeout=40)


@pytest.mark.parametrize('mode,code,status', [(None, 0, 'SUCCESS'), ('after_db_commit', 73, 'FAILED'),
                                             ('after_state_commit', 75, 'SUCCESS'), ('report_cache_failure', 1, 'SUCCESS')])
def test_cli_real_scrapy_and_kill_recovery(tmp_path, mode, code, status):
    db = database(tmp_path / 'batch.sqlite3')
    first = offline(tmp_path, mode)
    assert first.returncode == code, first.stdout + first.stderr
    before = rows(db)
    assert len(before) == 1 and before[0]['request_count'] == 3 and before[0]['received_count'] == 1
    if code in (73, 75):
        assert before[0]['status'] == 'RUNNING'
    second = offline(tmp_path, entry='collect')
    assert second.returncode == 0, second.stdout + second.stderr
    result = rows(db)
    assert len(result) == 2 and result[0]['status'] == status and result[1]['status'] == 'SUCCESS'
    assert result[1]['received_count'] == 0
    downloads = [json.loads(x) for x in (tmp_path / 'downloads.jsonl').read_text().splitlines()]
    assert sum('/article/' in x['url'] for x in downloads) == 1
    assert not (tmp_path / 'network_attempts.log').exists()
    with sqlite3.connect(tmp_path / 'data/articles.sqlite3') as c:
        body = json.loads(c.execute('SELECT payload FROM articles').fetchone()[0])
        assert '공유하기' in body['body'] and '공유하기' not in body['body_masked']


def test_load_recovers_interrupted_attempt_without_reinserting_articles(tmp_path):
    import load_project
    args = setup_articles(tmp_path)
    args.apply = args.record_batch_run = True
    db = database(tmp_path / 'batch.sqlite3')
    first = load_project.execute(args, lambda: db)
    assert first['inserted'] == 2
    # 이미 INSERT된 기사와 종료 기록이 없는 직전 적재 이력을 함께 재현합니다.
    interrupted = BatchHistory(args.data_dir, 'LOAD', ['hankyung'], DAY, DAY,
                               engine_factory=lambda: db).start()
    interrupted.received([{'source_code': 'hankyung'}, {'source_code': 'hankyung'}])
    result = load_project.execute(args, lambda: db)
    assert result['existing'] == 2 and result['inserted'] == 0
    assert [r['status'] for r in rows(db)] == ['SUCCESS', 'FAILED', 'SUCCESS']
    with db.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM article')).scalar() == 2


def test_empty_load_is_success_and_records_zero(tmp_path):
    import load_project
    args = setup_articles(tmp_path)
    args.apply = args.record_batch_run = True
    args.target_date = date(2026, 10, 20)
    db = database(tmp_path / 'batch.sqlite3')
    result = load_project.execute(args, lambda: db)
    assert result['status'] == 'complete' and result['articles_to_store'] == 0
    assert rows(db)[0]['status'] == 'SUCCESS' and rows(db)[0]['received_count'] == 0


def test_admin_does_not_record_or_connect(tmp_path):
    env = {**os.environ, 'OFFLINE_DIR': str(tmp_path),
           'OFFLINE_BATCH_DB': str(tmp_path / 'must-not-create.sqlite3'),
           'PYTHONPATH': os.pathsep.join([str(ROOT), str(Path(__file__).parent), str(CRAWLER), os.environ.get('PYTHONPATH', '')])}
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('offline_runner.py')),
                             '--data-dir', str(tmp_path / 'data'), '--status', '--record-batch-run'],
                            cwd=CRAWLER, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / 'must-not-create.sqlite3').exists()
    assert not (tmp_path / 'data/batch_runs').exists()
    assert not (tmp_path / 'network_attempts.log').exists()


def test_common_engine_factory_is_used_without_reading_secret(monkeypatch):
    import backend.app.db.session as session
    from news.batch_history import make_history_engine
    engine = object()
    make = Mock(return_value=engine)
    monkeypatch.setattr(session, 'make_engine', make)
    assert make_history_engine() is engine
    make.assert_called_once_with()


def test_unwritable_history_does_not_write_untraceable_db_row(tmp_path, monkeypatch, capsys):
    import news.batch_history as module
    db = database(tmp_path / 'batch.sqlite3')
    factory = Mock(return_value=db)
    run = new_run(DAY.isoformat(), ['hankyung'], START)
    begin_run(tmp_path, run)
    monkeypatch.setattr(module, 'atomic_json', Mock(side_effect=OSError('SECRET')))
    history = BatchHistory(tmp_path, 'COLLECT', ['hankyung'], DAY, DAY,
                           attempt=run['attempt_id'], engine_factory=factory).start()
    publish_run(tmp_path, run, {'execution_healthy': True})
    result = history.finish()
    assert result['status'] == 'SUCCESS' and result['sync_pending']
    assert rows(db) == []
    factory.assert_not_called()
    assert 'SECRET' not in capsys.readouterr().err
