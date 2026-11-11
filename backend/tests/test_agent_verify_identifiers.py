"""Keep stored identifiers intact before parsing quantitative claims (offline)."""
import unittest

from backend.app.agents.verify import inspect_sentence, mark_unverified
from backend.tests.test_agent_verify_regressions import result


class IdentifierRegressions(unittest.TestCase):
    def assert_result(self, text, rows, valid, numbers, **options):
        state = result(text, rows, **options)
        self.assertEqual(state.verification.status, 'PASS' if valid else 'NEEDS_REVIEW')
        self.assertEqual(state.verification.checks[0].status, 'PASS' if valid else 'MISMATCH')
        self.assertEqual(state.verification.checks[0].basis, 'SNAPSHOT')
        self.assertEqual(state.verification.checks[0].numbers, numbers)
        self.assertEqual(bool(state.draft.turn_summary), valid)
        return state

    def test_identifiers_are_not_quantities_or_dates(self):
        for name in ('시험품목0999', '3D프린터', '비타민B12', '장갑(100개)',
                     '부품2025-01', '샘플2024년형', '알코올(함량40%)',
                     '제품1억 2345만', '수입용 밸브3'):
            rows = [{'country_name': '미국', 'product_name': name, 'amount_usd': 100}]
            for amount, valid in ((100, True), (200, False)):
                text = f'미국 {name} 수출액은 {amount}달러입니다.'
                with self.subTest(text=text):
                    self.assert_result(text, rows, valid, [f'{amount}달러'])

    def test_numeric_suffixes_keep_exact_row_identity(self):
        rows = [{'country_name': '미국', 'product_name': '시험품목0999', 'amount_usd': 100},
                {'country_name': '미국', 'product_name': '시험품목09990', 'amount_usd': 200}]
        for name, expected_index in (('시험품목0999', '0'), ('시험품목09990', '1')):
            for amount in (100, 200):
                valid = amount == (100 if expected_index == '0' else 200)
                text = f'미국 {name} 수출액은 {amount}달러입니다.'
                with self.subTest(text=text):
                    self.assert_result(text, rows, valid, [f'{amount}달러'])
                    good, checks = inspect_sentence(text, [{'rows': rows}])
                    self.assertEqual(good, valid)
                    self.assertEqual(len(checks), 1)
                    self.assertEqual(checks[0].claim.targets, {'미국', name})
                    self.assertEqual(checks[0].matches,
                                     ((0, ('rows', expected_index, 'amount_usd')),) if valid else ())

    def test_short_numeric_identifier_is_not_an_alias_of_another(self):
        rows = [{'country_name': '미국', 'product_name': '시험품목09990', 'amount_usd': 100}]
        self.assert_result('미국 시험품목0999 수출액은 100달러입니다.', rows, False, ['100달러'],
                           known_names=('미국', '시험품목0999', '시험품목09990'))
        self.assert_result('미국 시험품목09990 수출액은 100달러입니다.', rows, True, ['100달러'])

    def test_coordinated_identifiers_preserve_order(self):
        rows = [{'country_name': '미국', 'product_name': '시험품목0999', 'amount_usd': 100},
                {'country_name': '미국', 'product_name': '시험품목09990', 'amount_usd': 200}]
        for first, second, valid in ((100, 200, True), (200, 100, False)):
            self.assert_result(f'미국 시험품목0999와시험품목09990은 각각 {first}달러와 {second}달러입니다.',
                               rows, valid, [f'{first}달러', f'{second}달러'])

    def test_identifier_date_does_not_replace_the_claim_period(self):
        rows = [{'country_name': '미국', 'product_name': '부품2024-03',
                 'month': '2025-01', 'amount_usd': 100}]
        for month, valid in ((1, True), (2, False)):
            self.assert_result(f'2025년 {month}월 미국 부품2024-03 수출액은 100달러입니다.',
                               rows, valid, ['100달러'])

    def test_compound_amount_outside_identifier_is_still_verified(self):
        rows = [{'country_name': '미국', 'product_name': '제품1억 2345만', 'amount_usd': 123450000}]
        self.assert_result('미국 제품1억 2345만 수출액은 1억 2,345만 달러입니다.',
                           rows, True, ['1억 2,345만 달러'])

    def test_numeric_hs_code_cannot_hide_a_real_amount(self):
        rows = [{'country_name': '미국', 'hs_code': '330499', 'amount_usd': 100}]
        self.assert_result('미국 HS 330499 수출액은 100달러입니다.', rows, True, ['100달러'])
        for amount in ('330499달러', '330499 USD', 'USD330499'):
            with self.subTest(amount=amount):
                self.assert_result(f'미국 수출액은 {amount}입니다.', rows, False, [amount])

    def test_currencies_remain_units_next_to_numeric_identifiers(self):
        rows = [{'country_name': '일본', 'product_name': '시험품목0999', 'amount_usd': 100}]
        for amount, valid in (('미국 달러100', True), ('100 미국 달러', True),
                              ('USD100', True), ('GBP100', False), ('100(원)', False)):
            with self.subTest(amount=amount):
                self.assert_result(f'일본 시험품목0999 수출액은 {amount}입니다.', rows, valid, [amount],
                                   known_names=('미국', '일본', '시험품목0999'))

    def test_invalid_metric_list_diagnostics_exclude_identifier_digits(self):
        rows = [{'country_name': '미국', 'product_name': '시험품목0999', 'share_pct': 40, 'yoy_pct': 5}]
        self.assert_result('미국 시험품목0999 점유율과 증감률은 각각 40%입니다.', rows, False, ['40%'])

    def test_timeout_does_not_treat_known_identifier_as_a_number(self):
        rows = [{'country_name': '미국', 'product_name': '시험품목0999', 'amount_usd': 100}]
        state = result('미국 시험품목0999 결과를 확인하세요.', rows)
        mark_unverified(state)
        self.assertEqual(state.verification.checks[0].numbers, [])
        self.assertEqual(state.verification.checks[0].status, 'PASS')
        self.assertEqual(state.draft.turn_summary, '')
        state = result('미국 시험품목0999 수출액은 100달러입니다.', rows)
        mark_unverified(state)
        self.assertEqual(state.verification.checks[0].numbers, ['100달러'])
        self.assertEqual(state.verification.checks[0].status, 'MISMATCH')
        self.assertEqual(state.draft.turn_summary, '')


if __name__ == '__main__':
    unittest.main()
