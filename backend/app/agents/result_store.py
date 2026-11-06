"""D-96/97: input identity and atomic durable result/step pointer publication."""
import hashlib
import json
from datetime import date, datetime

from backend.app.agents.state import ToolResult
from backend.app.api import results
from backend.app.api.common import ApiError, require

FINGERPRINT_VERSION = 1


def fingerprint(state, step, *, tool_version, versions, source_results):
    # Missing execution versions are not a cache hit, even if every other field agrees.
    if not versions or any(value is None or value == '' for value in versions.values()):
        return None
    task = next(t for t in state.plan.tasks if step in t.steps)
    payload = {'fingerprint_version': FINGERPRINT_VERSION, 'user_id': state.input.user_id,
        'job_id': state.input.job_id, 'task_id': task.task_id, 'step_id': step.step_id,
        'intent': task.intent, 'tool': step.tool, 'tool_version': tool_version,
        'conditions': task.conditions.model_dump(mode='json') if task.conditions else None,
        'custom_query': task.custom_query, 'args': step.args, 'resolved': task.resolved,
        'input_bindings': [binding.model_dump(mode='json') for binding in task.input_bindings],
        'report_request': task.report_request.model_dump(mode='json') if task.report_request else None,
        'source_results': source_results, 'versions': versions}
    def encode(value):
        if isinstance(value, (date, datetime)): return value.isoformat()
        raise TypeError('Fingerprint input is not JSON-compatible')
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False, default=encode)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


class ResultStore:
    def __init__(self, execution):
        self.execution = execution

    def check_owner(self, state):
        job = self.execution.job
        require(str(state.input.job_id) == str(job['job_id'])
                and str(state.input.session_id) == str(job['target_key'])
                and state.input.user_id == job['subject_user_id'],
                '결과 저장 작업의 소유권이 다릅니다.', 'JOB_CLAIM_LOST', 409)

    def reuse(self, state, step, expected):
        self.check_owner(state)
        if not expected: return None
        def read(store, job):
            entry = job['agent']['step_results'].get(step.step_id)
            if (not isinstance(entry, dict) or entry.get('fingerprint_version') != FINGERPRINT_VERSION
                    or entry.get('input_fingerprint') != expected or not entry.get('result_id')):
                return None
            try:
                snapshot = results.read_snapshot(store, state.input.user_id, entry['result_id'], 'RESULT')
            except ApiError as exc:
                if exc.code == 'RESULT_EXPIRED': return None
                raise  # Ownership/not-found is not a silent cache miss.
            meta = snapshot.get('meta', {})
            if (meta.get('fingerprint_version') != FINGERPRINT_VERSION
                    or meta.get('input_fingerprint') != expected or not meta.get('tool_result')):
                return None
            tool = ToolResult.model_validate({**meta['tool_result'], 'result_id': entry['result_id']})
            return state.result_for_storage(step.step_id, tool)
        return self.execution.transaction(read)

    def save(self, state, step, raw_result, *, rows, meta, conditions, input_fingerprint,
             source_results, versions, columns=None, sql=None):
        self.check_owner(state)
        propagated = state.result_for_storage(step.step_id, raw_result)
        if propagated.status != 'OK':
            raise ValueError('Only successful analysis results can create a result table')
        def write(store, job):
            metadata = {**meta, 'task_id': next(t.task_id for t in state.plan.tasks if step in t.steps),
                'fingerprint_version': FINGERPRINT_VERSION,
                'input_fingerprint': input_fingerprint, 'source_results': source_results,
                'versions': versions, 'completeness': propagated.completeness,
                'limitations': propagated.limitations, 'tool_result': propagated.model_dump(mode='json')}
            table = results.result_table(store, state.input.user_id, rows, metadata,
                                         conditions, sql=sql, columns=columns)
            job['agent']['step_results'][step.step_id] = {
                'result_id': table['result_id'], 'input_fingerprint': input_fingerprint,
                'fingerprint_version': FINGERPRINT_VERSION}
            return ToolResult.model_validate({**propagated.model_dump(), 'result_id': table['result_id']})
        # Claim checked under the same session lock and transaction as result_table.
        return self.execution.transaction(write)
