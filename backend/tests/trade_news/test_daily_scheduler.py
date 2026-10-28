"""일정은 시작하지 않고 등록만 검사. 작업 함수는 합성 HTTP·임시 SQLite로 직접 검증."""
import json
import os
import sqlite3
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text
from filelock import FileLock
from backend.app.scheduler.jobs import trade_news_daily as job
from news.save import Store
from .test_batch_history import database, rows

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).parent
COLLECTOR = ROOT / 'backend/app/scheduler/collectors/trade_news'
NOW = datetime.fromisoformat('2026-10-23T03:00:00+09:00')
DAY = '2026-10-22'


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    Store(root).close()
    env = tmp_path / 'test.env'
    env.write_text('NEWS_DAILY_ENABLED=false\n')
    monkeypatch.setattr(job, 'CONFIGURATION_ERRORS', tmp_path / 'config-errors')
    monkeypatch.delenv('NEWS_DAILY_ENABLED', raising=False)
    monkeypatch.delenv('NEWS_CRAWL_DATA_DIR', raising=False)
    monkeypatch.delenv('NEWS_SOURCE_MAP', raising=False)
    db = database(tmp_path / 'project.sqlite3')
    yield SimpleNamespace(root=root, env=env, db=db, tmp=tmp_path)
    db.dispose()


def test_schedule_registered_paused_with_kst_three_and_no_start(local, monkeypatch):
    start = Mock(side_effect=AssertionError('예약 실행 금지'))
    monkeypatch.setattr(BlockingScheduler, 'start', start)
    scheduler = BlockingScheduler(timezone=job.KST)
    runner = Mock(side_effect=AssertionError('작업 실행 금지'))
    monkeypatch.setattr(job, 'run', runner)
    job.register_jobs(scheduler, env_path=local.env)
    scheduled = scheduler.get_job(job.JOB_ID)
    assert scheduled.next_run_time is None and scheduled.max_instances == 1 and scheduled.coalesce
    assert scheduled.trigger.get_next_fire_time(None, datetime.fromisoformat('2026-10-22T23:00:00+09:00')).isoformat() == NOW.isoformat()
    assert [j.id for j in scheduler.get_jobs()] == [job.JOB_ID]
    runner.assert_not_called(); start.assert_not_called()


def test_common_scheduler_wires_paused_job_without_invoking_jobs(local, monkeypatch):
    # 공용 main의 다른 작업을 실행하지 않으며, 미사용 SDK를 생성하면 즉시 실패합니다.
    for name, attribute in [('openai', 'OpenAI'), ('chromadb', 'PersistentClient')]:
        module = types.ModuleType(name)
        setattr(module, attribute, Mock(side_effect=AssertionError('외부 서비스 사용 금지')))
        monkeypatch.setitem(sys.modules, name, module)
    from backend.app.scheduler import main
    # 다른 등록 함수도 실제 .env 대신 같은 시험 설정만 읽게 합니다.
    from dotenv.main import DotEnv
    read_config = DotEnv.dict
    def isolated_config(config):
        if str(config.dotenv_path) == str(ROOT / '.env'):
            config = DotEnv(local.env)
        return read_config(config)
    monkeypatch.setattr(DotEnv, 'dict', isolated_config)
    monkeypatch.setattr(job, 'settings', lambda _: {'NEWS_DAILY_ENABLED': 'false'})
    scheduler = main.build_scheduler(local.tmp / 'un', local.tmp / 'scope', local.env, env_path=local.env)
    scheduled = scheduler.get_job(job.JOB_ID)
    assert scheduled.func is job.run_with_rag and scheduled.next_run_time is None
    assert len(scheduler.get_jobs()) == 11
    assert not any(j.id == 'trade_news_startup' for j in scheduler.get_jobs())


@pytest.mark.parametrize('value,enabled', [('false',False),('',False),('typo',False),('true',True)])
def test_schedule_flag_only_affects_registration(local, monkeypatch, value, enabled):
    monkeypatch.setenv('NEWS_DAILY_ENABLED', value)
    scheduler = BlockingScheduler(timezone=job.KST)
    job.register_jobs(scheduler, env_path=local.env)
    scheduled = scheduler.get_job(job.JOB_ID)
    # 시작하지 않은 활성 예정 잡은 next_run_time이 아직 없고, 중단 잡은 명시적으로 None입니다.
    assert hasattr(scheduled, 'next_run_time') is (not enabled)


