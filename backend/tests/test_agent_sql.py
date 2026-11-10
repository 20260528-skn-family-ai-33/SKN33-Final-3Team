import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from backend.app.agents.sql import validate_sql, normalize_rows, execute_readonly
from backend.app.agents.state import SqlDraft
from backend.app.agents.state import Conditions
from backend.app.agents.sql import generated_analysis, SqlGenerationDraft
from backend.app.api.config import Settings
from backend.app.api.common import ApiError


def draft(sql, kind='string'):
    return SqlDraft(sql=sql, columns=[{'key': 'value', 'label': '결과', 'type': kind, 'unit': None}],
                    summary='조회', unsupported=None)


class SqlTest(unittest.TestCase):
    def test_or_filters_keep_confirmed_scope_without_rewriting_query(self):
        conditions = Conditions(country_codes=['US', 'CN'],
            hs_items=[{'hs_code':'3304','hs_ver':'HS2022'}, {'hs_code':'8542','hs_ver':'HS2022'}],
            period={'start':'2025-01-01','end':'2025-03-01'})
        base = ("SELECT SUM(e.amount_usd) AS value FROM export_summary e WHERE e.hs_ver='HS2022' "
                "AND e.trade_month BETWEEN '2025-01-01' AND '2025-03-31' AND {country} AND {product}")
        countries = "(e.country_code='US' OR 'CN'=e.country_code)"
        products = "(e.hs6_code LIKE '3304%' OR e.hs6_code LIKE '8542%')"
        for product in (products, "(e.hs6_code='330499' OR e.hs6_code IN ('854231'))"):
            result = validate_sql(draft(base.format(country=countries, product=product), 'number'), conditions)
            self.assertIn(' OR ', result)
        for country, product in [
            ("(e.country_code='US' OR 1=1)", products),
            ("(e.country_code='US' OR e.country_code='JP')", products),
            ("NOT (e.country_code='US' OR e.country_code='CN')", products),
            ("(e.country_code='US' OR x.country_code='CN')", products),
            (countries, "(e.hs6_code LIKE '3304%' OR e.hs6_code LIKE '15%')"),
            (countries, "(e.hs6_code LIKE '3304%' OR e.amount_usd>0)"),
        ]:
            with self.subTest(country=country, product=product), self.assertRaises(ValueError):
                validate_sql(draft(base.format(country=country, product=product), 'number'), conditions)
        grouped = base.format(country=countries, product=products).replace(
            'SUM(e.amount_usd) AS value', 'e.country_code AS value') + ' GROUP BY e.country_code'
        self.assertIn('GROUP BY', validate_sql(draft(grouped), conditions))

    def test_disjoint_period_or_requires_every_branch_to_be_bounded(self):
        conditions = Conditions(country_codes=['US'],
            period={'start':'2025-01-01','end':'2025-03-01'},
            compare_period={'start':'2024-01-01','end':'2024-03-01'})
        base = ("SELECT SUM(e.amount_usd) AS value FROM export_summary e "
                "WHERE e.country_code='US' AND e.hs_ver='HS2022' AND ")
        current = "e.trade_month BETWEEN '2025-01-01' AND '2025-03-31'"
        previous = "(e.trade_month>='2024-01-01' AND e.trade_month<'2024-04-01')"
        for dates in (f'({current} OR {previous})',
                      "e.trade_month>='2024-01-01' AND e.trade_month<'2025-04-01' AND "
                      "(e.trade_month<'2024-04-01' OR e.trade_month>='2025-01-01')"):
            self.assertIn(' OR ', validate_sql(draft(base+dates,'number'), conditions))
        for dates in (f'({current} OR 1=1)', f'({current} OR e.trade_month<\'2024-04-01\')',
                      f'({current} OR e.trade_month=\'2024-08-01\')', f'NOT ({current} OR {previous})',
                      f'({current} OR other.trade_month BETWEEN \'2024-01-01\' AND \'2024-03-01\')'):
            with self.subTest(dates=dates), self.assertRaises(ValueError):
                validate_sql(draft(base+dates,'number'), conditions)

    def test_sql_local_refusal_schema_stops_without_query_or_regeneration(self):
        client = MagicMock()
        client.parse.return_value = SqlGenerationDraft(sql='', columns=[], summary='',
            unsupported='국가를 확정해 주세요.')
        execution = MagicMock()
        execution.call.side_effect = lambda name, seconds, action, **kwargs: action(None)
        step = SimpleNamespace(attempts=0)
        task = SimpleNamespace(custom_query='그 나라 수출액', conditions=Conditions(
            period={'start':'2025-01-01','end':'2025-03-01'}))
        with patch('backend.app.agents.sql.schema_for', return_value=[]), \
                patch('backend.app.agents.sql.execute_readonly') as execute:
            result = generated_analysis(None, Settings({}), client, execution, task,
                finish_reserve=0, latest_month='2025-03', step=step)
        self.assertEqual(result.result.status, 'NO_DATA')
        self.assertEqual(result.result.error, '국가를 확정해 주세요.')
        self.assertEqual(step.attempts, 1)
        self.assertIs(client.parse.call_args.kwargs['schema'], SqlGenerationDraft)
        execute.assert_not_called()
        for sql, columns, unsupported in [('', [], None), ('SELECT 1', [], '거부'),
                ('', [dict(key='v',label='값',type='number',unit=None)], '거부')]:
            with self.subTest(sql=sql, unsupported=unsupported), self.assertRaises(ValueError):
                SqlGenerationDraft(sql=sql, columns=columns, summary='', unsupported=unsupported)

    def test_filtered_monthly_ctes_allow_calendar_and_comparison_left_joins(self):
        conditions = Conditions(country_codes=['US'], hs_items=[{'hs_code':'330499','hs_ver':'HS2022'}],
            period={'start':'2025-01-01','end':'2025-03-01'},
            compare_period={'start':'2024-01-01','end':'2024-03-01'})
        def monthly(year):
            return ("SELECT e.trade_month, SUM(e.weight_kg) AS weight FROM export_monthly e "
                    "WHERE e.country_code='US' AND e.hs6_code='330499' AND e.hs_ver='HS2022' "
                    f"AND e.trade_month BETWEEN '{year}-01-01' AND '{year}-03-31' GROUP BY e.trade_month")
        columns = [dict(key='month',label='월',type='string',unit=None),
                   dict(key='weight',label='중량',type='number',unit='KG')]
        calendar = (f"WITH amounts AS ({monthly(2025)}), "
            "months AS (SELECT '2025-01' AS month UNION ALL SELECT '2025-02' UNION ALL SELECT '2025-03') "
            "SELECT m.month AS month, a.weight AS weight FROM months m "
            "LEFT JOIN amounts a ON DATE_FORMAT(a.trade_month, '%Y-%m')=m.month ORDER BY m.month")
        comparison = (f"WITH current_period AS ({monthly(2025)}), prior_period AS ({monthly(2024)}) "
            "SELECT DATE_FORMAT(c.trade_month, '%Y-%m') AS month, p.weight AS weight "
            "FROM current_period c LEFT JOIN prior_period p ON MONTH(c.trade_month)=MONTH(p.trade_month) "
            "ORDER BY c.trade_month")
        for sql in (calendar, comparison):
            self.assertIn('LEFT JOIN', validate_sql(SqlDraft(sql=sql,columns=columns,summary='',unsupported=None), conditions))
            with self.assertRaises(ValueError):
                validate_sql(SqlDraft(sql=sql.replace("e.hs_ver='HS2022'", "e.hs_ver='HS2017'"),
                    columns=columns,summary='',unsupported=None), conditions)

    def test_trade_sql_requires_confirmed_scope_for_each_branch(self):
        conditions = Conditions(country_codes=['US'], hs_items=[{'hs_code':'3304','hs_ver':'HS2022'}],
            period={'start':'2025-01-01','end':'2025-03-01'},
            compare_period={'start':'2024-01-01','end':'2024-03-01'})
        def statement(period, *, country='US', edition='HS2022', product='3304%', table='export_summary'):
            return (f"SELECT SUM(amount_usd) AS value FROM {table} WHERE country_code='{country}' "
                    f"AND hs_ver='{edition}' AND hs6_code LIKE '{product}' AND {period}")
        current = "trade_month >= '2025-01-01' AND trade_month < '2025-04-01'"
        comparison = "trade_month BETWEEN '2024-01-01' AND '2024-03-01'"
        joined = ("SELECT SUM(CASE WHEN e.hs6_code LIKE '3304%' THEN e.amount_usd END) AS value FROM export_summary e "
                  "JOIN country c ON c.country_code=e.country_code WHERE c.country_role='COUNTRY' AND e.country_code='US' "
                  "AND hs_ver='HS2022' AND trade_month BETWEEN '2025-01-01' AND '2025-03-01'")
        # 확정 범위, 비교 기간, 수입표(무역수지), 그 국가 전체를 분모로 하는 비중(한정자 없는 열 포함)
        for valid in (statement(current), statement(current)+' UNION ALL '+statement(comparison),
                      statement(current, table='import_summary'), joined):
            self.assertIn('LIMIT 1000', validate_sql(draft(valid,'number'), conditions))
        rejected = [
            statement(current, country='JP'),
            statement(current, edition='HS2017'),
            statement(current, product='330_%'),
            joined.replace("LIKE '3304%'", "LIKE '8542%'"),
            joined.replace("e.country_code='US'", "(e.country_code='US' OR 1=1)")
                  .replace("CASE WHEN e.hs6_code LIKE '3304%'", "CASE WHEN (e.hs6_code LIKE '3304%' OR 1=1)"),
            # 집계 밖 CASE는 품목을 거른 것이 아니다(모든 품목을 읽어 '기타'로 묶음)
            joined.replace("SUM(CASE WHEN e.hs6_code LIKE '3304%' THEN e.amount_usd END) AS value",
                           "CASE WHEN e.hs6_code LIKE '3304%' THEN 1 ELSE 0 END AS value"),
            joined.replace("CASE WHEN e.hs6_code LIKE '3304%' THEN e.amount_usd END", "e.amount_usd"),
            statement("trade_month >= '2025-01-01'"),
            statement("trade_month BETWEEN '2024-01-01' AND '2025-03-01'"),
            statement(current)+' UNION ALL '+statement(comparison, country='JP'),
        ]
        for sql in rejected:
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                validate_sql(draft(sql,'number'), conditions)

    def test_grouping_by_country_requires_where_filter(self):
        conditions = Conditions(country_codes=['US'], hs_items=[{'hs_code':'3304','hs_ver':'HS2022'}],
            period={'start':'2025-01-01','end':'2025-03-01'})
        columns = [{'key':'cc','label':'국가','type':'string','unit':None},{'key':'value','label':'금액','type':'number','unit':'USD'}]
        base = ("SELECT e.country_code AS cc, SUM(CASE WHEN e.country_code='US' AND e.hs6_code LIKE '3304%' "
                "THEN e.amount_usd END) AS value FROM export_summary e JOIN country c ON c.country_code=e.country_code "
                "WHERE c.country_role='COUNTRY' AND e.hs_ver='HS2022' AND e.trade_month BETWEEN '2025-01-01' AND '2025-03-01' ")
        def check(sql):
            return validate_sql(SqlDraft(sql=sql, columns=columns, summary='조회', unsupported=None), conditions)
        # 국가별로 묶으면서 WHERE에서 국가를 거르지 않으면 다른 국가 행이 빈 값으로 나온다(GROUP BY 열·1·별칭 모두)
        for group in ('e.country_code', '1', 'cc'):
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, '국가별로 묶은 결과'):
                check(base + 'GROUP BY ' + group)
        # 조인한 country 별칭으로 걸러도 같은 행을 거른 것이다
        self.assertIn('LIMIT 1000', check(base + "AND c.country_code='US' GROUP BY 1"))
        # 스칼라 서브쿼리 안 국가 필터는 바깥 범위의 묶음이 아니다(미국 / 전체 수출 비중)
        inner = base.replace('e.', 'u.').replace(' e ', ' u ').split(' FROM ', 1)[1]
        share = (f"SELECT 'US' AS cc, (SELECT SUM(u.amount_usd) FROM {inner} AND u.country_code='US' AND u.hs6_code LIKE '3304%')"
                 f" / SUM(e.amount_usd) AS value FROM " + base.split(' FROM ', 1)[1])
        self.assertIn('LIMIT 1000', check(share))
        # 전체 국가 순위를 CTE에서 구한 뒤 바깥에서 미국만 고르는 것은 허용, 고르지 않으면 거부
        ranked = ("WITH r AS (SELECT e.country_code, SUM(CASE WHEN e.hs6_code LIKE '3304%' THEN e.amount_usd END) AS total FROM "
                  + base.split(' FROM ', 1)[1].replace("AND c.country_code='US' ", '') + "GROUP BY e.country_code) ")
        self.assertIn('LIMIT 1000', check(ranked + "SELECT country_code AS cc, total AS value FROM r WHERE country_code='US'"))
        with self.assertRaisesRegex(ValueError, '국가별로 묶은 결과'):
            check(ranked.replace("CASE WHEN e.hs6_code", "CASE WHEN e.country_code='US' AND e.hs6_code")
                  + "SELECT country_code AS cc, total AS value FROM r WHERE total > 0")

    def test_product_share_retains_confirmed_basis_and_denominator(self):
        conditions = Conditions(period={'start':'2025-01-01','end':'2025-03-01'},
                                ranking_group='HS6', share_basis='KOREA_TRADE_PARTNER')
        task = SimpleNamespace(custom_query='품목별 수출 비중', conditions=conditions)
        result_draft = SqlDraft(sql='SELECT 100 AS denominator_usd, 50 AS share_pct', summary='품목별 비중', unsupported=None,
            columns=[{'key':'denominator_usd','label':'분모','type':'number','unit':'USD'},
                     {'key':'share_pct','label':'비중','type':'number','unit':'%'}])
        client = MagicMock()
        client.parse.side_effect = [draft('SELECT 50 AS value','number'), result_draft]
        ledger = MagicMock()
        ledger.call.side_effect = lambda node, timeout, fn, finish_reserve: fn(timeout)
        rows = [{'denominator_usd':100, 'share_pct':50}]
        with patch('backend.app.agents.sql.schema_for',return_value=[]), \
             patch('backend.app.agents.sql.execute_readonly',return_value=(rows,result_draft.sql)) as execute:
            result = generated_analysis(object(), Settings({}), client, ledger, task,
                                        finish_reserve=40, latest_month='2025-03')
        execute.assert_called_once()
        payload = client.parse.call_args.kwargs['payload']
        self.assertEqual(payload['conditions']['share_basis'],'KOREA_TRADE_PARTNER')
        self.assertEqual(payload['conditions']['ranking_group'],'HS6')
        self.assertIn('denominator_usd', payload['previous_error'])
        self.assertEqual(result.meta['share_basis'],'KOREA_TRADE_PARTNER')
        self.assertEqual(result.result.digest['rows'][0]['denominator_usd'],100)

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
