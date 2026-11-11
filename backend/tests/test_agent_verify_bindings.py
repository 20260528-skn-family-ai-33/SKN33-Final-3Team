"""Snapshot regressions for the six verification findings of 2026-11-06."""
import unittest
from operator import ge, gt, le, lt

from backend.app.agents.verify import inspect_sentence, mark_unverified
from backend.tests.test_agent_verify_regressions import result


class SnapshotBindingTest(unittest.TestCase):
    def check(self, text, rows, valid, *, numbers=None, **options):
        state = result(text, rows, **options)
        check, = state.verification.checks
        self.assertEqual(check.basis, 'SNAPSHOT')
        self.assertEqual(check.status, 'PASS' if valid else 'MISMATCH', text)
        self.assertEqual(state.verification.status, 'PASS' if valid else 'NEEDS_REVIEW', text)
        self.assertEqual(state.draft.turn_summary, text if valid else '', text)
        if numbers is not None:
            self.assertEqual(check.numbers, numbers, text)
        return state

    def test_complete_names_select_their_own_row(self):
        short = '시험품목0999'
        for tail in ('-1', '.1', '0', '_A'):
            long = short + tail
            for order in ((short, long), (long, short)):
                rows = [dict(country_name='미국', product_name=name,
                             amount_usd=100 if name == short else 200) for name in order]
                for name, amount in ((short, 100), (long, 200)):
                    for shown in (100, 200):
                        text = f'미국 {name} 수출액은 {shown}달러입니다.'
                        with self.subTest(text=text, order=order):
                            self.check(text, rows, shown == amount)
                    # Even equal values must not allow the other item's cell as evidence.
                    same = [dict(row, amount_usd=100) for row in rows]
                    good, (binding,) = inspect_sentence(f'미국 {name} 수출액은 100달러입니다.', [{'rows': same}])
                    self.assertTrue(good)
                    self.assertEqual(binding.matches, ((0, ('rows', str(order.index(name)), 'amount_usd')),))
            self.check(f'{short} 수출액은 200달러입니다.', [dict(product_name=long, amount_usd=200)],
                       False, known_names=(short, long))

    def test_dictionary_abbreviation_requires_one_owner_or_an_exact_name(self):
        short = '전동축'
        left, right = '전동축[설명 A]', '전동축[설명 B]'
        rows = [dict(product_name=name, amount_usd=100) for name in (left, right)]
        text = '전동축 수출액은 100달러입니다.'
        self.check(text, rows[:1], True, known_names=(short,))
        self.check(text, rows, False, known_names=(short,))
        self.check(text, rows + [dict(product_name=short, amount_usd=200)], False, known_names=(short,))

    def test_currency_name_role_and_actual_amount_are_independent(self):
        for code in ('USD', 'GBP'):
            name = code + '100'
            for amount in (100, 200):
                rows = [dict(country_name='미국', product_name=name, amount_usd=amount)]
                for template, valid, numbers in (
                    ('미국 수출액은 {name}입니다.', code == 'USD' and amount == 100, [name]),
                    ('미국 수출액은 ({name})입니다.', code == 'USD' and amount == 100, [name]),
                    ('미국 수출액은 USD200입니다.', amount == 200, ['USD200']),
                    ('미국 {name} 수출액은 100달러입니다.', amount == 100, ['100달러']),
                    ('미국 {name}의 수출액은 200달러입니다.', amount == 200, ['200달러']),
                    ('미국 {name} 수출액은 {name}입니다.', code == 'USD' and amount == 100, [name]),
                    ('미국 수출액으로 {name}를 기록했습니다.', code == 'USD' and amount == 100, [name]),
                    ('미국은 {name}의 수출액을 기록했습니다.', code == 'USD' and amount == 100, [name]),
                    ('미국은 {name}입니다.', code == 'USD' and amount == 100, [name]),
                    ('{name}이 미국의 수출액입니다.', code == 'USD' and amount == 100, [name]),
                    ('품목명은 {name}입니다.', True, []),
                    ('{name}', False, [name]),  # Without a role, the name/quantity collision is unresolved.
                ):
                    text = template.format(name=name)
                    with self.subTest(text=text, amount=amount):
                        self.check(text, rows, valid, numbers=numbers)
        rows = [dict(product_name='USD100', amount_usd=10), dict(product_name='USD200', amount_usd=20)]
        for a, b in ((10, 20), (20, 10)):
            self.check(f'USD100과 USD200의 수출액은 각각 {a}와 {b}달러입니다.', rows, a == 10)
        state = self.check('수출액은 USD100입니다.', rows, False, numbers=['USD100'])
        mark_unverified(state)
        self.assertEqual(state.verification.checks[0].numbers, ['USD100'])
        self.assertEqual(state.verification.status, 'NEEDS_REVIEW')

    def test_compound_currency_name_is_resolved_before_normalization(self):
        name = '1억 2,345만 달러'
        rows = [dict(product_name=name, amount_usd=200)]
        self.check(f'{name}의 수출액은 200달러입니다.', rows, True, numbers=['200달러'])
        self.check(f'수출액은 {name}입니다.', rows, False, numbers=[name])
        self.check(name, [dict(product_name=name, amount_usd=123450000)], False)

    def test_hs_prefix_binds_the_code_even_if_absent_from_source(self):
        rows = [dict(country_name='미국', hs_code='330499', amount_usd=100),
                dict(country_name='미국', hs_code='330410', amount_usd=200),
                dict(country_name='일본', hs_code='330499', amount_usd=200)]
        for prefix in ('HS', 'HS ', 'hs', 'HS 코드 '):
            for code, amount, valid in (('330499', 100, True), ('330499', 200, False),
                                        ('330410', 200, True), ('330498', 100, False),
                                        ('330499-A', 100, False), ('9999', 100, False)):
                text = f'미국 {prefix}{code} 수출액은 {amount}달러입니다.'
                with self.subTest(text=text):
                    self.check(text, rows, valid, numbers=[f'{amount}달러'])
        self.check('미국 수출액은 330499달러입니다.', rows, False, numbers=['330499달러'])

    def test_shared_unit_binds_before_metric_and_row_selection(self):
        rows = [dict(country_name='미국', amount_usd=10, change_usd=100),
                dict(country_name='일본', amount_usd=200, change_usd=20)]
        for explicit in ('', '달러'):
            for a, b in ((10, 200), (100, 200), (10, 20), (200, 10)):
                join = '와' if explicit else '과'
                text = f'미국과 일본 수출액은 각각 {a}{explicit}{join} {b}달러입니다.'
                with self.subTest(text=text):
                    self.check(text, rows, (a, b) == (10, 200))
        self.check('미국과 일본의 증감액은 각각 100과 20달러입니다.', rows, True)
        self.check('미국과 일본의 증감액은 각각 10과 200달러입니다.', rows, False)
        self.check('미국과 일본의 수출액은 각각 10과 200원입니다.', rows, False)
        code_rows = [dict(row, hs_code='330499') for row in rows]
        self.check('미국과 일본 수출액은 각각 330499와 200달러입니다.', code_rows, False)
        matching_rows = [dict(code_rows[0], amount_usd=330499), code_rows[1]]
        self.check('미국과 일본 수출액은 각각 330499와 200달러입니다.', matching_rows, True)

    def test_thresholds_use_strict_or_inclusive_bounds_without_rounding(self):
        rows = [dict(country_name='미국', share_pct=40)]
        for word, compare in (('초과', gt), ('미만', lt), ('이상', ge), ('이하', le)):
            for particle in ('', '를 ', '는 '):
                for threshold in (39.9, 40, 40.1):
                    text = f'미국 점유율은 {threshold}%{particle}{word}입니다.'
                    with self.subTest(text=text):
                        self.check(text, rows, compare(40, threshold))
        for threshold in (30, 40, 50):
            self.check(f'미국 점유율은 {threshold}%를 초과하지 않습니다.', rows, 40 <= threshold)
            self.check(f'미국 점유율은 {threshold}% 이상이지 않습니다.', rows, 40 < threshold)
            self.check(f'미국 점유율은 {threshold}%를 넘습니다.', rows, 40 > threshold)
            self.check(f'미국 점유율은 {threshold}%를 다소 초과합니다.', rows, 40 > threshold)
        self.check('미국 점유율은 40%를 초과합니다.', [dict(country_name='미국', share_pct=40.01)], True)
        # A single saved share cannot prove an assertion about all past observations.
        self.check('미국 점유율은 30%를 초과한 적이 없습니다.', rows, False)

    def test_postposed_targets_bind_both_orders_and_preserve_metrics(self):
        rows = [dict(country_name='미국', export_amount_usd=100, import_amount_usd=50, rank=1),
                dict(country_name='일본', export_amount_usd=200, import_amount_usd=60, rank=2)]
        for names, amounts in ((('미국', '일본'), (100, 200)), (('일본', '미국'), (200, 100))):
            first, second = names
            for a, b in (amounts, amounts[::-1]):
                for text in (f'수출액이 {a}달러인 {first}과 {b}달러인 {second}이 확인됩니다.',
                             f'{first} 수출액은 {a}달러, {second}은 {b}달러입니다.',
                             f'수출액이 {a}달러인 {first}, {second}의 수출액은 {b}달러입니다.'):
                    with self.subTest(text=text):
                        self.check(text, rows, (a,b) == amounts)
        for second in (60, 200):
            self.check(f'수출액이 100달러인 미국과 수입액이 {second}달러인 일본입니다.', rows, second == 60)
        self.check('1위는 미국이고 2위는 일본입니다.', rows, True)
        self.check('1위는 미국입니다. 2위는 일본입니다.', rows, True)
        self.check('1위는 일본입니다. 2위는 미국입니다.', rows, False)

    def test_threshold_negation_keeps_its_particle_and_its_own_clause(self):
        rows = [dict(country_name='미국', share_pct=40), dict(country_name='일본', share_pct=20)]
        for ending, compare in (('를 초과하지는 않습니다', le), ('를 넘지는 못했습니다', le),
                                ('를 넘진 않습니다', le), ('에 미달하지도 않았습니다', ge),
                                (' 이상이진 않습니다', lt), (' 이하가 아닙니다', gt)):
            for threshold in (30, 40, 50):
                text = f'미국 점유율은 {threshold}%{ending}.'
                with self.subTest(text=text):
                    self.check(text, rows, compare(40, threshold))
        for japan_threshold in (10, 20, 30):
            text = f'미국 점유율은 30%를 초과하며, 일본 점유율은 {japan_threshold}%를 초과하지는 않습니다.'
            self.check(text, rows, 20 <= japan_threshold)
        for threshold in (30, 40, 50):
            self.check(f'미국 점유율은 {threshold}%를 초과하지는 않은 것은 아닙니다.', rows, 40 > threshold)
            self.check(f'미국 점유율은 {threshold}%를 초과하지는 않은 적이 있습니다.', rows, False)
            self.check(f'미국 점유율은 {threshold}% 이상이 아닌 것은 아닙니다.', rows, 40 >= threshold)
            self.check(f'미국 점유율은 {threshold}% 이상이 아닌 적이 있습니다.', rows, False)

    def test_relative_amounts_bind_to_the_named_row_even_with_a_decoy(self):
        rows = [dict(country_name=name, amount_usd=amount)
                for name, amount in (('캐나다', 100), ('일본', 200), ('미국', 150))]
        for link in ('인', '였던', '이었던'):
            for first, second in ((150, 200), (100, 200), (150, 100), (200, 150)):
                for text in (f'수출액이 {first}달러{link} 미국과 {second}달러{link} 일본이 확인됩니다.',
                             f'수출액이 {first}달러{link} 미국과 달리 일본의 수출액은 {second}달러였습니다.'):
                    with self.subTest(text=text):
                        self.check(text, rows, (first, second) == (150, 200))
                        _, checks = inspect_sentence(text, [{'rows': rows}])
                        self.assertEqual([c.claim.targets for c in checks], [{'미국'}, {'일본'}])

        # Equal values hide a missing target unless the selected cells are checked too.
        equal_rows = [dict(row, amount_usd=200) for row in rows]
        text = '수출액이 200달러였던 일본과 200달러였던 미국이 확인됩니다.'
        good, checks = inspect_sentence(text, [{'rows': equal_rows}])
        self.assertTrue(good)
        self.assertEqual([c.matches for c in checks],
                         [((0, ('rows', i, 'amount_usd')),) for i in ('1', '2')])

    def test_relative_amounts_keep_country_product_and_trade_flow_together(self):
        rows = [dict(country_name='미국', product_name='USD100', export_amount_usd=150, import_amount_usd=70),
                dict(country_name='일본', product_name='GBP100', export_amount_usd=60, import_amount_usd=200),
                dict(country_name='캐나다', product_name='USD100', export_amount_usd=100)]
        for first, second in ((150, 200), (100, 200), (150, 60)):
            text = f'수출액이 {first}달러였던 미국 USD100과 수입액이 {second}달러였던 일본 GBP100이 확인됩니다.'
            with self.subTest(text=text):
                self.check(text, rows, (first, second) == (150, 200),
                           numbers=[f'{first}달러', f'{second}달러'])

        rows = [dict(country_name='미국', product_name='반도체', amount_usd=150),
                dict(country_name='미국', product_name='기초화장품', amount_usd=100),
                dict(country_name='일본', product_name='반도체', amount_usd=200)]
        for first in (100, 150):
            text = f'수출액이 {first}달러였던 미국의 반도체에 비해 일본 반도체의 수출액은 200달러였습니다.'
            self.check(text, rows, first == 150)

    def test_currency_names_in_ordered_values_and_country_product_pairs(self):
        rows = [dict(country_name='미국', product_name='USD100', amount_usd=200),
                dict(country_name='일본', product_name='GBP100', amount_usd=100)]
        for a, b in ((200, 100), (100, 200)):
            for text in (f'미국과 일본의 수출액은 각각 USD{a}과 USD{b}입니다.',
                         f'수출액이 {a}달러인 미국 USD100과 {b}달러인 일본 GBP100입니다.',
                         f'미국 USD100과 일본 GBP100의 수출액은 각각 {a}달러와 {b}달러입니다.'):
                with self.subTest(text=text):
                    self.check(text, rows, a == 200)
        self.check('미국 GBP100과 일본 USD100의 수출액은 각각 200달러와 100달러입니다.', rows, False)
        self.check('미국과 일본의 USD100과 GBP100 수출액은 각각 200달러와 100달러입니다.', rows, False)


if __name__ == '__main__':
    unittest.main()
