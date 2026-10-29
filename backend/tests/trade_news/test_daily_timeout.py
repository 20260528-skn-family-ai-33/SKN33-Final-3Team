"""네트워크 차단 + 임시 SQLite + 시험 프로세스만으로 종료·복구 검증."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from filelock import FileLock
from sqlalchemy import text
from backend.app.scheduler.jobs import news_process
from .test_daily_scheduler import local, fixtures, offline_runner, job, NOW, DAY, ROOT, COLLECTOR
from .test_batch_history import rows


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', '1.5', '', 'bad'])
def test_bad_time_limit_does_not_launch(local, monkeypatch, value):
    monkeypatch.setenv('NEWS_COLLECTION_TIMEOUT_SECONDS', value)
    calls = []
    result = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, calls, bounded=True))
    assert result['status'] == 'failed' and result['stage'] == 'configuration' and not calls


def test_defaults_and_explicit_limits():
    assert job.time_limits({}) == {'collection': 79200, 'mysql': 3600, 'dictionary':3600, 'vector':3600}
    assert job.time_limits({'NEWS_COLLECTION_TIMEOUT_SECONDS': '123', 'NEWS_LOAD_TIMEOUT_SECONDS': '45'}) == {'collection': 123, 'mysql': 45, 'dictionary':3600, 'vector':3600}


@pytest.mark.parametrize('stage', ['collection', 'mysql'])
def test_timeout_is_recorded_and_same_day_recovers(local, monkeypatch, stage):
    fixtures(local)
    key = 'NEWS_COLLECTION_TIMEOUT_SECONDS' if stage == 'collection' else 'NEWS_LOAD_TIMEOUT_SECONDS'
    monkeypatch.setenv(key, '3')
    monkeypatch.setenv('OFFLINE_PAUSE_STAGE', stage)
    result = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, [], bounded=True))
    assert (local.tmp / 'child_started.json').exists()
    assert result['status'] == 'failed' and result['stage'] == stage
    assert result[stage] == {'status': 'failed', 'reason': 'timeout', 'timeout_seconds': 3}
    assert result['error_type'] == 'TimeoutExpired'
    if stage == 'collection':
        assert result['mysql']['status'] == 'not_started'
    with FileLock(str(local.root / 'crawler.lock'), timeout=0):
        pass
    monkeypatch.delenv('OFFLINE_PAUSE_STAGE')
    monkeypatch.delenv(key)
    recovered = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, [], bounded=True))
    assert recovered['status'] == 'complete' and recovered['mysql']['stored_articles'] == 2
    history = json.loads((local.root / 'daily_runs' / DAY / ('job_' + result['run_id'] + '.json')).read_text())
    assert history[stage]['reason'] == 'timeout'
    assert not (local.tmp / 'network_attempts.log').exists()


def test_timeout_after_article_commit_preserves_data_and_budget(local, monkeypatch):
    fixtures(local)
    monkeypatch.setenv('NEWS_LOAD_TIMEOUT_SECONDS', '3')
    result = job.run(local.root, now=NOW, env_path=local.env,
                     runner=offline_runner(local, [], 'pause_after_article_commit', bounded=True))
    assert result['mysql']['reason'] == 'timeout' and (local.tmp / 'article_committed').exists()
    with local.db.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar() == 1
    with sqlite3.connect(local.root / 'articles.sqlite3') as conn:
        budget_before = {(day, site): count for day, site, count in conn.execute('SELECT day,site,count FROM budget')}
    assert budget_before and sum(budget_before.values()) > 0
    downloads = (local.tmp / 'downloads.jsonl').read_text().splitlines()
    monkeypatch.delenv('NEWS_LOAD_TIMEOUT_SECONDS')
    result = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, [], bounded=True))
    assert result['status'] == 'complete'
    assert result['mysql']['inserted'] == 1 and result['mysql']['existing'] == 1
    # 완료 본문은 재요청하지 않습니다. 예산 DB도 같은 파일을 이어 사용합니다.
    after = (local.tmp / 'downloads.jsonl').read_text().splitlines()
    bodies = lambda lines: [json.loads(x)['url'] for x in lines if '/article/' in json.loads(x)['url'] or '/news/2026' in json.loads(x)['url']]
    assert bodies(after) == bodies(downloads)
    with sqlite3.connect(local.root / 'articles.sqlite3') as conn:
        budget_after = {(day, site): count for day, site, count in conn.execute('SELECT day,site,count FROM budget')}
    assert all(budget_after[key] >= count for key, count in budget_before.items())
    assert all(count <= 100 for count in budget_after.values())
    history = rows(local.db)
    assert all(row['status'] != 'RUNNING' for row in history)
    assert any(row['status'] == 'FAILED' for row in history)
    assert not (local.tmp / 'network_attempts.log').exists()


@pytest.mark.parametrize('stage', ['collection', 'mysql'])
def test_sigkill_parent_cleans_child_and_restarts(local, stage):
    fixtures(local)
    code = '''
import os,socket
from pathlib import Path
from types import SimpleNamespace
def deny(*a,**k):raise AssertionError('network disabled')
socket.socket.connect=socket.socket.connect_ex=socket.getaddrinfo=deny
from backend.tests.trade_news.test_daily_scheduler import offline_runner,NOW,job
p=Path(os.environ['JOB_TEST_ROOT'])
local=SimpleNamespace(tmp=p,root=p/'data',env=p/'test.env')
job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=True))
'''
    env = {**os.environ, 'JOB_TEST_ROOT': str(local.tmp), 'OFFLINE_PAUSE_STAGE': stage,
           'PYTHONPATH': os.pathsep.join([str(ROOT), str(COLLECTOR), os.environ.get('PYTHONPATH', '')])}
    parent = subprocess.Popen([sys.executable, '-c', code], env=env, cwd=ROOT)
    try:
        deadline = time.monotonic() + 12
        marker = local.tmp / 'child_started.json'
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        old = json.loads((local.root / 'current_job.json').read_text())
        child_pid = json.loads(marker.read_text())['pid']
        parent.kill(); parent.wait(timeout=3)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        else:
            pytest.fail('감시 프로세스가 부모 종료 후 자식을 정리하지 못함')
        result = job.run(local.root, now=NOW, env_path=local.env, runner=offline_runner(local, [], bounded=True))
        assert result['status'] == 'complete' and result['mysql']['stored_articles'] == 2
        archived = json.loads((local.root / 'daily_runs' / DAY / ('job_' + old['run_id'] + '.json')).read_text())
        assert archived['status'] == archived[stage]['status'] == 'interrupted'
        with local.db.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar() == 2
        assert not (local.tmp / 'network_attempts.log').exists()
    finally:
        if parent.poll() is None:
            parent.kill(); parent.wait(timeout=3)


def test_sigterm_ignoring_process_and_descendant_are_killed(tmp_path):
    marker = tmp_path / 'pids'
    code = '''
import os,signal,subprocess,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
Path(sys.argv[1]).write_text(str(os.getpid())+' '+str(child.pid))
time.sleep(60)
'''
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        news_process.run_bounded([sys.executable, '-c', code, str(marker)], timeout=1)
    assert time.monotonic() - start < 10
    for pid in map(int, marker.read_text().split()):
        # Linux 컨테이너에서는 init의 회수 전 좀비가 잠시 남을 수 있습니다.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            status = Path(f'/proc/{pid}/status')
            if status.exists() and any(line.startswith('State:') and 'Z' in line
                                       for line in status.read_text().splitlines()):
                break
            time.sleep(.05)
        else:
            pytest.fail('시간 초과 후에도 시험 프로세스가 살아 있음')


def test_nonzero_exit_is_not_timeout():
    assert news_process.run_bounded([sys.executable, '-c', 'raise SystemExit(7)'], timeout=2).returncode == 7


@pytest.mark.parametrize('code', [0, 7, 124, 125])
def test_child_exit_code_is_preserved(code):
    result = news_process.run_bounded([sys.executable, '-c', f'raise SystemExit({code})'], timeout=5)
    assert result.returncode == code
    if code:
        with pytest.raises(subprocess.CalledProcessError) as error:
            news_process.run_bounded([sys.executable, '-c', f'raise SystemExit({code})'], timeout=5, check=True)
        assert error.value.returncode == code


@pytest.mark.parametrize('stage', ['collection', 'mysql'])
def test_monitor_sigkill_cleans_group_and_daily_replay(local, monkeypatch, stage):
    import signal
    import threading
    fixtures(local)
    real_popen = subprocess.Popen
    threads, failures = [], []
    marker = local.tmp / ('child_started.json' if stage == 'collection' else 'article_committed')
    if stage == 'collection':
        monkeypatch.setenv('OFFLINE_PAUSE_STAGE', 'collection')
    def launch(command, **options):
        proc = real_popen(command, **options)
        entry = options.get('env', {}).get('OFFLINE_ENTRYPOINT')
        if '--monitor' in command and entry == ('daily_collect' if stage == 'collection' else 'load_project'):
            def terminate_monitor():
                deadline = time.monotonic() + 12
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                if not marker.exists():
                    failures.append('시험 자식 시작 실패')
                if proc.poll() is None:
                    os.kill(proc.pid, signal.SIGKILL)
            thread = threading.Thread(target=terminate_monitor)
            thread.start(); threads.append(thread)
        return proc
    monkeypatch.setattr(subprocess, 'Popen', launch)
    fault = 'pause_after_article_commit' if stage == 'mysql' else None
    result = job.run(local.root, now=NOW, env_path=local.env,
                     runner=offline_runner(local, [], fault, bounded=True))
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert not failures and threads
    assert result['status'] == 'failed' and result['stage'] == stage
    assert result['error_type'] == 'ProcessMonitorError'
    assert result[stage]['status'] == 'failed' and 'reason' not in result[stage]
    if stage == 'collection':
        assert result['mysql']['status'] == 'not_started'
    with FileLock(str(local.root / 'crawler.lock'), timeout=0):
        pass
    with local.db.connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM article')).scalar() == (1 if stage == 'mysql' else 0)
    with sqlite3.connect(local.root / 'articles.sqlite3') as connection:
        budgets = {(d, s): c for d, s, c in connection.execute('SELECT day,site,count FROM budget')}
    monkeypatch.setattr(subprocess, 'Popen', real_popen)
    monkeypatch.delenv('OFFLINE_PAUSE_STAGE', raising=False)
    restored = job.run(local.root, now=NOW, env_path=local.env,
                       runner=offline_runner(local, [], bounded=True))
    assert restored['status'] == 'complete' and restored['mysql']['stored_articles'] == 2
    assert restored['mysql']['inserted'] == (1 if stage == 'mysql' else 2)
    assert restored['mysql']['existing'] == (1 if stage == 'mysql' else 0)
    archived = json.loads((local.root / 'daily_runs' / DAY / ('job_' + result['run_id'] + '.json')).read_text())
    assert archived['error_type'] == 'ProcessMonitorError' and archived['status'] == 'failed'
    assert all(row['status'] != 'RUNNING' for row in rows(local.db))
    with sqlite3.connect(local.root / 'articles.sqlite3') as connection:
        after = {(d, s): c for d, s, c in connection.execute('SELECT day,site,count FROM budget')}
    assert all(after[key] >= value for key, value in budgets.items())
    assert all(value <= 100 for value in after.values())
    assert not (local.tmp / 'network_attempts.log').exists()


def test_monitor_death_before_pid_message_never_starts_command(tmp_path, monkeypatch):
    # 자식 생성 직후, PID를 부모에 전달하기 직전에 감시기를 종료합니다.
    marker = tmp_path / 'must_not_start'
    real_popen = subprocess.Popen
    module = str(Path(news_process.__file__).resolve())
    code = '''
import importlib.util,os,signal,sys
s=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
real=m.subprocess.Popen
def stop_before_announcement(*args,**kwargs):
    child=real(*args,**kwargs)
    os.kill(os.getpid(),signal.SIGKILL)
m.subprocess.Popen=stop_before_announcement
m.supervise(sys.argv[5:],float(sys.argv[3]),int(sys.argv[4]))
'''
    def launch(command, **options):
        if '--monitor' in command:
            command = [command[0], '-c', code, module, *command[2:]]
        return real_popen(command, **options)
    monkeypatch.setattr(subprocess, 'Popen', launch)
    with pytest.raises(news_process.ProcessMonitorError):
        news_process.run_bounded([sys.executable, '-c', f'from pathlib import Path;Path({str(marker)!r}).touch()'], timeout=2)
    time.sleep(.2)
    assert not marker.exists()


@pytest.mark.parametrize('outcome',['timeout','exit124','monitor_unconfirmed'])
def test_monitor_protocol_is_deterministic_without_os_processes(tmp_path,monkeypatch,outcome):
    """OS 신호 권한과 별개로 완료 메시지/시간 초과/종료 코드를 구분합니다."""
    import io
    from types import SimpleNamespace
    from unittest.mock import Mock
    fake=SimpleNamespace(stdin=io.BytesIO(),wait=Mock(return_value=1 if outcome=='monitor_unconfirmed' else 0),kill=Mock())
    stopped=[]
    def launch(command,**options):
        fd=int(command[4]);messages=[{'kind':'started','pid':987654}]
        if outcome!='monitor_unconfirmed':
            messages.append({'kind':'finished','returncode':124 if outcome=='exit124' else -15,'timed_out':outcome=='timeout'})
        os.write(fd, ''.join(json.dumps(m)+'\n' for m in messages).encode())
        return fake
    monkeypatch.setattr(news_process.subprocess,'Popen',launch)
    monkeypatch.setattr(news_process,'stop_group',lambda pid:stopped.append(pid))
    command=['synthetic-only']
    if outcome=='timeout':
        with pytest.raises(subprocess.TimeoutExpired):news_process.run_bounded(command,timeout=3)
    elif outcome=='monitor_unconfirmed':
        with pytest.raises(news_process.ProcessMonitorError):news_process.run_bounded(command,timeout=3)
    else:assert news_process.run_bounded(command,timeout=3).returncode==124
    assert stopped==([987654] if outcome=='monitor_unconfirmed' else [])
    assert fake.stdin.closed


def test_group_probe_permission_denied_never_emits_finished(monkeypatch):
    """권한 거부를 종료 완료로 바꾸지 않습니다. 모든 PID/신호는 가짜입니다."""
    import io,itertools,signal
    from types import SimpleNamespace
    from unittest.mock import Mock
    child=SimpleNamespace(pid=987654,stdin=io.BytesIO(),poll=Mock(return_value=None),wait=Mock())
    monkeypatch.setattr(news_process.subprocess,'Popen',lambda *a,**k:child)
    calls=[];events=[]
    def killpg(pid,sig):
        assert pid==987654
        calls.append(sig)
        if sig==0:raise PermissionError('synthetic group probe denied')
    monkeypatch.setattr(news_process.os,'killpg',killpg)
    monkeypatch.setattr(news_process.os,'read',lambda *a:b'1')
    monkeypatch.setattr(news_process.os,'write',lambda fd,b:events.append(json.loads(b)))
    selections=iter([([0],[],[]),([],[],[])])
    monkeypatch.setattr(news_process.select,'select',lambda *a:next(selections))
    clock=itertools.count(0,2);monkeypatch.setattr(news_process.time,'monotonic',lambda:next(clock))
    with pytest.raises(PermissionError,match='synthetic group probe denied'):
        news_process.supervise(['synthetic-only'],1,123)
    assert calls==[signal.SIGTERM,0]
    assert [e['kind'] for e in events]==['started']
    child.wait.assert_not_called()


@pytest.mark.parametrize('reported',['timeout','monitor_unconfirmed'])
def test_injected_termination_outcome_preserves_commit_budget_and_replay(local,monkeypatch,reported):
    """자식은 합성 SQLite 1건 커밋 후 자발적으로 종료합니다. 종료 결과만 주입합니다."""
    fixtures(local)
    first_runner=offline_runner(local,[],'after_article_commit',bounded=False)
    def interrupted(command,**options):
        result=first_runner(command,**options)
        if Path(command[1]).stem=='load_project':
            assert result.returncode==77
            if reported=='timeout':raise subprocess.TimeoutExpired(command,options['timeout'])
            raise news_process.ProcessMonitorError('synthetic unconfirmed cleanup') from PermissionError('synthetic group probe denied')
        return result
    result=job.run(local.root,now=NOW,env_path=local.env,runner=interrupted)
    assert result['status']=='failed' and result['stage']=='mysql'
    assert result['mysql']['status']=='failed'
    if reported=='timeout':
        assert result['error_type']=='TimeoutExpired' and result['mysql']['reason']=='timeout'
    else:
        assert result['error_type']=='ProcessMonitorError' and 'reason' not in result['mysql']
    with local.db.connect() as conn:assert conn.execute(text('SELECT COUNT(*) FROM article')).scalar()==1
    with sqlite3.connect(local.root/'articles.sqlite3') as conn:
        before={(day,site):count for day,site,count in conn.execute('SELECT day,site,count FROM budget')}
    assert before and sum(before.values())>0
    with FileLock(str(local.root/'crawler.lock'),timeout=0):pass
    restored=job.run(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=False))
    assert restored['status']=='complete'
    assert restored['mysql']['inserted']==restored['mysql']['existing']==1
    with sqlite3.connect(local.root/'articles.sqlite3') as conn:
        after={(day,site):count for day,site,count in conn.execute('SELECT day,site,count FROM budget')}
    assert all(after[key]>=value for key,value in before.items())
    assert all(value<=100 for value in after.values())
    archived=json.loads((local.root/'daily_runs'/DAY/('job_'+result['run_id']+'.json')).read_text(encoding='utf-8'))
    assert archived['error_type']==result['error_type'] and archived['status']=='failed'
    assert all(row['status']!='RUNNING' for row in rows(local.db))
    assert not (local.tmp/'network_attempts.log').exists()
