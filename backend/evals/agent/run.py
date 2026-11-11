"""이슈 86 에이전트 노드 평가: planner · conditions · compose_text · context.

실제 LLM(OpenAI)과 설정된 DB(조회만, 매 질문 rollback)에 연결한다. 단위 테스트(backend/tests)에서는 부르지 않는다.
방식과 기준은 docs/AGENT_EVAL.md.

    python -m backend.evals.agent.run planner --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna --repeat 3
    python -m backend.evals.agent.run conditions --split dev                  # LLM 없음(정답 슬롯 입력)
    python -m backend.evals.agent.run conditions --split dev --chain --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna
    python -m backend.evals.agent.run compose_text --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna --repeat 3
    python -m backend.evals.agent.run context --split dev --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna

모델을 여러 개 주면 차례로 돌리고, 결과를 한 쌍의 파일로 남긴다:
output/agent_eval/<영역>_<split>_compare_<시각>.json(모델별 요약·문항 결과)과 .md(지표·문항별 통과 비교 표).
"""
import argparse
import json
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

CASES = Path(__file__).with_name('cases')
OUT = Path(__file__).resolve().parents[3] / 'output' / 'agent_eval'
AREAS = ('planner', 'conditions', 'compose_text', 'context')
RECEIVED = '2026-11-09T09:00:00+09:00'
STRICT_SLOTS = ('products', 'countries', 'period', 'recent_months', 'trade_flow', 'answer_format',
                'ranking_group', 'ranking_limit', 'share_basis', 'comparison_requested', 'compare_period', 'article_period')
# 실행이 그 intent에서만 쓰는 칸. 다른 intent에서는 값이 있어도 답이 같아 정답에 적힌 경우에만 채점한다
SLOT_INTENTS = {'ranking_group': {'RANKING', 'MARKET_SHARE'}, 'share_basis': {'MARKET_SHARE'},
                'ranking_limit': {'RANKING', 'PROMISING_MARKET', 'MARKET_TYPE'}}
NAMES = {'US': '미국', 'CN': '중국', 'VN': '베트남', 'JP': '일본', 'IN': '인도', 'DE': '독일', 'ID': '인도네시아'}


# ---------- 데이터 ----------

def load(area, split='all'):
    rows = [json.loads(line) for line in (CASES / f'{area}.jsonl').read_text(encoding='utf-8').splitlines()
            if line.strip()]
    return [r for r in rows if split == 'all' or r['split'] == split]


def agent_state(question, context=None, *, first_question=False, interests=None):
    from backend.app.agents.state import AgentState
    context = {'session_id': 'eval', **(context or {})}
    context['recent'] = [{'answered_at': RECEIVED, **turn} for turn in context.get('recent', [])]
    return AgentState.model_validate({'input': {
        'job_id': 'eval', 'session_id': 'eval', 'user_id': 0, 'question': question, 'context': context,
        'interests': interests or {}, 'first_question': first_question, 'received_at': RECEIVED}})


# ---------- 실행 ----------