def fixtures(local, body_status=200, empty=False):
    spec = {}
    for host, feed, url, title_class, body_selector in [
        ('https://www.hankyung.com','/sitemap/latest-article.xml','https://www.hankyung.com/article/2026102200001','headline','id="articletxt"'),
        ('https://www.fnnews.com','/sitemap/news/latest.xml','https://www.fnnews.com/news/202610220000000001','article-view__title','class="article-view__body"')]:
        spec[host + '/robots.txt'] = {'body': 'User-agent: *\nAllow: /'}
        entry = f'<url><loc>{url}</loc><news:news><news:title>반도체 수출 계약</news:title><news:publication_date>{DAY}</news:publication_date></news:news></url>'
        spec[host + feed] = {'body': '<urlset xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">' + ('' if empty else entry) + '</urlset>'}
        spec[url] = {'status': body_status, 'body': f'<meta property="article:published_time" content="{DAY}"><h1 class="{title_class}">반도체 수출 계약</h1><div {body_selector}>한국 시험기업은 미국에 반도체 수출 100억원 계약을 체결했다.<br>공유하기<br>연락 sample@example.test</div>'}
    (local.tmp / 'responses.json').write_text(json.dumps(spec))


def offline_runner(local, calls, fault=None, bounded=False):
    def run(command, **options):
        calls.append(command)
        timeout = min(options.pop('timeout', 40), 40)
        entry = Path(command[1]).stem
        env = {**options.pop('env'), 'OFFLINE_DIR': str(local.tmp), 'OFFLINE_ENTRYPOINT': entry,
               'OFFLINE_BATCH_DB': str(local.tmp / 'project.sqlite3'), 'OFFLINE_VIRTUAL_CLOCK': '1',
               'OFFLINE_RUN_AT': NOW.isoformat(), 'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONPATH': os.pathsep.join([str(ROOT), str(TESTS), str(COLLECTOR), os.environ.get('PYTHONPATH', '')])}
        env.pop('OFFLINE_FAULT_MODE', None)
        fault_entry = 'load_project' if fault in ('after_article_commit', 'pause_after_article_commit') else 'daily_collect'
        if fault and entry == fault_entry:
            env['OFFLINE_FAULT_MODE'] = fault
        execute = job.run_bounded if bounded else subprocess.run
        return execute([command[0], str(TESTS / 'offline_runner.py'), *command[2:]],
                       env=env, timeout=timeout, **options)
    return run


def test_full_chain_same_date_source_clean_body_history_and_repeat(local):
    fixtures(local)
    calls = []
    for attempt in (1,2):
        # UTC 입력도 한국 시간 전날로 계산하며 두 자식에게 동일 날짜를 보냅니다.
        result = job.run(local.root, now=NOW.astimezone(ZoneInfo('UTC')), env_path=local.env,
                         runner=offline_runner(local,calls))
        assert result['status'] == 'complete', result
        assert result['target_date'] == DAY
        assert result['mysql']['inserted' if attempt==1 else 'existing'] == 2
        assert result['mysql']['run_id'] == result['run_id']
        assert json.loads((local.root / 'current_job.json').read_text()) == result
        assert json.loads((local.root / 'daily_runs' / DAY / 'job_latest.json').read_text()) == result
        detail=json.loads((local.root/result['collection']['report_file']).read_text())['daily']
        assert result['collection']['counts']==detail['counts']
        assert len(detail['articles'])==2 and 'report' not in result['collection']
        loaded=json.loads((local.root/result['mysql']['result_file']).read_text())
        assert len(loaded['article_results'])==2 and 'article_results' not in result['mysql']
    assert len(calls) == 4
    for command in calls:
        assert command[0] == sys.executable and command[command.index('--target-date')+1] == DAY
        assert '--record-batch-run' in command
    with local.db.connect() as c:
        stored = c.execute(text('SELECT source_code,published_date,body_masked FROM article')).all()
        assert len(stored) == 2 and {r[0] for r in stored} == {'HANKYUNG','FNNEWS'}
        assert all(str(r[1]) == DAY and '공유하기' not in r[2] and 'sample@example.test' not in r[2] for r in stored)
        schema = (ROOT/'db/mysql/init/01_schema.sql').read_text()
        import re
        for view in re.findall(r'CREATE VIEW v_article_.*?;',schema):c.execute(text(view))
        assert c.execute(text('SELECT SUM(article_count) FROM v_article_source_day')).scalar() == 2
    assert len(rows(local.db)) == 8 and all(r['status']=='SUCCESS' for r in rows(local.db))
    downloads = [json.loads(line)['url'] for line in (local.tmp/'downloads.jsonl').read_text().splitlines()]
    assert sum('/article/' in url or '/news/2026' in url for url in downloads) == 2
    assert not (local.tmp/'network_attempts.log').exists()
    with sqlite3.connect(local.root/'articles.sqlite3') as c:
        assert all('공유하기' in json.loads(r[0])['body'] for r in c.execute('SELECT payload FROM articles'))


