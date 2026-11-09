"""Semantic binding regressions: the 22 cases from the saved review, unchanged."""
import unittest
from copy import deepcopy
from decimal import Decimal
from backend.app.agents.verify import check_sentence, inspect_sentence, verify, mark_unverified
from backend.app.agents.verify_claims import NameCatalog, parse_sentence
from backend.app.agents.context import completed_context
from backend.app.agents.planner import planner_input
from backend.app.agents.state import Conditions
from backend.tests.test_agent_verify_regressions import result

PERIOD = {'start': '2025-01', 'end': '2025-12'}

PREVIOUS = {'start': '2024-01', 'end': '2024-12'}

TWO_YEARS = {'start': '2023-01', 'end': '2023-12'}

AMOUNT = [{'country_name': '미국', 'total_usd': 300_000_000}]

RATES = [{'country_name': '미국', 'share_pct': 40, 'yoy_pct': 5}]

GROUP = [{'country_name': '미국', 'total_usd': 100},
         {'country_name': '일본', 'total_usd': 200},
         {'label': '미국과 일본', 'total_usd': 300}]

FLOWS = [{'country_name': '미국', 'export_share_pct': 40, 'import_share_pct': 5}]

FLOW_COLUMNS = [{'key': 'export_share_pct', 'label': '수출 점유율', 'type': 'number', 'unit': '%'},
                {'key': 'import_share_pct', 'label': '수입 점유율', 'type': 'number', 'unit': '%'}]

MONTHLY = [
    {'country_name': '미국', 'month': '2025-01', 'amount_usd': 100, 'compare_month': '2024-01', 'compare_amount_usd': 70},
    {'country_name': '미국', 'month': '2025-02', 'amount_usd': 200, 'compare_month': '2024-02', 'compare_amount_usd': 80},
]

MONTH_OPTIONS = {'period': {'start': '2025-01', 'end': '2025-02'},
                 'compare': {'start': '2024-01', 'end': '2024-02'}}

def case(name, area, text, rows, expected, **options):
    return dict(name=name, area=area, text=text, rows=rows, expected=expected, **options)

CASES = [
    case('currency_prefix_gbp', 'currency', '미국 수출 총액은 GBP 3억입니다.', AMOUNT, False),
    case('currency_prefix_symbol', 'currency', '미국 수출 총액은 £3억입니다.', AMOUNT, False),
    case('currency_parenthesized', 'currency', '미국 수출 총액은 3억 (원)입니다.', AMOUNT, False),
    case('currency_suffix_gbp_control', 'currency', '미국 수출 총액은 3억 GBP입니다.', AMOUNT, False),
    case('currency_prefix_usd_control', 'currency', '미국 수출 총액은 USD 3억입니다.', AMOUNT, True),
    case('group_total_used_as_each', 'target', '미국과 일본 수출 총액은 각각 300달러입니다.', GROUP, False),
    case('group_total_control', 'target', '미국과 일본 수출 총액 합계는 300달러입니다.', GROUP, True),
    # This review is dated 2026-11-06: the explicit 2025 year also removes any relative-date ambiguity.
    case('last_year_is_a_date_not_comparison', 'relative_period', '지난해인 2025년 미국 점유율은 40%입니다.', RATES, True),
    case('absolute_year_control', 'relative_period', '2025년 미국 점유율은 40%입니다.', RATES, True),
    case('yoy_comparison_control', 'relative_period', '2025년 미국 수출은 작년 대비 100% 증가했습니다.',
         [{'country_name': '미국', 'change_pct': 100}], True, compare=PREVIOUS),
    case('wrong_yoy_comparison_control', 'relative_period', '2025년 미국 수출은 작년 대비 100% 증가했습니다.',
         [{'country_name': '미국', 'change_pct': 100}], False, compare=TWO_YEARS),
    # sql._check_conditions explicitly permits queries comparing export and import tables.
    case('import_share_used_for_export', 'sql_alias', '미국 수출 점유율은 5%입니다.', FLOWS, False, columns=FLOW_COLUMNS),
    case('export_share_control', 'sql_alias', '미국 수출 점유율은 40%입니다.', FLOWS, True, columns=FLOW_COLUMNS),
    case('metric_list_correct', 'joined_metrics', '미국의 점유율과 증감률은 각각 40%와 5%입니다.', RATES, True),
    case('metric_list_swapped', 'joined_metrics', '미국의 점유율과 증감률은 각각 5%와 40%입니다.', RATES, False),
    case('interleaved_metrics_control', 'joined_metrics', '미국의 점유율 40%와 증감률 5%가 확인됩니다.', RATES, True),
    case('interleaved_metrics_swapped_control', 'joined_metrics', '미국의 점유율 5%와 증감률 40%가 확인됩니다.', RATES, False),
    case('month_loses_inherited_year', 'mixed_period', '2025년 1월 미국 수출은 100달러, 2월은 80달러입니다.', MONTHLY, False, **MONTH_OPTIONS),
    case('month_inherited_year_control', 'mixed_period', '2025년 1월 미국 수출은 100달러, 2월은 200달러입니다.', MONTHLY, True, **MONTH_OPTIONS),
    case('explicit_wrong_month_control', 'mixed_period', '2025년 1월 미국 수출은 100달러, 2025년 2월은 80달러입니다.', MONTHLY, False, **MONTH_OPTIONS),
    case('expired_source_control', 'snapshot', '미국 수출 총액은 3억 달러입니다.', [], False, expired=True,
         digest={'total_usd': 300_000_000}),
    case('snapshot_wins_over_digest_control', 'snapshot', '미국 수출 총액은 3억 달러입니다.', AMOUNT, True,
         digest={'rows': [{'country_name': '미국', 'total_usd': 999}]}),
]

