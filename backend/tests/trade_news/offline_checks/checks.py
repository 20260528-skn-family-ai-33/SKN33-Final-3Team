import pytest
from pathlib import Path
@pytest.fixture(autouse=True)
def guarded_paths(monkeypatch,tmp_path):
    from backend.app.db import news_lock
    from backend.app.scheduler.jobs import news_load
    from backend.app.services import news_schema
    from backend.tests.trade_news.mysql_test_support import block_external_io
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

"""Fail closed before test execution; do not rely on CLI path deselection."""
from pathlib import Path
import json,os,subprocess
import pytest
repo=Path(os.environ['SCHEDULER_TEST_REPO']).resolve()
SAFE_TIMEOUT={
 'test_bad_time_limit_does_not_launch', 'test_defaults_and_explicit_limits',
 'test_monitor_protocol_is_deterministic_without_os_processes',
 'test_group_probe_permission_denied_never_emits_finished',
 'test_injected_termination_outcome_preserves_commit_budget_and_replay',
}
def prohibited(item):
 path=Path(item.path).resolve().relative_to(repo/'backend/tests').as_posix()
 name=item.originalname or item.name.split('[')[0]
 if path=='trade_news/test_daily_timeout.py':return name not in SAFE_TIMEOUT
 if path=='trade_news/test_daily_vector.py':return name!='test_interrupted_vector_recovered_and_stale_child_rejected'
 if path=='trade_news/test_daily_scheduler.py':return name=='test_parent_killed_while_child_alive_then_recovers'
 if path=='trade_news/test_project_tags.py':return name=='test_daily_generates_tags_stores_and_counts_countries'
 return path=='test_shortnews_worker.py'
def row(item):
 return {'nodeid':item.nodeid,'path':str(Path(item.path).resolve()),'originalname':item.originalname or item.name}
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config,items):
 excluded=[item for item in items if prohibited(item) or (item.originalname or '').startswith('test_public_search')]
 retained=[item for item in items if item not in excluded]
 assert all(Path(item.path).resolve().is_relative_to(repo/'backend/tests') for item in retained)
 assert not any(prohibited(item) for item in retained),'native test remained'
 config.hook.pytest_deselected(items=excluded)
 items[:]=retained
 record={'native_retained':0,'native_excluded':sum(prohibited(item) for item in excluded),'out_of_scope_excluded':sum(not prohibited(item) for item in excluded),'retained':[row(item) for item in retained],'excluded':[row(item) for item in excluded],
         'policy':'Exact source path and originalname, before any test executes; no native monitor launch permitted.'}
 (Path(os.environ['SCHEDULER_GUARD_LOG']).parent/'verified_collection.json').write_text(json.dumps(record,indent=2))
@pytest.fixture(autouse=True)
def no_native_monitor(monkeypatch):
 original=subprocess.Popen
 def launch(command,*args,**kwargs):
  if isinstance(command,(list,tuple)) and any(str(part) in ('--monitor','--worker') for part in command):
   raise AssertionError('SAFE_SCOPE: native monitor launch forbidden')
  return original(command,*args,**kwargs)
 monkeypatch.setattr(subprocess,'Popen',launch)

"""Permit real executor startup only for two reviewed, local fake-job fixtures."""
import json
import os
from pathlib import Path

import pytest
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.date import DateTrigger

_real_start = BaseScheduler.start
_repo = Path(os.environ['SCHEDULER_TEST_REPO']).resolve()
assert Path(__file__).resolve().is_relative_to(_repo/'backend/tests')

_cases = {
    'test_shortnews_daily.py::ShortnewsDailyTest::test_collection_failure_does_not_stop_other_executor_jobs': {
        'shortnews': 'fake_shortnews_failure', 'other': 'fake_other_job',
        'synthetic_executor_failure': 'fake_executor_failure',
    },
    'test_un_scheduler_integration.py::SchedulerIntegrationTest::test_real_scheduler_keeps_other_job_after_un_failure': {
        'un_data_startup': 'fake_un_failure', 'hs_code_embed': 'fake_other_job',
        'synthetic_executor_failure': 'fake_executor_failure',
    },
}

@pytest.fixture(autouse=True)
def only_reviewed_scheduler_fixture(request, monkeypatch, guarded_paths):
    matching = [key for key in _cases if request.node.nodeid.endswith(key)]
    if not matching:
        yield
        return  # The normal blanket start guard stays installed for every other test.
    key = matching[0]
    filename, classname, method = key.split('::')
    allowed_jobs = _cases[key]
    expected_file = (_repo/'backend/tests'/filename).resolve()
    approved = []
    log_path = Path(os.environ['SCHEDULER_GUARD_LOG'])

    def record(action, **detail):
        with log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'test':key, 'action':action, **detail})+'\n')

    def guarded_start(instance, *args, **kwargs):
        jobs = instance.get_jobs()
        valid = (
            type(instance) is BackgroundScheduler
            and {job.id for job in jobs} == set(allowed_jobs)
            and not args and not kwargs
            and all(type(store) is MemoryJobStore for store in instance._jobstores.values())
            and all(type(executor) is ThreadPoolExecutor for executor in instance._executors.values())
        )
        for job in jobs:
            function = job.func
            code = getattr(function, '__code__', None)
            valid = valid and (
                code is not None and Path(code.co_filename).resolve() == expected_file
                and function.__qualname__ == classname+'.'+method+'.<locals>.'+allowed_jobs.get(job.id, '')
                and not job.args and not job.kwargs and type(job.trigger) is DateTrigger
            )
        if not valid:
            record('denied', job_ids=sorted(job.id for job in jobs))
            raise AssertionError('Only the exact reviewed fake-job scheduler may start')
        record('allowed', job_ids=sorted(job.id for job in jobs), source=str(expected_file))
        approved.append(instance)
        return _real_start(instance)

    # Narrow the existing deny rule; never restore an unrestricted scheduler start.
    monkeypatch.setattr(BaseScheduler, 'start', guarded_start)
    unapproved = BackgroundScheduler()
    unapproved.add_job(lambda: None, 'date', id='unreviewed_job')
    with pytest.raises(AssertionError, match='exact reviewed'):
        unapproved.start()
    assert not unapproved.running
    yield
    assert len(approved) == 1
    assert not approved[0].running and approved[0]._thread is None
    record('shutdown_verified')