class Runner:
    def __init__(self, model=None, timeout=None, settings=None):
        from backend.app.api.config import Settings
        from backend.app.agents.llm import StructuredClient
        self.settings = settings or Settings()
        self.model, self.timeout = model, timeout
        self.client = StructuredClient(self.settings)

    def parse(self, node, prompt, payload, schema, tokens, validate=lambda value: value):
        """runtime.Services.structured와 같이 최대 2번 시도한다. (값, 시도 수, 오류 종류)를 돌려준다."""
        key = 'COMPOSE' if node == 'compose' else 'INTENT'
        timeout = self.timeout or float(self.settings.get(f'API_{key}_TIMEOUT_SECONDS', 20 if key == 'COMPOSE' else 10))
        error = None
        for attempt in (1, 2):
            try:
                output = self.client.parse(model=self.model, timeout=timeout, prompt=prompt, payload=payload,
                                           schema=schema, max_output_tokens=tokens)
                return validate(output), attempt, None
            except Exception as exc:   # 평가 기록용. 실패 종류만 남기고 다음 시도로 간다
                error = type(exc).__name__
                if node == 'planner' and isinstance(exc, ValueError):
                    from backend.app.agents.planner import PLANNER_RETRY_HINT
                    prompt += PLANNER_RETRY_HINT
        return None, 2, error

    def plan(self, state):
        """runtime.Services.planner와 같은 순서: 정규식 → LLM → 코드 안전망 → build_plan."""
        from backend.app.agents.planner import (PLANNER_PROMPT, build_plan, explicit_cause_followup, explicit_country_ranking, explicit_out_of_scope,
                                                explicit_over_limit, planner_input, postprocess, trade_terms)
        from backend.app.agents.state import PlannerOutput
        from backend.app.api.common import ApiError
        from backend.app.agents.planner import article_retry_plan
        if retry := article_retry_plan(state):
            return None,retry,{'attempts':0,'error':None,'error_code':None}
        question = state.input.question
        output = (explicit_over_limit(question) or explicit_out_of_scope(question) or explicit_country_ranking(question)
                  or explicit_cause_followup(question, state.input.context))
        attempts, error = 0, None
        if output is None:
            output, attempts, error = self.parse('planner', PLANNER_PROMPT, planner_input(state), PlannerOutput, 2000,
                lambda value: postprocess(value, question, trade_terms(self.settings),
                    previous_both_trade_flows=state.input.context.both_trade_flows,
                    previous_conditions=state.input.context.conditions))
        plan, code = None, None
        if output is not None:
            try: plan, _ = build_plan(output)
            except ApiError as exc: code = exc.code
        return output, plan, {'attempts': attempts, 'error': error, 'error_code': code}

    def store(self):
        from backend.app.api.database import Database, Store
        engine = self.settings.engine()
        db = Database(engine)
        db.settings = self.settings
        return engine, lambda connection: Store(db, connection)


def timed(fn):
    started = time.perf_counter()
    value = fn()
    return value, round((time.perf_counter() - started) * 1000)


# ---------- 채점: planner ----------

def norm(key, value):
    if hasattr(value, 'model_dump'): value = value.model_dump(mode='json')
    if key == 'custom_query': return bool(value)
    if value is None: return None
    if key == 'products': return sorted(' '.join(v.split()).casefold() for v in value)
    if key == 'countries': return sorted((c['name'] if isinstance(c, dict) else c) for c in value)
    if key in ('period', 'compare_period', 'article_period'): return {k: str(v)[:10] for k, v in value.items()}
    return value


def same(key, got, want):
    """정답이 {"$in": [...]}이면 그 중 하나와 같으면 맞다(모호해서 둘 다 허용하는 칸)."""
    options = want['$in'] if isinstance(want, dict) and '$in' in want else [want]
    if key == 'comparison_requested': return bool(got) in [bool(o) for o in options]
    return norm(key, got) in [norm(key, o) for o in options]


def score_planner(case, output, plan, info):
    expect = case['expect']
    if 'error' in expect:
        ok = info.get('error_code') == expect['error']
        return {'pass': ok, 'task_count': ok, 'fields': {'error': ok}}
    if output is None:
        return {'pass': False, 'task_count': False, 'fields': {'parse': False}}
    actual = [t.model_dump(mode='json') for t in output.tasks]
    fields = {'plan': plan is not None and all(task.answer_status != 'NO_DATA' for task in plan.tasks)}
    for i, want in enumerate(expect['tasks']):
        got = actual[i] if i < len(actual) else None
        slots = (got or {}).get('slots', {})
        fields[f't{i+1}.intent'] = bool(got) and same('intent', got['intent'], want['intent'])
        wanted = want.get('slots', {})
        intents = set(want['intent']['$in'] if isinstance(want['intent'], dict) else [want['intent']])
        for key in dict.fromkeys([*STRICT_SLOTS, *wanted]):
            if key not in wanted and (intents <= {'CONVERSATION', 'OUT_OF_SCOPE', 'HS_LOOKUP'}
                                      or (key in SLOT_INTENTS and not intents & SLOT_INTENTS[key])):
                continue   # 슬롯 없는 intent는 적힌 칸만, intent 전용 칸은 그 intent에서만 본다
            fields[f't{i+1}.{key}'] = got is not None and same(key, slots.get(key), wanted.get(key))
        if slots.get('countries'):
            fields[f't{i+1}.country_mentions'] = all(
                country['mention'].strip().casefold() in case['question'].casefold()
                for country in slots['countries'])
        if got:
            for selection in got['target_selections']:
                # 금액 순위 결과는 실행에서도 rank 별칭을 금액 순서로 해석한다.
                if (actual[selection['source_task'] - 1]['intent'] == 'RANKING'
                        and selection['order_by'] in ('country_rank', 'product_rank')):
                    selection['order_by'] = 'amount_usd'
        for key, default in (('custom_query', False), ('source_tasks', []), ('target_selections', [])):
            fields[f't{i+1}.{key}'] = got is not None and same(key, got.get(key), want.get(key, default))
        if 'REPORT' in intents:
            fields[f't{i+1}.previous_turn_result'] = (got is not None
                and bool(got.get('previous_turn_result')) == want.get('previous_turn_result', False))
        if patterns := want.get('custom_query_patterns'):
            fields[f't{i+1}.custom_query_content'] = got is not None and all(
                re.search(pattern, got.get('custom_query') or '', re.IGNORECASE) for pattern in patterns)
    count = len(actual) == len(expect['tasks'])
    return {'pass': count and all(fields.values()), 'task_count': count, 'fields': fields}