class SemanticBindingCases(unittest.TestCase):
    def test_saved_22_cases(self):
        for data in CASES:
            with self.subTest(name=data['name'], text=data['text']):
                options={key:value for key,value in data.items() if key not in ('name','area','text','rows','expected')}
                state=result(data['text'],data['rows'],**options)
                expected=data['expected']
                self.assertEqual(state.verification.status,'PASS' if expected else 'NEEDS_REVIEW')
                self.assertEqual(state.verification.checks[0].status,'PASS' if expected else 'MISMATCH')
                self.assertEqual(bool(state.draft.turn_summary),expected)
                self.assertEqual(state.verification.checks[0].basis,'SNAPSHOT')

class SemanticInvariants(unittest.TestCase):
    def assert_sentence(self,text,source,expected):
        self.assertEqual(check_sentence(text,[source],known_names=('미국','일본','반도체'),
                                        countries=('미국','일본'))[0],expected,text)

    def test_parsing_does_not_choose_meaning_from_matching_values(self):
        text='2025년 미국의 점유율과 증감률은 각각 40%와 5%입니다.'
        source={'period':PERIOD,'rows':RATES}
        parsed=parse_sentence(text,NameCatalog([source],countries=('미국',)))
        changed=deepcopy(source)
        changed['rows'][0].update(share_pct=5,yoy_pct=40)
        self.assertEqual(parsed,parse_sentence(text,NameCatalog([changed],countries=('미국',))))
        self.assertEqual([c.metric.family for c in parsed.claims],['SHARE','CHANGE'])
        self.assertEqual([c.quantity.value for c in parsed.claims],[Decimal(40),Decimal(5)])
        self.assertTrue(all(c.targets=={'미국'} and c.time.years=={'2025'} for c in parsed.claims))
        self.assert_sentence(text,source,True)
        self.assert_sentence(text,changed,False)

    def test_matches_keep_original_columns_and_source_coordinates(self):
        source={'period':PERIOD,'rows':FLOWS,'columns':FLOW_COLUMNS}
        ok,checks=inspect_sentence('미국 수출 점유율은 40%입니다.',[source])
        self.assertTrue(ok)
        self.assertEqual(checks[0].matches,((0,('rows','0','export_share_pct')),))
        self.assertEqual(checks[0].claim.targets,{'미국'})
        self.assertEqual(checks[0].claim.metric.flow,'EXPORT')
        # A matching number in the wrong column cannot change the parsed claim.
        other=deepcopy(source);other['rows'][0]['export_share_pct']=99
        other['rows'][0]['import_share_pct']=40
        bad,changed=inspect_sentence('미국 수출 점유율은 40%입니다.',[other])
        self.assertFalse(bad)
        self.assertEqual(checks[0].claim,changed[0].claim)

    def test_currency_positions_and_spacing_keep_every_numeric_claim(self):
        for unit,valid in [('USD',True),('usd',True),('$',True),('US$',True),
                           ('GBP',False),('gbp',False),('£',False),('KRW',False),('원',False)]:
            for amount in (f'{unit}3억',f'{unit} 3억',f'3억 {unit}',f'3억({unit})',f'3억 ({unit})'):
                text=f'미국 수출 총액은 {amount}입니다.'
                with self.subTest(text=text):
                    state=result(text,AMOUNT)
                    self.assertEqual(state.verification.status=='PASS',valid)
                    self.assertEqual(bool(state.draft.turn_summary),valid)
                    self.assertEqual(len(state.verification.checks[0].numbers),1)
        self.assertEqual(result('미국 수출 총액은 USD 3억(원)입니다.',AMOUNT).verification.status,'NEEDS_REVIEW')

    def test_metric_lists_both_orders_shared_units_and_wrong_cardinality(self):
        for first,second,a,b in [('점유율','증감률',40,5),('증감률','점유율',5,40)]:
            for connector in ('과 ',' 및 ', ', '):
                for left_unit in ('%',''):
                    for swapped in (False,True):
                        x,y=(b,a) if swapped else (a,b)
                        text=f'미국의 {first}{connector}{second}은 각각 {x}{left_unit}와 {y}%입니다.'
                        with self.subTest(text=text):
                            self.assertEqual(result(text,RATES).verification.status=='PASS',not swapped)
        for values in ('40%','40%와 5%와 40%'):
            self.assertEqual(result(f'미국의 점유율과 증감률은 각각 {values}입니다.',RATES).verification.status,'NEEDS_REVIEW')

    def test_flow_is_preserved_in_metric_lists(self):
        rows=[{'country_name':'미국','export_amount_usd':100,'import_amount_usd':200}]
        for a,b,valid in [(100,200,True),(200,100,False)]:
            self.assertEqual(result(f'미국 수출액과 수입액은 각각 {a}달러와 {b}달러입니다.',rows).verification.status=='PASS',valid)
        for a,b,valid in [(40,5,True),(5,40,False)]:
            self.assertEqual(result(f'미국 수출 점유율과 수입 점유율은 각각 {a}%와 {b}%입니다.',
                                    FLOWS,columns=FLOW_COLUMNS).verification.status=='PASS',valid)

    def test_ordered_month_list_preserves_each_period(self):
        for a,b,valid in [(100,200,True),(200,100,False),(70,80,False)]:
            self.assertEqual(result(f'2025년 1월과 2월 미국 수출은 각각 {a}달러와 {b}달러입니다.',
                                    MONTHLY,**MONTH_OPTIONS).verification.status=='PASS',valid)
        for a,b,valid in [(100,100,True),(100,50,False)]:
            rows=[{'month':'2025-01','amount_usd':a},{'month':'2025-02','amount_usd':b}]
            self.assertEqual(result('2025년 1월과 2월 수출은 모두 100달러입니다.',rows,**MONTH_OPTIONS).verification.status=='PASS',valid)

    def test_group_aggregate_never_proves_an_individual_claim(self):
        for members,values in [('미국과 일본',(100,200)),('일본과 미국',(200,100))]:
            for join in ('각각','모두'):
                for value in (values[0],values[1],300):
                    self.assertEqual(result(f'{members} 수출액은 {join} {value}달러입니다.',GROUP).verification.status,'NEEDS_REVIEW')
            self.assertEqual(result(f'{members} 수출액은 각각 {values[0]}달러와 {values[1]}달러입니다.',GROUP).verification.status,'PASS')
            self.assertEqual(result(f'{members} 수출 총액 합계는 300달러입니다.',GROUP).verification.status,'PASS')

    def test_month_and_metric_context_are_inherited_independently(self):
        source={'period':MONTH_OPTIONS['period'],'rows':[
            {'country_name':'미국','month':'2025-01','amount_usd':100,'share_pct':40,'yoy_pct':5},
            {'country_name':'미국','month':'2025-02','amount_usd':200,'share_pct':30,'yoy_pct':7}]}
        for connector in (', ','이며 ','이고 '):
            for value,valid in [(40,True),(30,False)]:
                self.assert_sentence(f'2025년 1월 미국 수출은 100달러{connector}점유율은 {value}%입니다.',source,valid)
            for value,valid in [(30,True),(7,False)]:
                self.assert_sentence(f'2025년 1월 미국 점유율은 40%{connector}2월은 {value}%입니다.',source,valid)
            for value,valid in [(200,True),(80,False)]:
                self.assertEqual(result(f'2025년 1월 미국 수출은 100달러{connector}2월은 {value}달러입니다.',MONTHLY,**MONTH_OPTIONS).verification.status=='PASS',valid)

    def test_subject_after_rank_stays_with_that_rank(self):
        source={'rows':[{'country_name':'미국','rank':1},{'country_name':'일본','rank':2}]}
        for text,valid in [('1위는 미국입니다.',True),('1위는 일본입니다.',False),
                           ('1위는 미국, 2위는 일본입니다.',True),('1위는 일본, 2위는 미국입니다.',False),
                           ('미국은 1위, 일본은 2위입니다.',True),('미국은 2위, 일본은 1위입니다.',False)]:
            self.assert_sentence(text,source,valid)

    def test_comparison_base_year_does_not_become_the_result_year(self):
        source={'period':PERIOD,'compare_period':PREVIOUS,
                'rows':[{'country_name':'미국','change_pct':100}]}
        for text,valid in [('2025년 미국 증감률은 100%입니다.',True),
                           ('2024년 미국 증감률은 100%입니다.',False),
                           ('2024년 대비 미국 수출은 100% 증가했습니다.',True)]:
            self.assert_sentence(text,source,valid)

    def test_one_changed_dimension_cannot_borrow_another_cells_number(self):
        rows=[{'country_name':'미국','product_name':'반도체','month':'2025-01',
               'export_amount_usd':100,'import_amount_usd':200,'monthly_avg_usd':50,'share_pct':30},
              {'country_name':'일본','product_name':'반도체','month':'2025-01','export_amount_usd':200},
              {'country_name':'미국','product_name':'기초화장품','month':'2025-01','export_amount_usd':200},
              {'country_name':'미국','product_name':'반도체','month':'2025-02','export_amount_usd':200}]
        source={'period':MONTH_OPTIONS['period'],'trade_flow':'EXPORT','rows':rows}
        text='2025년 1월 미국 반도체 수출액은 100달러입니다.'
        self.assert_sentence(text,source,True)
        for before,after in [('1월','2월'),('미국','일본'),('반도체','기초화장품'),
                             ('수출액','수입액'),('100달러','100원'),('수출액','수출 월평균')]:
            with self.subTest(dimension=(before,after)):
                self.assert_sentence(text.replace(before,after),source,False)
        self.assert_sentence(text,{**source,'rows':list(reversed(rows))},True)

    def test_saved_root_total_has_priority_over_digest_total(self):
        state=result('수출 총액은 100달러입니다.',[],digest={'total_usd':999})
        step=next(iter(state.results))
        source={'_verification_source':'SNAPSHOT','total_usd':100,'rows':[]}
        verify(state,snapshots={step:source})
        self.assertEqual(state.verification.status,'PASS')
        state.draft.conclusion[0].sentences=['수출 총액은 999달러입니다.']
        state.draft.turn_summary='수출 총액은 999달러입니다.'
        verify(state,snapshots={step:source})
        self.assertEqual(state.verification.status,'NEEDS_REVIEW')
        self.assertEqual(state.draft.turn_summary,'')
        # Verify's rejection reaches the next planner turn through the existing context contract.
        state.plan.tasks[0].conditions=Conditions(country_codes=['US'])
        state.input.context=completed_context(state)
        self.assertEqual(state.input.context.recent[-1].summary,'')
        self.assertEqual(planner_input(state)['recent'][-1]['summary'],'')

    def test_amount_level_change_and_threshold_are_distinct(self):
        source={'rows':[{'country_name':'미국','total_usd':100,'change_usd':20}]}
        for text,valid in [('미국 수출은 100달러입니다.',True),('미국 수출은 100달러 증가했습니다.',False),
                           ('미국 수출은 20달러 증가했습니다.',True),('미국 수출은 20달러입니다.',False),
                           ('미국 수출액은 100달러 미만입니다.',False),('미국 수출액은 101달러 미만입니다.',True),
                           ('미국 수출액은 100달러 이상입니다.',True),('미국 수출액은 100달러 초과입니다.',False)]:
            self.assert_sentence(text,source,valid)

    def test_summary_is_checked_even_when_visible_answer_is_correct(self):
        state=result('미국 점유율은 40%입니다.',RATES)
        state.draft.turn_summary='미국 점유율은 5%입니다.'
        verify(state,countries=('미국','일본'))
        self.assertEqual(state.verification.checks[0].status,'PASS')
        self.assertEqual(state.verification.status,'NEEDS_REVIEW')
        self.assertEqual(state.draft.turn_summary,'')
        state.draft.explanation=['미국 점유율은 5%입니다.']
        state.draft.turn_summary='미국 점유율은 40%입니다.'
        verify(state,countries=('미국','일본'))
        self.assertEqual([c.status for c in state.verification.checks],['PASS','MISMATCH'])
        self.assertEqual(state.draft.turn_summary,'미국 점유율은 40%입니다.')

    def test_timeout_retains_prefixed_quantity_and_clears_summary(self):
        state=result('미국 수출 총액은 USD3억입니다.',AMOUNT)
        mark_unverified(state)
        self.assertEqual(state.verification.status,'NEEDS_REVIEW')
        self.assertEqual(state.verification.checks[0].numbers,['USD3억'])
        self.assertEqual(state.draft.turn_summary,'')


if __name__=='__main__':
    unittest.main()
