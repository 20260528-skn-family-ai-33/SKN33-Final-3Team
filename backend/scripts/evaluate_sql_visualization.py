"""Offline regression-based evaluation; no LLM or production DB accuracy claims.

Run from the repository root with python -m backend.scripts.evaluate_sql_visualization.
Each invocation evaluates one source revision in a fresh process. Historical runs
replace only sql.py/charts.py in memory; fixtures, tests and dependencies stay fixed.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ('backend/app/agents/sql.py', 'backend/app/agents/charts.py')
GROUPS = [
    ('SQL', 'backend.tests.test_agent_sql.SqlTest', [
        ('S01', '조건 반영', 'trade_sql_requires_confirmed_scope_for_each_branch', '국가·HS·판본·기간이 다른 SQL을 거부하고 확정 범위는 허용한다.'),
        ('S02', '조건 반영', 'grouping_by_country_requires_where_filter', '국가별 집계에 확정 국가 필터를 요구한다.'),
        ('S03', '분모 보존', 'product_share_retains_confirmed_basis_and_denominator', '품목 비중의 기준과 실제 분모 금액을 보존한다.'),
        ('S04', '실패 처리', 'two_regenerations_then_failure_contains_guidance_and_no_numbers', '두 번 재생성 후 실패 시 임의 수치 없이 안내한다.'),
        ('S05', '실패 처리', 'failed_draft_regenerated_with_separate_reservation', '잘못된 SQL은 별도 실행 예산으로 재생성한다.'),
        ('S06', '시간 제한', 'insufficient_time_does_not_call_sql_model', '예산이 부족하면 모델을 호출하지 않는다.'),
        ('S07', 'SQL 안전성', 'allowed_select_cte_union_and_limit', '허용 SELECT·CTE·UNION을 실행 가능하게 유지하고 행 수를 제한한다.'),
        ('S08', 'SQL 안전성', 'rejects_private_tables_body_stars_side_effects_and_metadata', '비공개 표·본문·변경·파일·잠금 SQL을 거부한다.'),
        ('S09', '숫자 검증', 'numeric_values_are_finite_and_null_stays_null', '유한한 숫자만 허용하고 null을 보존한다.'),
        ('S10', '연결 정리', 'readonly_session_rollback_and_connection_disposal_even_on_error', '읽기 전용으로 실행하고 오류가 나도 롤백·연결 폐기를 수행한다.'),
    ]),
    ('SQL', 'backend.tests.test_agent_sql_visualization.SqlBoundaryTest', [
        ('S11', '숫자 검증', 'nonfinite_decimal_and_oversized_numeric_values_are_validation_errors', '특수 Decimal과 과도한 정수를 일관된 검증 오류로 처리한다.'),
        ('S12', '시간 제한', 'submillisecond_timeout_never_disables_mysql_timeout', '1ms 미만 설정이 무제한 실행으로 바뀌지 않는다.'),
        ('S13', '실패 처리', 'nonfinite_database_number_can_regenerate_and_cleanup_connections', '잘못된 DB 숫자 이후 재생성에 성공하고 각 연결을 정리한다.'),
        ('S14', '실패 처리', 'no_rows_and_unsupported_requests_never_regenerate', '빈 결과와 미지원 요청을 불필요하게 재생성하지 않는다.'),
    ]),
    ('SQL', 'backend.tests.test_agent_templates.TemplateTest', [
        ('S15', '집계 정확성', 'different_length_comparison_keeps_subtotal_and_null_unverified_total', '불완전 기간의 총액은 null이고 월평균은 검증 월만 사용한다.'),
        ('S16', '집계 정확성', 'country_ranking_uses_full_total', '순위의 분모를 전체 금액으로 유지한다.'),
        ('S17', '집계 정확성', 'selected_country_outside_top_ten_keeps_global_denominator', '상위 10 밖 지정 국가도 표시하면서 전체 분모를 보존한다.'),
    ]),
    ('Visualization', 'backend.tests.test_agent_sql_visualization.ChartContractTest', [
        ('V01', '월 정렬', 'trends_align_missing_months_on_shared_calendar', '국가별 월 축을 맞추고 누락 월은 null로 둔다.'),
        ('V02', '대상 선정', 'top_five_trends_are_chosen_by_amount_not_storage_order', '금액 상위 5개를 선택한다.'),
        ('V03', '이름 구분', 'share_names_that_shorten_to_same_label_keep_country_identity', '축약된 비중 라벨이 겹쳐도 국가를 구분한다.'),
        ('V04', '표시 상한', 'ranking_retains_requested_top_and_reports_chart_limit', '요청된 순위를 최대 60개까지 표시하고 생략을 안내한다.'),
        ('V05', '날짜 정렬', 'llm_dates_are_sorted_with_values_and_nulls', '날짜와 해당 값을 함께 정렬한다.'),
        ('V06', '날짜 검증', 'invalid_or_mixed_date_granularity_does_not_make_time_axis', '잘못되거나 혼합된 날짜 단위로 시간축을 만들지 않는다.'),
        ('V07', '기간 집계', 'long_trend_aggregation_identifies_actual_partial_period', '분기 집계의 실제 포함 기간을 안내한다.'),
        ('V08', '기간 집계', 'stored_chart_calendar_limits_and_missing_months', '저장 결과로 차트를 만들 때 월·분기·연도 상한과 누락 월을 올바르게 처리한다.'),
        ('V09', '이름 구분', 'country_product_names_remain_unique_after_shared_shortener', '국가·품목 이름이 같아도 고유한 시리즈 이름을 만든다.'),
        ('V10', '기간 집계', 'comparison_quarters_preserve_missing_values_in_each_period', '조회·비교 기간의 누락값을 각 집계에 독립적으로 반영한다.'),
        ('V11', '대상 선정', 'partial_totals_use_subtotals_and_preserve_tied_order', '부분 집계는 검증 소계로 정렬하고 동률 순서를 유지한다.'),
        ('V12', '이름 구분', 'disambiguated_names_do_not_collide_with_existing_literal_name', '코드가 붙은 이름과 실제 이름도 충돌하지 않는다.'),
        ('V13', '표 유지', 'incompatible_freeform_results_keep_table_without_chart', '차트에 맞지 않는 결과는 원본 표 참조를 유지한다.'),
        ('V14', '저장값 사용', 'ml_selection_uses_saved_pairs_and_actual_exports', '저장된 ML 대상과 실제 수출액을 사용하고 잘못된 선택을 거부한다.'),
    ]),
    ('SQL→Visualization', 'backend.tests.test_agent_sql_visualization.StoredSqlChartTest', [
        ('I01', '결과 연결', 'template_to_stored_chart_uses_same_values_without_requery', '수입·수출의 추이·순위·비중은 저장값으로 그리고 재조회하지 않는다.'),
        ('I02', '다국가 비교', 'multiple_countries_keep_separate_series_from_query_to_chart', '미국·중국의 수입·수출 시리즈와 값을 각각 보존한다.'),
        ('I03', '동률 일치', 'tied_top_five_matches_stored_table_and_digest_order', '7개국 동률에서 표·요약·차트의 상위 대상 순서가 일치한다.'),
        ('I04', '접근 제어', 'saved_chart_enforces_owner_and_expiration_without_requery', '타인 결과와 만료된 결과를 재조회 없이 거부한다.'),
        ('I05', '기간 비교', 'unequal_comparison_preserves_partial_data_and_monthly_average', '불완전·상이한 길이의 기간은 저장된 월평균으로 비교하고 월평균 사용을 안내한다.'),
    ]),
]
CASES = [dict(id=identity, agent=agent, category=category, criterion=criterion,
              test=f'{test_class}.test_{method}')
         for agent, test_class, entries in GROUPS
         for identity, category, method, criterion in entries]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def reject_network(*args, **kwargs):
    raise RuntimeError('Network access is disabled during offline agent evaluation')


def install_historical_sources(revision):
    """Fresh-process-only substitution; never changes the checkout or Git index."""
    hashes = {}
    for path in SOURCES:
        source = subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT)
        hashes[path] = digest_bytes(source)
        name = path[:-3].replace('/', '.')
        parent_name, attribute = name.rsplit('.', 1)
        parent = importlib.import_module(parent_name)
        module = types.ModuleType(name)
        module.__package__ = parent_name
        module.__file__ = f'{revision}:{path}'
        sys.modules[name] = module
        setattr(parent, attribute, module)
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return hashes


def evaluate_case(case):
    suite = unittest.defaultTestLoader.loadTestsFromName(case['test'])
    result = unittest.TestResult()
    suite.run(result)
    if result.errors:
        status = 'ERROR'
    elif result.failures or result.unexpectedSuccesses:
        status = 'FAIL'
    elif result.skipped:
        status = 'SKIP'
    elif result.expectedFailures:
        status = 'XFAIL'
    elif result.testsRun != 1:
        status = 'ERROR'
    else:
        status = 'PASS'
    details = [message for _, message in result.errors + result.failures + result.expectedFailures]
    details.extend(reason for _, reason in result.skipped)
    if result.testsRun != 1:
        details.append(f'Expected one test method, ran {result.testsRun}.')
    return {**case, 'status': status, 'details': details}


def summarize(cases):
    summary = {}
    for agent in dict.fromkeys(case['agent'] for case in cases):
        group = [case for case in cases if case['agent'] == agent]
        counts = {status.lower(): sum(case['status'] == status for case in group)
                  for status in ('PASS', 'FAIL', 'ERROR', 'SKIP', 'XFAIL')}
        summary[agent] = {'total': len(group), **counts,
                          'pass_rate_pct': round(100 * counts['pass'] / len(group), 2)}
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-ref', help='Evaluate sql.py/charts.py from this local Git commit.')
    parser.add_argument('--output', type=Path, required=True, help='JSON evaluation report path.')
    args = parser.parse_args(argv)
    if args.source_ref and args.source_ref.startswith('-'):
        parser.error('--source-ref must be a Git revision, not an option')
    revision = git('rev-parse', '--verify', '--end-of-options',
                   (args.source_ref or 'HEAD') + '^{commit}')
    test_paths = sorted({case['test'].rsplit('.', 2)[0].replace('.', '/') + '.py' for case in CASES})
    fixture_paths = ['backend/scripts/evaluate_sql_visualization.py',
                     'backend/tests/api_support.py', 'backend/app/agents/templates.py',
                     'backend/app/agents/state.py', 'backend/app/api/results.py']
    with ExitStack() as stack:
        for owner, name in ((socket, 'create_connection'), (socket, 'getaddrinfo'),
                            (socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                            (socket.socket, 'sendto')):
            stack.enter_context(patch.object(owner, name, reject_network))
        source_hashes = (install_historical_sources(revision) if args.source_ref else
                         {path: digest_bytes((ROOT / path).read_bytes()) for path in SOURCES})
        outcomes = [evaluate_case(case) for case in CASES]
    report = {
        'schema_version': 1,
        'evaluation': 'SQL/Visualization offline regression contract evaluation',
        'evaluated_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_mode': 'historical_modules' if args.source_ref else 'working_tree',
        'source_revision': revision, 'harness_revision': git('rev-parse', 'HEAD'),
        'source_sha256': source_hashes,
        'evidence_sha256': {path: digest_bytes((ROOT / path).read_bytes())
                            for path in test_paths + fixture_paths},
        'manifest_sha256': digest_bytes(json.dumps(CASES, ensure_ascii=False, sort_keys=True).encode()),
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                        'packages': {name: version(name) for name in ('sqlglot', 'SQLAlchemy', 'pydantic')}},
        'network': 'blocked; local SQLite and mocks only',
        'scoring': 'One test method is one case; all subtests must pass. Skips/errors count as non-passes.',
        'limitations': ['Curated regression cases, not held-out natural-language questions.',
                        'LLM generation is mocked; no live model, RDS, latency, cost or browser evaluation.',
                        'Historical comparison swaps only sql.py/charts.py; current tests and fixtures are unchanged.'],
        'summary': summarize(outcomes), 'cases': outcomes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for agent, values in report['summary'].items():
        print(f"{agent}: {values['pass']}/{values['total']} ({values['pass_rate_pct']}%)")
    print(f'Report: {args.output}')
    return 0 if all(case['status'] == 'PASS' for case in outcomes) else 1


if __name__ == '__main__':
    raise SystemExit(main())
