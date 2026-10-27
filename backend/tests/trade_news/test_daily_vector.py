"""실제 자식 진입점 연결은 합성 HTTP·SQLite·가짜 임베딩으로만 시험합니다."""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock
import pytest
from filelock import FileLock
from backend.app.scheduler.jobs import news_vector
from .test_daily_scheduler import local, fixtures, offline_runner, job, NOW


def test_real_child_full_chain_dictionary_tags_vector_and_repeat(local):
    fixtures(local)
    calls=[]
    for number in (1,2):
        result=job.run_with_rag(local.root, now=NOW, env_path=local.env,
                                runner=offline_runner(local,calls,bounded=True))
        assert result['status']=='complete',result
        assert result['dictionary']['status']=='complete'
        assert result['vector']['body']['collection']=='kotra_article'
        assert result['vector']['status']=='complete'
        assert result['mysql']['inserted' if number==1 else 'existing']==2
        if number==2:assert result['vector']['body']['embedded']==0
        assert json.loads((local.root/'current_job.json').read_text())==result
    assert len(calls)==8
    assert '--tag-dictionary' in calls[1] and '--include-tags' in calls[2]
    assert not (local.tmp/'network_attempts.log').exists()


@pytest.mark.parametrize('fault',['pause','exit124'])
def test_vector_timeout_or_124_preserves_sql_and_restarts(local,monkeypatch,fault):
    fixtures(local)
    monkeypatch.setenv('OFFLINE_VECTOR_FAULT',fault)
    monkeypatch.setenv('NEWS_VECTOR_TIMEOUT_SECONDS','2')
    result=job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=True))
    assert result['status']=='partial' and result['mysql']['status']=='complete',result
    assert result['vector']['status']=='failed'
    if fault=='pause':assert result['vector']['reason']=='timeout'
    else:assert 'reason' not in result['vector']  # 자발 종료 124는 시간 초과로 오인하지 않음.
    monkeypatch.delenv('OFFLINE_VECTOR_FAULT')
    monkeypatch.setenv('NEWS_VECTOR_TIMEOUT_SECONDS','40')
    result=job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=True))
    assert result['status']=='complete' and result['mysql']['existing']==2,result


def test_interrupted_vector_recovered_and_stale_child_rejected(local,monkeypatch):
    previous={'run_id':'a'*32,'target_date':'2026-10-21','status':'running','stage':'vector',
              'mysql':{'status':'complete'},'vector':{'status':'running'}}
    job.persist(local.root,previous)
    job.recover_previous(local.root)
    got=json.loads((local.root/'current_job.json').read_text())
    assert got['status']=='interrupted' and got['vector']['status']=='interrupted'
    assert got['mysql']['status']=='complete'
    monkeypatch.setenv('TRADE_NEWS_JOB_RUN_ID','b'*32)
    never=Mock(side_effect=AssertionError('stale worker forbidden'))
    with pytest.raises(ValueError):news_vector.execute(local.root,'2026-10-21',local.tmp/'vector.json',indexer=never)
    never.assert_not_called()


def test_parent_sigkill_in_vector_preserves_sql_and_restarts(local):
    import os,sys,time
    fixtures(local)
    code = """
import os,socket
from pathlib import Path
from types import SimpleNamespace
def deny(*a,**k):raise AssertionError('network disabled')
socket.socket.connect=socket.socket.connect_ex=socket.getaddrinfo=deny
from backend.tests.trade_news.test_daily_scheduler import offline_runner,NOW,job
p=Path(os.environ['JOB_TEST_ROOT'])
local=SimpleNamespace(tmp=p,root=p/'data',env=p/'test.env')
job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=True))
"""
    from .test_daily_scheduler import ROOT,COLLECTOR
    env={**os.environ,'JOB_TEST_ROOT':str(local.tmp),'OFFLINE_VECTOR_FAULT':'pause',
         'PYTHONPATH':os.pathsep.join([str(ROOT),str(COLLECTOR),os.environ.get('PYTHONPATH','')])}
    parent=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env)
    try:
        deadline=time.monotonic()+15
        while not (local.tmp/'vector_started').exists() and time.monotonic()<deadline:time.sleep(.02)
        assert (local.tmp/'vector_started').exists()
        old=json.loads((local.root/'current_job.json').read_text())
        assert old['status']=='running' and old['stage']=='vector' and old['mysql']['status']=='complete'
        parent.kill();parent.wait(timeout=3)
        with FileLock(str(local.root/'crawler.lock'),timeout=8):pass
        result=job.run_with_rag(local.root,now=NOW,env_path=local.env,runner=offline_runner(local,[],bounded=True))
        assert result['status']=='complete' and result['mysql']['existing']==2,result
        archived=json.loads((local.root/'daily_runs'/old['target_date']/('job_'+old['run_id']+'.json')).read_text())
        assert archived['status']==archived['vector']['status']=='interrupted'
        assert archived['mysql']['status']=='complete'
    finally:
        if parent.poll() is None:parent.kill();parent.wait(timeout=3)
