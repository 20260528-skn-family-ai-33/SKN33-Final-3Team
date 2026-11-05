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

    def test_outer_join_multi_edition_and_subquery(self):
        ok = [f"SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN country c ON c.country_code = e.country_code "
              "AND c.country_role = 'COUNTRY' WHERE e.hs_ver = 'HS2022'",
              "SELECT SUM(e.amount_usd) AS value FROM export_summary e WHERE e.hs_ver = 'HS2022' AND e.country_code IN "
              "(SELECT country_code FROM country WHERE country_role = 'COUNTRY')",
              f"SELECT SUM(e.amount_usd) AS value {self.JOIN}WHERE C.country_role = 'COUNTRY' AND E.hs_ver = 'HS2022'"]
        bad = ["SELECT SUM(e.amount_usd) AS value FROM export_summary e LEFT JOIN country c ON c.country_code = e.country_code "
               "AND c.country_role = 'COUNTRY' WHERE e.hs_ver = 'HS2022'",
               f"SELECT SUM(e.amount_usd) AS value {self.JOIN}WHERE c.country_role = 'COUNTRY' AND e.hs_ver IN ('HS2017', 'HS2022')",
               "SELECT SUM(e.amount_usd) AS value FROM export_summary e WHERE e.hs_ver = 'HS2022' AND e.country_code IN "
               "(SELECT country_code FROM country)"]
        for sql in ok:
            with self.subTest(sql=sql): self.assertTrue(validate_sql(draft(sql)))
        for sql in bad:
            with self.subTest(sql=sql), self.assertRaises(ValueError): validate_sql(draft(sql))

    def test_each_trade_table_needs_its_own_filters(self):
        ok = ["SELECT SUM(e.amount_usd) AS value FROM country c LEFT JOIN export_summary e ON e.country_code = c.country_code "
              "AND e.hs_ver = 'HS2022' WHERE c.country_role = 'COUNTRY'",
              "SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN import_summary i ON i.country_code = e.country_code "
              "AND i.trade_month = e.trade_month AND i.hs_ver = 'HS2022' AND i.country_code = 'US' "
              "WHERE e.country_code = 'US' AND e.hs_ver = 'HS2022'"]
        bad = ["SELECT SUM(i.amount_usd) AS value FROM export_summary e JOIN import_summary i ON i.trade_month = e.trade_month "
               "WHERE e.country_code = 'US' AND e.hs_ver = 'HS2022'",
               "SELECT SUM(e.amount_usd) AS value FROM export_summary e WHERE e.hs_ver = 'HS2022' AND e.country_code IN "
               "(SELECT region_id FROM country WHERE country_role = 'COUNTRY')",
               "SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN country c ON c.region_id = 1 "
               "WHERE c.country_role = 'COUNTRY' AND e.hs_ver = 'HS2022'"]
        for sql in ok:
            with self.subTest(sql=sql): self.assertTrue(validate_sql(draft(sql)))
        for sql in bad:
            with self.subTest(sql=sql), self.assertRaises(ValueError): validate_sql(draft(sql))

    def test_using_and_left_join_where_role_are_accepted(self):
        for sql in ["SELECT SUM(e.amount_usd) AS value FROM export_summary e JOIN country c USING (country_code) "
                    "WHERE c.country_role = 'COUNTRY' AND e.hs_ver = 'HS2022'",
                    "SELECT SUM(e.amount_usd) AS value FROM export_summary e LEFT JOIN country c "
                    "ON c.country_code = e.country_code WHERE c.country_role = 'COUNTRY' AND e.hs_ver = 'HS2022'"]:
            with self.subTest(sql=sql): self.assertTrue(validate_sql(draft(sql)))

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
        for blank in ('', '   '):
            with self.subTest(title=blank):
                self.assertIsNone(Draft(conclusion=[{'task_id': 't1', 'sentences': ['결론입니다.']}], turn_summary='',
                                        title=blank).title)