# ---------- 채점: conditions·context ----------

def outcome(state, task, step):
    pending = state.pending
    result = state.results.get(step.step_id) if step else None
    c = task.conditions
    return {'status': 'NEEDS_INPUT' if pending else (step.status if step else 'NO_STEP'),
            'pending_kind': pending.kind if pending else None,
            'choices': [x.choice_id for x in pending.choices] if pending else [],
            'country_codes': list(c.country_codes) if c else [],
            'hs_codes': [h.hs_code for h in c.hs_items] if c else [],
            'period': c.period.model_dump(mode='json') if c and c.period else None,
            'trade_flow': c.trade_flow if c else None,
            'compare_period': c.compare_period.model_dump(mode='json') if c and c.compare_period else None,
            'article_period': c.article_period.model_dump(mode='json') if c and c.article_period else None,
            'ranking_group': c.ranking_group if c else None,
            'ranking_limit': task.slots.ranking_limit,
            'share_basis': c.share_basis if c else None,
            'sql_template': next((s.args.get('template') for s in task.steps if s.tool == 'query_trade_data'), None),
            'latest_month': str(state.data_meta.trade_latest_month) if state.data_meta and state.data_meta.trade_latest_month else None,
            'error': result.error if result else None}


def months(period):
    s, e = (datetime.fromisoformat(period[k]) for k in ('start', 'end'))
    return (e.year - s.year) * 12 + e.month - s.month + 1


def score_conditions(expect, out):
    status = out['status']
    allowed = expect.get('status_in') or [expect['status']]
    f = {'status': status in allowed}
    metrics = {}
    if status == 'NEEDS_INPUT':
        if 'pending_kind' in expect: f['pending_kind'] = out['pending_kind'] == expect['pending_kind']
        if 'choices_include' in expect: f['choices_include'] = set(expect['choices_include']) <= set(out['choices'])
        if prefixes := expect.get('hs_candidate_prefixes'):
            f['hs_candidates'] = out['pending_kind'] == 'HS_ITEM' and all(
                any(code.startswith(prefix) for code in out['choices'][:5]) for prefix in prefixes)
            metrics['hs_candidate_recall'] = f['hs_candidates']
    if code := expect.get('country_candidate'):
        found = status == 'NEEDS_INPUT' and out['pending_kind'] == 'COUNTRY' and code in out['choices'][:5]
        metrics['country_candidate_recall'] = found
        if status == 'NEEDS_INPUT':
            f['country_candidates'] = found
    if status == 'DONE':
        for key in ('country_codes', 'hs_codes'):
            if key in expect: f[key] = sorted(out[key]) == sorted(expect[key])
        for key in ('period', 'trade_flow', 'article_period', 'ranking_group', 'ranking_limit', 'share_basis', 'sql_template'):
            if key in expect: f[key] = out[key] == expect[key]
        if 'period_months' in expect:
            f['period_months'] = (bool(out['period']) and months(out['period']) == expect['period_months']
                                  and out['period']['end'] == out['latest_month'])
        if 'compare_period' in expect:
            compare = expect['compare_period']
            if compare == 'previous_year':
                from backend.app.api.common import shift_month
                compare = {k: shift_month(datetime.fromisoformat(v).date(), -12).isoformat()
                           for k, v in (out['period'] or {}).items()}
            f['compare_period'] = out['compare_period'] == compare
    if status == 'FAILED' and 'error_contains' in expect:
        f['error_contains'] = expect['error_contains'] in (out['error'] or '')
    # 가장 나쁜 오류: 되묻거나 실패해야 할 질문을 틀린 값으로 확정, 또는 확정해야 할 값을 틀리게 확정
    wrong = status == 'DONE' and not all(f.values())
    # 되물을 필요가 없는데 되물은 경우만 센다. 종류만 틀린 되묻기는 pending_kind 정확도에 잡힌다
    unnecessary = status == 'NEEDS_INPUT' and 'NEEDS_INPUT' not in allowed
    return {'pass': all(f.values()), 'fields': f, 'wrong_confirm': wrong, 'unnecessary_ask': unnecessary, **metrics}


