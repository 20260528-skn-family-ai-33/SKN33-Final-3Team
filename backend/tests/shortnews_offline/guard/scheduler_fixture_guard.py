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
assert (_repo/'backend/tests/test_shortnews_daily.py').is_file(), 'Missing selected shortnews tests'
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
