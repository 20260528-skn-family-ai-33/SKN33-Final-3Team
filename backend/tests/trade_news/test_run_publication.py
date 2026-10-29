"""시작·완료의 모든 기록 경계에서 예외/강제 종료와 다음 날짜 복구를 검증합니다."""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from news import run_lifecycle as life
from news.failure_report import write_failure_report
from news.save import Store

DAY='2026-10-21'

class ForcedStop(BaseException):
    pass


def read(path):
    return json.loads(path.read_text())


def state(root):
    return read(root/'current_run.json')


def bundle(root,ref):
    return read(root/'run_records'/ref)


def start(root,day=DAY):
    run=life.new_run(day,['hankyung'],'2026-10-22T03:00:00+09:00')
    life.begin_run(root,run)
    return run


def complete(root,run,count):
    summary={'execution_healthy':True,'sites':{'hankyung':{'status':'available_candidates_processed'}}}
    return life.publish_run(root,run,summary,{'counts':{'eligible_stored':count},'operational':summary})


def setup(root,phase):
    root.mkdir(parents=True)
    store=Store(root)
    first=start(root);assert complete(root,first,1)
    second=life.new_run(DAY,['hankyung'],'2026-10-22T03:05:00+09:00')
    if phase=='publish':life.begin_run(root,second)
    return store,first,second


def act(root,run,phase):
    return complete(root,run,2) if phase=='publish' else life.begin_run(root,run)


def assert_consistent(root):
    confirmed=state(root)
    for ref in confirmed['success'].values():
        record=bundle(root,ref)['current']
        assert record['status']=='completed' and record['execution_healthy'] is True
    current=bundle(root,confirmed['latest']['_global'])['current']
    assert confirmed['history'][current['attempt_id']]==confirmed['latest']['_global']
    if current['status']!='completed':
        for ref in confirmed['success'].values():
            assert bundle(root,ref)['current']['attempt_id']!=current['attempt_id']
    return confirmed,current


@pytest.mark.parametrize('phase',['begin','publish'])
@pytest.mark.parametrize('kind',['exception_before','kill_before','kill_after'])
def test_every_write_boundary_and_next_day_recovery(tmp_path,phase,kind):
    # 정상 호출에서 실제 수행한 기록 지점을 구한 뒤 각 지점을 독립 실행으로 시험합니다.
    trace=[];real=life.atomic_json
    trace_root=tmp_path/'trace';store,_,run=setup(trace_root,phase)
    def record(path,value):
        trace.append(str(path.relative_to(trace_root)))
        return real(path,value)
    with patch.object(life,'atomic_json',record):act(trace_root,run,phase)
    store.close()
    assert 'current_run.json' in trace and len(trace)>=7
    for position in range(1,len(trace)+1):
        root=tmp_path/str(position);store,first,run=setup(root,phase)
        seen=[0]
        def fail(path,value):
            seen[0]+=1
            hit=seen[0]==position
            if hit and kind!='kill_after':
                if kind=='exception_before':raise OSError('synthetic write failure')
                raise ForcedStop()
            real(path,value)
            if hit:raise ForcedStop()
        try:
            with patch.object(life,'atomic_json',fail):act(root,run,phase)
        except OSError as error:
            # crawl.py와 같은 일반 예외 처리. 강제 종료에는 이 처리가 실행되지 않습니다.
            write_failure_report(store,SimpleNamespace(daily_target=date.fromisoformat(DAY)),
                                 phase,error,run['started_at'],run=run)
        except ForcedStop:
            pass
        assert seen[0]>=position
        confirmed,current=assert_consistent(root)
        expected_old=current['status']
        expected_id=current['attempt_id']
        # 다음 날 실행으로도 과거 날짜 상태를 복구합니다. 기사 DB/대기열은 조작하지 않습니다.
        new=start(root,'2026-10-22')
        confirmed,new_current=assert_consistent(root)
        assert new_current['attempt_id']==new['attempt_id']
        old=read(root/'daily_runs'/DAY/'latest.json')
        assert old['attempt_id']==expected_id
        assert old['status']==('interrupted' if expected_old=='running' else expected_old)
        assert not confirmed['pending_scopes'] and not confirmed['pending_history']
        success=read(root/'daily_runs'/DAY/'last_success.json')
        assert success['attempt_id']==bundle(root,confirmed['success'][DAY])['current']['attempt_id']
        assert success['status']=='completed'
        assert store.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==0
        store.close()


