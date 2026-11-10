"""Evaluate the 100 synthetic Writer Dev cases using the service compose routes. No database or article service is contacted."""
import argparse
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
from statistics import median
from time import monotonic
from unittest.mock import patch

import httpx
from dotenv import dotenv_values
from openai import OpenAI

from backend.app.agents.graph import compose_node
from backend.app.agents.llm import StructuredClient
from backend.app.agents.planner import build_plan
from backend.app.agents.runtime import Services
from backend.app.agents.state import AgentState, Conditions, PlannerOutput, ToolResult
from backend.app.agents.tools import article_result
from backend.app.api.config import Settings
from backend.app.schemas.rag import ArticleEvidence, RagResponse
from backend.tests.rag_fixtures import article_payload, response_payload
from backend.scripts.writer_dev_dataset import DATA, load_dataset, score


def statistic(amounts, flow='EXPORT', **comparison):
    months = ['2026-08', '2026-09'][-len(amounts):]
    return {'kind': 'TREND', 'trade_flow': flow, 'unit': 'USD', 'targets': [{
        'name': '전체 품목', 'country_name': '전체 국가',
        'total_usd': None if None in amounts else sum(amounts),
        'monthly': [{'month': m, 'amount_usd': v} for m, v in zip(months, amounts, strict=True)],
        **comparison}]}


def article(n, title, sentence, role, missing=()):
    row = article_payload(n)
    row.update(title=title, evidence_role=role,
               reference_limitation='통관 절차 공지만 확인됩니다.' if missing else None)
    row['summary'][0].update(text=sentence, unverified_question_parts=list(missing))
    row['evidence'][0]['quote'] = sentence
    return row


# Original six fixtures retained for baseline replay and regression tests.
DEV = {
    '01-large-export': [('STATISTICS', '9월 한국의 총수출액은 얼마인가요?', statistic([123460000]), [])],
    '02-zero-null': [('STATISTICS', '8월과 9월 수출액을 각각 알려 주세요. 두 달 전체 합계도 확인되나요?',
                      statistic([0, None]), [])],
    '03-import': [('STATISTICS', '9월 수입액과 비교 기간 수입액, 증감률을 알려 주세요.',
                   statistic([1234], 'IMPORT', compare_total_usd=1542, change_pct=-20.0), [])],
    '04-context': [('CAUSE', '9월 수출 증가 원인이 통관 절차 변경인가요?',
                    statistic([30000], compare_total_usd=25000, change_pct=20),
                    [article(1, '통관 절차 공지', '참고(직접 답변 아님): 통관 신고서 양식 개정이 공지됐다.',
                             'context_reference', ['수출 증가 원인'])])],
    '05-two-tasks': [('LOCAL_ISSUES', '9월 포장 기준 변경 내용을 기사로 알려 주세요.', None,
                      [article(1, '포장 기준 공지', '포장 기준에 분리 표시가 추가됐다.', 'direct_evidence')]),
                     ('LOCAL_ISSUES', '9월 창고 운영 변경 내용을 기사로 알려 주세요.', None,
                      [article(2, '창고 운영 공지', '창고가 야간 운영을 시작했다.', 'direct_evidence')])],
    '06-partial': [('CAUSE', '9월 수출액과 그 배경을 기사로 설명해 주세요.', statistic([26100000]), [])],
}


def dev_state(case):
    question = ' '.join(q for _, q, _, _ in case)
    state = AgentState(input={'question': question, 'job_id': 'dev', 'user_id': 1, 'session_id': '1',
                              'received_at': datetime(2026, 10, 1), 'context': {'session_id': '1'}})
    state.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': i, 'question': q} for i, q, _, _ in case]))
    for task, (_, _, stats, articles) in zip(state.plan.tasks, case, strict=True):
        period = {'start': '2026-09-01', 'end': '2026-09-30'}
        first = stats['targets'][0]['monthly'][0]['month'] if stats else '2026-09'
        task.conditions = Conditions(period={'start': first+'-01', 'end': '2026-09-01'}, article_period=period,
                                     trade_flow=stats['trade_flow'] if stats else 'EXPORT')
        for step in task.steps:
            step.status = 'DONE'
            if step.tool == 'query_trade_data':
                state.results[step.step_id] = ToolResult(status='OK', result_id=step.step_id, digest=stats)
            if step.tool == 'search_articles':
                def search(request, *, article_allowed=None, started=None):
                    data = response_payload(request.model_dump(mode='json'), count=len(articles),
                            status='insufficient_results' if articles else 'failed', articles=articles,
                            failures=[] if articles else [{'stage':'timeout', 'code':'synthetic_timeout'}],
                            unverified_question_parts=RagResponse.common_unverified_parts(
                                [ArticleEvidence.model_validate(a) for a in articles]))
                    return RagResponse.model_validate(data)
                with patch('backend.app.agents.tools.search_news', side_effect=search):
                    result = article_result(state, task, step)
                state.results[step.step_id] = result
                if result.status != 'OK':
                    step.status = 'FAILED'
        task.answer_status = state.task_outcome(task.task_id)
    return state


class Meter:
    def __init__(self):
        self.attempts = []

    def call(self, node, timeout, callback, **kwargs):
        attempt = {}
        self.attempts.append(attempt)
        started = monotonic()
        try:
            result = callback(timeout)
            attempt['accepted'] = True
            return result
        except Exception as exc:
            attempt.update(accepted=False, error=type(exc).__name__)
            raise
        finally:
            attempt['seconds'] = round(monotonic() - started, 3)

    def response(self, response):
        response.read()
        data = response.json()
        self.attempts[-1].update(http_status=response.status_code, usage=data.get('usage'),
            output=data.get('output'), status=data.get('status'), incomplete_details=data.get('incomplete_details'),
            resolved_model=data.get('model'),
            service_tier=data.get('service_tier'))

    def request(self, request):
        self.attempts[-1]['request'] = json.loads(request.content)

    def client(self, **kwargs):
        transport = httpx.Client(timeout=kwargs['timeout'],
                                 event_hooks={'request': [self.request], 'response': [self.response]})
        return OpenAI(**kwargs, base_url='https://api.openai.com/v1', http_client=transport)


