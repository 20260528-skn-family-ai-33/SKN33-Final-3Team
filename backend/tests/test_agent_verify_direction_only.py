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
        for text in ('미국 수입은 전년 대비 감소했다.',
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

    def test_article_subject_and_cited_trade_claim(self):
        for text in ('미국 수출 가격은 전년 대비 감소했다.', '수요가 감소했습니다 [1].'):
            self.check(text, True, [])
        for word, expected in [('감소', True), ('증가', False)]:
            self.check(f'일본 수요 둔화에도 미국 수출은 전년 대비 {word}했습니다 [1].',
                       expected, [(0, 'yoy_pct' if expected else None)])
        self.check('미국 자동차 수출은 전년 대비 감소했다.', False, [(0, None)])

    def test_aside_positions_preserve_trade_subjects(self):
        for prefix in ('일본 수요 둔화에도 미국 수출은', '미국 수출은 일본 수요 둔화에도',
                       '수요가 둔화된 가운데 미국 수출은'):
            for word, ok in [('감소', True), ('증가', False)]:
                self.check(f'{prefix} 전년 대비 {word}했다 [1].', ok,
                           [(0, 'yoy_pct' if ok else None)])
        for word, ok in [('감소', True), ('증가', False)]:
            self.check(f'미국 수출이 전년 대비 {word}했고 수요가 감소한 영향으로 일본 수출은 전년 대비 증가했다.',
                       ok, [(0, 'yoy_pct' if ok else None), (1, 'yoy_pct')])
            self.check(f'미국 수출은 전년 대비 {word}한 반면 수요가 감소했다.',
                       ok, [(0, 'yoy_pct' if ok else None)])

    def test_reported_change_and_unresolved_direction(self):
        for word, ok in [('감소', True), ('증가', False)]:
            self.check(f'미국 수출은 전년 대비 {word}한 것으로 집계됐습니다 [1].',
                       ok, [(0, 'yoy_pct' if ok else None)])
        for text in ('미국 수출은 꾸준한 증가세를 보이고 있습니다.',
                     '미국 수출은 증가 양상을 나타냈습니다.'):
            self.check(text, False, [(0, None)])
        self.check('미국 수출은 100달러로 꾸준한 증가세를 보이고 있습니다.',
                   False, [(0, 'amount_usd'), (0, None)], numbers=['100달러'])
        self.check('미국 수출의 전년 대비 증가 여부는 표에서 확인하세요.', True, [])

    def test_amount_level_and_direction_have_independent_cells(self):
        for text in ('미국 수출은 100달러로 전년 대비 감소했다.',
                     '미국 수출은 전년 대비 100달러로 감소했다.',
                     '미국 수출은 100달러를 기록하며 전년 대비 감소했다.',
                     '미국 수출은 100달러로 전년 대비 증가하지 않았다.'):
            self.check(text, True, [(0, 'amount_usd'), (0, 'yoy_pct')], numbers=['100달러'])
        self.check('미국 수출은 100달러로 전월 대비 증가했다.', True,
                   [(0, 'amount_usd'), (0, 'mom_pct')], numbers=['100달러'])
        self.check('미국 수출은 100달러로 전년 대비 증가했다.', False,
                   [(0, 'amount_usd'), (0, None)], numbers=['100달러'])
        self.check('미국 수출은 200달러로 전년 대비 감소했다.', False,
                   [(0, None), (0, 'yoy_pct')], numbers=['200달러'])
        self.check('미국 수출은 100달러로 전년 대비 증가했다.', False,
                   [(0, 'amount_usd'), (0, None)], numbers=['100달러'],
                   rows=[dict(country_name='미국', amount_usd=100, mom_pct=10)])
        self.check('미국 수출은 전년 대비 100달러 증가했다.', True,
                   [(0, 'change_usd')], numbers=['100달러'],
                   rows=[dict(country_name='미국', amount_usd=300, change_usd=100)],
                   period=dict(start='2025-02', end='2025-02'),
                   compare=dict(start='2024-02', end='2024-02'))

    def test_direction_before_amount(self):
        rows = [dict(country_name='미국', amount_usd=100, yoy_pct=5, mom_pct=-5)]
        for phrase, ok, key in [('전년 대비 증가해', True, 'yoy_pct'),
                                ('전년 대비 감소해', False, None),
                                ('전월 대비 줄어', True, 'mom_pct'),
                                ('전월 대비 하락한 결과', True, 'mom_pct'),
                                ('전년 대비 감소하지 않아', True, 'yoy_pct')]:
            self.check(f'미국 수출은 {phrase} 100달러가 됐다.', ok,
                       [(0, 'amount_usd'), (0, key)], numbers=['100달러'], rows=rows)
        self.check('미국 수출은 전년 대비 증가해 200달러가 됐다.', False,
                   [(0, None), (0, 'yoy_pct')], numbers=['200달러'], rows=rows)
        self.check('미국 수출은 전년 대비 증가해 100달러가 됐다.', False,
                   [(0, 'amount_usd'), (0, None)], numbers=['100달러'],
                   rows=[dict(country_name='미국', amount_usd=100, mom_pct=5)])
        self.check('수요가 감소해 미국 수출은 100달러가 됐다.', True,
                   [(0, 'amount_usd')], numbers=['100달러'], rows=rows)
        self.check('미국 수출은 전월 대비 5% 감소해 100달러가 됐다.', True,
                   [(0, 'mom_pct'), (0, 'amount_usd')], numbers=['5%', '100달러'], rows=rows)

    def test_review_article_background_pairs(self):
        period = dict(start='2025-01', end='2025-12')
        prior = dict(start='2024-01', end='2024-12')
        cases = (
            '수요 회복으로 미국 수출이 전년 대비 증가했습니다 [1].',
            '수요 회복에 힘입어 미국 수출이 전년 대비 증가했습니다.',
            '현지 수요가 회복된 시기에 미국 수출도 전년 대비 증가했습니다 [1].',
            '수요 회복과 함께 미국 수출은 전년 대비 증가했습니다 [1].',
            '반도체 가격 반등과 맞물려 미국 수출이 전년 대비 증가했습니다 [2].',
            '수요가 회복되는 가운데 미국 수출은 전년 대비 증가했습니다 [1].',
            '수요가 회복된 가운데 미국 수출은 전년 대비 증가했습니다 [1].',
            '일본 수요 둔화에도 미국 수출은 전년 대비 증가했습니다 [1].',
            '수요 회복으로 미국 수출이 전년 대비 50% 증가했습니다 [1].',
        )
        for value in (50, -50):
            rows = [dict(country_name='미국', change_pct=value),
                    dict(country_name='일본', change_pct=-value),
                    dict(country_name='미국', product_name='반도체', change_pct=-value)]
            for sentence in cases:
                with self.subTest(value=value, sentence=sentence):
                    self.check(sentence, value > 0, [(0, 'change_pct' if value > 0 else None)],
                               rows=rows, period=period, compare=prior,
                               numbers=['50%'] if '50%' in sentence else [])

    def test_review_unknown_subject_and_qualitative_claims(self):
        rows = [dict(country_name='미국', yoy_pct=50, mom_pct=-10),
                dict(country_name='미국', product_name='반도체', yoy_pct=-50)]
        for text in ('미국 수출은 수요 회복에 힘입어 전년 대비 증가했습니다.',
                     '수요 회복으로 전년 대비 미국 수출이 증가했습니다.'):
            self.check(text, True, [(0, 'yoy_pct')], rows=rows)
            self.check(text.replace('증가', '감소'), False, [(0, None)], rows=rows)
            self.check(text.replace('전년', '전월').replace('증가', '감소'),
                       True, [(0, 'mom_pct')], rows=rows)
        text = '수요 회복으로 미국 자동차 수출은 전년 대비 50% 증가했습니다.'
        self.check(text, False, [(0, None)], rows=rows, numbers=['50%'])
        self.check(text, True, [(2, 'yoy_pct')], numbers=['50%'],
                   rows=rows + [dict(country_name='미국', product_name='자동차', yoy_pct=50)])
        for text in ('수요 회복으로 미국 수출이 전년 대비 급감했습니다.',
                     '수요 회복으로 미국 수출은 전년과 비슷한 수준입니다.'):
            self.check(text, False, [(0, None)], rows=rows)
        self.check('미국 수출의 급감 여부를 확인하세요.', True, [], rows=rows)

    def test_product_qualifier_order_never_borrows_country_change(self):
        country = dict(country_name='미국', yoy_pct=50)
        product = dict(country_name='미국', product_name='자동차', yoy_pct=-50)
        for body in ('자동차의 미국 수출은 전년 대비 50% 증가했습니다.',
                     '미국 자동차 수출은 전년 대비 50% 증가했습니다.',
                     '미국 수출 중 자동차는 전년 대비 50% 증가했습니다.',
                     '미국의 전년 대비 수출 증가율은 자동차 기준 50%입니다.'):
            with self.subTest(body=body):
                self.check(body, False, [(0, None)], rows=[country], numbers=['50%'])
                self.check(body, False, [(0, None)], rows=[country, product], numbers=['50%'])
                self.check(body.replace('증가', '감소'), True, [(1, 'yoy_pct')],
                           rows=[country, product], numbers=['50%'])

    def test_extreme_direction_with_and_without_magnitude(self):
        rows = [dict(country_name='미국', yoy_pct=50, mom_pct=-50)]
        for phrase in ('급락했습니다', '곤두박질쳤습니다'):
            for text in (f'미국 수출은 전년 대비 {phrase}.', f'전년 대비 미국 수출이 {phrase}.'):
                self.check(text, False, [(0, None)], rows=rows)
            for axis, ok in [('전년', False), ('전월', True)]:
                self.check(f'미국 수출은 {axis} 대비 50% {phrase}.', ok,
                           [(0, 'mom_pct' if ok else None)], numbers=['50%'], rows=rows)
        self.check('전년 대비 미국 수출은 50% 치솟았습니다.', True,
                   [(0, 'yoy_pct')], numbers=['50%'], rows=rows)
        self.check('전월 대비 미국 수출은 50% 치솟았습니다.', False,
                   [(0, None)], numbers=['50%'], rows=rows)
        self.check('미국 수출은 전년 대비 50% 곤두박질쳤습니다.', False,
                   [(0, None)], numbers=['50%'], rows=[dict(country_name='미국', mom_pct=-50)])
        self.check('미국 수출의 급락 여부를 확인하세요.', True, [], rows=rows)

    def test_single_scoped_axis_and_unsupported_path(self):
        rows = [dict(country_name='미국', yoy_pct=-20, amount_usd=100),
                dict(country_name='일본', mom_pct=10)]
        self.check('미국 수출은 감소했다.', True, [(0, 'yoy_pct')], rows=rows)
        self.check('미국 수출은 증가했다.', False, [(0, None)], rows=rows)
        for text in ('미국 수출은 꾸준히 증가하는 추세입니다.',
                     '미국 수출은 하반기로 갈수록 감소했다.'):
            self.check(text, False, [(0, None)], rows=rows)
        self.check('미국 수출은 100달러였고 하반기로 갈수록 꾸준히 증가했다.',
                   False, [(0, 'amount_usd'), (0, None)], rows=rows, numbers=['100달러'])

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