def test_zero_articles_is_complete_without_filling(local):
    fixtures(local,empty=True)
    result = job.run(local.root, now=NOW, env_path=local.env,runner=offline_runner(local,[]))
    assert result['status'] == 'complete' and result['mysql']['inserted'] == 0
    assert result['collection']['counts']['title_candidates'] == 0


def test_site_403_remains_partial_and_does_not_fetch_more(local):
    fixtures(local,body_status=403)
    result = job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
    assert result['status'] == 'partial' and result['mysql']['inserted'] == 0
    assert all(s['status']=='manual_stop' for s in result['collection']['sites'].values())
    assert not (local.tmp/'network_attempts.log').exists()


@pytest.mark.parametrize('fault', ['after_db_commit','after_state_commit','report_cache_failure'])
def test_collection_crash_and_publication_recovery(local, fault):
    fixtures(local)
    calls=[]
    failed = job.run(local.root, now=NOW,env_path=local.env,runner=offline_runner(local,calls,fault))
    assert failed['status'] == 'failed'
    if fault == 'after_db_commit':
        assert len(calls) == 1 and failed['mysql']['status'] == 'not_started'
    # 새 실행으로 종료 미확정 항목만 복구. 완료된 원문의 본문은 재요청하지 않습니다.
    recovered = job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
    assert recovered['status']=='complete' and recovered['mysql']['stored_articles']==2
    with local.db.connect() as c:assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==2


def test_stale_report_never_triggers_load(local):
    fixtures(local)
    first = job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
    calls=[]
    def stale(command,**kwargs):calls.append(command);return SimpleNamespace(returncode=0)
    result=job.run(local.root,now=NOW,env_path=local.env,runner=stale)
    assert result['status']=='failed' and len(calls)==1 and result['mysql']['status']=='not_started'
    assert result['run_id']!=first['run_id']


def test_conflicting_insert_preserves_existing_values_and_reports_failure(local):
    fixtures(local)
    first=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
    with local.db.begin() as c:c.execute(text("UPDATE article SET body_masked='보존할 기존 값' WHERE source_code='HANKYUNG'"))
    failed=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
    assert failed['status']=='failed' and failed['mysql']['failed']==1
    with local.db.connect() as c:assert c.execute(text("SELECT body_masked FROM article WHERE source_code='HANKYUNG'")).scalar()=='보존할 기존 값'


def test_job_lock_prevents_second_call_without_replacing_status(local):
    marker={'status':'running','run_id':'a'*32,'target_date':DAY}
    job.atomic_json(local.root/'current_job.json',marker)
    runner=Mock(side_effect=AssertionError('호출 금지'))
    with FileLock(str(local.root/'daily_job.lock')):
        result=job.run(local.root,now=NOW,env_path=local.env,runner=runner)
    assert result['status']=='already_running' and json.loads((local.root/'current_job.json').read_text())==marker
    runner.assert_not_called()