def run_check(store, state, task):
    """하위 질문 하나의 check_conditions. confirm_report·resolve_bindings는 넘기지 않는다(REPORT·결과 연결은 평가 밖)."""
    from backend.app.agents.conditions import check_conditions
    step = next((s for s in task.steps if s.tool == 'check_conditions'), None)
    if step is not None: check_conditions(store, state, step)
    return step


def conditions_case(runner, connect, case, chain):
    from backend.app.agents.planner import build_plan
    from backend.app.agents.state import PlannerOutput
    state = agent_state(case['question'], case.get('context'), first_question=case.get('first_question', False),
                        interests=case.get('interests'))
    if chain:
        output, plan, info = runner.plan(state)
        info = {**info, 'planner_output': output.model_dump(mode='json') if output else None}
        if plan is None: return {'pass': False, 'fields': {'plan': False}, 'wrong_confirm': False, 'unnecessary_ask': False}, info
    else:
        plan, _ = build_plan(PlannerOutput(tasks=[{'intent': case['intent'], 'question': case['question'],
                                                    'slots': case['slots']}]))
    state.plan = plan
    task = plan.tasks[0]
    with connect() as store:
        step = run_check(store, state, task)
        out = outcome(state, task, step)
    score = score_conditions(case['expect'], out)
    if chain:
        out['planner'] = info
        score['fields'].update(intent=task.intent == case['intent'], task_count=len(plan.tasks) == 1)
        score['pass'] = all(score['fields'].values())
        score['wrong_confirm'] = out['status'] == 'DONE' and not score['pass']
    return score, out


# ---------- 채점: compose_text ----------

NUMBER = re.compile(r'\d')


def score_conclusions(expect, draft):
    fields = {}
    for conclusion, rules in zip(draft.conclusion, expect['conclusions'], strict=True):
        text = ' '.join(conclusion.sentences).casefold()
        text = re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', text)
        prefix = conclusion.task_id
        fields[f'{prefix}.required'] = all(re.search(pattern, text) for pattern in rules.get('required', []))
        fields[f'{prefix}.forbidden'] = not any(re.search(pattern, text) for pattern in rules.get('forbidden', []))
        if ordered := rules.get('ordered'):
            matches = [re.search(pattern, text) for pattern in ordered]
            positions = [m.start() for m in matches if m]
            fields[f'{prefix}.order'] = len(positions) == len(ordered) and positions == sorted(set(positions))
        if rules.get('no_numbers'):
            fields[f'{prefix}.no_numbers'] = not NUMBER.search(text)
    if 'explanation_empty' in expect:
        fields['explanation_empty'] = (not draft.explanation) == expect['explanation_empty']
    return fields


