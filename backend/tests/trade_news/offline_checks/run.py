"""격리된 크롤링 회귀. 설치·운영 연결·실제 예약 시작을 하지 않습니다."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.dont_write_bytecode=True
repo=Path(__file__).resolve().parents[4]
guard=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--collect-only',action='store_true')
parser.add_argument('--smoke',default=None)
args=parser.parse_args()
keep={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','TMPDIR','TMP','TEMP'}}
os.environ.clear();os.environ.update(keep)
run=Path(tempfile.mkdtemp(prefix='trade-news-offline-'))
root=run/'isolated';root.mkdir()
os.environ.update(PYTHONDONTWRITEBYTECODE='1',TRADE_NEWS_OFFLINE_ROOT=str(root),
                  ANONYMIZED_TELEMETRY='False',SCHEDULER_GUARD_LOG=str(run/'scheduler_guard.jsonl'),
                  SCHEDULER_TEST_REPO=str(repo),PYTHONPATH=os.pathsep.join([str(guard),str(repo)]))
os.chdir(root);sys.path[:0]=[str(guard),str(repo),str(repo/'backend/app/scheduler/collectors/trade_news')]
from environment import install,verify_modules
install()
print('RUN_EVIDENCE='+str(run),flush=True)
SMOKES=['import','registration','usage_lazy','help:crawl','help:collect','help:daily_collect','help:load_project',
        'help:backend.app.scheduler.main','help:backend.app.scheduler.jobs.news_vector',
        'help:backend.app.scheduler.jobs.news_embed','help:backend.app.scheduler.jobs.rag_index']
if args.smoke=='all':
    results=[]
    for mode in SMOKES:
        with (run/(mode.replace(':','_')+'.log')).open('w') as stream:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--smoke',mode],stdout=stream,stderr=subprocess.STDOUT,timeout=60)
        results.append({'mode':mode,'exit_code':result.returncode})
    (run/'smoke_results.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results));raise SystemExit(int(any(r['exit_code'] for r in results)))
if args.smoke:
    if args.smoke not in SMOKES:parser.error('Unknown smoke check')
    from apscheduler.schedulers.base import BaseScheduler
    BaseScheduler.start=lambda *a,**k: (_ for _ in ()).throw(AssertionError('운영 예약 시작 금지'))
    if args.smoke=='import':
        for name in ('backend.app.scheduler.main','backend.app.scheduler.jobs.trade_news_daily','backend.app.scheduler.jobs.news_vector','backend.app.scheduler.jobs.news_embed','backend.app.scheduler.jobs.rag_index','load_project','daily_collect'):
            importlib.import_module(name)
    elif args.smoke=='registration':
        from backend.app.scheduler import main
        from backend.app.scheduler.jobs import trade_news_daily as job
        env=root/'synthetic.env';env.write_text('NEWS_DAILY_ENABLED=false\n')
        scheduler=main.build_scheduler(root/'un',root/'scope',env,env_path=env)
        scheduled=scheduler.get_job(job.JOB_ID)
        assert scheduled.func is job.run_with_rag and scheduled.next_run_time is None
        assert scheduled.max_instances==1 and scheduled.coalesce and not scheduler.running
    elif args.smoke=='usage_lazy':
        from backend.app.scheduler.jobs import news_embed as ne
        api=NS(embeddings=NS(create=lambda **kw:NS(data=[NS(index=0,embedding=[0.0])],usage=NS(total_tokens=7))),close=lambda:None)
        os.environ['OPENAI_API_KEY']='synthetic-not-a-key'
        with patch.object(ne,'OpenAI',return_value=api):
            provider=ne.openai_embed()
            try:
                assert provider(['synthetic'])==[[0.0]]
                assert provider.usage=={'tokens':7,'model_calls':1,'reported_responses':1}
            finally:provider.close()
    else:
        name=args.smoke[5:];sys.argv=[name,'--help']
        try:runpy.run_module(name,run_name='__main__')
        except SystemExit as error:assert error.code==0
    verify_modules(repo,run/'imports.json')
    (run/'smoke_result.json').write_text(json.dumps({'mode':args.smoke,'status':'passed','internal_module_stubs':False}))
    raise SystemExit(0)
DEFAULT_TESTS=['backend/tests/trade_news/test_admin_erd.py', 'backend/tests/trade_news/test_batch_history.py', 'backend/tests/trade_news/test_clean_focus_storage.py', 'backend/tests/trade_news/test_common_news_vector.py', 'backend/tests/trade_news/test_daily_scheduler.py', 'backend/tests/trade_news/test_daily_timeout.py', 'backend/tests/trade_news/test_daily_vector.py', 'backend/tests/trade_news/test_independent_flow.py', 'backend/tests/trade_news/test_index_resources.py', 'backend/tests/trade_news/test_loader_views.py', 'backend/tests/trade_news/test_migration_lock.py', 'backend/tests/trade_news/test_mysql_admin_integration.py', 'backend/tests/trade_news/test_mysql_fixture_cleanup.py', 'backend/tests/trade_news/test_mysql_integration.py', 'backend/tests/trade_news/test_mysql_store.py', 'backend/tests/trade_news/test_news_usage.py', 'backend/tests/trade_news/test_pipeline.py', 'backend/tests/trade_news/test_prepared_report_isolation.py', 'backend/tests/trade_news/test_project_tags.py', 'backend/tests/trade_news/test_run_lifecycle.py', 'backend/tests/trade_news/test_run_publication.py', 'backend/tests/trade_news/test_traffic.py', 'backend/tests/trade_news/test_vector_resource_contract.py', 'backend/tests/trade_news/test_view_reapply.py', 'backend/tests/test_news_keywords.py', 'backend/tests/test_news_load.py', 'backend/tests/test_news_embed.py', 'backend/tests/test_un_scheduler_integration.py']
selected=[str(repo/p) for p in DEFAULT_TESTS]
for path in selected:assert Path(path).is_file(),path
for name in ('backend.app.scheduler.main','backend.app.scheduler.jobs.trade_news_daily','backend.app.scheduler.jobs.news_vector'):
    importlib.import_module(name)
verify_modules(repo,run/'imports.json')
(run/'manifest.json').write_text(json.dumps({'repo':str(repo),'selected':selected,'test_file_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in selected}},indent=2))
import pytest
code=pytest.main(selected+['-q','-p','no:cacheprovider','-p','checks','--basetemp='+str(root/'pytest'),
                          '--junitxml='+str(run/'results.xml'),*(['--collect-only'] if args.collect_only else [])])
verify_modules(repo,run/'final_imports.json')
(run/'exit.json').write_text(json.dumps({'exit_code':code}))
raise SystemExit(code)
