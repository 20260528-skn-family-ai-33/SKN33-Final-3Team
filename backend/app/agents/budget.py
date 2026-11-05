"""D-85: execution budget transitions, persisted by the caller under the job lock.

The ledger is small MySQL execution metadata, never a duplicate AgentState.
Each transition returns a copy so a failed transaction cannot mutate its caller.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math

from backend.app.agents.state import RUN_TIMEOUT_SECONDS, MAX_WORKER_RECOVERIES
from backend.app.api.common import require

# TODO(C-28): tune from node duration logs; settings override each provisional value.
DEFAULT_SECONDS = {'planner': 10, 'check': 15, 'direct_input': 10, 'sql': 30,
    'llm_sql': 50, 'ml': 30, 'news': 15, 'chart': 5, 'report': 10,
    'compose': 20, 'verify': 5, 'finalize': 10}


def seconds(settings, node):
    value = float(settings.get('API_CHAT_' + node.upper() + '_SECONDS', DEFAULT_SECONDS[node]))
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Node timeout must be finite and positive')
    return value


def new_round(round_id, claim_token):
    return {'round_id': round_id, 'claim_token': claim_token, 'charged_seconds': 0.0,
            'round_received_at': datetime.now(timezone.utc).isoformat(),
            'recovery_count': 0, 'reservation': None, 'settled_call_ids': [],
            'progress': {'done': 0, 'total': 0, 'label': '조건 확인 중'},
            'report_wait': None, 'step_results': {}}


def _owned(ledger, round_id, claim_token):
    require(ledger['round_id'] == round_id and ledger['claim_token'] == claim_token,
            '작업 소유권이 다른 실행기로 이전되었습니다.', 'JOB_CLAIM_LOST', 409)


def reserve(ledger, *, round_id, claim_token, call_id, timeout, finish_reserve=0, node='unknown', enforce=True):
    """enforce=False는 이미 끝난 결과를 마무리하는 호출(finalize, 리포트 완료 확인)만 쓴다. 완성된 답변·리포트를
    앞 호출의 초과 시간 때문에 버리지 않는다(§13.4 마무리 몫). 예약과 정산은 그대로 해서 실제 걸린 시간은 기록한다."""
    _owned(ledger, round_id, claim_token)
    if not all(math.isfinite(x) for x in (timeout, finish_reserve)) or timeout <= 0 or finish_reserve < 0:
        raise ValueError('Invalid budget reservation')
    if call_id in ledger['settled_call_ids']:
        return deepcopy(ledger)
    existing = ledger['reservation']
    if existing:
        require(existing['call_id'] == call_id and existing['seconds'] == timeout
                and existing['node'] == node and existing['claim_token'] == claim_token,
                '호출 예약 내용이 다릅니다.', 'BUDGET_CONFLICT', 409)
        return deepcopy(ledger)
    require(not enforce or ledger['charged_seconds'] + timeout + finish_reserve <= RUN_TIMEOUT_SECONDS,
            '실행 시간이 부족합니다. 질문을 나누어 다시 요청해 주세요.', 'RUN_TIMEOUT', 409)
    result = deepcopy(ledger)
    result['charged_seconds'] += timeout
    result['reservation'] = {'call_id': call_id, 'node': node, 'seconds': timeout,
                            'claim_token': claim_token, 'reserved_at': datetime.now(timezone.utc).isoformat()}
    return result


def settle(ledger, *, round_id, claim_token, call_id, elapsed):
    _owned(ledger, round_id, claim_token)
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError('Elapsed time must be finite and nonnegative')
    if call_id in ledger['settled_call_ids']:
        return deepcopy(ledger)
    call = ledger['reservation']
    require(call is not None and call['call_id'] == call_id and call['claim_token'] == claim_token,
            '호출 예약의 소유권이 다릅니다.', 'JOB_CLAIM_LOST', 409)
    result = deepcopy(ledger)
    # Account for a timeout handler that itself took time; never hide an overrun.
    result['charged_seconds'] += elapsed - call['seconds']
    result['reservation'] = None
    result['settled_call_ids'] = [*result['settled_call_ids'], call_id][-50:]
    return result


def recover(ledger, *, round_id, previous_claim, claim_token, max_recoveries=MAX_WORKER_RECOVERIES):
    _owned(ledger, round_id, previous_claim)
    require(claim_token > previous_claim, '새 실행 소유권이 필요합니다.', 'JOB_CLAIM_LOST', 409)
    require(ledger['recovery_count'] < max_recoveries,
            '작업 복구 횟수를 초과했습니다. 다시 질문해 주세요.', 'RECOVERY_EXHAUSTED', 409)
    result = deepcopy(ledger)
    result['claim_token'] = claim_token
    result['recovery_count'] += 1
    # Unsettled reservations remain charged across downtime and checkpoint loss.
    result['reservation'] = None
    return result


def finishing_seconds(state, settings):
    charts = sum(s.tool == 'build_chart' and s.status in ('PENDING', 'RUNNING')
                 for s in state.plan.steps()) if state.plan else 0
    return charts * seconds(settings, 'chart') + sum(seconds(settings, node)
                for node in ('compose', 'verify', 'finalize'))