def compose_case(runner, case):
    from backend.app.agents.compose import COMPOSE_TEXT_PROMPT, assemble, compose_input
    from backend.app.agents.planner import build_plan
    from backend.app.agents.state import Conditions, Draft, PlannerOutput, ToolResult
    from backend.app.agents.verify import verify
    state = agent_state(case['question'], {'recent': case.get('recent', [])}, first_question=case.get('first_question', False))
    state.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': t['intent'], 'question': t['question'],
        'slots': {'answer_format': t.get('answer_format')}} for t in case['tasks']]))
    products = case.get('product_names', {})
    for task, spec in zip(state.plan.tasks, case['tasks']):
        task.conditions = Conditions.model_validate(spec['conditions']) if spec.get('conditions') else None
        for step in task.steps:
            step.status = 'DONE'
            if step.tool != 'check_conditions':
                state.results[step.step_id] = ToolResult(status='OK', digest=spec.get('digest'))
        task.answer_status = state.task_outcome(task.task_id)
    def describe(c):
        if not c: return {}
        from backend.app.agents.templates import api_conditions
        display=c.model_dump(mode='json')
        public=api_conditions(c)
        display.update({key:public[key] for key in ('period','compare_period')})
        return {**display, 'countries': [{'code': k, 'name': NAMES.get(k, k)} for k in c.country_codes],
                'products': [{'code': p.hs_code, 'name': products.get(p.hs_code, p.hs_code)} for p in c.hs_items]}
    payload, fixed = compose_input(state, describe, [])
    draft, attempts, error = runner.parse('compose', COMPOSE_TEXT_PROMPT, payload, Draft, 3000)
    if draft is not None:
        try: draft = assemble(state, draft, fixed, describe)
        except ValueError: draft, error = None, 'LimitError'
    if draft is None:
        return {'pass': False, 'fields': {'format': False}, 'grounded': False}, {'error': error, 'attempts': attempts}
    state.draft = draft
    had_summary = bool(draft.turn_summary)
    verify(state, known_names=[*NAMES.values(), *products.values()], countries=list(NAMES.values()), product_labels=products)
    sentences = draft.sentences()
    expect = case['expect']
    f = {'format': True, 'grounded': state.verification.status == 'PASS', **score_conclusions(expect, draft)}
    checked = [c for c in state.verification.checks if c.numbers]
    return ({'pass': all(f.values()), 'fields': f, 'grounded': f['grounded'],
             'number_sentences': len(checked), 'mismatch_sentences': sum(c.status == 'MISMATCH' for c in checked)},
             {'sentences': sentences, 'turn_summary': draft.turn_summary, 'attempts': attempts,
              'summary_status': ('PASS' if draft.turn_summary else 'REJECTED') if had_summary else 'EMPTY',
              'sentence_checks': [c.model_dump(mode='json') for c in state.verification.checks],
              'references': [rules['reference'] for rules in expect['conclusions']]})


# ---------- context(여러 턴) ----------

def score_context_turn(expect, task, out, task_count):
    scored = score_conditions({'status': expect['status'], **expect.get('conditions', {})}, out)
    scored['fields'].update(intent=same('intent', task.intent, expect['intent']), task_count=task_count == 1)
    scored['pass'] = all(scored['fields'].values())
    return scored


def context_case(runner, connect, case):
    from backend.app.agents.context import completed_context
    from backend.app.agents.state import HISTORY_TURNS, Draft, SessionContext, TaskConclusion
    context = SessionContext(session_id='eval')
    turns = []
    for number, turn in enumerate(case['turns'], 1):
        started = time.perf_counter()
        state = agent_state(turn['question'], context.model_dump(mode='json'), first_question=number == 1)
        _, plan, info = runner.plan(state)
        if plan is None:
            turns.append({'turn': number, 'pass': False, 'fields': {'plan': False}, 'info': info,
                          'ms': round((time.perf_counter() - started) * 1000)})
            continue
        state.plan = plan
        task = plan.tasks[0]
        with connect() as store:
            step = run_check(store, state, task)
            out = outcome(state, task, step)
        scored = score_context_turn(turn['expect'], task, out, len(plan.tasks))
        # SQL/RAG 대신 조건 확인에 성공한 단일 작업의 요약을 저장해 다음 턴을 평가한다.
        if len(plan.tasks) == 1 and (out['status'] == 'DONE' or
                                     (out['status'] == 'NO_STEP' and task.intent == 'CONVERSATION'
                                      and task.answer_status is None)):
            task.answer_status = 'ANSWERED'
            state.draft = Draft(conclusion=[TaskConclusion(task_id=task.task_id, sentences=['평가용 결론입니다.'])],
                                turn_summary=turn['summary'])
            updated = completed_context(state)
            recent = updated.recent
            scored['fields']['context_saved'] = (
                bool(recent) and recent[:-1] == context.recent[1 - HISTORY_TURNS:]
                and recent[-1].question == state.input.question
                and recent[-1].intent == task.intent
                and recent[-1].summary == turn['summary']
                and recent[-1].conditions == task.conditions
                and updated.conditions == (task.conditions or context.conditions))
            context = updated
        scored['pass'] = all(scored['fields'].values())
        turns.append({'turn': number, **scored, 'intent': task.intent, 'out': out,
                      'ms': round((time.perf_counter() - started) * 1000)})   # 플래너 + 조건 확인
    return {'pass': all(t['pass'] for t in turns), 'turns': turns,
            'turn_pass': sum(t['pass'] for t in turns), 'turn_total': len(turns)}