class ProductShareTest(unittest.TestCase):
    """품목별 비중(MARKET_SHARE + ranking_group=HS6): 분모를 확인한 뒤에만 LLM SQL로 조회한다(D-87)."""
    def check(self, slots, resolved=None):
        from backend.app.agents.conditions import check_conditions
        from backend.app.agents.planner import build_plan
        from backend.app.api.database import Database, Store
        from backend.tests.api_support import reference_data, test_engine
        from backend.tests.test_agent_state import state
        engine, meta = test_engine(); self.addCleanup(engine.dispose)
        reference_data(engine, meta)
        db = Database(engine); db.settings = Settings({'API_HS_VECTOR_ENABLED': 'false'})
        connection = engine.connect(); self.addCleanup(connection.close)
        s = state(); s.input.question = '2025년 품목별 수출 비중'
        s.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': 'MARKET_SHARE', 'question': s.input.question,
                                                     'slots': {'period': {'start': '2025-01-01', 'end': '2025-12-01'}, **slots}}]))
        task = s.plan.tasks[0]; task.resolved.update(resolved or {})
        check_conditions(Store(db, connection), s, task.steps[0])
        return s, task, next(x for x in task.steps if x.tool == 'query_trade_data')

    def test_ambiguous_basis_is_asked_first(self):
        s, task, sql = self.check({'ranking_group': 'HS6'})
        self.assertEqual(s.pending.kind, 'SHARE_BASIS')
        self.assertIn('품목별', s.pending.choices[0].label)
        self.assertEqual(sql.args, {'template': 'SHARE'})

    def test_korea_basis_goes_to_llm_sql(self):
        for slots, resolved in [({'ranking_group': 'HS6', 'share_basis': 'KOREA_TRADE_PARTNER'}, None),
                                ({'ranking_group': 'HS6'}, {'t1:share_basis': {'share_basis': 'KOREA_TRADE_PARTNER'}})]:
            with self.subTest(slots=slots, resolved=resolved):
                s, task, sql = self.check(slots, resolved)
                self.assertIsNone(s.pending)
                self.assertEqual((task.steps[0].status, sql.args, task.custom_query),
                                 ('DONE', {'template': 'LLM_SQL'}, '2025년 품목별 수출 비중'))

    def test_foreign_basis_stays_unsupported(self):
        s, task, sql = self.check({'ranking_group': 'HS6', 'share_basis': 'FOREIGN_IMPORT_MARKET'})
        self.assertEqual(task.steps[0].status, 'FAILED')
        self.assertIn('지원하지 않습니다', s.results[task.steps[0].step_id].error)

    def test_country_share_keeps_template(self):
        s, task, sql = self.check({'share_basis': 'KOREA_TRADE_PARTNER'})
        self.assertEqual((task.steps[0].status, sql.args), ('DONE', {'template': 'SHARE'}))


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


class FinishBudgetTest(unittest.TestCase):
    """마무리 단계에서 시간이 모자라도 완성된 답변을 버리지 않는다(§1, §13.4)."""
    def answered_state(self):
        from backend.tests.test_agent_compose import ComposeTest
        from backend.app.agents.state import ToolResult
        state = ComposeTest().state(['STATISTICS']); task = state.plan.tasks[0]; task.answer_status = 'ANSWERED'
        step = next(x for x in task.steps if x.tool == 'query_trade_data')
        state.results[step.step_id] = ToolResult(status='OK', result_id='r', digest={'rows': [{'country_name': '중국', 'amount_usd': 999}]})
        state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['중국 수출은 999 USD입니다.']}],
                            explanation=['설명 문장입니다.'], turn_summary='중국 999 USD')
        return state

    def test_verify_without_time_marks_sentences_for_review(self):
        from backend.app.agents.runtime import Services
        from backend.app.api.common import ApiError
        state = self.answered_state()
        execution = MagicMock()
        execution.call.side_effect = ApiError(409, 'RUN_TIMEOUT', '실행 시간이 부족합니다.')
        Services(execution, Settings({}), client=MagicMock()).verify(state)
        self.assertEqual(execution.call.call_args.kwargs['finish_reserve'], 10)   # finalize 몫을 남긴다
        self.assertEqual(state.verification.status, 'NEEDS_REVIEW')
        # 숫자가 있는 문장만 "확인 필요"다. 숫자 없는 설명 문장은 대조할 것이 없어 PASS다.
        self.assertEqual([(c.index, c.status) for c in state.verification.checks], [(0, 'MISMATCH'), (1, 'PASS')])
        self.assertEqual(state.verification.checks[0].numbers, ['999 USD'])
        self.assertEqual(state.draft.turn_summary, '')   # 확인하지 않은 숫자를 다음 턴 요약에 남기지 않는다

    def test_finalize_reserves_even_when_budget_is_spent(self):
        from backend.app.agents.budget import new_round, reserve
        from backend.app.agents.runtime import Services
        from backend.app.api.common import ApiError
        ledger = new_round('r', 1); ledger['charged_seconds'] = 179.0   # compose가 예약보다 오래 걸린 경우
        with self.assertRaises(ApiError):
            reserve(ledger, round_id='r', claim_token=1, call_id='x', timeout=10, node='finalize')
        execution = MagicMock(); execution.read.return_value = {'agent': ledger}; execution.job = {'claim_token': 1}
        reserved = []
        execution.transition.side_effect = lambda change: reserved.append(change(ledger))
        Services(execution, Settings({}), client=MagicMock()).finalize(self.answered_state())
        self.assertEqual(reserved[0]['reservation']['node'], 'finalize')
        execution.transaction.assert_called_once()   # 저장은 그대로 진행한다



if __name__ == '__main__':
    unittest.main()