def test_parent_interrupted_is_archived_before_new_attempt(local):
    fixtures(local)
    marker={'status':'running','run_id':'b'*32,'target_date':'2026-10-21'}
    job.atomic_json(local.root/'current_job.json',marker)
    calls=[]
    result=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,calls))
    assert result['status']=='complete'
    previous=json.loads((local.root/'daily_runs/2026-10-21/job_latest.json').read_text())
    assert previous['status']=='interrupted'
    assert all(command[command.index('--target-date')+1]==DAY for command in calls)
    assert result['target_date']==DAY


@pytest.mark.parametrize('bad_mapping', [
    {'hankyung':'OTHER','fnnews':'FNNEWS'},
    {'hankyung':'HANKYUNG','fnnews':'FNNEWS','extra':'HANKYUNG'},
    {'hankyung':'HANKYUNG','fnnews':'FNNEWS','extra':None},
])
def test_missing_budget_and_bad_mapping_do_not_request(local,bad_mapping):
    never=Mock(side_effect=AssertionError('요청 금지'))
    missing=local.tmp/'does-not-exist'
    result=job.run(missing,now=NOW,env_path=local.env,runner=never)
    assert result['status']=='failed' and not missing.exists()
    mapping=local.tmp/'bad-map.json';mapping.write_text(json.dumps(bad_mapping))
    result=job.run(local.root,mapping,now=NOW,env_path=local.env,runner=never)
    assert result['status']=='failed'
    assert json.loads((local.root/'current_job.json').read_text())['status']=='failed'
    never.assert_not_called()


def test_parent_process_killed_after_load_recovers_without_double_insert(local):
    fixtures(local)
    code = """
import os,socket
from pathlib import Path
from types import SimpleNamespace
def deny(*args,**kwargs):raise AssertionError('외부 네트워크 금지')
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.getaddrinfo=deny
from backend.tests.trade_news.test_daily_scheduler import offline_runner,NOW
from backend.app.scheduler.jobs import trade_news_daily as job
p=Path(os.environ['JOB_TEST_ROOT'])
local=SimpleNamespace(tmp=p,root=p/'data',env=p/'test.env')
runner=offline_runner(local,[])
def interrupted(command,**options):
    result=runner(command,**options)
    if Path(command[1]).name=='load_project.py':os._exit(76)
    return result
job.run(local.root,now=NOW,env_path=local.env,runner=interrupted)
"""
    env={**os.environ,'JOB_TEST_ROOT':str(local.tmp),
         'PYTHONPATH':os.pathsep.join([str(ROOT),str(COLLECTOR),os.environ.get('PYTHONPATH','')])}
    crashed=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
    assert crashed.returncode==76,crashed.stderr
    previous=json.loads((local.root/'current_job.json').read_text())
    assert previous['status']=='running' and previous['stage']=='mysql'
    assert previous['mysql']['status']=='running'
    with local.db.connect() as c:assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==2
    recovered=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
    assert recovered['status']=='complete' and recovered['mysql']['existing']==2 and recovered['mysql']['inserted']==0
    history=json.loads((local.root/'daily_runs'/DAY/('job_'+previous['run_id']+'.json')).read_text())
    assert history['status']=='interrupted'
    assert not (local.tmp/'network_attempts.log').exists()


def test_empty_file_is_not_accepted_as_existing_budget(local):
    root=local.tmp/'empty-budget';root.mkdir()
    (root/'articles.sqlite3').touch()
    never=Mock(side_effect=AssertionError('요청 금지'))
    result=job.run(root,now=NOW,env_path=local.env,runner=never)
    assert result['status']=='failed' and (root/'articles.sqlite3').stat().st_size==0
    never.assert_not_called()


def test_busy_child_does_not_replace_previous_job(local):
    marker={'status':'running','run_id':'c'*32,'target_date':DAY,'stage':'collection'}
    job.atomic_json(local.root/'current_job.json',marker)
    runner=Mock(side_effect=AssertionError('살아 있는 자식과 중첩 실행 금지'))
    with FileLock(str(local.root/'crawler.lock')):
        result=job.run(local.root,now=NOW,env_path=local.env,runner=runner)
    assert result['status']=='already_running' and result['active_lock']=='crawler.lock'
    assert json.loads((local.root/'current_job.json').read_text())==marker
    runner.assert_not_called()