# ---------- 요약 ----------

def rate(values):
    values = list(values)
    return round(sum(values) / len(values), 4) if values else None


def latency(prefix, values):
    """지연 요약(ms). crash로 0ms가 된 실행은 뺀다(실제로 잰 시간이 아니다)."""
    values = sorted(v for v in values if v)
    if not values: return {f'{prefix}_p50': None, f'{prefix}_p95': None, f'{prefix}_max': None, f'{prefix}_mean': None}
    return {f'{prefix}_p50': statistics.median(values), f'{prefix}_p95': values[min(len(values) - 1, int(len(values) * 0.95))],
            f'{prefix}_max': values[-1], f'{prefix}_mean': round(statistics.fmean(values))}


def summarize(area, rows, elapsed_seconds=None):
    """비율 지표는 0~1(1 = 100%)이다. *_rate 중 wrong_confirm·unnecessary_ask·parse_failure·sentence_mismatch는 낮을수록 좋다."""
    ok = [r for r in rows if 'score' in r]
    summary = {'cases': len({r['id'] for r in rows}), 'runs': len(rows), 'elapsed_seconds': elapsed_seconds,
               'pass_rate': rate(r['score']['pass'] for r in ok),
               **latency('latency_ms', (r['ms'] for r in ok))}
    if area == 'context':
        summary['turn_pass_rate'] = rate(t['pass'] for r in ok for t in r['score']['turns'])
        summary.update(latency('turn_latency_ms', (t.get('ms') for r in ok for t in r['score']['turns'])))
    else:
        fields = {}
        for r in ok:
            for k, v in r['score']['fields'].items():
                fields.setdefault(re.sub(r'^t\d+\.', '', k), []).append(v)
        summary['field_accuracy'] = {k: rate(v) for k, v in sorted(fields.items())}
    if area == 'planner':
        summary['task_count_accuracy'] = rate(r['score']['task_count'] for r in ok)
        summary['parse_failure_rate'] = rate(r['detail'].get('error') is not None and not r['detail'].get('error_code') for r in ok)
        summary['model_pass_rate'] = rate(r['score']['pass'] for r in ok if r['detail'].get('attempts') != 0)
        summary['regex_pass_rate'] = rate(r['score']['pass'] for r in ok if r['detail'].get('attempts') == 0)
    if area == 'conditions':
        summary['wrong_confirm_count'] = sum(r['score']['wrong_confirm'] for r in ok)
        summary['wrong_confirm_rate'] = rate(r['score']['wrong_confirm'] for r in ok)
        summary['unnecessary_ask_rate'] = rate(r['score']['unnecessary_ask'] for r in ok)
        summary['quality_gate_pass'] = (bool(ok) and summary['wrong_confirm_count'] == 0
                                        and 10 * sum(r['score']['pass'] for r in ok) >= 9 * len(ok))
        for metric in ('hs_candidate_recall', 'country_candidate_recall'):
            summary[f'{metric}_at_5'] = rate(r['score'][metric] for r in ok if metric in r['score'])
    if area == 'compose_text':
        summary['grounded_rate'] = rate(r['score'].get('grounded', False) for r in ok)
        total = sum(r['score'].get('number_sentences', 0) for r in ok)
        summary['sentence_mismatch_rate'] = round(sum(r['score'].get('mismatch_sentences', 0) for r in ok) / total, 4) if total else None
    by_case = {}
    for r in ok: by_case.setdefault(r['id'], []).append(r['score']['pass'])
    summary['consistent_rate'] = rate(len(set(v)) == 1 for v in by_case.values())   # 반복 실행 결과가 같은 비율
    summary['by_tag'] = {}
    for r in ok:
        for tag in r.get('tags', []): summary['by_tag'].setdefault(tag, []).append(r['score']['pass'])
    summary['by_tag'] = {k: rate(v) for k, v in sorted(summary['by_tag'].items())}
    return summary


