"""이전 성공·현재 실행·강제 종료를 구분하고 같은 날짜 재실행 복구를 확인합니다."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from datetime import date
import pytest
from news.run_lifecycle import new_run, begin_run, publish_run, read_record
from news.failure_report import write_failure_report

ROOT=Path(__file__).resolve().parents[3]
B=ROOT/'backend/app/scheduler/collectors/trade_news'
T=Path(__file__).parent
DAY='2026-10-21'

def start(root, day=DAY):
    run=new_run(day,['hankyung'],'2026-10-22T03:00:00+09:00')
    begin_run(root,run)
    return run

def complete(root, run):
    summary={'execution_healthy':True,'sites':{'hankyung':{'status':'available_candidates_processed'}}}
    publish_run(root,run,summary,{'counts':{'eligible_stored':1},'operational':summary} if run['target_date'] else None)

@pytest.mark.parametrize('day',[DAY,None])
def test_current_and_previous_success_are_separate(tmp_path,day):
    first=start(tmp_path,day);complete(tmp_path,first)
    previous=(tmp_path/'last_success_summary.json').read_bytes()
    second=start(tmp_path,day)
    assert second['attempt_id']!=first['attempt_id']
    for path in [tmp_path/'current_run.json',tmp_path/'run_summary.json']:
        record=read_record(path)
        assert record['status']=='running' and record['execution_healthy'] is None
        assert record['counts'] is None and not record['counts_available']
        assert record['attempt_id']==second['attempt_id']
    assert (tmp_path/'last_success_summary.json').read_bytes()==previous
    if day:
        directory=tmp_path/'daily_runs'/day
        assert read_record(directory/'latest.json')['status']=='running'
        assert read_record(directory/'last_success.json')['counts']['eligible_stored']==1

def test_previous_day_running_becomes_interrupted_without_recollection(tmp_path):
    previous=start(tmp_path)
    current=start(tmp_path,'2026-10-22')
    old=read_record(tmp_path/'daily_runs'/DAY/'latest.json')
    assert old['status']=='interrupted' and old['attempt_id']==previous['attempt_id']
    assert read_record(tmp_path/'current_run.json')['attempt_id']==current['attempt_id']
    archive=list((tmp_path/'run_history').glob('interrupted_*.json'))
    assert len(archive)==1 and read_record(archive[0])['attempt_id']==previous['attempt_id']

def test_legacy_success_migrates_and_failure_does_not_replace_it(tmp_path):
    directory=tmp_path/'daily_runs'/DAY;directory.mkdir(parents=True)
    legacy={'operational':{'execution_healthy':True},'counts':{'eligible_stored':3}}
    (directory/'latest.json').write_text(json.dumps(legacy))
    run=start(tmp_path)
    saved=read_record(directory/'last_success.json')
    assert saved==legacy
    result=write_failure_report(SimpleNamespace(root=tmp_path),SimpleNamespace(daily_target=date.fromisoformat(DAY)),
        'initialization',OSError('synthetic failure'),run['started_at'],run=run)
    assert result['attempt_id']==run['attempt_id']
    assert read_record(tmp_path/'current_run.json')['status']=='execution_error'
    assert read_record(directory/'last_success.json')==saved

def test_external_id_does_not_remove_unique_attempt_identity(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADE_NEWS_RUN_ID','external-job')
    first=start(tmp_path);second=start(tmp_path)
    assert first['run_id']==second['run_id']=='external-job'
    assert first['attempt_id']!=second['attempt_id']

def test_failed_engine_not_saved_as_success(tmp_path):
    run=start(tmp_path)
    publish_run(tmp_path,run,{'execution_healthy':False,'errors':{'log_count/ERROR':1}},
                {'counts':{'eligible_stored':0}})
    assert read_record(tmp_path/'current_run.json')['status']=='execution_error'
    assert not (tmp_path/'last_success_summary.json').exists()
    assert not (tmp_path/'daily_runs'/DAY/'last_success.json').exists()

def test_initial_database_failure_is_recorded_after_start(tmp_path,monkeypatch):
    import crawl
    from unittest.mock import Mock
    urls=tmp_path/'urls.txt';urls.write_text('')
    engine=Mock(side_effect=AssertionError('수집 엔진을 시작하면 안 됨'))
    monkeypatch.setattr(crawl,'CrawlerProcess',engine)
    def fail_store(*args,**kwargs):raise OSError('synthetic DB initialization failure')
    monkeypatch.setattr(crawl,'Store',fail_store)
    monkeypatch.setattr(sys,'argv',['crawl','--data-dir',str(tmp_path),'--urls-file',str(urls)])
    with pytest.raises(OSError,match='initialization'):
        crawl.main()
    current=read_record(tmp_path/'current_run.json')
    assert current['status']=='execution_error' and current['failed_stage']=='initialization'
    assert current['attempt_id'] and current['counts'] is None
    engine.assert_not_called()

def test_status_and_locked_second_process_do_not_overwrite_run(tmp_path,monkeypatch):
    import crawl
    from filelock import FileLock
    run=start(tmp_path)
    previous=(tmp_path/'current_run.json').read_bytes()
    monkeypatch.setattr(sys,'argv',['crawl','--data-dir',str(tmp_path),'--status'])
    crawl.main()
    assert (tmp_path/'current_run.json').read_bytes()==previous
    urls=tmp_path/'urls.txt';urls.write_text('')
    monkeypatch.setattr(sys,'argv',['crawl','--data-dir',str(tmp_path),'--urls-file',str(urls)])
    with FileLock(str(tmp_path/'crawler.lock')):
        with pytest.raises(SystemExit,match='이미 실행 중'):
            crawl.main()
    assert (tmp_path/'current_run.json').read_bytes()==previous
    assert not list((tmp_path/'run_history').glob('interrupted_*.json'))

@pytest.mark.parametrize('mode,code',[('after_db_commit',73),('before_json_replace',74),('pipeline_failure',1),
                                    ('report_cache_failure',1),('after_state_commit',75)])
def test_real_scrapy_interruption_report_and_recovery(tmp_path,mode,code):
    feed='https://www.hankyung.com/sitemap/latest-article.xml'
    urls=['https://www.hankyung.com/article/2026102100001','https://www.hankyung.com/article/2026102100002']
    def fixtures(count):
        items=''.join(f'<url><loc>{u}</loc><news:news><news:title>반도체 수출 계약</news:title><news:publication_date>{DAY}</news:publication_date></news:news></url>' for u in urls[:count])
        spec={'https://www.hankyung.com/robots.txt':{'body':'User-agent: *\nAllow: /'},
              feed:{'body':'<urlset xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'+items+'</urlset>'}}
        for url in urls:
            spec[url]={'body':f'<meta property="article:published_time" content="{DAY}"><h1 class="headline">반도체 수출 계약</h1><div id="articletxt">한국 시험기업은 미국에 반도체 수출 100억원 계약을 체결했다.<br>다음 달부터 미국 기업에 공급한다.<br>공유하기</div>'}
        (tmp_path/'responses.json').write_text(json.dumps(spec))
    env={**os.environ,'OFFLINE_DIR':str(tmp_path),'OFFLINE_ENTRYPOINT':'daily_collect',
         'OFFLINE_RUN_AT':'2026-10-22T03:00:00+09:00','OFFLINE_VIRTUAL_CLOCK':'1','PYTHONDONTWRITEBYTECODE':'1',
         'PYTHONPATH':os.pathsep.join([str(T),str(B),os.environ.get('PYTHONPATH','')])}
    data=tmp_path/'data';directory=data/'daily_runs'/DAY
    def execute(fault=''):
        e={**env,'OFFLINE_FAULT_MODE':fault,'OFFLINE_FAIL_SAVE':'1' if fault=='pipeline_failure' else '0'}
        return subprocess.run([sys.executable,str(T/'offline_runner.py'),'--data-dir',str(data),'--sites','hankyung'],
                              cwd=B,env=e,capture_output=True,text=True,timeout=40)
    fixtures(1);initial=execute();assert initial.returncode==0,initial.stderr
    previous=(directory/'last_success.json').read_bytes()
    first=read_record(directory/'latest.json')
    fixtures(2);interrupted=execute(mode);assert interrupted.returncode==code,interrupted.stderr
    current=read_record(data/'current_run.json')
    committed = mode in ('report_cache_failure','after_state_commit')
    if committed:
        confirmed=read_record(data/'current_run.json')
        current=read_record(data/'run_records'/confirmed['latest']['_global'])['current']
        assert current['status']=='completed' and confirmed['pending_scopes']
        if mode=='report_cache_failure':assert '보고서 복구 대기' in interrupted.stderr
    else:
        expected='execution_error' if mode=='pipeline_failure' else 'running'
        assert current['status']==read_record(directory/'latest.json')['status']==expected
    assert current['attempt_id']!=first['attempt_id']
    if not committed:assert read_record(directory/'latest.json')['execution_healthy'] is not True
    assert (directory/'last_success.json').read_bytes()==previous
    if mode!='pipeline_failure':
        if not committed:assert read_record(directory/'latest.json')['counts'] is None
        with sqlite3.connect(data/'articles.sqlite3') as db:
            assert db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM export_pending').fetchone()[0]==(0 if committed else 1)
    recovered=execute();assert recovered.returncode==0,recovered.stderr
    report=read_record(directory/'latest.json')
    assert report['status']=='completed' and report['counts']['eligible_stored']==2
    assert report['attempt_id']!=current['attempt_id']
    assert read_record(directory/'last_success.json')==report
    with sqlite3.connect(data/'articles.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM export_pending').fetchone()[0]==0
    assert len(list((data/'articles').glob('*.json')))==2
    if mode!='pipeline_failure':
        archived=[read_record(p) for p in (data/'run_history').glob('interrupted_*.json')]
        assert any(r['attempt_id']==current['attempt_id'] for r in archived) is (not committed)
        downloads=[json.loads(line)['url'] for line in (tmp_path/'downloads.jsonl').read_text().splitlines()]
        assert downloads.count(urls[1])==1  # DB 커밋이 끝난 본문은 복구 중 재요청하지 않습니다.
    assert not (tmp_path/'network_attempts.log').exists()
