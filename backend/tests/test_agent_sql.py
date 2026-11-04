import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from backend.app.agents.sql import validate_sql, normalize_rows, execute_readonly
from backend.app.agents.state import SqlDraft
from backend.app.agents.state import Conditions
from backend.app.agents.sql import generated_analysis
from backend.app.api.config import Settings
from backend.app.api.common import ApiError


def draft(sql, kind='string'):
    return SqlDraft(sql=sql, columns=[{'key': 'value', 'label': '결과', 'type': kind, 'unit': None}],
                    summary='조회', unsupported=None)


class SqlTest(unittest.TestCase):
    def test_two_regenerations_then_failure_contains_guidance_and_no_numbers(self):
        task=SimpleNamespace(custom_query='수출 통계',conditions=Conditions(period={'start':'2025-01-01','end':'2025-03-01'}))
        step=SimpleNamespace(attempts=0)
        client=MagicMock()
        client.parse.return_value=draft('DELETE FROM country')
        ledger=MagicMock()
        ledger.call.side_effect=lambda node,timeout,fn,finish_reserve:fn(timeout)
        engine=MagicMock()
        with patch('backend.app.agents.sql.schema_for',return_value=[]):
            result=generated_analysis(engine,Settings({}),client,ledger,task,finish_reserve=40,
                                      latest_month='2025-03',step=step)
        previous=[c.kwargs['payload']['previous_error'] for c in client.parse.call_args_list]
        self.assertEqual(len(previous),3)
        self.assertIsNone(previous[0])
        self.assertTrue(all(previous[1:]))
        self.assertEqual(step.attempts,3)
        engine.connect.assert_not_called()
        self.assertEqual((result.result.status,result.rows,result.result.digest),('FAILED',[],None))
        self.assertIn('좁혀',result.result.error)
        self.assertNotRegex(result.result.error,r'\d')

    def test_failed_draft_regenerated_with_separate_reservation(self):
        task=SimpleNamespace(custom_query='국가별 조회',conditions=Conditions(period={'start':'2025-01-01','end':'2025-03-01'}))
        client=MagicMock(); client.parse.side_effect=[draft('DELETE FROM country'),draft('SELECT 1 AS value','number')]
        ledger=MagicMock(); ledger.call.side_effect=lambda node,timeout,fn,finish_reserve:fn(timeout)
        engine=MagicMock()
        connection=engine.connect.return_value.__enter__.return_value
        connection.execution_options.return_value=connection
        connection.exec_driver_sql.return_value.mappings.return_value.fetchmany.return_value=[{'value':1}]
        with patch('backend.app.agents.sql.schema_for',return_value=[]):
            result=generated_analysis(engine,Settings({}),client,ledger,task,finish_reserve=40,latest_month='2025-03')
        self.assertEqual(result.result.status,'OK')
        self.assertEqual(result.rows,[{'value':1}])
        self.assertEqual(ledger.call.call_count,2)
        self.assertEqual(client.parse.call_args.kwargs['model'],'gpt-4.1')
        self.assertTrue(client.parse.call_args.kwargs['payload']['previous_error'])
        self.assertEqual(engine.connect.call_count,1)

    def test_insufficient_time_does_not_call_sql_model(self):
        task=SimpleNamespace(custom_query='조회',conditions=Conditions(period={'start':'2025-01-01','end':'2025-03-01'}))
        client=MagicMock(); ledger=MagicMock(); ledger.call.side_effect=ApiError(409,'RUN_TIMEOUT','시간 부족')
        result=generated_analysis(None,Settings({}),client,ledger,task,finish_reserve=40,latest_month='2025-03')
        self.assertEqual(result.result.status,'FAILED'); client.parse.assert_not_called()

    def test_allowed_select_cte_union_and_limit(self):
        for sql in ['SELECT country_code AS value FROM country',
                    'WITH x AS (SELECT country_code FROM country) SELECT country_code AS value FROM x',
                    'SELECT country_code AS value FROM country UNION ALL SELECT country_code FROM country']:
            with self.subTest(sql=sql): self.assertIn('LIMIT 1000', validate_sql(draft(sql)))
        self.assertIn('LIMIT 1000', validate_sql(draft('SELECT name_ko AS value FROM country LIMIT 10000')))
        self.assertIn('LIMIT 5', validate_sql(draft('SELECT name_ko AS value FROM country LIMIT 5')))
        self.assertIn('COUNT(*)', validate_sql(draft('SELECT COUNT(*) AS value FROM article', 'number')))

    def test_rejects_private_tables_body_stars_side_effects_and_metadata(self):
        rejected = [
            'SELECT name AS value FROM users',
            'WITH country AS (SELECT name FROM users) SELECT name AS value FROM country',
            'SELECT table_name AS value FROM information_schema.tables',
            'SELECT body_masked AS value FROM article',
            'SELECT a.body_masked AS value FROM article a',
            'SELECT * FROM article', 'SELECT a.* FROM article a',
            'SELECT value FROM (SELECT * FROM article) a',
            'SELECT name_ko AS value FROM country FOR UPDATE',
            "SELECT name_ko AS value INTO OUTFILE '/tmp/out' FROM country",
            'SELECT SLEEP(10) AS value', 'SELECT BENCHMARK(1000,1) AS value',
            "SELECT LOAD_FILE('/etc/passwd') AS value", "SELECT GET_LOCK('x',1) AS value",
            'SELECT custom_udf() AS value', 'SELECT @@version AS value',
            'SELECT 1 AS value; DELETE FROM country', 'DELETE FROM country',
            'SELECT name_ko AS other FROM country', 'SELECT 1 AS value LIMIT -1',
            'SELECT /*+ SET_VAR(max_execution_time=0) */ 1 AS value',
        ]
        for sql in rejected:
            with self.subTest(sql=sql), self.assertRaises(ValueError): validate_sql(draft(sql))

    def test_numeric_values_are_finite_and_null_stays_null(self):
        columns = draft('SELECT 1 AS value', 'number').columns
        self.assertEqual(normalize_rows([{'value': Decimal('1.5')}, {'value': None}], columns),
                         [{'value': 1.5}, {'value': None}])
        for value in ('1.5', True, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_rows([{'value': value}], columns)

    def test_readonly_session_rollback_and_connection_disposal_even_on_error(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execution_options.return_value = connection
        connection.exec_driver_sql.return_value.mappings.return_value.fetchmany.return_value = [{'value': 1}]
        rows, sql = execute_readonly(engine, draft('SELECT 1 AS value', 'number'))
        self.assertEqual(rows, [{'value': 1}])
        # 생성 SQL은 파라미터 없이 보낸다(pymysql이 DATE_FORMAT·LIKE의 %를 포맷 문자로 읽지 않게)
        connection.execution_options.assert_called_once_with(no_parameters=True)
        statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
        self.assertEqual(statements[:2], ['SET SESSION MAX_EXECUTION_TIME = 30000', 'START TRANSACTION READ ONLY'])
        self.assertEqual(statements[2], sql)
        connection.rollback.assert_called_once()
        connection.invalidate.assert_called_once()
        connection.reset_mock()
        connection.exec_driver_sql.side_effect = RuntimeError('timeout')
        with self.assertRaises(RuntimeError): execute_readonly(engine, draft('SELECT 1 AS value', 'number'))
        connection.rollback.assert_called_once()
        connection.invalidate.assert_called_once()
