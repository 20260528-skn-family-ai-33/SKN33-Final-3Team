"""Snapshot verification regressions reproduced against c1af084.

Run from the repository root:
    python -m unittest -v backend.tests.test_agent_verify_regressions
All values are synthetic. No LLM, database, or network calls are made.
"""
import unittest
from backend.app.agents.verify import verify, snapshot_evidence, check_sentence
from backend.app.agents.state import Draft, ToolResult
from backend.tests import test_agent_compose as compose_tests

PERIOD = {'start': '2025-01', 'end': '2025-12'}
NAMES = {'미국', '일본', '기초화장품', '반도체'}


def result(sentence, rows, *, period=PERIOD, compare=None, digest=None, expired=False, columns=(),
           known_names=NAMES, countries=('미국', '일본')):
    state = compose_tests.ComposeTest().state(['STATISTICS'])
    task = state.plan.tasks[0]
    task.answer_status = 'ANSWERED'
    step = next(s for s in task.steps if s.tool == 'query_trade_data')
    conditions = {'period': period, 'trade_flow': 'EXPORT'}
    if compare:
        conditions['compare_period'] = compare
    payload = {'conditions': conditions, 'rows': rows, 'columns': list(columns)}
    state.results[step.step_id] = ToolResult(
        status='OK', result_id='audit-result', digest=digest or {**conditions, 'rows': rows, 'columns': list(columns)})
    state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': [sentence]}],
                        turn_summary=sentence)
    source = ({'_verification_source': 'SNAPSHOT', '_source_unavailable': True, 'rows': []}
              if expired else snapshot_evidence(payload))
    verify(state, known_names=known_names, countries=countries, snapshots={step.step_id: source})
    return state


AMOUNT = [{'country_name': '미국', 'total_usd': 300_000_000}]
RATES = [{'country_name': '미국', 'share_pct': 40, 'yoy_pct': 5}]
TARGETS = [
    {'country_name': '미국', 'product_name': '기초화장품', 'amount_usd': 100},
    {'country_name': '미국', 'product_name': '반도체', 'amount_usd': 50},
    {'country_name': '일본', 'product_name': '반도체', 'amount_usd': 200},
]
YEAR_ROWS = [{'country_name': '미국', 'total_usd': 200, 'compare_total_usd': 100,
              'change_pct': 100}]
PREVIOUS_YEAR = {'start': '2024-01', 'end': '2024-12'}


class UncoveredCases(unittest.TestCase):
    def assert_unconfirmed(self, state):
        self.assertEqual(state.verification.checks[0].status, 'MISMATCH')
        self.assertEqual(state.verification.status, 'NEEDS_REVIEW')
        self.assertEqual(state.draft.turn_summary, '')

    def test_won_cannot_validate_usd(self):
        self.assert_unconfirmed(result('미국 수출 총액은 3억 원입니다.', AMOUNT))

    def test_euro_cannot_validate_usd(self):
        self.assert_unconfirmed(result('미국 수출 총액은 3억 유로입니다.', AMOUNT))

    def test_expired_total_cannot_be_restored_from_digest(self):
        state = result('수출 총액은 3억 달러입니다.', [],
                       digest={'total_usd': 300_000_000}, expired=True)
        self.assert_unconfirmed(state)
        self.assertEqual(state.verification.checks[0].reason, 'SOURCE_UNAVAILABLE')

    def test_expired_denominator_cannot_be_restored_from_digest(self):
        state = result('전체 교역액은 3억 달러입니다.', [],
                       digest={'denominator_usd': 300_000_000}, expired=True)
        self.assert_unconfirmed(state)
        self.assertEqual(state.verification.checks[0].reason, 'SOURCE_UNAVAILABLE')

    def test_product_change_must_keep_country(self):
        self.assert_unconfirmed(result('미국 기초화장품은 100달러, 반도체는 200달러입니다.', TARGETS))

    def test_annual_amounts_cannot_be_exchanged(self):
        self.assert_unconfirmed(result(
            '2024년 미국 수출 총액은 200달러이고 2025년은 100달러입니다.',
            YEAR_ROWS, compare=PREVIOUS_YEAR))

    def test_two_year_comparison_is_not_year_over_year(self):
        self.assert_unconfirmed(result(
            '2025년 미국 수출은 전년 대비 100% 증가했습니다.', YEAR_ROWS,
            compare={'start': '2023-01', 'end': '2023-12'}))

    def test_growth_cannot_validate_share_label(self):
        self.assert_unconfirmed(result('미국 점유율은 5%입니다.', RATES))

    def test_share_cannot_validate_change_rate_label(self):
        self.assert_unconfirmed(result('미국 증감률은 40%입니다.', RATES))


