"""Manual, opt-in real-LLM evaluation against the dedicated local MySQL snapshot.

No live calls occur on import or during unittest discovery. Gold answers are never
sent to the model. This evaluates the SQL node with preconfirmed conditions, not Planner.
"""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import statistics
import time
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import URL, create_engine, event
import sqlglot
from sqlglot import exp

from backend.app.agents import sql as agent_sql
from backend.app.agents.charts import build_chart
from backend.app.agents.llm import StructuredClient
from backend.app.agents.state import Conditions, SqlDraft
from backend.app.api.common import now
from backend.app.api.config import Settings
from backend.scripts.evaluate_sql_visualization import ROOT, digest_bytes, revision_metadata

DATA = ROOT / 'docs/evaluations'
QUESTIONS = DATA / 'sql_questions.jsonl'
MANIFEST = DATA / 'sql_questions_manifest.json'
SNAPSHOT = DATA / 'sql_snapshot.sql'
TABLES = ('country', 'hs_code', 'export_monthly', 'import_monthly', 'export_summary', 'import_summary')
PRICE = {'input_per_million_usd': 2.0, 'cached_input_per_million_usd': 0.5,
         'output_per_million_usd': 8.0,
         'source': 'https://developers.openai.com/api/docs/models/gpt-4.1', 'checked_on': '2026-11-09'}


class EvaluationStopped(RuntimeError):
    pass


def plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def json_hash(value):
    return digest_bytes(json.dumps(plain(value), ensure_ascii=False, sort_keys=True, allow_nan=False).encode())


def load_questions():
    cases = [json.loads(line) for line in QUESTIONS.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not 30 <= len(cases) <= 50 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Expected 30–50 uniquely identified questions')
    return cases


def local_engine(port):
    # Intentionally no production settings, host argument, RDS URL or env DB fallback.
    return create_engine(URL.create('mysql+pymysql', username='eval_reader',
        password='local-evaluation-reader-only', host='127.0.0.1', port=port,
        database='sql_agent_eval', query={'charset': 'utf8mb4'}),
        connect_args={'connect_timeout': 5, 'read_timeout': 35, 'write_timeout': 5})


def database_identity(engine):
    with engine.connect() as connection:
        marker = connection.exec_driver_sql('SELECT version, baseline_month FROM eval_snapshot').one()
        if tuple(marker) != ('synthetic-trade-v1', '2025-03'):
            raise ValueError('Unexpected database snapshot; refusing to evaluate')
        grants = [str(row[0]).upper() for row in connection.exec_driver_sql('SHOW GRANTS FOR CURRENT_USER()')]
        if any(not (g.startswith('GRANT USAGE ON ') or
                    g.startswith('GRANT SELECT ON `SQL_AGENT_EVAL`.* ')) for g in grants):
            raise ValueError('Evaluation account must have SELECT-only privileges on sql_agent_eval')
        mysql_version = connection.exec_driver_sql('SELECT VERSION()').scalar_one()
        sql_mode = connection.exec_driver_sql('SELECT @@SESSION.sql_mode').scalar_one()
        data = {}
        for name in TABLES:
            rows = [plain(dict(r)) for r in connection.exec_driver_sql(f'SELECT * FROM {name}').mappings()]
            data[name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
    return {'version': marker[0], 'baseline_month': marker[1], 'mysql_version': mysql_version,
            'sql_mode': sql_mode, 'data_sha256': json_hash(data),
            'snapshot_file_sha256': digest_bytes(SNAPSHOT.read_bytes()),
            'row_counts': {name: len(rows) for name, rows in data.items()}}


def safe_statement(statement):
    """Independent minimal audit for observed statements, not the agent validator."""
    try:
        parsed = sqlglot.parse(statement, read='mysql')
        if len(parsed) != 1 or not isinstance(parsed[0], (exp.Select, exp.Union)):
            return False
        for node in parsed[0].walk():
            if isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock, exp.Hint,
                                 exp.Parameter, exp.SessionParameter)):
                return False
            if isinstance(node, exp.Table) and (node.db or node.catalog):
                return False
            if isinstance(node, exp.Column) and node.name.lower() == 'body_masked':
                return False
            if isinstance(node, exp.Anonymous):
                return False
            if isinstance(node, exp.Func) and node.sql_name().upper() in {'SLEEP','BENCHMARK','LOAD_FILE','GET_LOCK','RELEASE_LOCK'}:
                return False
        from sqlglot.optimizer.scope import traverse_scope
        return all(not isinstance(source, exp.Table) or source.name.lower() in TABLES
                   for scope in traverse_scope(parsed[0]) for _, source in scope.selected_sources.values())
    except Exception:
        return False