def test_failed_commit_never_replaces_previous_success(tmp_path):
    root=tmp_path/'data';store,first,run=setup(root,'publish');real=life.atomic_json
    old_success=(root/'daily_runs'/DAY/'last_success.json').read_bytes()
    def fail_state(path,value):
        if path==root/'current_run.json':raise OSError('commit failed')
        return real(path,value)
    with patch.object(life,'atomic_json',fail_state):
        with pytest.raises(OSError):complete(root,run,2)
    confirmed,current=assert_consistent(root)
    assert current['status']=='running' and current['attempt_id']==run['attempt_id']
    assert (root/'daily_runs'/DAY/'last_success.json').read_bytes()==old_success
    store.close()


def test_output_error_after_commit_is_pending_not_false_failure(tmp_path):
    root=tmp_path/'data';store,first,run=setup(root,'publish');real=life.atomic_json
    def fail_cache(path,value):
        if path==root/'daily_runs'/DAY/'last_success.json':raise OSError('cache failed')
        return real(path,value)
    with patch.object(life,'atomic_json',fail_cache):
        assert complete(root,run,2) is False
    confirmed,current=assert_consistent(root)
    assert current['status']=='completed' and current['attempt_id']==run['attempt_id']
    assert confirmed['pending_scopes']
    # 확정 이후 호출자 오류도 확정 결과를 번복하지 않습니다.
    result=write_failure_report(store,SimpleNamespace(daily_target=date.fromisoformat(DAY)),
        'publish_report',OSError('late output error'),run['started_at'],run=run)
    assert result['status']=='completed' and result['attempt_id']==run['attempt_id']
    assert not state(root)['pending_scopes']
    assert read(root/'daily_runs'/DAY/'last_success.json')['counts']['eligible_stored']==2
    store.close()


def test_partial_start_repairs_old_date_even_when_cache_attempt_differs(tmp_path):
    root=tmp_path/'data';store,first,run=setup(root,'begin');real=life.atomic_json
    latest=root/'daily_runs'/DAY/'latest.json'
    def kill(path,value):
        if path==latest:raise ForcedStop()
        return real(path,value)
    with patch.object(life,'atomic_json',kill):
        with pytest.raises(ForcedStop):life.begin_run(root,run)
    assert read(latest)['attempt_id']==first['attempt_id']
    start(root,'2026-10-22')
    assert read(latest)['attempt_id']==run['attempt_id']
    assert read(latest)['status']=='interrupted'
    assert read(root/'daily_runs'/DAY/'last_success.json')['attempt_id']==first['attempt_id']
    store.close()


def test_cache_cannot_promote_failed_attempt_back_to_success(tmp_path):
    root=tmp_path/'data';store,first,run=setup(root,'publish')
    write_failure_report(store,SimpleNamespace(daily_target=date.fromisoformat(DAY)),
                         'test',ValueError('synthetic failure'),run['started_at'],run=run)
    # 이전 버전의 잘못된 성공 파일 또는 손상된 캐시를 확정 기록으로 승격하지 않습니다.
    bad={**run,'status':'completed','execution_healthy':True,'counts':{'eligible_stored':999}}
    (root/'daily_runs'/DAY/'last_success.json').write_text(json.dumps(bad))
    (root/'daily_runs'/DAY/'latest.json').write_text(json.dumps(bad))
    next_run=start(root)
    assert_consistent(root)
    assert read(root/'daily_runs'/DAY/'last_success.json')['attempt_id']==first['attempt_id']
    assert read(root/'daily_runs'/DAY/'latest.json')['attempt_id']==next_run['attempt_id']
    store.close()
