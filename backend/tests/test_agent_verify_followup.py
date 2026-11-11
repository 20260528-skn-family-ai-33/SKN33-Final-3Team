"""Second verify review: synthetic snapshot cases, including false passes and false rejections."""
import unittest

from backend.app.agents.state import SqlColumn
from backend.app.agents.verify import check_sentence, snapshot_evidence
from backend.tests.test_agent_verify_regressions import result

PERIOD = {'start': '2025-01', 'end': '2025-12'}

PREVIOUS = {'start': '2024-01', 'end': '2024-12'}

TWO_YEARS = {'start': '2023-01', 'end': '2023-12'}

AMOUNT = [{'country_name': '미국', 'total_usd': 300_000_000}]

RATES = [{'country_name': '미국', 'share_pct': 40, 'yoy_pct': 5}]

COUNTRIES = [{'country_name': '미국', 'total_usd': 100}, {'country_name': '일본', 'total_usd': 200}]

PRODUCTS = [
    {'country_name': '미국', 'product_name': '기초화장품', 'amount_usd': 100},
    {'country_name': '미국', 'product_name': '반도체', 'amount_usd': 50},
    {'country_name': '일본', 'product_name': '반도체', 'amount_usd': 200},
]

YEARS = [{'country_name': '미국', 'total_usd': 200, 'compare_total_usd': 100, 'change_pct': 100}]

def case(name, sentence, rows, expected, **kwargs):
    return dict(name=name, sentence=sentence, rows=rows, expected=expected, **kwargs)

CASES = [
    case('expired_total', '수출 총액은 3억 달러입니다.', [], False, expired=True, digest={'total_usd': 300_000_000}),
    case('expired_denominator', '전체 교역액은 3억 달러입니다.', [], False, expired=True, digest={'denominator_usd': 300_000_000}),
    case('usd_control', '미국 수출 총액은 3억 달러입니다.', AMOUNT, True),
    case('won_rejected', '미국 수출 총액은 3억 원입니다.', AMOUNT, False),
    case('gbp_rejected', '미국 수출 총액은 3억 GBP입니다.', AMOUNT, False),
    case('aud_rejected', '미국 수출 총액은 3억 호주달러입니다.', AMOUNT, False),
    case('share_control', '미국 점유율은 40%입니다.', RATES, True),
    case('swapped_share', '미국 점유율은 5%입니다.', RATES, False),
    case('separate_metrics_control', '미국 점유율은 40%이며, 증감률은 5%입니다.', RATES, True),
    case('joined_metrics', '미국의 점유율 40%와 증감률 5%가 확인됩니다.', RATES, True),
    case('sql_change_alias', '미국 증감률은 5%입니다.', [{'country_name': '미국', 'comparison_pct': 5}], True,
         columns=[{'key': 'comparison_pct', 'label': '비교 증감률', 'type': 'number', 'unit': '%'}]),
    case('sql_share_alias', '미국 점유율은 40%입니다.', [{'country_name': '미국', 'export_share_pct': 40}], True,
         columns=[{'key': 'export_share_pct', 'label': '수출 점유율', 'type': 'number', 'unit': '%'}]),
    case('inherited_country_control', '미국 기초화장품은 100달러, 반도체는 50달러입니다.', PRODUCTS, True),
    case('inherited_country_wrong', '미국 기초화장품은 100달러, 반도체는 200달러입니다.', PRODUCTS, False),
    case('grouped_countries_each', '미국과 일본의 수출액은 각각 200달러입니다.', COUNTRIES, False),
    case('grouped_countries_sum', '미국과 일본 수출 총액 합계는 200달러입니다.', COUNTRIES, False),
    case('grouped_products_each', '미국 기초화장품과 반도체의 수출액은 각각 50달러입니다.', PRODUCTS, False),
    case('annual_control', '2024년 미국 수출 총액은 100달러이고 2025년은 200달러입니다.', YEARS, True, compare=PREVIOUS),
    case('annual_swapped', '2024년 미국 수출 총액은 200달러이고 2025년은 100달러입니다.', YEARS, False, compare=PREVIOUS),
    case('yoy_control', '2025년 미국 수출은 전년 대비 100% 증가했습니다.', YEARS, True, compare=PREVIOUS),
    case('wrong_yoy_canonical', '2025년 미국 수출은 전년 대비 100% 증가했습니다.', YEARS, False, compare=TWO_YEARS),
    case('wrong_yoy_synonym', '2025년 미국 수출은 작년 대비 100% 증가했습니다.', YEARS, False, compare=TWO_YEARS),
    case('mixed_annual_monthly', '2024년 미국 수출 총액은 100달러, 2025년 1월은 50달러입니다.',
         [{'country_name': '미국', 'compare_total_usd': 100, 'month': '2025-01', 'amount_usd': 50}], True, compare=PREVIOUS),
    case('two_period_ranges', '2024년 1월부터 12월까지 미국 수출 총액은 100달러, 2025년 1월부터 12월까지는 200달러입니다.',
         YEARS, True, compare=PREVIOUS),
]