LOWER_IS_BETTER = ('wrong_confirm_rate', 'unnecessary_ask_rate', 'parse_failure_rate', 'sentence_mismatch_rate')


def run_cases(args, runner, connect, cases):
    """한 모델로 문항 전체를 실행한다. 질문 하나의 오류로 전체 평가를 멈추지 않고 실패로 센다."""
    rows = []
    for case in cases:
        for repeat in range(args.repeat):
            row = {'id': case['id'], 'repeat': repeat + 1, 'tags': case.get('tags', [])}
            try:
                if args.area == 'planner':
                    (output, plan, info), row['ms'] = timed(lambda: runner.plan(agent_state(
                        case['question'], case.get('context'), first_question=case.get('first_question', False))))
                    row['score'] = score_planner(case, output, plan, info)
                    row['detail'] = {**info, 'output': output.model_dump(mode='json') if output else None}
                elif args.area == 'conditions':
                    (row['score'], row['detail']), row['ms'] = timed(lambda: conditions_case(runner, connect, case, args.chain))
                elif args.area == 'compose_text':
                    (row['score'], row['detail']), row['ms'] = timed(lambda: compose_case(runner, case))
                else:
                    row['score'], row['ms'] = timed(lambda: context_case(runner, connect, case))
            except Exception as exc:
                row['score'] = {'pass': False, 'fields': {'crash': False}, 'turns': [], 'task_count': False,
                                'wrong_confirm': False, 'unnecessary_ask': False}
                row['ms'], row['detail'] = 0, {'crash': f'{type(exc).__name__}: {str(exc)[:200]}'}
            rows.append(row)
            print(f"[{runner.model or 'nollm'}] {row['id']}#{row['repeat']} "
                  f"{'PASS' if row['score']['pass'] else 'FAIL'} {row['ms']}ms", flush=True)
    return rows


def flat_metrics(summary):
    """비교 표의 행: 요약의 숫자 지표, 칸별 정확도, 유형별 통과율."""
    metrics = {k: v for k, v in summary.items() if not isinstance(v, dict)}
    metrics.update({f'field_accuracy.{k}': v for k, v in summary.get('field_accuracy', {}).items()})
    metrics.update({f'by_tag.{k}': v for k, v in summary.get('by_tag', {}).items()})
    return metrics


def shown(key, value):
    if value is None: return '-'
    if isinstance(value, bool): return 'PASS' if value else 'FAIL'
    if key.endswith(('_rate', '_at_5')) or key.startswith(('field_accuracy.', 'by_tag.')): return f'{value * 100:.1f}%'
    if isinstance(value, float): return f'{value:,.1f}'
    return f'{value:,}'