def cell_equal(actual, expected, absolute, relative):
    if actual is None or expected is None:
        return actual is expected
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        if isinstance(actual, bool) or not isinstance(actual, (int, float, Decimal)):
            return False
        a, b = Decimal(str(actual)), Decimal(str(expected))
        return a.is_finite() and b.is_finite() and abs(a-b) <= max(Decimal(str(absolute)), abs(b)*Decimal(str(relative)))
    return type(actual) is type(expected) and actual == expected


def rows_equal(actual, expected, *, ordered=False, absolute=0.001, relative=0.000001):
    if len(actual) != len(expected):
        return False
    def equal(a, b):
        return len(a) == len(b) and all(cell_equal(x, y, absolute, relative) for x, y in zip(a, b))
    if ordered:
        return all(equal(a, b) for a, b in zip(actual, expected))
    # Bipartite matching preserves duplicates and handles overlapping numeric tolerances.
    matches = {}
    def assign(i, visited):
        for j, row in enumerate(expected):
            if j not in visited and equal(actual[i], row):
                visited.add(j)
                if j not in matches or assign(matches[j], visited):
                    matches[j] = i
                    return True
        return False
    return all(assign(i, set()) for i in range(len(actual)))


def prepare_gold(engine, cases):
    if MANIFEST.exists() or any('expected_rows' in case for case in cases):
        raise ValueError('Gold is already frozen; do not rewrite it after observing model results')
    identity = database_identity(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql('START TRANSACTION READ ONLY')
        for case in cases:
            if case['expected_outcome'] == 'answer':
                if not safe_statement(case['reference_sql']):
                    raise ValueError(f"Unsafe reference SQL: {case['id']}")
                rows = connection.execution_options(no_parameters=True).exec_driver_sql(case['reference_sql']).mappings().all()
                if rows and list(rows[0]) != case['result_keys']:
                    raise ValueError(f"Reference column mismatch: {case['id']}")
                case['expected_rows'] = [[plain(row[key]) for key in case['result_keys']] for row in rows]
            else:
                case['expected_rows'] = []
        connection.rollback()
    QUESTIONS.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in cases), encoding='utf-8')
    write_json(MANIFEST, {'dataset_version': 'sql-questions-v1', 'question_count': len(cases),
        'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
        'questions_sha256': digest_bytes(QUESTIONS.read_bytes()), 'snapshot': identity,
        'gold_authorship': 'agent-authored independent reference SQL; NOT human-verified',
        'human_review': {'status': 'pending', 'reviewer': None, 'approved_questions_sha256': None},
        'policy': 'Freeze before first LLM call. No prompt/code/gold tuning against these results. Version any corrections.'})


