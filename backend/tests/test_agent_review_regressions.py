"""PR 116 최종 검수: 근거 보존·품목 별칭·CTE 재생성·첫 질문 표시 형식."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.app.agents.planner import postprocess
from backend.app.agents.sql import generated_analysis, period_covered, _retry_flat_totals
from backend.app.agents.state import Conditions, PlannerOutput, SqlDraft
from backend.app.agents.verify import check_sentence, snapshot_evidence
from backend.app.api.config import Settings
from backend.tests import test_agent_templates, test_agent_result_store


class MonthlyGroundsTest(unittest.TestCase):
    def payload(self):
        identity = dict(hs_code='330499', hs_ver='HS2022', name='기타 기초·미용 화장품',
                        country_code=None, country_name='전체 국가')
        amounts = [562352114] + [700000000] * 7 + [853024939] + [700000000] * 3
        return dict(conditions={'period': {'start': '2025-01-01', 'end': '2025-12-31'}, 'trade_flow': 'EXPORT'},
                    meta={'kind': 'TREND', 'period_totals': True},
                    rows=[{**identity, 'total_usd': sum(amounts)}],
                    verification={'kind': 'TREND_MONTHLY_V1', 'rows': [
                        {**identity, 'month': f'2025-{m:02}', 'amount_usd': v} for m, v in enumerate(amounts, 1)]})

    def test_name_and_code_are_one_product_but_wrong_code_is_rejected(self):
        sources = [snapshot_evidence(self.payload())]
        sentence = '기타 기초·미용 화장품(HS 330499)의 2025년 1월 수출액은 5억 6,235만 달러입니다.'
        self.assertTrue(check_sentence(sentence, sources)[0])
        self.assertFalse(check_sentence(sentence.replace('330499', '330410'), sources)[0])
        self.assertFalse(check_sentence(sentence.replace('수출액', '수입액'), sources)[0])
        self.assertFalse(check_sentence(sentence.replace('1월', '2월'), sources)[0])

    def test_parenthesized_months_bind_to_each_range_endpoint(self):
        source = snapshot_evidence(self.payload())
        sentence = '월별 수출액은 5억 6,235만 달러(1월)에서 8억 5,302만 달러(9월) 사이입니다.'
        self.assertTrue(check_sentence(sentence, [source])[0])
        for wrong in (sentence.replace('(9월)', '(8월)'), sentence.replace('5억', '6억'),
                      sentence.replace('8억 5,302만 달러(9월)', '7억 달러(2월)')):
            with self.subTest(wrong=wrong): self.assertFalse(check_sentence(wrong, [source])[0])

    def test_legacy_or_missing_months_do_not_prove_full_period_extremum(self):
        payload = self.payload()
        sentence = '월별 수출액의 최저는 1월 5억 6,235만 달러입니다.'
        self.assertTrue(check_sentence(sentence, [snapshot_evidence(payload)])[0])
        payload['verification']['rows'].pop()
        self.assertFalse(check_sentence(sentence, [snapshot_evidence(payload)])[0])
        del payload['verification']
        self.assertFalse(check_sentence(sentence, [snapshot_evidence(payload)])[0])

    def test_ambiguous_name_is_not_alias_for_two_codes(self):
        from backend.app.agents.verify_claims import NameCatalog
        catalog = NameCatalog([{'rows': [{'name': '화장품', 'hs_code': '330499'},
                                       {'name': '화장품', 'hs_code': '330410'}]}])
        self.assertTrue(catalog.grouped(frozenset({'화장품', '330499'})))
        self.assertTrue(catalog.grouped(frozenset({'330499', '330410'})))

    def test_template_keeps_raw_months_without_changing_total_table(self):
        fixture = test_agent_templates.TemplateTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from backend.app.agents.templates import run_template
        task = SimpleNamespace(conditions=Conditions(country_codes=['US'], hs_items=[
            {'hs_code': '330499', 'hs_ver': 'HS2022'}], period={'start': '2025-01-01', 'end': '2025-03-01'}))
        total = run_template(fixture.store, 1, task, SimpleNamespace(args={'template': 'TREND', 'period_totals': True}))
        monthly = run_template(fixture.store, 1, task, SimpleNamespace(args={'template': 'TREND'}))
        self.assertEqual(len(total.rows), 1)
        self.assertNotIn('month', total.rows[0])
        self.assertEqual([(r['month'], r['amount_usd']) for r in total.verification_rows],
                         [(r['month'], r['amount_usd']) for r in monthly.rows])
        self.assertEqual(total.rows[0]['total_usd'], sum(r['amount_usd'] for r in total.verification_rows))

    def test_saved_months_survive_reuse_without_leaking_into_table(self):
        fixture = test_agent_result_store.ResultStoreTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        from backend.app.agents.state import ToolResult
        from backend.app.api.database import Store
        from backend.app.api.results import get_table, read_snapshot
        payload = self.payload()
        tool = fixture.store.save(fixture.state, fixture.step, ToolResult(status='OK'),
            rows=payload['rows'], meta=payload['meta'], conditions=payload['conditions'],
            verification_rows=payload['verification']['rows'], input_fingerprint=fixture.key,
            source_results=[], versions=fixture.versions)
        self.assertEqual(fixture.store.reuse(fixture.state, fixture.step, fixture.key), tool)
        with fixture.fixture.engine.connect() as conn:
            store = Store(fixture.fixture.database, conn)
            saved = read_snapshot(store, 1, tool.result_id, 'RESULT')
            table = get_table(store, 1, tool.result_id)
        self.assertEqual(saved['verification'], payload['verification'])
        self.assertNotIn('verification', table)
        self.assertEqual(table['total_rows'], 1)
        self.assertEqual(len(snapshot_evidence(saved)['rows']), 13)


class SqlTotalRetryTest(unittest.TestCase):
    flat = ("SELECT e.hs6_code AS hs_code, SUM(e.amount_usd) AS amount_usd FROM export_summary e "
            "WHERE e.hs_ver='HS2022' AND e.trade_month >= '2025-01-01' AND e.trade_month < '2026-01-01' "
            "AND e.country_code NOT IN (SELECT country_code FROM country WHERE country_role <> 'COUNTRY') "
            "GROUP BY e.hs6_code HAVING SUM(e.amount_usd) >= 20000000 ORDER BY amount_usd DESC LIMIT 1000")
    cte = 'WITH totals AS (' + flat + ') SELECT hs_code, amount_usd FROM totals ORDER BY amount_usd DESC LIMIT 1000'

    def execute(self, statements):
        conditions = Conditions(period={'start': '2025-01-01', 'end': '2025-12-01'})
        client, ledger = MagicMock(), MagicMock()
        columns = [dict(key='hs_code', label='HS 코드', type='string', unit=None),
                   dict(key='amount_usd', label='금액', type='number', unit='USD')]
        client.parse.side_effect = [SqlDraft(sql=s, columns=columns, summary='품목별 수출 합계', unsupported=None) for s in statements]
        ledger.call.side_effect = lambda node, timeout, fn, **kwargs: fn(timeout)
        with patch('backend.app.agents.sql.schema_for', return_value=[]), patch(
                'backend.app.agents.sql.execute_readonly', side_effect=lambda engine, draft, *a, **kw:
                ([{'hs_code': '330499', 'amount_usd': 30000000}], draft.sql)):
            analysis = generated_analysis(None, Settings({}), client, ledger,
                SimpleNamespace(custom_query='2025년 품목별 2천만 달러 이상 수출 품목', conditions=conditions),
                finish_reserve=0, latest_month='2025-12')
        return analysis, client

    def test_cte_retry_proves_period_without_relaxing_validator(self):
        analysis, client = self.execute([self.cte, self.flat])
        self.assertEqual(client.parse.call_count, 2)
        self.assertIn('CTE', client.parse.call_args.kwargs['payload']['previous_error'])
        self.assertTrue(analysis.meta['period_verified'])
        self.assertFalse(period_covered(self.cte, Conditions(period={'start': '2025-01-01', 'end': '2025-12-01'})))

    def test_retries_bounded_and_unproven_result_stays_unproven(self):
        analysis, client = self.execute([self.cte] * 3)
        self.assertEqual(client.parse.call_count, 3)
        self.assertFalse(analysis.meta['period_verified'])

    def test_flat_query_with_unproven_alias_is_also_regenerated(self):
        wrong = self.flat.replace('AS hs_code', 'AS product_identifier')
        analysis, client = self.execute([wrong, self.flat])
        self.assertEqual(client.parse.call_count, 2)
        self.assertTrue(analysis.meta['period_verified'])

    def test_monthly_and_extremum_queries_are_not_forced_into_period_totals(self):
        c = Conditions(period={'start': '2025-01-01', 'end': '2025-12-01'})
        self.assertFalse(_retry_flat_totals(self.cte.replace('SUM(', 'MAX('), c))
        self.assertFalse(_retry_flat_totals(self.cte.replace('GROUP BY e.hs6_code', 'GROUP BY MONTH(e.trade_month)'), c))

    def test_redundant_hs6_generated_depth_is_safe_but_other_filters_are_not(self):
        c=Conditions(period={'start':'2025-01-01','end':'2025-12-01'})
        sql=self.flat.replace('WHERE e.hs_ver', 'INNER JOIN hs_code h ON h.hs_code=e.hs6_code AND h.hs_ver=e.hs_ver AND h.depth=6 WHERE e.hs_ver')
        self.assertTrue(period_covered(sql,c))
        for wrong in (sql.replace('h.depth=6','h.depth=4'), sql.replace('h.depth=6','e.amount_usd=6'),
                      sql.replace('INNER JOIN','LEFT JOIN'),sql.replace('h.hs_code=e.hs6_code AND ','')):
            with self.subTest(sql=wrong): self.assertFalse(period_covered(wrong,c))


class FirstTextRequestTest(unittest.TestCase):
    def test_monthly_balance_summary_does_not_call_balance_exports(self):
        from backend.app.agents.monthly_summary import monthly_sentences, monthly_facts
        task=SimpleNamespace(conditions=Conditions(period={'start':'2025-01-01','end':'2025-03-01'}),
                             slots=SimpleNamespace(trade_balance=True))
        rows=[{'month':f'2025-{m:02}', 'amount_usd':m*100} for m in range(1,4)]
        target=dict(total_usd=600, country_code='US',country_name='미국',hs_code='330499',name='기타 기초·미용 화장품')
        sentences=monthly_sentences(task,target,monthly_facts(rows))
        self.assertTrue(all('무역수지' in s for s in sentences))
        self.assertFalse(any('수출액' in s for s in sentences))

    def test_amount_threshold_is_not_lost_to_plain_total_template(self):
        question = '2025년 전체 국가에서 품목별 수출액을 합산해서 2천만 달러 이상인 품목을 알려줘'
        output = PlannerOutput.model_validate({'tasks': [{'intent': 'STATISTICS', 'question': question,
            'slots': {'amount_view': 'TOTAL'}}]})
        task = postprocess(output, question).tasks[0]
        self.assertEqual(task.custom_query, question)
        self.assertIsNone(task.slots.amount_view)

    def test_first_question_text_request_and_negation(self):
        for question, expected in [('2025년 기초화장품 수출액을 표 없이 글로만 알려줘', 'TEXT'),
                                   ('2025년 수출액을 글로만 말고 표로도 알려줘', None)]:
            output = PlannerOutput.model_validate({'tasks': [{'intent': 'STATISTICS', 'question': question, 'slots': {}}]})
            task = postprocess(output, question).tasks[0]
            self.assertEqual(task.slots.answer_format, expected)