class ValidControls(unittest.TestCase):
    def assert_confirmed(self, state):
        self.assertEqual(state.verification.status, 'PASS')
        self.assertEqual(state.verification.checks[0].status, 'PASS')
        self.assertTrue(state.draft.turn_summary)

    def test_usd(self):
        self.assert_confirmed(result('미국 수출 총액은 3억 달러입니다.', AMOUNT))

    def test_rates(self):
        self.assert_confirmed(result('미국 점유율은 40%입니다.', RATES))
        self.assert_confirmed(result('미국 증감률은 5%입니다.', RATES))

    def test_targets(self):
        self.assert_confirmed(result('미국 기초화장품은 100달러, 반도체는 50달러입니다.', TARGETS))

    def test_annual_amounts(self):
        self.assert_confirmed(result(
            '2024년 미국 수출 총액은 100달러이고 2025년은 200달러입니다.',
            YEAR_ROWS, compare=PREVIOUS_YEAR))

    def test_year_over_year(self):
        self.assert_confirmed(result(
            '2025년 미국 수출은 전년 대비 100% 증가했습니다.', YEAR_ROWS, compare=PREVIOUS_YEAR))


class BoundaryCases(unittest.TestCase):
    def test_explicit_currency_codes_and_compound_amounts(self):
        for unit in ('원', 'KRW', '유로', 'EUR', '엔', 'JPY', '위안', 'CNY'):
            with self.subTest(unit=unit):
                state = result(f'미국 수출 총액은 1억 2,346만 {unit}입니다.',
                               [{'country_name': '미국', 'total_usd': 123_456_789}])
                self.assertEqual(state.verification.checks[0].status, 'MISMATCH')
                self.assertIn(unit, state.verification.checks[0].numbers[0])
        for unit in ('USD', 'usd', '달러'):
            with self.subTest(unit=unit):
                self.assertEqual(result(f'미국 수출 총액은 3억 {unit}입니다.', AMOUNT).verification.status, 'PASS')

    def test_omitted_currency_retains_existing_behavior(self):
        self.assertEqual(result('미국 수출 총액은 3억입니다.', AMOUNT).verification.status, 'PASS')

    def test_share_and_change_synonyms(self):
        for label, good, bad in [('비중', 40, 5), ('점유율', 40, 5), ('증감률', 5, 40),
                                 ('증가율', 5, 40), ('변동률', 5, 40)]:
            with self.subTest(label=label):
                self.assertEqual(result(f'미국 {label}은 {good}%입니다.', RATES).verification.status, 'PASS')
                self.assertEqual(result(f'미국 {label}은 {bad}%입니다.', RATES).verification.status, 'NEEDS_REVIEW')

    def test_country_change_keeps_product_and_explicit_pair_replaces_both(self):
        cases = [
            ('미국 반도체는 50달러, 일본은 200달러입니다.', True),
            ('미국 반도체는 50달러, 일본은 100달러입니다.', False),
            ('미국 기초화장품은 100달러, 일본 반도체는 200달러입니다.', True),
            ('미국 기초화장품은 100달러, 일본 반도체는 50달러입니다.', False),
        ]
        for text, valid in cases:
            with self.subTest(text=text):
                self.assertEqual(result(text, TARGETS).verification.status == 'PASS', valid)

    def test_typed_rows_supply_country_dimension_without_dictionary(self):
        for amount, expected in ((50, True), (200, False)):
            sentence = f'미국 기초화장품은 100달러, 반도체는 {amount}달러입니다.'
            self.assertEqual(check_sentence(sentence, [{'rows': TARGETS}], known_names=NAMES)[0], expected)

    def test_year_claims_in_both_orders_and_with_commas(self):
        for connector in (', ', '이고 '):
            for first, second in ((2024, 2025), (2025, 2024)):
                values = {2024: 100, 2025: 200}
                for swapped in (False, True):
                    a, b = values[first], values[second]
                    if swapped: a, b = b, a
                    text = f'{first}년 미국 수출 총액은 {a}달러{connector}{second}년은 {b}달러입니다.'
                    with self.subTest(text=text):
                        self.assertEqual(result(text, YEAR_ROWS, compare=PREVIOUS_YEAR).verification.status == 'PASS', not swapped)

    def test_year_range_is_one_aggregate(self):
        period = {'start': '2024-01', 'end': '2025-12'}
        self.assertEqual(result('2024년부터 2025년까지 미국 수출 총액은 300달러입니다.',
                                [{'country_name': '미국', 'total_usd': 300}], period=period).verification.status, 'PASS')
        # 두 연도의 합계를 어느 한 해의 값으로 대신하지 않는다.
        state = result('2024년과 2025년 미국 수출 총액 합계는 200달러입니다.', YEAR_ROWS, compare=PREVIOUS_YEAR)
        self.assertEqual(state.verification.status, 'NEEDS_REVIEW')

    def test_yoy_requires_same_months_in_previous_year(self):
        period = {'start': '2025-01', 'end': '2025-03'}
        for compare, expected in [({'start': '2024-01', 'end': '2024-03'}, True),
                                  ({'start': '2024-02', 'end': '2024-04'}, False),
                                  ({'start': '2024-01', 'end': '2024-12'}, False)]:
            with self.subTest(compare=compare):
                state = result('미국 수출은 전년 동기 대비 100% 증가했습니다.', YEAR_ROWS,
                               period=period, compare=compare)
                self.assertEqual(state.verification.status == 'PASS', expected)

    def test_mom_requires_previous_single_month(self):
        period = {'start': '2025-01', 'end': '2025-01'}
        for compare, expected in [({'start': '2024-12', 'end': '2024-12'}, True),
                                  ({'start': '2024-01', 'end': '2024-01'}, False),
                                  ({'start': '2024-10', 'end': '2024-12'}, False)]:
            with self.subTest(compare=compare):
                state = result('미국 수출은 전월 대비 100% 증가했습니다.', YEAR_ROWS,
                               period=period, compare=compare)
                self.assertEqual(state.verification.status == 'PASS', expected)

    def test_expired_result_does_not_block_independent_task(self):
        state = compose_tests.ComposeTest().state(['STATISTICS', 'STATISTICS'])
        snapshots, conclusions = {}, []
        for i, task in enumerate(state.plan.tasks):
            task.answer_status = 'ANSWERED'
            step = next(s for s in task.steps if s.tool == 'query_trade_data')
            state.results[step.step_id] = ToolResult(status='OK', result_id=f'r{i}', digest={'total_usd': 100 + i})
            snapshots[step.step_id] = ({'_source_unavailable': True, '_verification_source': 'SNAPSHOT', 'rows': []}
                                      if i == 0 else snapshot_evidence({'rows': [{'total_usd': 101}]}))
            conclusions.append({'task_id': task.task_id, 'sentences': [f'수출 총액은 {100+i}달러입니다.']})
        state.draft = Draft(conclusion=conclusions, explanation=['수출 총액은 101달러입니다.'],
                            turn_summary='수출 총액은 100달러입니다.')
        verify(state, snapshots=snapshots)
        self.assertEqual([(c.status, c.reason) for c in state.verification.checks],
                         [('MISMATCH', 'SOURCE_UNAVAILABLE'), ('PASS', 'MATCH'), ('PASS', 'MATCH')])
        self.assertEqual(state.draft.turn_summary, '')