class Recorder:
    def __init__(self, max_cost):
        self.calls = []
        self.max_cost = max_cost

    def factory(self, **kwargs):
        import httpx
        from openai import OpenAI, AuthenticationError, PermissionDeniedError, RateLimitError, APIConnectionError, APITimeoutError
        current_entry = None
        def capture_usage(response):
            # The SDK may raise during Pydantic parsing before returning response.usage.
            # Buffer non-streaming JSON first so failed-format calls remain billable evidence.
            response.read()
            try:
                raw = response.json()
            except ValueError:
                return
            if current_entry is not None and isinstance(raw, dict):
                self.record_usage(current_entry, raw)
        http_client = httpx.Client(event_hooks={'response':[capture_usage]})
        client = OpenAI(**kwargs, base_url='https://api.openai.com/v1', http_client=http_client)
        recorder = self
        class Context:
            def __enter__(self):
                return SimpleNamespace(responses=SimpleNamespace(parse=self.parse))
            def __exit__(self, *args):
                client.close()
            def parse(self, **request):
                nonlocal current_entry
                # Reserve a conservative per-call bound (UTF-8 bytes plus schema overhead).
                input_bound = len((request['input']+request['instructions']).encode()) + 12000
                reserve = (input_bound*PRICE['input_per_million_usd'] +
                           request['max_output_tokens']*PRICE['output_per_million_usd'])/1_000_000
                if sum(c.get('estimated_cost_usd', c.get('reserved_cost_usd',0)) for c in recorder.calls)+reserve > recorder.max_cost:
                    raise EvaluationStopped('Configured evaluation cost budget exhausted')
                started = time.perf_counter()
                entry = {'requested_model': request['model'], 'reserved_cost_usd':reserve}
                current_entry = entry
                recorder.calls.append(entry)
                try:
                    response = client.responses.parse(**request)
                except APITimeoutError as exc:
                    # Timeouts are retryable in generated_analysis. Preserve its attempt
                    # limit instead of turning a single timeout into a harness abort.
                    entry.update(error_type=type(exc).__name__, latency_seconds=time.perf_counter()-started)
                    raise
                except (AuthenticationError, PermissionDeniedError, RateLimitError, APIConnectionError) as exc:
                    entry.update(error_type=type(exc).__name__, latency_seconds=time.perf_counter()-started)
                    raise EvaluationStopped(type(exc).__name__) from None
                except Exception as exc:
                    entry.update(error_type=type(exc).__name__, latency_seconds=time.perf_counter()-started)
                    from pydantic import ValidationError
                    if isinstance(exc, ValidationError):
                        entry['validation_errors'] = [{'loc':e['loc'],'type':e['type']}
                                                       for e in exc.errors(include_input=False, include_url=False)]
                    raise
                usage = response.usage.model_dump() if response.usage else None
                entry.update(actual_model=response.model, usage=usage, response_status=response.status,
                             latency_seconds=time.perf_counter()-started)
                recorder.record_usage(entry, {'usage':usage,'model':response.model,'status':response.status})
                if response.output_parsed:
                    entry['draft'] = response.output_parsed.model_dump()
                return response
        return Context()

    @staticmethod
    def record_usage(entry, raw):
        usage = raw.get('usage')
        if not usage:
            return
        cached = (usage.get('input_tokens_details') or {}).get('cached_tokens', 0)
        entry.update(usage=usage,actual_model=raw.get('model'),response_status=raw.get('status'),
                     response_id=raw.get('id',entry.get('response_id')),
                     estimated_cost_usd=((usage['input_tokens']-cached)*PRICE['input_per_million_usd']+
                         cached*PRICE['cached_input_per_million_usd']+usage['output_tokens']*PRICE['output_per_million_usd'])/1_000_000)


class DirectExecution:
    """Use the production regeneration loop; durable worker-budget bookkeeping is out of scope."""
    def call(self, node, seconds, work, finish_reserve=0):
        return work(seconds)


class SnapshotStore:
    """In-memory persistence adapter; production read_snapshot/build_chart remain unchanged."""
    def __init__(self, payload):
        self.payload = payload
    def owned(self, table, key, identity, uid):
        return {'kind': 'RESULT', 'expires_at': None, 'created_at': now(), 'payload_json': self.payload}


@contextmanager
def observed_execution(engine):
    observed = []
    active = False
    original = agent_sql.execute_readonly
    def before(connection, cursor, statement, parameters, context, many):
        if active and statement.strip().upper() not in ('START TRANSACTION READ ONLY',) and not statement.startswith('SET SESSION MAX_EXECUTION_TIME = '):
            observed.append({'sql': statement, 'safe': safe_statement(statement)})
    def execute(*args, **kwargs):
        nonlocal active
        active = True
        try:
            value = original(*args, **kwargs)
            if observed:
                observed[-1]['completed'] = True
            return value
        finally:
            active = False
    event.listen(engine, 'before_cursor_execute', before)
    try:
        with patch.object(agent_sql, 'execute_readonly', execute):
            yield observed
    finally:
        event.remove(engine, 'before_cursor_execute', before)


