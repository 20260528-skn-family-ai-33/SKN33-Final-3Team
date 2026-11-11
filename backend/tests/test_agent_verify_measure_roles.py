"""Assert evidence paths as well as verdicts for level and change measures."""
import unittest

from backend.app.agents.verify import inspect_sentence
from backend.tests.test_agent_verify_regressions import result


class MeasureRoleTest(unittest.TestCase):
    def check(self, text, row, prior, columns):
        current = {'start': '2025-02', 'end': '2025-02'}
        compare = {'start': prior, 'end': prior}
        source = dict(period=current, compare_period=compare, rows=[row], trade_flow='EXPORT')
        valid = all(columns)
        with self.subTest(text=text, prior=prior, columns=columns):
            ok, checks = inspect_sentence(text, [source])
            self.assertEqual(ok, valid)
            self.assertEqual([c.matches for c in checks], [
                ((0, ('rows', '0', key)),) if key else () for key in columns])
            state = result(text, [row], period=current, compare=compare)
            self.assertEqual(state.verification.checks[0].basis, 'SNAPSHOT')
            self.assertEqual(state.verification.status, 'PASS' if valid else 'NEEDS_REVIEW')
            self.assertEqual(state.draft.turn_summary, text if valid else '')

    def test_orders_and_comparison_axes(self):
        for marker, prior, other, axis in (
                ('전년 대비', '2024-02', '2025-01', 'yoy'),
                ('전년 동월 대비', '2024-02', '2025-01', 'yoy'),
                ('작년 같은 달보다', '2024-02', '2025-01', 'yoy'),
                ('전월 대비', '2025-01', '2024-02', 'mom'),
                ('지난달보다', '2025-01', '2024-02', 'mom')):
            row = dict(total_usd=200, compare_total_usd=250, change_usd=-50,
                       yoy_pct=-20 if axis=='yoy' else 10, mom_pct=-20 if axis=='mom' else 10)
            for value, unit, direction, key in ((20, '%', '감소', axis+'_pct'),
                                                (10, '%', '증가', None),
                                                (50, '달러', '감소', 'change_usd'),
                                                (50, '달러', '증가', None)):
                for level, level_key in ((200, 'total_usd'), (250, None)):
                    self.check(f'{marker} {value}{unit} {direction}한 {level}달러입니다.',
                               row, prior, [key, level_key])
                    self.check(f'{level}달러로 {marker} {value}{unit} {direction}했습니다.',
                               row, prior, [level_key, key])
            self.check(f'{marker} 50달러 감소했습니다.', row, other, [None])

    def test_relative_levels_and_equal_display_values(self):
        row = dict(country_name="미국", total_usd=300000000, compare_total_usd=316400000, change_pct=-5.2)
        self.check('미국 수출은 전년 대비 5.2% 감소한 3억 달러입니다.', row, '2024-02',
                   ['change_pct', 'total_usd'])
        for prefix, prior in [('전년','2024-02'), ('전월','2025-01')]:
            self.check(f'{prefix} 수출액은 3억 달러, 당기 수출액은 3억 달러입니다.', row, prior,
                       ['compare_total_usd','total_usd'])
        self.check('전월 수출액은 3억 달러입니다.', row, '2024-02', [None])
        self.check('2월 수출액은 3억 달러입니다.', row, '2024-02', ['total_usd'])

    def test_four_roles_and_decoy_columns(self):
        row = dict(total_usd=200, compare_total_usd=250, change_usd=-50,
                   yoy_pct=-20, mom_pct=-8)
        for level, expected in ((200, 'total_usd'), (250, None)):
            self.check(f'비교 기간 수출액은 250달러, 당기 수출액은 {level}달러, '
                       '전년 대비 감소액은 50달러이고 전년 동월 대비 증감률은 -20%입니다.',
                       row, '2024-02', ['compare_total_usd', expected, 'change_usd', 'yoy_pct'])
        for key in ('total_usd', 'compare_total_usd'):
            self.check('전년 대비 50달러 감소했습니다.', {key: 50}, '2024-02', [None])
        self.check('수출액은 50달러입니다.', {'change_usd': 50}, '2024-02', [None])

    def test_monthly_delta_keeps_its_own_month(self):
        source = dict(period={'start': '2025-01', 'end': '2025-12'},
                      compare_period={'start': '2024-01', 'end': '2024-12'},
                      rows=[dict(month='2025-01', change_usd=-50), dict(month='2025-02', change_usd=-60)])
        for amount, path in ((60, ((0, ('rows', '1', 'change_usd')),)), (50, ())):
            ok, (check,) = inspect_sentence(f'2월 수출은 전년 동월 대비 {amount}달러 감소했습니다.', [source])
            self.assertEqual(check.matches, path)
            self.assertEqual(ok, bool(path))

    def test_comparison_links_and_change_negation(self):
        row = dict(yoy_pct=-20, mom_pct=10, change_usd=-50)
        for marker in ('전년 동월 대비', '지난해 같은 달에 비해', '전년 동월과 비교해', '작년과 비교했을 때'):
            for predicate, column in (('20% 감소했습니다', 'yoy_pct'), ('10% 증가했습니다', None),
                                      ('20% 감소하지 않았습니다', None), ('20% 증가하지는 않았습니다', 'yoy_pct'),
                                      ('10% 감소하지 않았습니다', 'yoy_pct'), ('20% 안 늘었습니다', 'yoy_pct'),
                                      ('50달러 줄어들지 않았습니다', None), ('50달러 늘어나지 않았습니다', 'change_usd')):
                self.check(f'수출은 {marker} {predicate}.', row, '2024-02', [column])
        for marker in ('전월에 비하여', '지난달과 비교하면'):
            self.check(f'{marker} 10% 증가하지 않았습니다.', row, '2024-02', [None])
            self.check(f'{marker} 10% 감소하지 않았습니다.', row, '2024-02', ['mom_pct'])
        for marker in ('직전 분기에 비해', '2025년 2월 수출은 직전 분기와 비교해'):
            self.check(f'{marker} 10% 증가하지 않았습니다.', row, '2024-02', [None])
        self.check('작년과 비교해 20% 증가하지 않았습니다.', {'mom_pct': -20}, '2024-02', [None])
        self.check('작년과 비교해 20% 증가하지 않았습니다.', {'yoy_pct': None}, '2024-02', [None])
        self.check('작년과 비교해 20% 증가하지 않았고 지난달에 비해 10% 증가했습니다.',
                   row, '2024-02', ['yoy_pct', 'mom_pct'])
        self.check('지난달에 비해 10% 증가했고 작년과 비교해 20% 감소하지 않았습니다.',
                   row, '2024-02', ['mom_pct', None])
        self.check('전년 대비 20% 증가하지 않았습니다.', dict(yoy_pct=20, change_pct=-20), '2024-02', [None])
        for threshold, column in ((10, None), (20, None), (30, 'yoy_pct')):
            self.check(f'지난해 같은 달에 비해 {threshold}% 이상 감소하지 않았습니다.',
                       row, '2024-02', [column])
        self.check('전년 대비 20% 감소하지 않은 적이 있습니다.', row, '2024-02', [None])