@pytest.mark.parametrize('stage', ['collection','mysql'])
def test_parent_killed_while_child_alive_then_recovers(local, stage):
    import time
    from filelock import Timeout
    fixtures(local)
    code='''
import os,socket
from pathlib import Path
from types import SimpleNamespace
def deny(*args,**kwargs):raise AssertionError('실제 네트워크 금지')
socket.socket.connect=socket.socket.connect_ex=socket.getaddrinfo=deny
from backend.tests.trade_news.test_daily_scheduler import offline_runner,NOW
from backend.app.scheduler.jobs import trade_news_daily as job
p=Path(os.environ['JOB_TEST_ROOT'])
local=SimpleNamespace(tmp=p,root=p/'data',env=p/'test.env')
job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
'''
    env={**os.environ,'JOB_TEST_ROOT':str(local.tmp),'OFFLINE_PAUSE_STAGE':stage,
         'PYTHONPATH':os.pathsep.join([str(ROOT),str(COLLECTOR),os.environ.get('PYTHONPATH','')])}
    parent=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    released=False
    try:
        deadline=time.monotonic()+12
        while not (local.tmp/'child_started.json').exists() and time.monotonic()<deadline:
            time.sleep(0.02)
        assert (local.tmp/'child_started.json').exists(),parent.poll()
        child=json.loads((local.tmp/'child_started.json').read_text())
        previous_bytes=(local.root/'current_job.json').read_bytes()
        previous=json.loads(previous_bytes)
        assert previous['stage']==stage
        parent.kill();parent.wait(timeout=3)
        os.kill(child['pid'],0)  # 부모 종료 뒤에도 별도 자식이 살아 있는 실제 상황
        calls=[]
        busy=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,calls))
        assert busy['status']=='already_running' and busy['active_lock']=='crawler.lock'
        assert calls==[] and (local.root/'current_job.json').read_bytes()==previous_bytes
        (local.tmp/'release_child').touch();released=True
        deadline=time.monotonic()+12
        unlocked=False
        while time.monotonic()<deadline:
            try:
                with FileLock(str(local.root/'crawler.lock'),timeout=0):unlocked=True
                break
            except Timeout:time.sleep(0.02)
        assert unlocked
        restored=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[]))
        assert restored['status']=='complete' and restored['mysql']['stored_articles']==2
        assert restored['mysql']['inserted']==(2 if stage=='collection' else 0)
        assert restored['mysql']['existing']==(0 if stage=='collection' else 2)
        archived=json.loads((local.root/'daily_runs'/DAY/('job_'+previous['run_id']+'.json')).read_text())
        assert archived['status']=='interrupted'
        with local.db.connect() as c:assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==2
        downloads=[json.loads(line)['url'] for line in (local.tmp/'downloads.jsonl').read_text().splitlines()]
        assert sum('/article/' in url or '/news/2026' in url for url in downloads)==2
        assert not (local.tmp/'network_attempts.log').exists()
    finally:
        if not released:(local.tmp/'release_child').touch()
        if parent.poll() is None:parent.kill();parent.wait(timeout=3)


