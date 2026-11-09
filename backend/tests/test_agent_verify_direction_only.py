"""Direction-only assertions require a comparison cell; instructions do not."""
import unittest
from backend.app.agents.verify import inspect_sentence, mark_unverified
from backend.tests.test_agent_verify_regressions import result


class DirectionOnlyTest(unittest.TestCase):
    rows = [dict(country_name='미국', yoy_pct=-20, mom_pct=10, amount_usd=100),
            dict(country_name='일본', yoy_pct=20, mom_pct=-10, amount_usd=200)]

    def check(self, text, expected, cells, *, rows=None, numbers=None, **options):
        rows = self.rows if rows is None else rows
        source = dict(period=options.get('period'), compare_period=options.get('compare'),
                      trade_flow='EXPORT', rows=rows)
        ok, checks = inspect_sentence(text, [source], known_names=('미국', '일본', '반도체'), countries=('미국','일본'))
        self.assertEqual(ok, expected, text)
        self.assertEqual([c.matches for c in checks], [
            ((0, ('rows', str(i), key)),) if key else () for i, key in cells], text)
        state = result(text, rows, **options)
        check, = state.verification.checks
        self.assertEqual(check.basis, 'SNAPSHOT')
        self.assertEqual(check.numbers, numbers or [])
        self.assertEqual(check.status, 'PASS' if expected else 'MISMATCH', text)
        self.assertEqual(state.draft.turn_summary, text if expected else '', text)
        return state

    def test_period_subject_flow_sign_and_absence(self):
        for marker, up, key in [('전년 대비', False, 'yoy_pct'), ('지난달과 비교해', True, 'mom_pct')]:
            for word, sign in [('증가했습니다', True), ('감소했다', False), ('늘었다', True), ('줄었습니다', False)]:
                self.check(f'미국 수출은 {marker} {word}.', sign==up, [(0, key if sign==up else None)])
        for text in ('미국 수입은 전년 대비 감소했다.', '미국 수출 가격은 전년 대비 감소했다.',
                     '미국 수출은 직전 분기에 비해 감소했다.', '미국 수출은 증가했다.',
                     '전년 대비 증가했다.', '미국 반도체 수출은 전년 대비 감소했다.'):
            self.check(text, False, [(0, None)])
        for value in (None, 0):
            self.check('미국 수출은 전년보다 증가했다.', False, [(0,None)],
                       rows=[dict(country_name='미국',yoy_pct=value,mom_pct=10,amount_usd=100)])
        self.check('미국 수출은 전년보다 감소했다.', False, [(0,None)], rows=[])
        self.check('전년 대비 증가했다.', True, [(0,'yoy_pct')], rows=[dict(yoy_pct=10)])
        self.check('미국 수출은 전년보다 감소했다.', False, [(0,None)],
                   rows=[dict(country_name='미국',product_name='반도체',yoy_pct=-2)])
        self.check('미국 수출은 전년보다 감소했다.', False, [(0,None)],
                   rows=[dict(country_name='미국',yoy_pct=-2),dict(country_name='미국',yoy_pct=2)])

    def test_measure_and_comparison_periods(self):
        period=dict(start='2025-02',end='2025-02')
        prior=dict(start='2024-02',end='2024-02')
        rows=[dict(country_name='미국',product_name='반도체',change_usd=-2,share_change_pp=2)]
        self.check('미국 반도체 수출은 전년보다 감소했다.',True,[(0,'change_usd')],rows=rows,period=period,compare=prior)
        self.check('미국 반도체 점유율은 지난해보다 증가했다.',True,[(0,'share_change_pp')],rows=rows,period=period,compare=prior)
        self.check('미국 반도체 점유율은 지난달보다 증가했다.',False,[(0,None)],rows=rows,period=period,compare=prior)

    def test_separate_assertions_and_negation(self):
        self.check('미국 수출은 전년보다 감소했고 일본은 전월보다 하락했습니다.',True,[(0,'yoy_pct'),(1,'mom_pct')])
        self.check('미국 수출은 전년보다 증가했고, 일본은 전년보다 증가했습니다.',False,[(0,None),(1,'yoy_pct')])
        self.check('미국 수출은 전년보다 증가하지 않았습니다.',True,[(0,'yoy_pct')])
        self.check('미국 수출은 전년보다 감소하지 않았습니다.',False,[(0,None)])
        self.check('미국 수출은 100달러이고 일본 수출은 전월 대비 감소했습니다.',True,
                   [(0,'amount_usd'),(1,'mom_pct')],numbers=['100달러'])
        self.check('미국 수출은 전년 대비 증가했고 일본 수출은 200달러입니다.',False,
                   [(0,None),(1,'amount_usd')],numbers=['200달러'])

    def test_guidance_and_timeout(self):
        for text in ('아래 표를 참고하세요.', '전년 대비 증가 여부를 확인하세요.',
                     '전년 대비 증가했는지 확인하세요.', '감소한 품목은 아래 표에서 확인할 수 있습니다.'):
            state=self.check(text,True,[])
            mark_unverified(state)
            self.assertEqual(state.verification.checks[0].status,'PASS')
        state=self.check('미국 수출은 전년 대비 감소했습니다.',True,[(0,'yoy_pct')])
        mark_unverified(state)
        self.assertEqual(state.verification.checks[0].status,'MISMATCH')
        self.assertEqual(state.draft.turn_summary,'')

    def test_date_and_wrong_metric_decoys(self):
        period = dict(start='2025-01', end='2025-02')
        rows = [dict(country_name='미국', month='2025-01', yoy_pct=1),
                dict(country_name='미국', month='2025-02', yoy_pct=-2)]
        self.check('미국 2월 수출은 전년 동월보다 감소했다.', True, [(1,'yoy_pct')], rows=rows, period=period)
        self.check('미국 2월 수출은 전년 동월보다 증가했다.', False, [(0,None)], rows=rows, period=period)
        self.check('미국 수출은 전년보다 증가했다.', False, [(0,None)],
                   rows=[dict(country_name='미국', export_price_yoy_pct=9)])
        self.check('결과는 다음 줄.', True, [])
