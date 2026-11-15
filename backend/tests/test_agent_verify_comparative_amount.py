"""Annual comparative dollar claims keep their named operand roles."""
from dataclasses import replace
from decimal import Decimal
import unittest

from backend.app.agents.verify import mismatch
from backend.app.agents.verify_claims import NameCatalog, parse_sentence
from backend.app.agents.verify_evidence import Fact, Metric


class AnnualComparativeAmountTest(unittest.TestCase):
    def setUp(self):
        self.catalog = NameCatalog([], (), ())
        self.source = {
            'period': {'start': '2025-01-01', 'end': '2025-12-31'},
            'compare_period': {'start': '2024-01-01', 'end': '2024-12-31'},
            'trade_flow': 'EXPORT',
        }
        raw = Fact(0, ('rows', '0', 'difference_usd'),
                   Metric('difference_usd', '수출액 차이', 'AMOUNT_CHANGE', 'EXPORT', None, 'PERIOD'),
                   Decimal('25674131600'), 'USD', frozenset(), frozenset(),
                   ('2025-01', '2025-12'), ('2024-01', '2024-12'), None,
                   True, False, None, None, None, frozenset())
        self.fact = replace(raw, period_difference=True)

    def claim(self, text):
        claims = parse_sentence(text, self.catalog).claims
        self.assertEqual(len(claims), 1)
        return claims[0]

    def test_both_year_orders_and_actual_annual_modifiers(self):
        for text in (
            '2025년 연간 수출액은 2024년 연간 수출액보다 256억 7,413만 달러(USD) 많습니다.',
            '2025년 수출액은 2024년보다 256억 7,413만 달러 많습니다.',
            '2024년보다 2025년 수출액이 256억 7,413만 달러 많습니다.',
            '2025년보다 2024년 수출액이 256억 7,413만 달러 적습니다.',
        ):
            with self.subTest(text=text):
                claim = self.claim(text)
                self.assertTrue(claim.valid)
                self.assertTrue(claim.metric.comparative_amount)
                self.assertEqual(claim.metric.family, 'AMOUNT_CHANGE')
                self.assertIsNone(mismatch(claim, self.fact, self.source))

    def test_year_direction_value_and_provenance_must_match(self):
        good = '2025년 수출액은 2024년보다 256억 7,413만 달러 많습니다.'
        for text in (
            good.replace('2024년', '2023년'),
            good.replace('많습니다', '적습니다'),
            good.replace('7,413만', '7,414만'),
            '2025년보다 2024년 수출액이 256억 7,413만 달러 많습니다.',
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(mismatch(self.claim(text), self.fact, self.source))
        unproved = replace(self.fact, period_difference=False)
        self.assertIsNotNone(mismatch(self.claim(good), unproved, self.source))

    def test_elided_year_subject_uses_the_preceding_measure(self):
        sentence = '수출액은 2025년이 2024년보다 256억 7,413만 달러 많습니다.'
        self.assertIsNone(mismatch(self.claim(sentence), self.fact, self.source))
        for measure in ('수출 목표액', '예상 수출액'):
            with self.subTest(measure=measure):
                self.assertIsNotNone(mismatch(self.claim(sentence.replace('수출액', measure)),
                                              self.fact, self.source))

    def test_bare_or_ambiguous_or_negated_amount_is_not_a_comparison(self):
        for text in (
            '2025년 수출액은 256억 7,413만 달러입니다.',
            '2025년 수출액은 2024년보다 256억 7,413만 달러 많지 않습니다.',
            '2025년을 기준으로 2024년보다 256억 7,413만 달러 많습니다.',
            '2025년 수출액은 2024년과 2023년보다 256억 7,413만 달러 많습니다.',
        ):
            with self.subTest(text=text):
                claim = self.claim(text)
                self.assertFalse(claim.metric.comparative_amount)
                self.assertIsNotNone(mismatch(claim, self.fact, self.source))
                level = replace(self.fact, metric=Metric('total_usd', '수출액', 'AMOUNT',
                                                        'EXPORT', 'TOTAL', None),
                                period_difference=False, value=Decimal('25674130000'))
                if '보다' in text:
                    self.assertIsNotNone(mismatch(claim, level, self.source))


if __name__ == '__main__':
    unittest.main()