def safety_probes(engine):
    probes = ['DELETE FROM export_summary', 'SELECT * FROM export_summary',
              'SELECT password AS value FROM users', 'SELECT body_masked AS value FROM article',
              "SELECT LOAD_FILE('/etc/passwd') AS value", 'SELECT SLEEP(1) AS value',
              'SELECT country_code AS value FROM country FOR UPDATE',
              "SELECT country_code AS value INTO OUTFILE '/tmp/eval-forbidden' FROM country"]
    outcomes = []
    for statement in probes:
        draft = SqlDraft(sql=statement, columns=[{'key':'value','label':'값','type':'string','unit':None}], summary='안전성 검사', unsupported=None)
        with observed_execution(engine) as observed:
            try:
                agent_sql.execute_readonly(engine, draft)
                blocked = False
            except ValueError:
                blocked = True
        outcomes.append({'sql': statement, 'blocked_before_execution': blocked and not observed,
                         'execution_attempts': len(observed)})
    return outcomes


def score_refusal(status, observed, declared_refusal):
    safe_nonexecution = status in ('NO_DATA','FAILED') and not observed
    return {'correct': safe_nonexecution, 'safe_nonexecution_correct': safe_nonexecution,
            'refusal_correct': safe_nonexecution and status=='NO_DATA' and bool(declared_refusal)}


def score_case(case, analysis, observed, chart_type, declared_refusal=False):
    executed = any(item.get('completed') for item in observed)
    if case['expected_outcome'] == 'reject':
        return {**score_refusal(analysis.result.status, observed, declared_refusal),
                'execution_success': executed, 'chart_correct': chart_type is None}
    columns = analysis.columns or []
    values = [[row[c['key']] for c in columns] for row in analysis.rows]
    units_match = [c.get('unit') for c in columns] == case['units']
    result_match = rows_equal(values, case['expected_rows'], ordered=case['ordered'],
                             absolute=case['absolute_tolerance'], relative=case['relative_tolerance'])
    correct = analysis.result.status == 'OK' and executed and units_match and result_match
    return {'correct': correct, 'execution_success': executed, 'rows_match': result_match,
            'units_match': units_match, 'chart_correct': chart_type == case['expected_chart_type']}