@pytest.mark.parametrize('entry,stage',[('daily_collect','collection'),('load_project','mysql')])
def test_late_old_child_cannot_overwrite_new_run_or_write_db(local,entry,stage):
    # 부모가 끝난 뒤 아직 잠금을 얻지 못했던 자식이 늦게 시작하는 경계도 보호합니다.
    fixtures(local)
    marker={'status':'running','stage':stage,'run_id':'e'*32,'target_date':DAY}
    job.atomic_json(local.root/'current_job.json',marker)
    env={**os.environ,'OFFLINE_DIR':str(local.tmp),'OFFLINE_ENTRYPOINT':entry,
         'OFFLINE_BATCH_DB':str(local.tmp/'project.sqlite3'),'OFFLINE_VIRTUAL_CLOCK':'1',
         'TRADE_NEWS_RUN_ID':'d'*32,'TRADE_NEWS_JOB_RUN_ID':'d'*32,
         'PYTHONPATH':os.pathsep.join([str(ROOT),str(TESTS),str(COLLECTOR),os.environ.get('PYTHONPATH','')])}
    command=[sys.executable,str(TESTS/'offline_runner.py'),'--data-dir',str(local.root),
             '--sites','hankyung','fnnews','--target-date',DAY,'--record-batch-run']
    if entry=='load_project':
        command+=['--source-map',str(COLLECTOR/'source_map.sample.json'),'--apply',
                  '--result-file',str(local.tmp/'old-load-result.json')]
    old=subprocess.run(command,env=env,cwd=COLLECTOR,capture_output=True,text=True,timeout=15)
    assert old.returncode!=0
    assert json.loads((local.root/'current_job.json').read_text())==marker
    assert not (local.root/'current_run.json').exists()
    assert not (local.root/'batch_runs').exists()
    assert not (local.tmp/'downloads.jsonl').exists()
    assert not (local.tmp/'network_attempts.log').exists()
    with local.db.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM article')).scalar()==0
        assert c.execute(text('SELECT COUNT(*) FROM batch_run')).scalar()==0


def test_daily_entry_runs_crawler_in_same_process(local,monkeypatch):
    import daily_collect,collect
    monkeypatch.chdir(local.tmp)
    monkeypatch.setattr(sys,'argv',['daily_collect.py'])
    child=Mock(side_effect=AssertionError('중간 수집 프로세스 생성 금지'))
    monkeypatch.setattr(collect.subprocess,'run',child)
    launch=Mock()
    monkeypatch.setattr(daily_collect.runpy,'run_path',launch)
    daily_collect.main(['--data-dir',str(local.root),'--target-date',DAY,'--sites',*job.SITES,'--record-batch-run'])
    launch.assert_called_once_with(str(COLLECTOR/'crawl.py'),run_name='__main__')
    assert sys.argv[sys.argv.index('--daily-target')+1]==DAY
    assert sys.argv[sys.argv.index('--daily-limit')+1]=='100'
    assert '--record-batch-run' in sys.argv
    child.assert_not_called()


@pytest.mark.parametrize('trade_enabled,short_enabled', [(False, False), (True, False), (False, True), (True, True)])
def test_integrated_source_switches_are_independent(local, monkeypatch, trade_enabled, short_enabled):
    """두 수집 설정은 독립이며 등록만으로 예약·수집을 실행하지 않습니다."""
    from apscheduler.schedulers.base import BaseScheduler
    from backend.app.scheduler import main
    from backend.app.scheduler.jobs import shortnews_daily
    monkeypatch.delenv('SHORTNEWS_DAILY_ENABLED', raising=False)
    local.env.write_text(f'NEWS_DAILY_ENABLED={str(trade_enabled).lower()}\n'
                         f'SHORTNEWS_DAILY_ENABLED={str(short_enabled).lower()}\n')
    execute = Mock(side_effect=AssertionError('예약/수집 실행 금지'))
    monkeypatch.setattr(BaseScheduler, 'start', execute)
    monkeypatch.setattr(job, 'run_with_rag', execute)
    monkeypatch.setattr(shortnews_daily, 'run', execute)
    scheduler = main.build_scheduler(local.tmp/'un', local.tmp/'scope', local.env, env_path=local.env)
    jobs = {item.id: item for item in scheduler.get_jobs()}
    assert len(jobs) == 11 + (2 if short_enabled else 0)
    assert ('shortnews_daily' in jobs) is short_enabled
    assert ('shortnews_startup' in jobs) is short_enabled
    trade = jobs['trade_news_daily']
    assert (getattr(trade, 'next_run_time', 'pending') is None) is (not trade_enabled)
    assert trade.kwargs['env_path'] == local.env
    assert trade.trigger.get_next_fire_time(None, NOW) == NOW
    assert not scheduler.running
    execute.assert_not_called()
