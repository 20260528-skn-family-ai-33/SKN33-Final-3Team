"""PR #116 P3: shared format rules, currency thresholds, bounded monthly evidence."""
from collections import Counter
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.app.agents.planner import build_plan, postprocess
from backend.app.agents.state import Conditions, PlannerOutput
from backend.app.agents.templates import months, run_template
from backend.app.agents.verify import check_sentence, snapshot_evidence
from backend.tests import test_agent_templates


class FormatAndThresholdTest(unittest.TestCase):
    def planned(self, question, **slots):
        return postprocess(PlannerOutput(tasks=[dict(intent='STATISTICS', question=question,
                                                     slots=slots)]), question)

    def test_first_and_year_comparison_share_format_and_negation(self):
        cases = [('말로만 설명해줘', 'TEXT'), ('텍스트로 만 설명해줘', 'TEXT'),
                 ('글로만 하지 말고 표도 보여줘', None), ('글로만 말고 표로 보여줘', None),
                 ('글로만 아니고 표로 보여줘', None), ('글로는 아니고 표로 보여줘', None),
                 ('표로만 하지 말고 말로만 설명해줘', 'TEXT'),
                 ('글로만 설명하다가 표로 보여줘', None)]
        for prefix in ('2025년 미국 화장품 수출액을 ', '2023년 2024년 2025년 미국 수출액을 비교해서 '):
            for suffix, expected in cases:
                with self.subTest(question=prefix + suffix):
                    output = self.planned(prefix + suffix, answer_format='TEXT')
                    self.assertEqual(output.tasks[0].slots.answer_format, expected)
                    plan, _ = build_plan(output)
                    self.assertEqual(any(s.tool == 'build_chart' for s in plan.steps()), expected != 'TEXT')

    def test_format_word_boundary_and_no_request_preserve_planner(self):
        for question in ('2025년 미국 화장품 수출액을 알려줘', '한글로만 설명해줘 미국 화장품 수출'):
            self.assertIsNone(self.planned(question).tasks[0].slots.answer_format)
            self.assertEqual(self.planned(question, answer_format='TEXT').tasks[0].slots.answer_format, 'TEXT')

    def test_global_and_similar_words_do_not_disable_charts(self):
        questions = ['글로벌 화장품 수출 추이 알려줘', '미국 글로벌 공급망 관련 품목별 수출',
                     '글로비스 관련 수출 추이', '말로니 관련 수출 추이', '글로컬 시장 수출 추이',
                     '지표로 본 수출 추이', '연말로 갈수록 수출이 어떻게 변해?']
        for question in questions:
            for suffix in ('', ' 2023년 2024년 2025년 수출액 비교해줘'):
                with self.subTest(question=question + suffix):
                    output = self.planned(question + suffix)
                    self.assertIsNone(output.tasks[0].slots.answer_format)
                    plan, _ = build_plan(output)
                    self.assertTrue(any(s.tool == 'build_chart' for s in plan.steps()))
                    self.assertEqual(self.planned(question + suffix, answer_format='TEXT')
                                     .tasks[0].slots.answer_format, 'TEXT')

    def test_global_can_still_have_an_explicit_format_request(self):
        requests = [('글로만 알려줘', 'TEXT'), ('말로만 설명해줘', 'TEXT'),
                    ('문장으로는 설명해줘', 'TEXT'), ('텍스트로도 알려줘', 'TEXT'),
                    ('글로', 'TEXT'), ('글로, 설명해줘', 'TEXT'),
                    ('글로만설명해줘', 'TEXT'), ('글로는 아니고 표로 보여줘', None),
                    ('글로말고 표도 보여줘', None), ('글로하지 말고 표도 보여줘', None),
                    ('글로만하지 말고 표도 보여줘', None), ('표로 보여줘', None),
                    ('차트로만 보여줘', None), ('그래프로는 보여줘', None)]
        for request, expected in requests:
            question = '2025년 글로벌 화장품 수출액을 ' + request
            with self.subTest(question=question):
                output = self.planned(question, answer_format='TEXT')
                self.assertEqual(output.tasks[0].slots.answer_format, expected)
                plan, _ = build_plan(output)
                self.assertEqual(any(s.tool == 'build_chart' for s in plan.steps()), expected != 'TEXT')

    def test_compound_tasks_keep_their_own_format(self):
        output = PlannerOutput(tasks=[
            dict(intent='STATISTICS', question='미국 수출액은 말로만', slots={'answer_format': 'TEXT'}),
            dict(intent='STATISTICS', question='중국 수출액은 표로', slots={})])
        output = postprocess(output, '미국 수출액은 말로만 설명하고 중국 수출액은 표로 보여줘')
        self.assertEqual([t.slots.answer_format for t in output.tasks], ['TEXT', None])

    def test_spoken_format_does_not_block_standard_year_comparison(self):
        question = '2023년 2024년 2025년 수출액을 말로만 비교해줘'
        output = postprocess(PlannerOutput(tasks=[dict(intent='STATISTICS', question=question,
            custom_query=question, slots={})]), question)
        self.assertIsNone(output.tasks[0].custom_query)
        self.assertEqual(output.tasks[0].slots.answer_format, 'TEXT')
        plan, _ = build_plan(output)
        self.assertEqual(next(s for s in plan.steps() if s.tool == 'query_trade_data').args['template'], 'TREND')

    def test_currency_threshold_spellings_keep_custom_sql(self):
        for amount in ('100만 달러', 'USD 1,000,000', '$1M', '$1.5m', 'usd 1000000',
                       '1,000,000 USD', '2억 3천만 불', 'USD 100만'):
            for comparison in ('이상', '이하', '초과', '미만'):
                for group in ('국가별', '품목별로'):
                    question = f'2025년 {group} 수출액 {amount} {comparison}인 대상을 알려줘'
                    with self.subTest(question=question):
                        output = self.planned(question, amount_view='TOTAL', amount_difference_only=True)
                        task = output.tasks[0]
                        self.assertEqual(task.custom_query, question)
                        self.assertIsNone(task.slots.amount_view)
                        self.assertIsNone(task.slots.amount_difference_only)
                        plan, _ = build_plan(output)
                        self.assertEqual(next(s for s in plan.steps() if s.tool == 'query_trade_data').args['template'], 'LLM_SQL')

    def test_non_money_conditions_do_not_force_custom_sql(self):
        for question in ('국가별 수출액을 USD로 표시하고 증가율 10% 이상인 국가',
                         '품목별 수출 중량 100만 kg 이상', '국가별 달러 이상 현상을 설명해줘',
                         '국가별 수출액 USD 1,000,000을 기준으로 비교해줘'):
            with self.subTest(question=question):
                self.assertIsNone(self.planned(question, amount_view='TOTAL').tasks[0].custom_query)


