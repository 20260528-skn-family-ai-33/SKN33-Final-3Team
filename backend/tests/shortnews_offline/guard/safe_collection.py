"""Fail closed before test execution; do not rely on CLI path deselection."""
from pathlib import Path
import json,os,subprocess
import pytest
repo=Path(os.environ['SCHEDULER_TEST_REPO']).resolve()
NATIVE_WORKER_TESTS={'test_stop_handler_waits_for_job_completion', 'test_second_worker_is_rejected_while_first_runs', 'test_force_killed_worker_can_be_restarted', 'test_sigterm_waits_for_running_job_and_releases_lock'}
def prohibited(item):
 path=Path(item.path).resolve().relative_to(repo/'backend/tests').as_posix()
 name=item.originalname or item.name.split('[')[0]
 return path=='test_shortnews_worker.py' and name in NATIVE_WORKER_TESTS
def row(item):
 return {'nodeid':item.nodeid,'path':str(Path(item.path).resolve()),'originalname':item.originalname or item.name}
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config,items):
 excluded=[item for item in items if prohibited(item)]
 retained=[item for item in items if not prohibited(item)]
 assert len(excluded)==4, ('expected exactly 4 S native tests',len(excluded))
 assert not any('MonthlyPipelineTest' in item.nodeid for item in retained)
 assert all(Path(item.path).resolve().is_relative_to(repo/'backend/tests') for item in retained)
 assert not any(prohibited(item) for item in retained),'native test remained'
 config.hook.pytest_deselected(items=excluded)
 items[:]=retained
 record={'native_retained':0,'retained':[row(item) for item in retained],'excluded':[row(item) for item in excluded],
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
