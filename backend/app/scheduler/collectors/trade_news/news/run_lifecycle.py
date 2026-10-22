"""실행 확정은 단일 상태 파일로 처리하고, 표시용 보고서는 확정 기록에서 복구합니다."""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from news.save import KST

GLOBAL = '_global'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_record(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def new_run(target, sites, started_at):
    attempt = uuid4().hex
    return {'run_id':os.environ.get('TRADE_NEWS_RUN_ID') or attempt,
            'attempt_id':attempt, 'started_at':started_at, 'generated_at':started_at,
            'target_date':target, 'requested_sites':list(sites), 'timezone':'Asia/Seoul',
            'status':'running', 'execution_healthy':None, 'counts':None,
            'counts_available':False, 'mysql_loaded':False,
            'notes':['실행 중 또는 종료 결과 미확정입니다. 이전 성공 건수를 이번 결과로 사용하지 않습니다.']}


def is_success(record):
    if not record or record.get('status') in ('running','interrupted','execution_error'):
        return False
    operational = record.get('operational')
    return (record.get('execution_healthy') is True
            or isinstance(operational, dict) and operational.get('execution_healthy') is True)


def _scope_paths(root, scope):
    if scope == GLOBAL:
        return root/'run_summary.json', root/'last_success_summary.json'
    day = date.fromisoformat(scope).isoformat()
    directory = root/'daily_runs'/day
    return directory/'latest.json', directory/'last_success.json'


def _bundle(root, ref):
    if not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{32}\.json', ref):
        raise ValueError('실행 결과 참조 형식 오류')
    value = read_record(root/'run_records'/ref)
    if value is None:
        raise ValueError('확정된 실행 결과를 읽을 수 없습니다')
    return value


def _write_bundle(root, current, summary=None, daily=None):
    # 확정 전 결과는 새 파일에만 씁니다. 과거 정상 결과를 덮어쓰지 않습니다.
    ref = uuid4().hex + '.json'
    atomic_json(root/'run_records'/ref,
                {'current':current, 'summary':summary or current, 'daily':daily})
    return ref


def _mark_pending(state, scopes=(), attempts=()):
    state['pending_scopes'] = sorted(set(state['pending_scopes']) | set(scopes))
    state['pending_history'] = sorted(set(state['pending_history']) | set(attempts))


def _set_current(state, ref, current):
    state['latest'][GLOBAL] = ref
    scopes = [GLOBAL]
    if current.get('target_date'):
        state['latest'][current['target_date']] = ref
        scopes.append(current['target_date'])
    state['history'][current['attempt_id']] = ref
    _mark_pending(state, scopes, [current['attempt_id']])


def _legacy(root, state, scope):
    if scope in state['latest'] or scope in state['success']:
        return  # 확정 기록이 있는 범위는 표시용 파일에서 역으로 상태를 가져오지 않습니다.
    latest_path, success_path = _scope_paths(root, scope)
    latest = read_record(latest_path)
    def save(value, current=None):
        current = dict(current or value)
        current.setdefault('attempt_id', uuid4().hex)
        current.setdefault('run_id', current['attempt_id'])
        current.setdefault('target_date', None if scope == GLOBAL else scope)
        current.setdefault('status', 'completed' if is_success(value) else 'execution_error')
        return _write_bundle(root,current,value if scope == GLOBAL else current,
                             value if scope != GLOBAL else None)
    if scope not in state['latest']:
        current = read_record(root/'current_run.json') if scope == GLOBAL else latest
        if current or latest:
            ref = save(latest or current, current)
            state['latest'][scope] = ref
            record = _bundle(root,ref)['current']
            state['history'].setdefault(record['attempt_id'], ref)
            _mark_pending(state,[scope],[record['attempt_id']])
    if scope not in state['success']:
        previous = read_record(success_path)
        candidate = previous if is_success(previous) else latest if is_success(latest) else None
        if candidate:
            # 이전 버전에서 실패한 시도를 성공 파일만 보고 다시 성공으로 인정하지 않습니다.
            current = read_record(root/'current_run.json')
            if (current and candidate.get('attempt_id') and candidate.get('attempt_id') == current.get('attempt_id')
                    and current.get('status') in ('running','interrupted','execution_error')):
                return
            state['success'][scope] = save(candidate)
            _mark_pending(state,[scope])


def _load_state(root, target=None):
    path = root/'current_run.json'
    state = read_record(path)
    if path.exists() and (state is None or ('version' in state and state['version'] != 1)):
        raise ValueError('실행 확정 상태 파일 오류: 이전 상태를 덮어쓰지 않습니다')
    if state is None or 'version' not in state:
        state = {'version':1,'latest':{},'success':{},'history':{},
                 'pending_scopes':[],'pending_history':[]}
        _legacy(root,state,GLOBAL)
        current = read_record(root/'current_run.json')
        if current and current.get('target_date'):
            _legacy(root,state,current['target_date'])
    if target:
        _legacy(root,state,target)
    return state


def _commit(root, state):
    # 현재/날짜별 최신/마지막 성공/이력을 한 파일 교체로 함께 확정합니다.
    current = _bundle(root,state['latest'][GLOBAL])['current']
    atomic_json(root/'current_run.json', {**state, **current, 'version':1})


def _project(root, state):
    """표시용 파일은 캐시입니다. 확정 상태를 바꾸지 않고 다시 만들 수 있습니다."""
    for scope in state['pending_scopes']:
        latest_path, success_path = _scope_paths(root,scope)
        key = 'summary' if scope == GLOBAL else 'daily'
        if scope in state['latest']:
            bundle = _bundle(root,state['latest'][scope])
            atomic_json(latest_path,bundle[key] or bundle['current'])
        if scope in state['success']:
            success = _bundle(root,state['success'][scope])
            atomic_json(success_path,success[key] or success['current'])
        elif success_path.exists():
            atomic_json(success_path,{'status':'no_confirmed_success','execution_healthy':None,
                                      'counts':None,'counts_available':False})
    for attempt in state['pending_history']:
        bundle = _bundle(root,state['history'][attempt])
        current = bundle['current']
        prefix = 'interrupted_' if current['status'] == 'interrupted' else 'result_'
        # 레거시 식별자가 파일 경로에 섞이지 않게 파일 이름은 결과 참조에서 만듭니다.
        name = state['history'][attempt]
        atomic_json(root/'run_history'/(prefix+name),current)
        if current.get('target_date'):
            directory = root/'daily_runs'/date.fromisoformat(current['target_date']).isoformat()
            atomic_json(directory/('attempt_'+name),bundle['daily'] or current)


def repair_reports(root, state=None):
    """확정 후 파일 출력 실패는 수집 성공을 실패로 번복하지 않고 복구 대기로 남깁니다."""
    state = state if state is not None else _load_state(root)
    try:
        _project(root,state)
        acknowledged = {**state,'pending_scopes':[],'pending_history':[]}
        _commit(root,acknowledged)
        return True
    except (OSError, ValueError, TypeError) as error:
        print(f'실행 결과는 current_run.json에 확정됨. 표시용 보고서 복구 대기: {error}',file=sys.stderr)
        return False


def begin_run(root, run):
    """반드시 잠금 획득 후 호출합니다. 과거 날짜는 상태만 복구하고 재수집하지 않습니다."""
    state = _load_state(root,run['target_date'])
    if GLOBAL in state['latest']:
        previous = _bundle(root,state['latest'][GLOBAL])['current']
        if previous.get('status') == 'running':
            interrupted = {**previous,'status':'interrupted','execution_healthy':False,
                           'detected_at':run['started_at'],'counts':None,'counts_available':False,
                           'notes':['종료 결과 없이 남은 실행을 새 실행의 잠금 획득으로 중단 확인했습니다.']}
            ref = _write_bundle(root,interrupted,daily=interrupted)
            _set_current(state,ref,interrupted)
    ref = _write_bundle(root,run,daily=run if run['target_date'] else None)
    _set_current(state,ref,run)
    _commit(root,state)
    if not repair_reports(root,state):
        raise OSError('시작 상태 보고서 출력 실패: 수집을 시작하지 않습니다')


def publish_run(root, run, summary, report=None):
    state = _load_state(root,run['target_date'])
    current = _bundle(root,state['latest'][GLOBAL])['current']
    if current['attempt_id'] != run['attempt_id'] or current['status'] != 'running':
        raise ValueError('현재 실행과 완료 대상이 일치하지 않습니다')
    metadata = {key:run[key] for key in ('run_id','attempt_id','started_at','target_date')}
    metadata.update(status='completed' if summary['execution_healthy'] else 'execution_error',
                    execution_healthy=summary['execution_healthy'],finished_at=datetime.now(KST).isoformat())
    summary = {**summary,**metadata}
    report = {**report,**metadata,'counts_available':True} if report is not None else None
    current = {**run,**metadata,'notes':[]}
    ref = _write_bundle(root,current,summary,report)
    _set_current(state,ref,current)
    if summary['execution_healthy']:
        state['success'][GLOBAL] = ref
        if run['target_date']:
            state['success'][run['target_date']] = ref
    _commit(root,state)
    # 확정 뒤 출력 오류에는 예외를 던지지 않습니다. 호출자는 별도의 출력 실패 종료 코드를 냅니다.
    return repair_reports(root,state)


def record_failure(root, run, report):
    state = _load_state(root,run['target_date'])
    if GLOBAL in state['latest']:
        previous = _bundle(root,state['latest'][GLOBAL])
        current = previous['current']
        if current['attempt_id'] == run['attempt_id'] and current['status'] == 'completed':
            # 완료 확정 후 출력/호출자 오류는 확정된 수집 결과를 실패로 번복하지 않습니다.
            repair_reports(root,state)
            return previous['daily'] or previous['summary']
    ref = _write_bundle(root,report,report,report if run['target_date'] else None)
    _set_current(state,ref,report)
    _commit(root,state)
    repair_reports(root,state)
    return report