# Standard USD per million tokens (input, cache read, default cache write, output), 2026-11-09.
STANDARD_PRICES = {
    'gpt-4.1-mini': (0.40, 0.10, None, 1.60),
    'gpt-5.6-luna': (0.20, 0.02, 0.25, 1.20),
    'gpt-6-luna': (0.10, 0.01, 0.125, 0.50),
}


def estimated_cost(model, attempt):
    usage = attempt.get('usage')
    if not usage or attempt.get('service_tier') != 'default':
        return None
    input_price, read_price, write_price, output_price = STANDARD_PRICES[model]
    details = usage.get('input_tokens_details') or {}
    reads = details.get('cached_tokens', 0)
    writes = details.get('cache_write_tokens', 0)
    if writes and write_price is None:
        return None  # No published separate cache-write price for this model.
    ordinary = usage['input_tokens'] - reads - writes
    return (ordinary * input_price + reads * read_price + writes * (write_price or 0) +
            usage['output_tokens'] * output_price) / 1_000_000


def total_if_known(values):
    return sum(values) if values and None not in values else None


def measurements(model, attempts):
    estimate = total_if_known([estimated_cost(model, a) for a in attempts]) if attempts else 0.0
    reasoning = [((a.get('usage') or {}).get('output_tokens_details') or {}).get('reasoning_tokens')
                 for a in attempts]
    return {'first_draft_accepted': bool(attempts[0]['accepted']) if attempts else None,
            'fixed': not attempts, 'fallback': bool(attempts) and not any(a['accepted'] for a in attempts),
            'retries': max(0, len(attempts) - 1), 'seconds': round(sum(a['seconds'] for a in attempts), 3),
            'incomplete': sum(a.get('status') == 'incomplete' for a in attempts),
            'reasoning_tokens': total_if_known(reasoning) if attempts else 0,
            'estimated_usd': round(estimate, 12) if estimate is not None else None}


def generate_case(case, model, key):
    """Run the Writer on its input alone so a blind evaluation can seal predictions before scoring."""
    state = case.state.model_copy(deep=True)
    # The service graph derives these outcomes from tool results immediately before compose.
    for task in state.plan.tasks:
        task.answer_status = state.task_outcome(task.task_id)
    names = (case.country_names, {(code, 'HS2022'): label for code, label in case.product_names.items()})
    settings = Settings({'OPENAI_API_KEY': key, 'API_COMPOSE_MODEL': model})
    meter = Meter()
    services = Services(meter, settings, StructuredClient(settings, meter.client))
    with patch.object(services, 'read_store', return_value=nullcontext()), \
         patch('backend.app.agents.templates._names', return_value=names):
        if compose_node(state) == 'compose_text':
            services.compose_text(state)
        else:
            services.compose(state)
    record = {'case': case.case_id, 'group': case.group, 'model': model, 'route': compose_node(state),
              'attempts': meter.attempts, 'metrics': measurements(model, meter.attempts),
              'draft': state.draft.model_dump(mode='json'), 'warnings': state.warnings}
    return state, record


def evaluate(models, key, out, dataset=None):
    metrics = {model: [] for model in models}
    if dataset is None:
        dataset = load_dataset()
    for case, gold in dataset:
        for model in models:
            state, record = generate_case(case, model, key)
            result = record['metrics']
            metrics[model].append(result)
            record['evaluation'] = score(state, gold, fixed=result['fixed'])
            print(json.dumps(record, ensure_ascii=False), file=out, flush=True)
            print(case.case_id, model, len(record['attempts']), flush=True)
    for model, rows in metrics.items():
        totals = {k: total_if_known([r[k] for r in rows]) for k in
                  ('fixed', 'retries', 'fallback', 'incomplete', 'reasoning_tokens', 'estimated_usd')}
        called = [r for r in rows if not r['fixed']]
        totals['model_cases'] = len(called)
        totals['first_draft_accepted'] = sum(r['first_draft_accepted'] for r in called)
        print(json.dumps({'model': model, 'cases': len(rows), 'median_seconds': median(r['seconds'] for r in called) if called else None,
                          **totals}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DATA / 'writer_dev_cases.jsonl')
    parser.add_argument('--gold', type=Path, default=DATA / 'writer_dev_gold.jsonl')
    parser.add_argument('--validate-only', action='store_true', help='Validate the frozen inputs and gold without an API key.')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--models', nargs='+', choices=STANDARD_PRICES, default=list(STANDARD_PRICES),
                        help='비교할 Writer 모델 목록. 생략하면 세 모델을 모두 평가합니다.')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if len(set(args.models)) != len(args.models):
        parser.error('동일한 모델을 두 번 지정할 수 없습니다.')
    dataset = load_dataset(args.cases, args.gold)
    if args.validate_only:
        print(f'Writer Dev: {len(dataset)} matched cases and gold records')
        return
    if not args.env_file or not args.output:
        parser.error('--env-file and --output are required for model evaluation')
    key = dotenv_values(args.env_file, interpolate=False)['OPENAI_API_KEY']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as out:
        evaluate(args.models, key, out, dataset)


if __name__ == '__main__':
    main()
