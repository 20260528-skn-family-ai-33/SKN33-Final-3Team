"""Snapshot and cell-level checks for percentage-point comparisons."""
import unittest

from backend.app.agents.verify import inspect_sentence
from backend.tests.test_agent_verify_regressions import result


class SharePointsTest(unittest.TestCase):
    def check(self, marker, row, key, *, compare=None, columns=(), amount='2.1', direction='상승', month=''):
        text = f'미국 {month}점유율은 {marker} {amount}%p {direction}했습니다.'
        current = dict(start='2025-02', end='2025-02')
        prior = dict(start=compare, end=compare) if compare else None
        row = dict(country_name='미국', **row)
        with self.subTest(text=text, row=row, compare=compare, columns=columns):
            ok, (claim,) = inspect_sentence(text, [dict(rows=[row], period=current, compare_period=prior, columns=columns)])
            self.assertEqual(ok, bool(key))
            self.assertEqual(claim.matches, ((0, ('rows', '0', key)),) if key else ())
            state = result(text, [row], period=current, compare=prior, columns=columns)
            check, = state.verification.checks
            self.assertEqual(check.basis, 'SNAPSHOT')
            self.assertEqual(check.status, 'PASS' if key else 'MISMATCH')
            self.assertEqual(state.verification.status, 'PASS' if key else 'NEEDS_REVIEW')
            self.assertEqual(state.draft.turn_summary, text if key else '')

    def test_named_axes_and_equal_decoys(self):
        row = dict(share_yoy_pp=2.1, share_mom_pp=2.1, yoy_pct=2.1, share_pct=2.1)
        self.check('전년보다', row, 'share_yoy_pp')
        self.check('지난달에 비해', row, 'share_mom_pp')
        self.check('전년보다', dict(share_mom_pp=2.1, yoy_pct=2.1), None)
        self.check('전년보다', row, None, direction='하락')
        self.check('전월보다', row, None, amount='3.1')
        self.check('전년보다', dict(share_change_pp=None), None, compare='2024-02')

    def test_saved_ranges_and_row_month(self):
        row = dict(share_change_pp=2.1)
        for marker, prior, key in [('전년보다', '2024-02', 'share_change_pp'),
                                    ('전월보다', '2025-01', 'share_change_pp'),
                                    ('전년보다', '2025-01', None), ('전월보다', '2024-02', None),
                                    ('전년보다', '2024-01', None), ('전년보다', None, None)]:
            self.check(marker, row, key, compare=prior)
        self.check('전년 동월 대비', dict(row, month='2025-02', compare_month='2024-02'), 'share_change_pp', month='2월 ')
        self.check('전월보다', dict(row, month='2025-02', compare_month='2025-01'), 'share_change_pp', month='2월 ')
        self.check('전년보다', dict(row, month='2025-02', compare_month='2024-02'), None, compare='2025-01')
        self.check('전년보다', dict(row, month='bad', compare_month='2024-02'), None)
        self.check('전년보다', dict(row, month='2025-02', compare_month='2024-13'), None)


    def test_saved_labels_and_conflicts(self):
        row = dict(custom_pp=2.1)
        for label, key in [('전년 동월 대비 점유율 변화', 'custom_pp'), ('전월 대비 점유율 변화', None),
                           ('Market share change (YoY)', 'custom_pp'),
                           ('전년 대비 수익률 변화', None), ('점유율 변화', None)]:
            self.check('전년보다', row, key, columns=[dict(key='custom_pp', label=label, unit='%p')])
        self.check('전년보다', dict(share_yoy_pp=2.1), None,
                   columns=[dict(key='share_yoy_pp', label='전월 대비 비중 변화', unit='%p')])
        self.check('전년보다', dict(share_change_pp=2.1), None, compare='2024-02',
                   columns=[dict(key='share_change_pp', label='직전 분기 대비 점유율 변화', unit='%p')])
        self.check('전년보다', dict(share_yoy_pp=2.1), None,
                   columns=[dict(key='share_yoy_pp', label='점유율', unit='%')])

    def test_two_claims_keep_their_own_axes_and_months(self):
        rows = [dict(month='2025-01', share_yoy_pp=2.1, share_mom_pp=-1),
                dict(month='2025-02', share_yoy_pp=3.1, share_mom_pp=-1.5)]
        period = dict(start='2025-01', end='2025-02')
        for value, valid in [(1.5, True), (1, False)]:
            text = f'1월 점유율은 전년보다 2.1%p 상승했고, 2월 점유율은 전월보다 {value}%p 하락했습니다.'
            ok, checks = inspect_sentence(text, [dict(rows=rows, period=period)])
            self.assertEqual(ok, valid)
            self.assertEqual([c.matches for c in checks], [
                ((0, ('rows','0','share_yoy_pp')),),
                ((0, ('rows','1','share_mom_pp')),) if valid else ()])
