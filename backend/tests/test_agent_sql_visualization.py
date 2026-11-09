"""SQL/visualization contracts, using stored fixtures and SQLite only."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from backend.app.agents.charts import build_chart, time_series
from backend.app.agents.sql import execute_readonly, normalize_rows, generated_analysis
from backend.app.agents.state import Conditions, SqlDraft
from backend.app.agents.templates import run_template
from backend.app.api import results
from backend.app.api.common import ApiError, now, shift_month
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.tests.api_support import test_engine, reference_data


def task(**conditions):
    return SimpleNamespace(task_id='t1', conditions=Conditions(
        **{'period': {'start': '2025-01-01', 'end': '2025-03-01'}, **conditions}))


def trend(code, amounts, *, name=None):
    total = sum(value for value in amounts.values() if value is not None)
    return [{'country_code': code, 'country_name': name or code, 'hs_code': None,
             'hs_ver': None, 'name': '전체 품목', 'month': month, 'amount_usd': value,
             'total_usd': total, 'verified_subtotal_usd': total,
             'requested_months': 3, 'compare_requested_months': 0}
            for month, value in amounts.items()]


def chart(kind, rows, *, current=None, columns=None, meta=None):
    snapshot = {'meta': {'kind': kind, **(meta or {})}, 'rows': rows, 'columns': columns or []}
    original = deepcopy(snapshot)
    with patch('backend.app.agents.charts.read_snapshot', return_value=snapshot) as read:
        result = build_chart(None, 7, current or task(), 'saved-result')
    read.assert_called_once_with(None, 7, 'saved-result', 'RESULT')
    if snapshot != original:
        raise AssertionError('Chart generation mutated the saved result')
    return result


class ChartContractTest(unittest.TestCase):
    def test_trends_align_missing_months_on_shared_calendar(self):
        rows = (trend('US', {'2025-03': 30, '2025-01': 10})
                + trend('CN', {'2025-02': 20, '2025-01': 5}))
        result, full, _ = chart('TREND', rows)
        self.assertEqual(result.chart.labels, ['2025-01', '2025-02', '2025-03'])
        self.assertEqual(full, result.chart.labels)
        self.assertEqual([s.values for s in result.chart.series], [[10, None, 30], [5, 20, None]])

    def test_top_five_trends_are_chosen_by_amount_not_storage_order(self):
        codes = ['US', 'CN', 'JP', 'DE', 'FR', 'GB']
        rows = [row for i, code in enumerate(codes, 1)
                for row in trend(code, {'2025-01': i, '2025-02': i, '2025-03': i})]
        result, _, warnings = chart('TREND', rows)
        self.assertEqual([s.name for s in result.chart.series], list(reversed(codes[1:])))
        self.assertEqual(result.completeness, 'COMPLETE')
        self.assertTrue(warnings)

    def test_share_names_that_shorten_to_same_label_keep_country_identity(self):
        names = ['가' * 25 + middle + '나' * 25 for middle in ('첫째', '둘째')]
        rows = [{'country_code': code, 'label': name, 'share_pct': amount}
                for code, name, amount in zip(('US', 'CN'), names, (60, 40))]
        result, full, _ = chart('SHARE', rows)
        self.assertEqual(full, names)
        self.assertEqual(len(set(result.chart.labels)), 2)
        self.assertTrue(all(len(label) <= 40 for label in result.chart.labels))
        self.assertIn('US', result.chart.labels[0])
        self.assertEqual(result.chart.series[0].values, [60, 40])

    def test_ranking_retains_requested_top_and_reports_chart_limit(self):
        rows = [{'name': f'대상{i}', 'country_code': f'C{i}', 'rank': i,
                 'export_amount_usd': 1000-i} for i in range(1, 66)]
        for size in (12, 65):
            with self.subTest(size=size):
                result, full, warnings = chart('RANKING', rows[:size], meta={'requested_top': size})
                self.assertEqual(len(result.chart.labels), min(size, 60))
                self.assertEqual(len(full), min(size, 60))
                self.assertEqual(bool(warnings), size > 60)

    def test_llm_dates_are_sorted_with_values_and_nulls(self):
        columns = [{'key': 'month', 'label': '월', 'type': 'string', 'unit': None},
                   {'key': 'amount', 'label': '금액', 'type': 'number', 'unit': 'USD'}]
        rows = [{'month': '2025-03', 'amount': 30}, {'month': '2025-01', 'amount': 10},
                {'month': '2025-02', 'amount': None}]
        result, full, _ = chart('LLM_SQL', rows, columns=columns)
        self.assertEqual(result.chart.type, 'line')
        self.assertEqual(full, ['2025-01', '2025-02', '2025-03'])
        self.assertEqual(result.chart.series[0].values, [10, None, 30])

    def test_invalid_or_mixed_date_granularity_does_not_make_time_axis(self):
        columns = [{'key': 'day', 'label': '날짜', 'type': 'string', 'unit': None},
                   {'key': 'value', 'label': '값', 'type': 'number', 'unit': 'COUNT'}]
        for labels in (['2025-02-30'], ['2025-13'], ['2025-01', '2025-01-02']):
            with self.subTest(labels=labels):
                result, _, _ = chart('LLM_SQL', [{'day': label, 'value': 1} for label in labels], columns=columns)
                self.assertEqual(result.status, 'OK')
                self.assertIsNone(result.chart)

    def test_long_trend_aggregation_identifies_actual_partial_period(self):
        rows = trend('US', {f'{2020+i//12}-{i%12+1:02d}': 1 for i in range(1, 62)})
        current = task(period={'start': '2020-02-01', 'end': '2025-02-01'})
        result, _, warnings = chart('TREND', rows, current=current)
        self.assertEqual(result.chart.granularity, 'QUARTER')
        self.assertIn('분기', result.chart.title)
        self.assertEqual(result.chart.series[0].values[0], 2)
        self.assertTrue(any('2020-02' in w and '2025-02' in w for w in warnings))

    def test_calendar_boundaries_and_missing_months_survive_aggregation(self):
        for size, expected in ((60, 'MONTH'), (61, 'QUARTER'), (180, 'QUARTER'),
                               (181, 'YEAR'), (720, 'YEAR')):
            with self.subTest(size=size):
                calendar = [shift_month(date(2000, 1, 1), i).strftime('%Y-%m') for i in range(size)]
                rows = [{'month': month, 'amount_usd': 1} for month in calendar if month != calendar[1]]
                interval, labels, values = time_series(rows, ['amount_usd'], calendar)
                self.assertEqual(interval, expected)
                self.assertLessEqual(len(labels), 60)
                self.assertIsNone(values[0][1 if expected == 'MONTH' else 0])
        calendar = [f'{year}-01' for year in range(2000, 2061)]
        self.assertIsNone(time_series([], ['amount_usd'], calendar))

    def test_country_product_names_remain_unique_after_shared_shortener(self):
        rows = []
        for i, code in enumerate(('US', 'CN'), 1):
            for row in trend(code, {'2025-01': i, '2025-02': i, '2025-03': i}, name='같은 국가명'):
                rows.append({**row, 'hs_code': '330499', 'hs_ver': 'HS2022', 'name': '같은 품목명'})
        result, _, _ = chart('TREND', rows)
        names = [s.name for s in result.chart.series]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(len(name) <= 40 for name in names))
        self.assertIn('CN', names[0])
        self.assertIn('US', names[1])

    def test_stored_chart_calendar_limits_and_missing_months(self):
        # Exercise the public entry point so historical implementations use the same input contract.
        for size, expected in ((60, 'MONTH'), (61, 'QUARTER'), (180, 'QUARTER'),
                               (181, 'YEAR'), (720, 'YEAR'), (732, None)):
            with self.subTest(size=size):
                start = date(2000, 1, 1)
                end = shift_month(start, size - 1)
                calendar = [shift_month(start, i).strftime('%Y-%m') for i in range(size)]
                rows = trend('US', {month: 1 for i, month in enumerate(calendar) if i != 1})
                current = task(period={'start': start, 'end': end})
                result, _, warnings = chart('TREND', rows, current=current)
                if expected is None:
                    self.assertIsNone(result.chart)
                    self.assertTrue(warnings)
                else:
                    self.assertEqual(result.chart.granularity, expected)
                    self.assertLessEqual(len(result.chart.labels), 60)
                    self.assertIsNone(result.chart.series[0].values[1 if expected == 'MONTH' else 0])

    def test_comparison_quarters_preserve_missing_values_in_each_period(self):
        calendar = [shift_month(date(2020, 1, 1), i).strftime('%Y-%m') for i in range(61)]
        rows = trend('US', {month: None if i == 1 else 1 for i, month in enumerate(calendar)})
        for i, row in enumerate(rows):
            row.update(requested_months=61, compare_requested_months=61,
                       compare_amount_usd=None if i == 4 else 2)
        current = task(period={'start': '2020-01-01', 'end': '2025-01-01'},
                       compare_period={'start': '2010-01-01', 'end': '2015-01-01'})
        result, full, _ = chart('TREND', rows, current=current)
        self.assertEqual((result.chart.type, result.chart.granularity), ('line', 'QUARTER'))
        self.assertEqual((full[0], full[-1], len(full)), ('2020-Q1', '2025-Q1', 21))
        self.assertEqual(result.chart.series[0].values, [None] + [3] * 19 + [1])
        self.assertEqual(result.chart.series[1].values, [6, None] + [6] * 18 + [2])

    def test_partial_totals_use_subtotals_and_preserve_tied_order(self):
        codes = ['VN', 'US', 'JP', 'CN', 'TH', 'DE', 'AU']
        rows = []
        for code in codes:
            for row in trend(code, {'2025-01': 50 if code == 'VN' else 100,
                                    '2025-02': None, '2025-03': 0}):
                rows.append({**row, 'total_usd': None})
        result, _, warnings = chart('TREND', rows)
        self.assertEqual([s.name for s in result.chart.series], codes[1:6])
        self.assertTrue(all(s.values == [100, None, 0] for s in result.chart.series))
        self.assertTrue(warnings)

    def test_disambiguated_names_do_not_collide_with_existing_literal_name(self):
        rows = [{'country_code': code, 'label': name, 'share_pct': amount}
                for code, name, amount in [('US', '동일명', 30), ('CN', '동일명', 20),
                                           ('JP', '동일명(US)', 50)]]
        result, full, _ = chart('SHARE', rows)
        self.assertEqual(len(set(result.chart.labels)), 3)
        self.assertTrue(all(len(label) <= 40 for label in result.chart.labels))
        self.assertEqual(full, ['동일명', '동일명', '동일명(US)'])
        self.assertEqual(result.chart.series[0].values, [30, 20, 50])

    def test_incompatible_freeform_results_keep_table_without_chart(self):
        columns = [{'key': 'month', 'label': '월', 'type': 'string', 'unit': None},
                   {'key': 'value', 'label': '값', 'type': 'number', 'unit': 'USD'}]
        for rows, schema in (
            ([{'month': str(i), 'value': i} for i in range(61)], columns),
            ([{'month': '2025-01', 'value': 1}, {'month': '2025-01', 'value': 2}], columns),
            ([{'month': '2025-01', 'value': 1, 'ratio': 50}], columns + [
                {'key': 'ratio', 'label': '비율', 'type': 'number', 'unit': '%'}]),
        ):
            with self.subTest(rows=len(rows), columns=len(schema)):
                result, _, _ = chart('LLM_SQL', rows, columns=schema)
                self.assertEqual((result.status, result.result_id, result.chart), ('OK', 'saved-result', None))

    def test_ml_selection_uses_saved_pairs_and_actual_exports(self):
        rows = [{'country_code': code, 'hs6': hs, 'hs_ver': 'HS2022', 'name': hs,
                 'country_rank': 1, 'product_rank': 2, 'exp_12m': amount, 'growth_pct': 99}
                for code, hs, amount in (('US', '330499', 100), ('CN', '150910', 200))]
        snapshot = {'rows': rows, 'meta': {'kind': 'PREDICTION', 'group_by': 'PAIR'}}
        with patch('backend.app.agents.charts.read_snapshot', return_value=snapshot):
            result, _, _ = build_chart(None, 7, task(), 'saved-result',
                {'country_code': 'CN', 'hs_code': '150910', 'hs_ver': 'HS2022'})
            self.assertEqual(result.chart.series[0].values, [200])
            self.assertEqual(result.chart.unit, 'USD')
            self.assertEqual(result.chart_selector.result_id, result.chart.result_id)
            for selection in ({'country_code': 'US', 'hs_code': '150910', 'hs_ver': 'HS2022'},
                              {'country_code': 'US'},
                              {'country_code': 'US', 'hs_code': '330499', 'hs_ver': 'HS2017'}):
                with self.subTest(selection=selection), self.assertRaises(ApiError) as caught:
                    build_chart(None, 7, task(), 'saved-result', selection)
                self.assertEqual(caught.exception.status, 400)


class SqlBoundaryTest(unittest.TestCase):
    @staticmethod
    def draft():
        return SqlDraft(sql='SELECT 1 AS value', columns=[
            {'key': 'value', 'label': '값', 'type': 'number', 'unit': 'COUNT'}],
            summary='조회', unsupported=None)

    def test_nonfinite_decimal_and_oversized_numeric_values_are_validation_errors(self):
        for value in (Decimal('Infinity'), Decimal('-Infinity'), Decimal('NaN'),
                      Decimal('sNaN'), 10 ** 400):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                normalize_rows([{'value': value}], self.draft().columns)

    def test_submillisecond_timeout_never_disables_mysql_timeout(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execution_options.return_value = connection
        connection.exec_driver_sql.return_value.mappings.return_value.fetchmany.return_value = [{'value': 1}]
        execute_readonly(engine, self.draft(), timeout_seconds=0.0005)
        statement = connection.exec_driver_sql.call_args_list[0].args[0]
        self.assertEqual(statement, 'SET SESSION MAX_EXECUTION_TIME = 1')

    def test_nonfinite_database_number_can_regenerate_and_cleanup_connections(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execution_options.return_value = connection
        connection.exec_driver_sql.return_value.mappings.return_value.fetchmany.side_effect = [
            [{'value': Decimal('Infinity')}], [{'value': 42}]]
        client = MagicMock()
        client.parse.return_value = self.draft()
        execution = MagicMock()
        execution.call.side_effect = lambda node, seconds, work, finish_reserve: work(seconds)
        current = task()
        current.custom_query = '조회'
        step = SimpleNamespace(attempts=0)
        with patch('backend.app.agents.sql.schema_for', return_value=[]):
            analysis = generated_analysis(engine, Settings({}), client, execution, current,
                finish_reserve=40, latest_month='2025-03', step=step)
        self.assertEqual(analysis.rows, [{'value': 42}])
        self.assertEqual(step.attempts, 2)
        self.assertEqual(connection.rollback.call_count, 2)
        self.assertEqual(connection.invalidate.call_count, 2)
        self.assertIn('숫자', client.parse.call_args.kwargs['payload']['previous_error'])

    def test_no_rows_and_unsupported_requests_never_regenerate(self):
        for unsupported in (None, '지원하지 않는 계산입니다.'):
            with self.subTest(unsupported=unsupported):
                client = MagicMock()
                client.parse.return_value = self.draft().model_copy(update={'unsupported': unsupported})
                execution = MagicMock()
                execution.call.side_effect = lambda node, seconds, work, finish_reserve: work(seconds)
                current = task()
                current.custom_query = '조회'
                with patch('backend.app.agents.sql.schema_for', return_value=[]), \
                     patch('backend.app.agents.sql.execute_readonly', return_value=([], 'SELECT 1 AS value')) as execute:
                    analysis = generated_analysis(None, Settings({}), client, execution, current,
                        finish_reserve=40, latest_month='2025-03')
                self.assertEqual((analysis.result.status, analysis.rows, analysis.result.digest), ('NO_DATA', [], None))
                self.assertEqual(client.parse.call_count, 1)
                self.assertEqual(execute.call_count, 0 if unsupported else 1)


class StoredSqlChartTest(unittest.TestCase):
    def setUp(self):
        engine, self.meta = test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine, self.meta)
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.store = Store(Database(engine), self.connection)

    def save(self, kind, current):
        analysis = run_template(self.store, 7, current, SimpleNamespace(args={'template': kind}))
        saved = results.result_table(self.store, 7, analysis.rows, analysis.meta,
            analysis.conditions, sql=analysis.sql, columns=analysis.columns)
        return analysis, saved['result_id']

    def test_template_to_stored_chart_uses_same_values_without_requery(self):
        for flow in ('EXPORT', 'IMPORT'):
            for kind, expected in (('TREND', 'line'), ('RANKING', 'bar'), ('SHARE', 'donut')):
                with self.subTest(flow=flow, kind=kind):
                    current = task(trade_flow=flow, ranking_group='COUNTRY', share_basis='KOREA_TRADE_PARTNER')
                    analysis, identity = self.save(kind, current)
                    with patch('backend.app.api.trade.aggregate', side_effect=AssertionError('SQL re-executed')):
                        result, _, _ = build_chart(self.store, 7, current, identity)
                    self.assertEqual((result.result_id, result.chart.result_id), (identity, identity))
                    self.assertEqual((result.chart.type, result.chart.task_id), (expected, current.task_id))
                    key = ('amount_usd' if kind == 'TREND' else 'share_pct' if kind == 'SHARE'
                           else 'export_amount_usd' if flow == 'EXPORT' else 'import_amount_usd')
                    self.assertEqual(result.chart.series[0].values, [float(r[key]) for r in analysis.rows])
                    self.assertEqual(results.read_snapshot(self.store, 7, identity, 'RESULT')['total_rows'], len(analysis.rows))

    def test_multiple_countries_keep_separate_series_from_query_to_chart(self):
        for flow in ('IMPORT', 'EXPORT'):
            with self.subTest(flow=flow):
                current = task(country_codes=['US', 'CN'], trade_flow=flow)
                analysis, identity = self.save('TREND', current)
                with patch('backend.app.api.trade.aggregate', side_effect=AssertionError('SQL re-executed')):
                    result, _, _ = build_chart(self.store, 7, current, identity)
                self.assertEqual(result.chart.type, 'line')
                self.assertEqual(result.chart.title, '수입액' if flow == 'IMPORT' else '수출액')
                self.assertEqual(result.chart.labels, ['2025-01', '2025-02', '2025-03'])
                self.assertEqual({s.name for s in result.chart.series}, {'미국', '중국'})
                for series in result.chart.series:
                    country_rows = sorted(
                        (r for r in analysis.rows if r['country_name'] == series.name),
                        key=lambda r: r['month'])
                    self.assertEqual(series.values, [float(r['amount_usd']) for r in country_rows])
                self.assertNotEqual(result.chart.series[0].values, result.chart.series[1].values)

    def test_tied_top_five_matches_stored_table_and_digest_order(self):
        from sqlalchemy import update
        codes = ['VN', 'US', 'JP', 'CN', 'TH', 'DE', 'AU']
        self.connection.execute(self.meta.tables['country'].insert(), [
            {'country_code': code, 'name_ko': code, 'country_role': 'COUNTRY'}
            for code in codes if code not in ('US', 'CN')])
        for flow, source in (('IMPORT', 'import_summary'), ('EXPORT', 'export_summary')):
            self.connection.execute(update(self.meta.tables[source]).values(amount_usd=0))
            for comparison in (None, {'start': '2024-01-01', 'end': '2024-03-01'}):
                with self.subTest(flow=flow, comparison=comparison):
                    current = task(country_codes=codes, trade_flow=flow, compare_period=comparison)
                    analysis, identity = self.save('TREND', current)
                    snapshot = results.read_snapshot(self.store, 7, identity, 'RESULT')
                    stored_codes = list(dict.fromkeys(r['country_code'] for r in snapshot['rows']))
                    self.assertEqual(stored_codes, codes)
                    self.assertEqual([t['country_code'] for t in analysis.result.digest['targets']], codes)
                    self.assertTrue(all(r['total_usd'] == 0 for r in snapshot['rows']))
                    names = {r['country_code']: r['country_name'] for r in snapshot['rows']}
                    result, _, warnings = build_chart(self.store, 7, current, identity)
                    actual = result.chart.labels if comparison else [s.name for s in result.chart.series]
                    self.assertEqual(actual, [names[code] for code in stored_codes[:5]])
                    self.assertTrue(warnings)

    def test_saved_chart_enforces_owner_and_expiration_without_requery(self):
        from sqlalchemy import update
        current = task()
        _, identity = self.save('TREND', current)
        with self.assertRaises(ApiError) as caught:
            build_chart(self.store, 8, current, identity)
        self.assertEqual((caught.exception.status, caught.exception.code), (404, 'NOT_FOUND'))
        table = self.meta.tables['api_snapshot']
        self.connection.execute(update(table).where(table.c.snapshot_id == identity).values(expires_at=now()))
        with patch('backend.app.api.trade.aggregate', side_effect=AssertionError('SQL re-executed')):
            with self.assertRaises(ApiError) as caught:
                build_chart(self.store, 7, current, identity)
        self.assertEqual((caught.exception.status, caught.exception.code), (410, 'RESULT_EXPIRED'))

    def test_unequal_comparison_preserves_partial_data_and_monthly_average(self):
        current = task(country_codes=['US'], compare_period={'start': '2023-12-01', 'end': '2024-01-01'})
        analysis, identity = self.save('TREND', current)
        result, _, warnings = build_chart(self.store, 7, current, identity)
        self.assertEqual(analysis.result.completeness, 'PARTIAL')
        self.assertIsNone(analysis.rows[0]['compare_total_usd'])
        self.assertEqual(result.chart.type, 'grouped_bar')
        self.assertEqual([s.values for s in result.chart.series], [[1_000_000], [1_130_000]])
        self.assertTrue(any('월평균' in w for w in warnings))


if __name__ == '__main__':
    unittest.main()
