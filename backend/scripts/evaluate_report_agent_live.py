"""Report Agent 실제 LLM 평가(이슈 #82). 수동으로만 실행하며 import·unittest 탐색 때는 호출하지 않는다.

리포트 요청을 이해하는 부분(플래너: 의도·선행 결과 연결·수출입·국가·품목·기간)을 운영 경로 그대로
실제 OpenAI로 실행해 고정 정답과 비교한다. 정답은 모델에 보내지 않는다. 조건 확인·미리보기·승인·접수·완료는
오프라인 회귀 평가(evaluate_report_agent.py)가 맡는다. 팀 SQL 평가(evaluate_sql_live.py)와 같은 규칙을 쓴다:
질문·정답을 먼저 고정(manifest 해시)하고, 사람 검토 전 정답은 PROVISIONAL로 표시하며, 비용 상한을 넘기 전에 멈춘다.

  python -m backend.scripts.evaluate_report_agent_live --freeze --set live
  python -m backend.scripts.evaluate_report_agent_live --run --set live --allow-unreviewed-gold
  python -m backend.scripts.evaluate_report_agent_live --run --set holdout --allow-unreviewed-gold   # 잠금 파일로 한 번만
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from types import SimpleNamespace

from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes, revision_metadata

MODEL = 'gpt-6-luna'  # 평가 실행에서만 플래너 모델을 지정한다.
DATA = ROOT / 'docs/evaluations'
SETS = {
    'live': {'questions': DATA / 'report_agent_live_questions.jsonl', 'manifest': DATA / 'report_agent_live_manifest.json',
             'review': DATA / 'report_agent_live_gold_review.md', 'result': ROOT / 'tmp' / f'report_agent_live_{MODEL}.json',
             'lock': None, 'role': 'development'},
    'holdout': {'questions': DATA / 'report_agent_holdout_questions.jsonl', 'manifest': DATA / 'report_agent_holdout_manifest.json',
                'review': DATA / 'report_agent_holdout_gold_review.md', 'result': DATA / 'report_agent_holdout_result.json',
                'lock': DATA / 'report_agent_holdout_run_lock.json', 'role': 'holdout'},
}
PRICE = {'input_per_million_usd': 0.1, 'cached_input_per_million_usd': 0.01,
         'cache_write_per_million_usd': 0.125, 'output_per_million_usd': 0.5,
         'source': 'https://developers.openai.com/api/docs/models/gpt-6-luna', 'checked_on': '2026-11-09'}
FIELDS = ('intent', 'report_present', 'sources', 'trade_flow', 'countries', 'products', 'period')
HARNESS = ['backend/scripts/evaluate_report_agent_live.py', 'backend/app/agents/planner.py', 'backend/app/agents/runtime.py',
           'backend/app/agents/state.py', 'backend/app/agents/llm.py']


class EvaluationStopped(RuntimeError):
    pass


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def load_questions(path):
    cases = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not 20 <= len(cases) <= 50 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('질문은 20~50개이고 ID가 겹치지 않아야 합니다')
    return cases


# ── 채점 ─────────────────────────────────────────────────────
def plan_view(plan):
    """플래너 결과를 채점에 쓰는 모양으로. 선행 결과 연결은 1부터 시작하는 하위 질문 번호로 바꾼다."""
    tasks = []
    for task in plan.tasks:
        slots = task.slots
        tasks.append({'intent': task.intent, 'question': task.question,
                      'sources': [int(b.source_task_id.lstrip('t')) for b in task.input_bindings],
                      'trade_flow': slots.trade_flow,
                      'countries': None if slots.countries is None else [c.name for c in slots.countries],
                      'products': slots.products,
                      'recent_months': slots.recent_months,
                      'period': None if slots.period is None else {'start': slots.period.start.strftime('%Y-%m'),
                                                                    'end': slots.period.end.strftime('%Y-%m')}})
    return tasks


# INHERIT: 질문에 없는 조건은 이전 대화 조건을 이어받는다. 비워 두거나(프롬프트 지시) 이전과 같은 값을 다시 적으면
# 확정 조건이 같아 결과가 같다. 같은 값은 정답의 inherit_equivalent에 적는다(채점 2판, 2026-11-09).
def _countries(expected, actual, same=None):
    if expected == 'INHERIT':
        return actual is None or (same is not None and actual is not None and set(actual) == set(same))
    return actual is not None and {a.strip() for a in actual} == {e.strip() for e in expected}


def _products(expected, actual, same=None):
    if expected == 'INHERIT':
        return actual is None or (same is not None and _products(same, actual))
    if actual is None or len(actual) != len(expected):
        return False
    squeeze = lambda text: ''.join(text.split()).casefold()
    return all(any(squeeze(e) in squeeze(a) for a in actual) for e in expected)


def _period(expected, task, same=None):
    if expected == 'INHERIT':
        empty = task['period'] is None and task['recent_months'] is None
        return empty or (same is not None and _period(same, task))
    if 'recent_months' in expected:
        return task['recent_months'] == expected['recent_months']
    return task['period'] == {'start': expected['start'], 'end': expected['end']}


def score_case(case, tasks):
    """정답에 적힌 항목만 채점한다(null은 채점하지 않음). correct는 적용되는 항목이 모두 맞을 때만 True."""
    expected = case['expected']
    intents = [t['intent'] for t in tasks]
    marks = {'intent': intents in expected['intents']}
    report = expected['report']
    reports = [i for i, t in enumerate(tasks) if t['intent'] == 'REPORT']
    marks['report_present'] = bool(reports) == (report is not None)
    if report is not None and reports:
        actual = tasks[reports[0]]
        marks['sources'] = actual['sources'] == report['sources']
        # 조건은 결과 기반 리포트면 선행 하위 질문, 아니면 리포트 하위 질문에 있다
        holder = tasks[report['sources'][0] - 1] if report['sources'] and len(tasks) >= report['sources'][0] else actual
        flow = actual['trade_flow'] or holder['trade_flow'] or 'EXPORT'   # 없으면 시스템 기본값 수출
        marks['trade_flow'] = flow == report['trade_flow']
        same = report.get('inherit_equivalent') or {}
        if report['countries'] is not None:
            marks['countries'] = _countries(report['countries'], holder['countries'], same.get('countries'))
        if report['products'] is not None:
            marks['products'] = _products(report['products'], holder['products'], same.get('products'))
        if report['period'] is not None:
            marks['period'] = _period(report['period'], holder, same.get('period'))
    elif report is not None:
        for key in ('sources', 'trade_flow'):
            marks[key] = False
    return {'correct': all(marks.values()), 'marks': marks}


def summarize(records, cases):
    def rate(selected, test):
        passed = sum(1 for r in selected if test(r))
        return {'passed': passed, 'total': len(selected), 'rate_pct': round(100 * passed / len(selected), 2) if selected else None}
    calls = [c for r in records for c in r.get('calls', [])]
    report_expected = [r for r in records if r['expects_report']]
    report_absent = [r for r in records if not r['expects_report']]
    return {
        'evaluated': len(records), 'planned': len(cases),
        'overall_accuracy': rate(records, lambda r: r['correct']),
        'by_field': {f: rate([r for r in records if f in r['marks']], lambda r, f=f: r['marks'][f]) for f in FIELDS},
        'report_detection': {'recall': rate(report_expected, lambda r: r['marks'].get('report_present')),
                             'false_positive_avoided': rate(report_absent, lambda r: r['marks'].get('report_present'))},
        'by_category': {c: rate([r for r in records if r['category'] == c], lambda r: r['correct'])
                        for c in dict.fromkeys(case['category'] for case in cases)},
        'planner_failures': sum(1 for r in records if r.get('planner_failed')),
        'latency_seconds_mean': statistics.mean(r['latency_seconds'] for r in records) if records else None,
        'latency_seconds_median': statistics.median(r['latency_seconds'] for r in records) if records else None,
        'input_tokens': sum((c.get('usage') or {}).get('input_tokens', 0) for c in calls),
        'output_tokens': sum((c.get('usage') or {}).get('output_tokens', 0) for c in calls),
        'estimated_cost_usd': round(sum(c.get('estimated_cost_usd', 0) for c in calls), 6),
        'calls_without_usage': sum(1 for c in calls if not c.get('usage')),
    }


# ── 실제 호출 ────────────────────────────────────────────────
class Recorder:
    """OpenAI 호출마다 토큰·비용·지연을 남기고, 비용 상한을 넘기 전에 멈춘다(팀 SQL 평가와 같은 방식)."""
    def __init__(self, max_cost):
        self.calls, self.max_cost = [], max_cost

    def factory(self, **kwargs):
        from openai import OpenAI, AuthenticationError, PermissionDeniedError, RateLimitError, APIConnectionError
        client = OpenAI(**kwargs, base_url='https://api.openai.com/v1')
        recorder = self

        class Context:
            def __enter__(self):
                return SimpleNamespace(responses=SimpleNamespace(parse=self.parse))

            def __exit__(self, *args):
                client.close()

            def parse(self, **request):
                bound = len((request['input'] + request['instructions']).encode()) + 12000
                reserve = (bound * PRICE['cache_write_per_million_usd'] + request['max_output_tokens'] * PRICE['output_per_million_usd']) / 1_000_000
                spent = sum(c.get('estimated_cost_usd', c.get('reserved_cost_usd', 0)) for c in recorder.calls)
                if spent + reserve > recorder.max_cost:
                    raise EvaluationStopped('설정한 평가 비용 상한에 도달했습니다')
                entry = {'requested_model': request['model'], 'reserved_cost_usd': reserve}
                recorder.calls.append(entry)
                started = time.perf_counter()
                try:
                    response = client.responses.parse(**request)
                except (AuthenticationError, PermissionDeniedError, RateLimitError, APIConnectionError) as exc:
                    entry.update(error_type=type(exc).__name__, latency_seconds=time.perf_counter() - started)
                    raise EvaluationStopped(type(exc).__name__) from None
                except Exception as exc:
                    entry.update(error_type=type(exc).__name__, latency_seconds=time.perf_counter() - started)
                    from pydantic import ValidationError
                    if isinstance(exc, ValidationError):   # 어느 칸이 계획 스키마에 맞지 않았는지(값은 남기지 않음)
                        entry['validation_errors'] = [{'loc': e['loc'], 'type': e['type']}
                                                      for e in exc.errors(include_input=False, include_url=False)]
                    raise
                usage = response.usage.model_dump() if response.usage else None
                entry.update(actual_model=response.model, usage=usage, response_status=response.status,
                             latency_seconds=time.perf_counter() - started)
                if usage:
                    details = usage.get('input_tokens_details') or {}
                    cached = details.get('cached_tokens', 0)
                    writes = details.get('cache_write_tokens', 0)
                    entry['estimated_cost_usd'] = ((usage['input_tokens'] - cached - writes) * PRICE['input_per_million_usd'] +
                                                   writes * PRICE['cache_write_per_million_usd'] + cached * PRICE['cached_input_per_million_usd'] +
                                                   usage['output_tokens'] * PRICE['output_per_million_usd']) / 1_000_000
                return response
        return Context()


class DirectExecution:
    """운영 플래너 경로(Services.planner → structured)를 그대로 쓰되, 실행 예산 장부(MySQL)는 평가 범위 밖이다."""
    def call(self, node, seconds, work, **_):
        return work(seconds)


def run_planner(question, previous_conditions, client, settings):
    from backend.app.agents.runtime import Services
    from backend.app.agents.state import AgentState
    context = {'session_id': '1', **({'conditions': previous_conditions} if previous_conditions else {})}
    state = AgentState.model_validate({'input': {'job_id': '1', 'session_id': '1', 'user_id': 1, 'question': question,
                                                 'context': context, 'received_at': datetime(2026, 11, 9, 9)}})
    Services(DirectExecution(), settings, client).planner(state)
    return state


# ── 고정·실행 ────────────────────────────────────────────────
def model_input_hashes():
    """모델 출력에 영향을 주는 플래너 프롬프트와 출력 스키마의 해시. 고정할 때 기록하고 실행 전에 대조한다."""
    from backend.app.agents.planner import PLANNER_PROMPT
    from backend.app.agents.state import PlannerOutput
    return {'prompt_sha256': digest_bytes(PLANNER_PROMPT.encode()),
            'schema_sha256': digest_bytes(json.dumps(PlannerOutput.model_json_schema(), sort_keys=True).encode())}


def changed_inputs(manifest):
    """고정 기록과 다른 프롬프트·스키마 이름 목록."""
    return [k for k, v in model_input_hashes().items() if manifest.get(k) != v]


def freeze(target):
    from backend.app.agents.planner import PLANNER_PROMPT
    from backend.app.agents.state import PlannerOutput
    cases = load_questions(target['questions'])
    manifest = {'schema_version': 1, 'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
                'questions_sha256': digest_bytes(target['questions'].read_bytes()), 'questions': len(cases),
                'categories': {c: sum(1 for x in cases if x['category'] == c) for c in dict.fromkeys(x['category'] for x in cases)},
                **model_input_hashes(),
                'code_revision': revision_metadata(HARNESS), 'gold_author': 'AI 초안(Claude)',
                'human_review': {'status': 'pending', 'reviewer': None, 'approved_questions_sha256': None}}
    write_json(target['manifest'], manifest)
    lines = ['# Report Agent 평가 정답 검토표', '', f"질문 {len(cases)}개 · 고정 {manifest['frozen_at_utc']} · 사람 검토 대기", '',
             '`INHERIT`는 질문에 없는 조건을 비워 이전 대화 조건을 이어받아야 한다는 뜻이다. null은 채점하지 않는다.', '',
             '| ID | 유형 | 질문 | 기대 의도 | 연결 | 수출입 | 국가 | 품목 | 기간 | 검토 |', '|---|---|---|---|---|---|---|---|---|---|']
    for c in cases:
        r = c['expected']['report'] or {}
        intents = ' 또는 '.join('→'.join(seq) for seq in c['expected']['intents'])
        lines.append(f"| {c['id']} | {c['category']} | {c['question']} | {intents} | {r.get('sources', '—')} | {r.get('trade_flow', '—')} | "
                     f"{r.get('countries', '—')} | {r.get('products', '—')} | {r.get('period', '—')} | ☐ |")
    target['review'].write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"{len(cases)}개 질문을 고정했습니다(사람 검토 대기): {target['manifest'].name}")


def run(target, args):
    from backend.app.agents.llm import StructuredClient
    from backend.app.api.config import Settings
    from backend.app.agents.planner import PLANNER_PROMPT
    cases = load_questions(target['questions'])
    manifest = json.loads(target['manifest'].read_text(encoding='utf-8'))
    questions_hash = digest_bytes(target['questions'].read_bytes())
    if questions_hash != manifest['questions_sha256']:
        raise ValueError('고정한 뒤 질문·정답이 바뀌었습니다. 새 세트로 다시 고정하세요')
    # holdout은 고정한 프롬프트·스키마로만 한 번 실행한다(API 키 로딩·잠금 파일 생성 전에 거부).
    # 개발 세트는 플래너를 고친 뒤 같은 질문으로 다시 돌려 비교하는 용도라 실행하되 결과에 바뀐 항목을 남긴다.
    changed = changed_inputs(manifest)
    if changed and target['role'] == 'holdout':
        raise ValueError(f"고정한 뒤 {', '.join(changed)}가 바뀌었습니다. holdout은 고정한 프롬프트·스키마로만 실행합니다")
    review = manifest['human_review']
    approved = review['status'] == 'approved' and bool(review.get('reviewer')) and review.get('approved_questions_sha256') == questions_hash
    if not approved and not args.allow_unreviewed_gold:
        raise ValueError('사람이 검토한 정답이 필요합니다. 검토 전 실행은 --allow-unreviewed-gold')
    if target['lock'] and target['lock'].exists():
        raise ValueError('holdout은 한 번만 실행합니다. 잠금 파일이 있어 다시 실행하지 않습니다')
    # 앱 설정에서는 API 키만 읽는다. DB·모델 주소는 받지 않는다
    key = next((v for k, v in Settings().values.items() if k.lower() == 'openai_api_key' and v), None)
    if not key:
        raise ValueError('OPENAI_API_KEY가 없습니다')
    settings = Settings({'OPENAI_API_KEY': key, 'API_PLANNER_MODEL': MODEL})
    recorder = Recorder(args.max_cost_usd)
    client = StructuredClient(settings, client_factory=recorder.factory)
    if target['lock']:
        write_json(target['lock'], {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'questions_sha256': questions_hash,
                                    'code_revision': revision_metadata(HARNESS), 'note': '이 파일이 있으면 holdout을 다시 실행하지 않는다'})
    report = {'schema_version': 1, 'kind': 'report_agent_planner_real_llm', 'set': args.set, 'dataset_role': target['role'],
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'gold_status': 'human_approved' if approved else 'PROVISIONAL_UNREVIEWED_GOLD',
              'model_requested': MODEL, 'prompt_sha256': digest_bytes(PLANNER_PROMPT.encode()),
              'changed_since_freeze': changed,
              'questions_sha256': questions_hash, 'manifest_frozen_at_utc': manifest['frozen_at_utc'],
              'harness': revision_metadata(HARNESS), 'evidence_sha256': {p: digest_bytes((ROOT / p).read_bytes()) for p in HARNESS},
              'hash_normalization': 'CRLF to LF before SHA256', 'pricing': PRICE, 'cost_cap_usd': args.max_cost_usd,
              'scoring': '정답에 적힌 항목(의도 순서·리포트 여부·선행 연결·수출입·국가·품목·기간)이 모두 맞아야 정답. null은 채점하지 않음.',
              'limitations': ['플래너(리포트 요청 이해)만 실제 호출한다. 조건 확정(HS·국가 사전)·미리보기·승인·접수·완료는 오프라인 회귀 평가가 맡는다.',
                              '정답은 AI가 작성한 초안이며 사람 검토 전이다(PROVISIONAL_UNREVIEWED_GOLD).',
                              '고정 질문 세트의 결과이며 실제 사용자 질문 분포나 서비스 전체 정확도가 아니다.',
                              '비용은 사용량 기반 추정이며 청구 내역이 아니다.'],
              'cases': []}
    for case in cases:
        offset = len(recorder.calls)
        started = time.perf_counter()
        record = {k: case[k] for k in ('id', 'category', 'question')}
        record['expects_report'] = case['expected']['report'] is not None
        try:
            state = run_planner(case['question'], case.get('previous_conditions'), client, settings)
            tasks = plan_view(state.plan)
            record['planner_failed'] = any('질문을 해석하지 못했습니다' in d for t in state.plan.tasks for d in t.applied_defaults)
            record.update(actual=tasks, warnings=list(state.warnings), **score_case(case, tasks))
        except EvaluationStopped as exc:
            record.update(correct=False, marks={'intent': False}, aborted=str(exc))
            report['aborted'] = str(exc)
        except Exception as exc:
            record.update(correct=False, marks={'intent': False}, error_type=type(exc).__name__)
        record.update(calls=recorder.calls[offset:], latency_seconds=time.perf_counter() - started)
        report['cases'].append(record)
        report['summary'] = summarize(report['cases'], cases)
        write_json(args.output or target['result'], report)
        print(f"{case['id']}: correct={record['correct']}", flush=True)
        if report.get('aborted'):
            break
    report['questions_unchanged'] = digest_bytes(target['questions'].read_bytes()) == questions_hash
    report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(args.output or target['result'], report)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    return 2 if report.get('aborted') or not report['questions_unchanged'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--set', choices=sorted(SETS), required=True)
    parser.add_argument('--freeze', action='store_true', help='질문·정답을 고정하고 검토표를 만든다(모델 호출 없음)')
    parser.add_argument('--run', action='store_true', help='실제 OpenAI로 실행한다')
    parser.add_argument('--allow-unreviewed-gold', action='store_true')
    parser.add_argument('--max-cost-usd', type=float, default=1.0)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.freeze == args.run:
        parser.error('--freeze 또는 --run 중 하나만 고르세요')
    if not 0 < args.max_cost_usd <= 5:
        parser.error('비용 상한은 0보다 크고 5달러 이하여야 합니다')
    target = SETS[args.set]
    if args.freeze:
        freeze(target)
        return 0
    return run(target, args)


if __name__ == '__main__':
    raise SystemExit(main())
