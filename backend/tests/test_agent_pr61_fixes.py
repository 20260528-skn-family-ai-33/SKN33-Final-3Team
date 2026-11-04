"""PR #61 재검수 지적 회귀: SQL 재생성·필터, 선행 결과 정규화, 초안 제목, 비중 기준, 후속 질문 국가."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.app.agents.sql import generated_analysis, safe_error, validate_sql, SQL_PROMPT
from backend.app.agents.state import Conditions, SqlDraft, Draft, PlannerOutput, ResultBinding
from backend.app.api.config import Settings


def draft(sql):
    return SqlDraft(sql=sql, columns=[{'key': 'value', 'label': '결과', 'type': 'number', 'unit': 'USD'}],
                    summary='요약', unsupported=None)


class SqlFilterTest(unittest.TestCase):
    def test_trade_sum_requires_country_role_and_hs_ver(self):
        good = ("SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN country c ON c.country_code = e.country_code "
                "WHERE c.country_role = 'COUNTRY' AND e.hs_ver = 'HS2022'")
        self.assertIn('LIMIT 1000', validate_sql(draft(good)))
        self.assertTrue(validate_sql(draft("SELECT SUM(amount_usd) AS value FROM export_summary "
                                           "WHERE country_code = 'US' AND hs_ver = 'HS2022'")))
        for bad in ["SELECT SUM(amount_usd) AS value FROM export_summary WHERE hs_ver = 'HS2022'",
                    "SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN country c ON c.country_code = e.country_code "
                    "WHERE c.country_role = 'COUNTRY'"]:
            with self.subTest(sql=bad), self.assertRaises(ValueError):
                validate_sql(draft(bad))

    def test_prompt_names_group_by_and_total_rows(self):
        self.assertIn('ONLY_FULL_GROUP_BY', SQL_PROMPT)
        self.assertIn("country_role = 'COUNTRY'", SQL_PROMPT)

    def test_safe_mysql_errors_reach_regeneration(self):
        from sqlalchemy.exc import OperationalError
        group_by = OperationalError('SELECT ...', {}, Exception(1055, "Expression #1 of SELECT list is not in GROUP BY clause"))
        login = OperationalError('SELECT ...', {}, Exception(1045, "Access denied for user 'admin'@'10.0.0.1'"))
        self.assertIn('1055', safe_error(group_by))
        self.assertIn('GROUP BY', safe_error(group_by))
        self.assertNotIn('admin', safe_error(login))

        task = SimpleNamespace(custom_query='2025년 미국 수출의 분기별 합계',
                               conditions=Conditions(period={'start': '2025-01-01', 'end': '2025-12-01'}))
        ok = ("SELECT SUM(amount_usd) AS value FROM export_summary WHERE country_code = 'US' AND hs_ver = 'HS2022'")
        client = MagicMock(); client.parse.side_effect = [draft(ok), draft(ok)]
        ledger = MagicMock(); ledger.call.side_effect = lambda node, timeout, fn, finish_reserve: fn(timeout)
        calls = []
        def run(engine, d, timeout):
            calls.append(d)
            if len(calls) == 1: raise group_by
            return [{'value': 1}], d.sql
        with patch('backend.app.agents.sql.schema_for', return_value=[]), \
             patch('backend.app.agents.sql.execute_readonly', side_effect=run):
            result = generated_analysis(object(), Settings({}), client, ledger, task, finish_reserve=40, latest_month='2025-12')
        self.assertEqual(result.result.status, 'OK')
        self.assertIn('1055', client.parse.call_args.kwargs['payload']['previous_error'])


class SqlFilterBypassTest(unittest.TestCase):
    JOIN = "FROM export_summary e JOIN country c ON c.country_code = e.country_code "

    def test_filters_must_be_and_conditions_in_every_select(self):
        ok = [f"SELECT SUM(e.amount_usd) AS value {self.JOIN}WHERE (c.country_role = 'COUNTRY' AND (e.hs_ver = 'HS2022'))",
              f"WITH t AS (SELECT e.amount_usd {self.JOIN}WHERE c.country_role = 'COUNTRY' AND e.hs_ver = 'HS2022') "
              "SELECT SUM(amount_usd) AS value FROM t"]
        bad = [f"SELECT SUM(e.amount_usd) AS value {self.JOIN}WHERE c.country_role = 'COUNTRY' AND e.hs_ver = 'HS2022' OR 1=1",
               f"SELECT SUM(e.amount_usd) AS value {self.JOIN}WHERE c.country_role = 'COUNTRY' AND (e.hs_ver = 'HS2022' OR 1=1)",
               "SELECT SUM(amount_usd) AS value FROM export_summary WHERE country_code = 'US' AND hs_ver = 'HS2022' "
               "UNION ALL SELECT SUM(amount_usd) FROM import_summary"]
        for sql in ok:
            with self.subTest(sql=sql): self.assertTrue(validate_sql(draft(sql)))
        for sql in bad:
            with self.subTest(sql=sql), self.assertRaises(ValueError): validate_sql(draft(sql))

    def test_missing_table_error_hides_database_name(self):
        from sqlalchemy.exc import ProgrammingError
        error = ProgrammingError('SELECT ...', {}, Exception(1146, "Table 'borderless_prod.users' doesn't exist"))
        self.assertNotIn('borderless_prod', safe_error(error))


class RankingSourceTest(unittest.TestCase):
    def test_rank_order_on_ranking_result_is_amount(self):
        from backend.app.agents.bindings import resolve
        stored = {'period': {'start': '2025-01-01', 'end': '2025-12-01'}, 'trade_flow': 'EXPORT',
                  'country_codes': [], 'hs_items': []}
        ranking = {'conditions': stored, 'meta': {'kind': 'RANKING', 'group': 'COUNTRY'}, 'rows': [
            {'country_code': 'US', 'export_amount_usd': 300}, {'country_code': 'CN', 'export_amount_usd': 200},
            {'country_code': 'VN', 'export_amount_usd': 100}]}
        binding = ResultBinding(source_task_id='t1', source_step_id='t1.sql',
                                selection={'dimension': 'COUNTRY', 'limit': 2, 'order_by': 'country_rank'})
        task = SimpleNamespace(task_id='t2', input_bindings=[binding], resolved={}, applied_defaults=[],
                               question='그 국가들의 월별 추이', steps=[SimpleNamespace(step_id='t2.check')])
        state = SimpleNamespace(input=SimpleNamespace(question='수출 상위 국가 2곳과 그 국가들의 월별 추이'),
                                results={}, pending=None)
        with patch('backend.app.agents.bindings.source_versions', return_value=[(ranking, {})]):
            out = resolve(None, state, task, Conditions())
        self.assertEqual(out.country_codes, ['US', 'CN'])
        self.assertIsNone(state.pending)


class DraftTitleTest(unittest.TestCase):
    def test_long_title_is_truncated_not_rejected(self):
        title = '2025년 미국·중국 화장품 수출 추이 비교 분석'
        d = Draft(conclusion=[{'task_id': 't1', 'sentences': ['결론입니다.']}], turn_summary='', title=title)
        self.assertEqual(d.title, title[:20])


class ProductShareTest(unittest.TestCase):
    def test_product_share_goes_to_llm_sql(self):
        from backend.app.agents.planner import build_plan
        output = PlannerOutput(tasks=[{'intent': 'MARKET_SHARE', 'question': '2025년 품목별 수출 비중',
                                       'slots': {'ranking_group': 'HS6'}}])
        plan, _ = build_plan(output)
        task = plan.tasks[0]
        self.assertEqual(task.intent, 'STATISTICS')
        self.assertEqual(task.custom_query, '2025년 품목별 수출 비중')
        self.assertIsNone(task.slots.ranking_group)
        self.assertEqual(next(s for s in task.steps if s.tool == 'query_trade_data').args, {'template': 'LLM_SQL'})

    def test_country_share_unchanged(self):
        from backend.app.agents.planner import build_plan
        plan, _ = build_plan(PlannerOutput(tasks=[{'intent': 'MARKET_SHARE', 'question': '2025년 국가별 수출 비중'}]))
        self.assertEqual(plan.tasks[0].intent, 'MARKET_SHARE')


class CheckBudgetTest(unittest.TestCase):
    def test_check_without_time_fails_only_that_step(self):
        from backend.app.agents.runtime import Services
        from backend.app.api.common import ApiError
        from backend.tests.test_agent_state import state
        s = state(); step = s.plan.tasks[0].steps[0]
        execution = MagicMock()
        execution.call.side_effect = ApiError(409, 'RUN_TIMEOUT', '실행 시간이 부족합니다.')
        Services(execution, Settings({}), client=MagicMock()).check(s, step)
        self.assertEqual(step.status, 'FAILED')
        self.assertEqual(s.results[step.step_id].status, 'FAILED')
        execution.call.side_effect = ApiError(409, 'JOB_CLAIM_LOST', '소유권')
        step.status = 'PENDING'
        with self.assertRaises(ApiError):
            Services(execution, Settings({}), client=MagicMock()).check(s, step)


if __name__ == '__main__':
    unittest.main()