def compare_report(args, results):
    """모델별 결과를 한 문서로 비교한다: 지표 표(행 = 지표, 열 = 모델)와 문항별 통과 표."""
    names = list(results)
    header = '| {} | ' + ' | '.join(names) + ' |'
    rule = '|---' * (len(names) + 1) + '|'
    lines = [f'# {args.area} 평가 비교 ({args.split})', '',
             f'- 실행 시각(UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}',
             f'- 반복: {args.repeat}회' + (', 플래너 출력 사용(--chain)' if args.chain else '')
             + (f', 국가 유사도 {args.country_threshold}' if args.country_threshold is not None else ''),
             '- 비율은 %, 시간은 ms(elapsed_seconds만 초). ↓는 낮을수록 좋은 지표다.', '',
             '## 지표', '', header.format('지표'), rule]
    metrics = {name: flat_metrics(results[name]['summary']) for name in names}
    for key in dict.fromkeys(k for name in names for k in metrics[name]):
        down = ' ↓' if key in LOWER_IS_BETTER or 'latency' in key or key == 'elapsed_seconds' else ''
        lines.append(f'| {key}{down} | ' + ' | '.join(shown(key, metrics[n].get(key)) for n in names) + ' |')
    lines += ['', '## 문항별 통과 (통과 수 / 반복 수)', '', header.format('문항'), rule]
    for case_id in dict.fromkeys(r['id'] for n in names for r in results[n]['rows']):
        cells = []
        for n in names:
            runs = [r['score']['pass'] for r in results[n]['rows'] if r['id'] == case_id]
            cells.append(f'{sum(runs)}/{len(runs)}' if runs else '-')
        lines.append(f'| {case_id} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('area', choices=AREAS)
    parser.add_argument('--split', choices=('dev', 'holdout', 'all'), default='dev')
    parser.add_argument('--model', action='append',
                        help='플래너·compose 모델. 여러 번 쓰거나 쉼표로 나열하면 차례로 돌리고 한 리포트로 비교한다 '
                             '(예: --model gpt-4.1-mini,gpt-5.6-luna,gpt-6-luna)')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--timeout', type=float, help='LLM 호출 하나의 시간 상한(초). 생략하면 API_*_TIMEOUT_SECONDS')
    parser.add_argument('--chain', action='store_true', help='conditions: 정답 슬롯 대신 플래너 출력으로 실행')
    parser.add_argument('--country-threshold', type=float, help='conditions: API_CHAT_COUNTRY_SIMILARITY(C-28) 덮어쓰기')
    parser.add_argument('--case', action='append', help='이 id만 실행(여러 번 지정 가능)')
    args = parser.parse_args(argv)
    models = list(dict.fromkeys(m.strip() for value in args.model or [] for m in value.split(',') if m.strip()))
    needs_llm = args.area != 'conditions' or args.chain
    if needs_llm and not models: parser.error('LLM을 쓰는 평가는 --model이 필요합니다')
    if not needs_llm: models = [None]   # 정답 슬롯 conditions는 모델과 무관하다
    settings = Runner(None, args.timeout).settings
    if args.country_threshold is not None: settings.values['API_CHAT_COUNTRY_SIMILARITY'] = str(args.country_threshold)
    connect = None
    if args.area in ('conditions', 'context'):
        from contextlib import contextmanager
        engine, make_store = Runner(None, args.timeout, settings).store()
        @contextmanager
        def connect():
            with engine.connect() as connection:
                try: yield make_store(connection)
                finally: connection.rollback()   # 평가는 쓰기를 남기지 않는다
    cases = [c for c in load(args.area, args.split) if (not args.case or c['id'] in args.case)
             and not (args.area == 'conditions' and args.chain and c.get('unit_only'))]
    if not cases:
        parser.error('선택한 평가 모드와 문항 조건에 맞는 사례가 없습니다')
    results = {}
    for model in models:
        started = time.perf_counter()
        rows = run_cases(args, Runner(model, args.timeout, settings), connect, cases)
        name = model or 'nollm'
        results[name] = {'summary': summarize(args.area, rows, round(time.perf_counter() - started, 1)), 'rows': rows}
        print(json.dumps({name: results[name]['summary']}, ensure_ascii=False, indent=1))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    label = 'nollm' if models == [None] else ('compare' if len(models) > 1 else models[0].replace('/', '_'))
    base = OUT / f"{args.area}_{args.split}_{label}{'_chain' if args.chain else ''}_{stamp}"
    base.with_suffix('.json').write_text(json.dumps({'args': {**vars(args), 'model': models}, 'models': results},
                                                    ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    base.with_suffix('.md').write_text(compare_report(args, results), encoding='utf-8')
    print('saved', base.with_suffix('.json'), base.with_suffix('.md'))


if __name__ == '__main__':
    main()