if __name__ == '__main__':
    unittest.main()


RANKING_SOURCE = {'kind': 'RANKING', 'group': 'COUNTRY', 'hs_code': '3304', 'hs_name': '화장품', 'period': PERIOD,
              'trade_flow': 'EXPORT', 'unit': 'USD', 'total_usd': 1804000000,
              'top': [{'rank': 1, 'code': 'CN', 'name': '중국', 'amount_usd': 512300000, 'share_pct': 28.4},
                      {'rank': 2, 'code': 'US', 'name': '미국', 'amount_usd': 401200000, 'share_pct': 22.2}]}


def ranking_check(sentence, source=RANKING_SOURCE):
    from backend.app.agents.verify import inspect_sentence
    good, _ = inspect_sentence(sentence, [source], known_names=['중국', '미국', '화장품', '반도체'], countries=['중국', '미국'])
    return good


class RankingProductScopeTest(unittest.TestCase):
    """k007: 단일 품목 국가 순위의 품목명·HS 코드·기간 범위를 근거 범위로 대조한다."""
    check = staticmethod(ranking_check)

    def test_product_name_period_range_and_hs_code_are_scope_not_values(self):
        for sentence in ['2025년 1월~12월 화장품 수출 상위 국가는 중국(5억 1,230만 달러, 28.4%), 미국(4억 120만 달러, 22.2%) 순입니다.',
                         '같은 기간 화장품 수출 총액은 18억 400만 달러입니다.',
                         '2025년 HS 3304 화장품 수출 상위 국가는 중국(5억 1,230만 달러), 미국(4억 120만 달러) 순입니다.']:
            with self.subTest(sentence=sentence):
                self.assertTrue(self.check(sentence))

    def test_wrong_value_or_other_product_is_still_rejected(self):
        self.assertFalse(self.check('2025년 화장품 수출 1위는 중국(6억 1,230만 달러)입니다.'))
        self.assertFalse(self.check('2025년 반도체 수출 1위는 중국(5억 1,230만 달러)입니다.'))