class MonthlyEvidenceSizeTest(unittest.TestCase):
    def test_many_targets_keep_all_totals_but_only_answer_targets_months(self):
        fixture = test_agent_templates.TemplateTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        countries = [f'{a}{b}' for a in 'AB' for b in 'ABCDEFGHIJ']
        products = [f'33049{i}' for i in range(5)]
        conditions = Conditions(country_codes=countries,
            hs_items=[{'hs_code': code, 'hs_ver': 'HS2022'} for code in products],
            period={'start': '2024-01-01', 'end': '2025-12-01'},
            compare_period={'start': '2022-01-01', 'end': '2023-12-01'})
        all_months = months(conditions.period) + months(conditions.compare_period)
        def aggregate(store, table, start, end, hs, codes, group):
            return [dict(hs6_code=code, hs_ver='HS2022', country_code=country, trade_month=m,
                         amount_usd=Decimal(1000000 + i * 10000 + j * 100 + m.month) + Decimal('.25'))
                    for i, country in enumerate(countries) for j, code in enumerate(products)
                    for m in all_months if start <= m <= end]
        with patch('backend.app.agents.templates.trade.aggregate', side_effect=aggregate), patch(
                'backend.app.agents.templates.trade.ready_months', return_value=all_months):
            result = run_template(fixture.store, 1, SimpleNamespace(conditions=conditions),
                                  SimpleNamespace(args={'template': 'TREND', 'period_totals': True}))
        key = lambda t: (t['hs_code'], t['hs_ver'], t['country_code'])
        targets = {key(t) for t in result.result.digest['targets']}
        self.assertTrue(targets)
        self.assertLessEqual(len(targets), 10)
        self.assertEqual(len(result.rows), 100)
        self.assertEqual(result.result.completeness, 'COMPLETE')
        self.assertEqual(Counter(key(r) for r in result.verification_rows), {t: 24 for t in targets})
        self.assertLessEqual(len(result.verification_rows), 240)
        for row in result.rows:
            if key(row) not in targets:
                continue
            raw = [r for r in result.verification_rows if key(r) == key(row)]
            self.assertEqual(sum(r['amount_usd'] for r in raw), row['total_usd'])
            self.assertEqual(sum(r['compare_amount_usd'] for r in raw), row['compare_total_usd'])
            self.assertTrue(all(r['amount_usd'] % 1 == Decimal('.25') for r in raw))
            self.assertEqual(raw[0]['month'], '2024-01')
            self.assertEqual(raw[-1]['month'], '2025-12')
            self.assertEqual(raw[0]['compare_month'], '2022-01')

    def test_omitted_target_month_cannot_be_borrowed_from_other_target(self):
        rows = [dict(hs_code='330499', hs_ver='HS2022', name='화장품', country_code=c,
                     country_name=n, total_usd=2400) for c, n in [('US', '미국'), ('CN', '중국')]]
        payload = dict(meta={'kind': 'TREND', 'period_totals': True}, rows=rows,
                       conditions={'period': {'start': '2025-01-01', 'end': '2025-12-31'}, 'trade_flow': 'EXPORT'},
                       verification={'kind': 'TREND_MONTHLY_V1', 'rows': [
                           {**rows[0], 'month': '2025-01', 'amount_usd': 200}]})
        source = snapshot_evidence(payload)
        self.assertTrue(check_sentence('미국 화장품의 2025년 1월 수출액은 200달러입니다.', [source])[0])
        self.assertFalse(check_sentence('중국 화장품의 2025년 1월 수출액은 200달러입니다.', [source])[0])
        self.assertTrue(check_sentence('중국 화장품의 2025년 수출액은 2,400달러입니다.', [source])[0])


if __name__ == '__main__':
    unittest.main()