class ReviewRegressions(unittest.TestCase):
    def assert_result(self, sentence, rows, expected, **kwargs):
        for column in kwargs.get('columns', []):
            SqlColumn.model_validate(column)
        state = result(sentence, rows, **kwargs)
        check = state.verification.checks[0]
        self.assertEqual(check.status, 'PASS' if expected else 'MISMATCH')
        self.assertEqual(state.verification.status, 'PASS' if expected else 'NEEDS_REVIEW')
        self.assertEqual(bool(state.draft.turn_summary), expected)
        self.assertEqual(check.basis, 'SNAPSHOT')
        if kwargs.get('expired'):
            self.assertEqual(check.reason, 'SOURCE_UNAVAILABLE')

    def test_all_24_review_cases(self):
        for case in CASES:
            with self.subTest(name=case['name'], sentence=case['sentence']):
                self.assert_result(**{key: value for key, value in case.items() if key != 'name'})

    def test_foreign_currency_variants_and_real_usd(self):
        names={'미국','일본','호주','캐나다','스위스','영국'}
        for unit in ('GBP', 'gbp', '파운드', '영국 파운드', 'AUD', '호주달러', '호주 달러',
                     'CAD', '캐나다 달러', 'CHF', '스위스 프랑', 'KZT', '페소', '£', '€'):
            with self.subTest(unit=unit):
                self.assert_result(f'미국 수출 총액은 3억 {unit}입니다.', AMOUNT, False,
                                   known_names=names, countries=names)
        for unit in ('USD', 'usd', '달러', '미국 달러', '미달러', 'US$', '$', ''):
            with self.subTest(unit=unit):
                self.assert_result(f'일본 수출 총액은 3억 {unit}입니다.',
                                   [{'country_name': '일본', 'total_usd': 300_000_000}], True,
                                   known_names=names, countries=names)
        self.assert_result('호주 수출 총액은 3억 호주달러입니다.',
                           [{'country_name': '호주', 'total_usd': 300_000_000}], False,
                           known_names=names, countries=names)

    def test_unknown_or_conflicting_percentage_metadata_is_not_a_change_metric(self):
        for key, label in (('ratio', '비율'), ('export_share_pct', '증감률')):
            for text in ('미국 증감률은 40%입니다.', '미국 수출은 40% 증가했습니다.'):
                with self.subTest(key=key, text=text):
                    self.assert_result(text, [{'country_name': '미국', key: 40}], False,
                                       columns=[{'key': key, 'label': label, 'type': 'number', 'unit': '%'}])

    def test_grouped_each_checks_every_target_and_keeps_common_country(self):
        for text, rows, good in [
            ('미국과 일본은 각각 100달러입니다.', [{'country_name': name, 'total_usd': 100} for name in ('미국', '일본')], True),
            ('미국과 일본은 각각 100달러와 200달러입니다.', COUNTRIES, True),
            ('미국과 일본은 각각 200달러와 100달러입니다.', COUNTRIES, False),
            ('미국과일본은 각각 100달러입니다.', COUNTRIES, False),
            ('일본과미국은 각각 200달러와 100달러입니다.', COUNTRIES, True),
            ('미국과 일본은 모두 200달러입니다.', COUNTRIES, False),
            ('미국 기초화장품과 반도체는 각각 100달러와 50달러입니다.', PRODUCTS, True),
            ('미국 기초화장품과 반도체는 각각 100달러와 200달러입니다.', PRODUCTS, False),
            ('미국 기초화장품은 100달러이고 일본 반도체는 200달러입니다.', PRODUCTS, True),
        ]:
            with self.subTest(text=text):
                self.assert_result(text, rows, good)

    def test_group_total_requires_saved_aggregate(self):
        rows = [{'label': '미국과 일본', 'total_usd': 300}]
        self.assert_result('미국과 일본 수출 총액 합계는 300달러입니다.', rows, True)
        self.assert_result('미국과 일본 수출 총액 합계는 200달러입니다.', rows, False)
        # 단독 국가 행 또는 일부 행의 임의 합산을 묶음 전체의 저장 근거로 쓰지 않는다.
        self.assert_result('미국과 일본 수출 총액 합계는 300달러입니다.', COUNTRIES, False)

    def test_relative_period_synonyms_check_both_comparison_endpoints(self):
        for expression in ('전년', '작년', '지난해', '지난 해'):
            for compare, good in ((PREVIOUS, True), (TWO_YEARS, False),
                                  ({'start': '2024-02', 'end': '2024-12'}, False)):
                with self.subTest(expression=expression, compare=compare):
                    self.assert_result(f'미국 수출은 {expression} 대비 100% 증가했습니다.', YEARS, good, compare=compare)
        for expression in ('전월', '지난달', '지난 달'):
            for before, good in (('2024-12', True), ('2024-01', False)):
                with self.subTest(expression=expression, before=before):
                    self.assert_result(f'미국 수출은 {expression} 대비 100% 증가했습니다.', YEARS, good,
                                       period={'start': '2025-01', 'end': '2025-01'},
                                       compare={'start': before, 'end': before})

    def test_sql_metric_aliases_do_not_exchange_meaning_or_sign(self):
        for share_key, change_key, labels in (('export_share_pct', 'comparison_pct', False),
                                               ('ratio_a', 'ratio_b', True)):
            columns = [{'key': share_key, 'unit': '%', 'type': 'number', 'label': '수출 점유율' if labels else '비율 A'},
                       {'key': change_key, 'unit': '%', 'type': 'number', 'label': '비교 증감률' if labels else '비율 B'}]
            rows = [{'country_name': '미국', share_key: 40, change_key: -5}]
            for text, good in [('미국 점유율은 40%입니다.', True), ('미국 증감률은 -5%입니다.', True),
                               ('미국 감소율은 5%입니다.', True), ('미국 증가율은 5%입니다.', False),
                               ('미국 점유율은 5%입니다.', False), ('미국 증감률은 40%입니다.', False),
                               ('미국 수출이 40% 증가했습니다.', False), ('미국 점유율은 40%p입니다.', False)]:
                with self.subTest(key=share_key, text=text):
                    self.assert_result(text, rows, good, columns=columns)

    def test_sql_comparison_alias_keeps_period_scope_in_monthly_rows(self):
        rows = [{'country_name': '미국', 'month': '2025-01', 'comparison_pct': 100}]
        for label in ('비교 증감률', '전년 대비 증감률'):
            columns = [{'key': 'comparison_pct', 'label': label, 'unit': '%', 'type': 'number'}]
            for compare, good in ((PREVIOUS, True), (TWO_YEARS, False)):
                with self.subTest(label=label, compare=compare):
                    self.assert_result('2025년 미국 수출은 작년 대비 100% 증가했습니다.', rows, good,
                                       columns=columns, compare=compare)

    def test_joined_metrics_bind_each_number_in_both_orders(self):
        for connector in ('와 ', ' 및 ', '이고 ', '이며 '):
            for first, second in (('점유율 40%', '증감률 5%'), ('증감률 5%', '점유율 40%')):
                good = f'미국의 {first}{connector}{second}가 확인됩니다.'
                bad = good.replace('40%', '99%', 1).replace('5%', '40%', 1).replace('99%', '5%', 1)
                with self.subTest(text=good):
                    self.assert_result(good, RATES, True)
                    self.assert_result(bad, RATES, False)
        self.assert_result('미국의 40%의 점유율과 5%의 증감률이 확인됩니다.', RATES, True)
        self.assert_result('미국의 5%의 점유율과 40%의 증감률이 확인됩니다.', RATES, False)

    def test_mixed_time_granularity_both_orders_and_swapped_values(self):
        rows = [{'country_name': '미국', 'compare_total_usd': 100, 'month': '2025-01', 'amount_usd': 50}]
        for first, second in (('2024년 미국 수출 총액은 100달러', '2025년 1월은 50달러'),
                              ('2025년 1월 미국 수출은 50달러', '2024년 총액은 100달러')):
            for connector in (', ', '이고 '):
                good = first + connector + second + '입니다.'
                bad = good.replace('100달러', '999달러').replace('50달러', '100달러').replace('999달러', '50달러')
                with self.subTest(text=good):
                    self.assert_result(good, rows, True, compare=PREVIOUS)
                    self.assert_result(bad, rows, False, compare=PREVIOUS)

    def test_multiple_ranges_remain_whole_with_or_without_comma(self):
        for connector in (', ', '이고 '):
            good = f'2024년 1월부터 12월까지 미국 수출 총액은 100달러{connector}2025년 1월부터 12월까지는 200달러입니다.'
            bad = good.replace('100달러', '999달러').replace('200달러', '100달러').replace('999달러', '200달러')
            with self.subTest(text=good):
                self.assert_result(good, YEARS, True, compare=PREVIOUS)
                self.assert_result(bad, YEARS, False, compare=PREVIOUS)

    def test_month_names_without_separate_values_cannot_drop_the_first_month(self):
        self.assert_result('2025년 1월과 2월은 모두 50달러입니다.',
                           [{'country_name': '미국', 'month': '2025-01', 'amount_usd': 100},
                            {'country_name': '미국', 'month': '2025-02', 'amount_usd': 50}], False)

    def test_prediction_anchor_is_distinct_from_twelve_month_horizon(self):
        source=snapshot_evidence({'meta': {'kind': 'PREDICTION', 'anchor': '2025-02'},
                                  'rows': [{'country_name': '미국', 'exp_12m': 100, 'growth_pct': 10}]})
        for sentence in ('2025년 2월 기준 향후 12개월 수출 증가율은 10%입니다.',
                         '2025년 2월 기준 과거 12개월 수출액은 100달러입니다.'):
            with self.subTest(sentence=sentence):
                self.assertTrue(check_sentence(sentence, [source])[0])
                self.assertFalse(check_sentence(sentence.replace('2월', '3월', 1), [source])[0])
                self.assertFalse(check_sentence(sentence.replace('12개월', '1개월'), [source])[0])


if __name__ == '__main__':
    unittest.main()