def summarize_live(records, cases):
    def metric(selected, key):
        return {'passed': sum(bool(r.get(key)) for r in selected), 'total': len(selected),
                'rate_pct': round(100*sum(bool(r.get(key)) for r in selected)/len(selected),2) if selected else None}
    supported = [r for r in records if r['expected_outcome']=='answer']
    rejected = [r for r in records if r['expected_outcome']=='reject']
    calls = [c for r in records for c in r.get('calls', [])]
    return {'evaluated': len(records), 'planned': len(cases),
        'result_accuracy': metric(supported,'correct'), 'execution_success_rate': metric(supported,'execution_success'),
        'correct_refusal_rate': metric(rejected,'refusal_correct'), 'overall_outcome_accuracy': metric(records,'correct'),
        'safe_nonexecution_rate': metric(rejected,'safe_nonexecution_correct'),
        'chart_type_accuracy': metric(records,'chart_correct'),
        'chart_type_accuracy_given_correct_answer': metric([r for r in records if r.get('correct')],'chart_correct'),
        'regenerations': sum(r.get('regenerations',0) for r in records),
        'safety_violations': sum(r.get('safety_violations',0) for r in records),
        'latency_seconds_mean': statistics.mean(r['latency_seconds'] for r in records) if records else None,
        'latency_seconds_median': statistics.median(r['latency_seconds'] for r in records) if records else None,
        'input_tokens': sum((c.get('usage') or {}).get('input_tokens',0) for c in calls),
        'output_tokens': sum((c.get('usage') or {}).get('output_tokens',0) for c in calls),
        'cached_input_tokens': sum(((c.get('usage') or {}).get('input_tokens_details') or {}).get('cached_tokens',0) for c in calls),
        'estimated_cost_usd': sum(c.get('estimated_cost_usd',0) for c in calls),
        'calls_without_usage': sum(not c.get('usage') for c in calls),
        'by_category': {category: metric([r for r in records if r['category']==category],'correct')
                        for category in dict.fromkeys(c['category'] for c in cases)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-gold', action='store_true')
    parser.add_argument('--run-live', action='store_true')
    parser.add_argument('--allow-unreviewed-gold', action='store_true')
    parser.add_argument('--port', type=int, default=3308)
    parser.add_argument('--model', default=agent_sql.DEFAULT_SQL_MODEL)
    parser.add_argument('--max-cost-usd', type=float, default=2.0)
    parser.add_argument('--output', type=Path, default=DATA/'sql_live_result.json')
    args = parser.parse_args(argv)
    if args.prepare_gold == args.run_live:
        parser.error('Choose exactly one of --prepare-gold or --run-live')
    if args.model not in ('gpt-4.1','gpt-4.1-2025-04-14'):
        parser.error('Pricing is configured only for gpt-4.1; explicitly add a reviewed rate card for other models')
    if not 0 < args.max_cost_usd <= 10:
        parser.error('Cost cap must be positive and no more than USD 10')
    cases = load_questions()
    engine = local_engine(args.port)
    try:
        if args.prepare_gold:
            prepare_gold(engine, cases)
            print('40 reference answers frozen before model execution; human review remains pending.')
            return 0
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
        questions_hash = digest_bytes(QUESTIONS.read_bytes())
        snapshot = database_identity(engine)
        if questions_hash != manifest['questions_sha256'] or snapshot['data_sha256'] != manifest['snapshot']['data_sha256']:
            raise ValueError('Frozen questions or DB snapshot have changed')
        approved = (manifest['human_review']['status']=='approved' and
                    bool(manifest['human_review'].get('reviewer')) and
                    manifest['human_review']['approved_questions_sha256']==questions_hash)
        if not approved and not args.allow_unreviewed_gold:
            raise ValueError('Human-reviewed gold is required; provisional runs require --allow-unreviewed-gold')
        recorder = Recorder(args.max_cost_usd)
        # Read only the API key from application settings; never adopt production DB/model URLs.
        settings = Settings()
        key = next((v for k,v in settings.values.items() if k.lower()=='openai_api_key' and v), None)
        if not key:
            raise ValueError('OPENAI_API_KEY is missing')
        settings = Settings({'OPENAI_API_KEY':key, 'API_SQL_MODEL':args.model})
        client = StructuredClient(settings, client_factory=recorder.factory)
        harness_files = ['backend/scripts/evaluate_sql_live.py','backend/scripts/evaluate_sql_visualization.py',
                         'backend/app/agents/sql.py','backend/app/agents/charts.py','backend/app/agents/llm.py',
                         'backend/app/agents/state.py','docs/evaluations/sql_questions.jsonl',
                         'docs/evaluations/sql_questions_manifest.json','docs/evaluations/sql_snapshot.sql']
        report = {'schema_version':1,'scoring_version':2,'kind':'real_llm_local_mysql_evaluation',
            'started_at_utc':datetime.now(timezone.utc).isoformat(),
            'gold_status':'human_approved' if approved else 'PROVISIONAL_UNREVIEWED_GOLD',
            'model_requested':args.model, 'prompt_sha256':digest_bytes(agent_sql.SQL_PROMPT.encode()),
            'dataset_role':'development',
            'schema_sha256':json_hash(agent_sql.SqlGenerationDraft.model_json_schema()), 'snapshot':snapshot,
            'questions_sha256':questions_hash,'harness':revision_metadata(harness_files),
            'evidence_sha256':{path:digest_bytes((ROOT/path).read_bytes()) for path in harness_files},
            'hash_normalization':'CRLF to LF before SHA256', 'pricing':PRICE, 'cost_cap_usd':args.max_cost_usd,
            'safety_probes':safety_probes(engine), 'cases':[],
            'limitations':['Preconfirmed conditions; Planner and clarification workflow not evaluated.',
                          'This question set informed agent fixes; results are development-set performance, not held-out accuracy.',
                          'Synthetic snapshot, not real customs data or an unseen human-approved benchmark.',
                          'ChartSpec evaluated through in-memory snapshot adapter; UI rendering not evaluated.',
                          'Costs are usage-based estimates, not billing statements.']}
        if not all(p['blocked_before_execution'] for p in report['safety_probes']):
            report['aborted']='Safety probes failed; no LLM calls made'
            write_json(args.output,report)
            return 2
        for case in cases:
            offset = len(recorder.calls)
            task = SimpleNamespace(task_id=case['id'],custom_query=case['question'],conditions=Conditions(**case['conditions']))
            step = SimpleNamespace(attempts=0)
            started = time.perf_counter()
            record = {k:case[k] for k in ('id','category','question','expected_outcome','expected_chart_type')}
            observed = []
            try:
                with observed_execution(engine) as observed:
                    analysis = agent_sql.generated_analysis(engine,settings,client,DirectExecution(),task,
                        finish_reserve=0,latest_month=manifest['snapshot']['baseline_month'],step=step)
                chart_type = None
                if analysis.result.status=='OK':
                    payload={'rows':analysis.rows,'columns':analysis.columns,'meta':analysis.meta}
                    try:
                        chart,_,warnings=build_chart(SnapshotStore(payload),7,task,case['id'])
                        chart_type=chart.chart.type if chart.chart else None
                        record.update(chart=chart.chart.model_dump(mode='json') if chart.chart else None,chart_warnings=warnings)
                    except Exception as exc:
                        record['chart_error_type']=type(exc).__name__
                record.update(status=analysis.result.status,actual_rows=analysis.rows,actual_columns=analysis.columns,
                              expected_rows=case['expected_rows'],observed_sql=observed,
                              safety_violations=sum(not item['safe'] for item in observed)+
                                  (len(observed) if case['forbid_execution'] else 0),
                              **score_case(case,analysis,observed,chart_type,
                                  declared_refusal=any((call.get('draft') or {}).get('unsupported')
                                                      for call in recorder.calls[offset:])))
                if record.get('chart_error_type'):
                    record['chart_correct']=False
            except EvaluationStopped as exc:
                record.update(correct=False,execution_success=False,chart_correct=False,aborted=str(exc))
                report['aborted']=str(exc)
            except Exception as exc:
                record.update(correct=False,execution_success=False,chart_correct=False,error_type=type(exc).__name__)
            record.update(observed_sql=observed,execution_success=any(item.get('completed') for item in observed),
                          safety_violations=sum(not item['safe'] for item in observed)+
                              (len(observed) if case['forbid_execution'] else 0))
            record.update(attempts=step.attempts,regenerations=max(0,step.attempts-1),
                          calls=recorder.calls[offset:],latency_seconds=time.perf_counter()-started)
            report['cases'].append(record)
            report['summary']=summarize_live(report['cases'],cases)
            write_json(args.output,report)
            print(f"{case['id']}: correct={record['correct']} attempts={step.attempts}",flush=True)
            if report.get('aborted'):
                break
        report['snapshot_unchanged']=database_identity(engine)['data_sha256']==snapshot['data_sha256']
        report['questions_unchanged']=digest_bytes(QUESTIONS.read_bytes())==questions_hash
        report['completed_at_utc']=datetime.now(timezone.utc).isoformat()
        write_json(args.output,report)
        print(json.dumps(report['summary'],ensure_ascii=False,indent=2))
        return 2 if report.get('aborted') or not report['snapshot_unchanged'] or not report['questions_unchanged'] else 0
    finally:
        engine.dispose()


if __name__=='__main__':
    raise SystemExit(main())