class PeriodEndDayTest(unittest.TestCase):
    check = staticmethod(ranking_check)

    def test_month_end_restatement_matches_only_the_source_end(self):
        self.assertTrue(self.check('2025년 1월~12월(종료일 2025년 12월 31일) 화장품 수출 1위는 중국(5억 1,230만 달러)입니다.'))
        self.assertTrue(self.check('2025년 12월 31일까지 화장품 수출 총액은 18억 400만 달러입니다.'))
        self.assertFalse(self.check('2025년 1월~12월(종료일 2025년 11월 30일) 화장품 수출 1위는 중국(5억 1,230만 달러)입니다.'))
        self.assertFalse(self.check('2025년 12월 화장품 수출 1위는 중국(5억 1,230만 달러)입니다.'))


class ScopeBoundaryTest(unittest.TestCase):
    def test_article_period_end_day_is_checked_against_article_period(self):
        from backend.app.agents.verify import inspect_sentence
        source = {'kind': 'ARTICLES', 'article_period': {'start': '2026-08-01', 'end': '2026-10-31'}, 'items': []}
        good = lambda sentence: inspect_sentence(sentence, [source])[0]
        self.assertTrue(good('2026년 10월 31일까지 발행된 기사를 찾았습니다.'))
        self.assertFalse(good('2026년 9월 30일까지 발행된 기사를 찾았습니다.'))

    def test_country_alone_does_not_pick_one_of_several_product_rows(self):
        from backend.app.agents.verify import inspect_sentence
        rows = [{'name': f'베트남 {product}', 'country_code': 'VN', 'hs_code': code,
                 'monthly': [{'month': '2025-12', 'amount_usd': amount}]}
                for product, code, amount in (('기초화장품', '330499', 28400000), ('라면', '190230', 5000000))]
        source = {'kind': 'TREND', 'period': {'start': '2025-12', 'end': '2025-12'}, 'trade_flow': 'EXPORT',
                  'unit': 'USD', 'targets': rows}
        good = lambda sentence: inspect_sentence(sentence, [source], known_names=['베트남', '기초화장품', '라면'],
                                                 countries=['베트남'])[0]
        self.assertTrue(good('베트남 기초화장품 수출은 12월 2,840만 달러입니다.'))
        self.assertFalse(good('베트남 수출은 12월 2,840만 달러입니다.'))
