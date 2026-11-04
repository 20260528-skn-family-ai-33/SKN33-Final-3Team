import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.app.agents.charts import build_chart, time_series
from backend.app.agents.state import Conditions
from backend.app.agents.verify import check_sentence, snapshot_evidence


class EvidenceTest(unittest.TestCase):
    def test_prediction_clause_inherits_target_when_omitted(self):
        digest=snapshot_evidence({'meta':{'kind':'PREDICTION','anchor':'2025-02'},'rows':[
            {'country_name':'미국','exp_12m':100,'growth_pct':10},
            {'country_name':'일본','exp_12m':200,'growth_pct':90}]})
        for growth,expected in ((10,True),(90,False)):
            text=f'미국의 과거 12개월 수출액은 100달러고 향후 12개월 수출 증가율은 {growth}%입니다.'
            self.assertEqual(check_sentence(text,[digest],known_names=('미국','일본'))[0],expected)

    def test_prediction_horizon_is_not_the_anchor_month(self):
        digest=snapshot_evidence({'meta':{'kind':'PREDICTION','anchor':'2025-02'},
                                 'rows':[{'exp_12m':100,'growth_pct':10}]})
        self.assertTrue(check_sentence('과거 12개월 수출액은 100달러입니다.',[digest])[0])
        self.assertTrue(check_sentence('향후 12개월 수출 증가율은 10%입니다.',[digest])[0])
        self.assertFalse(check_sentence('과거 1개월 수출액은 100달러입니다.',[digest])[0])
        self.assertFalse(check_sentence('향후 12개월 수출액은 100달러입니다.',[digest])[0])
        self.assertFalse(check_sentence('과거 12개월 수출 증가율은 10%입니다.',[digest])[0])
        self.assertTrue(check_sentence('과거 12개월 수출액은 100달러, 향후 12개월 수출 증가율은 10%입니다.',[digest])[0])
        self.assertTrue(check_sentence('과거 12개월 수출액은 100달러이고 향후 12개월 수출 증가율은 10%입니다.',[digest])[0])
        self.assertTrue(check_sentence('과거 12개월 수출액은 100달러, 향후 수출 증가율은 10%입니다.',[digest])[0])
        self.assertTrue(check_sentence('과거 대비 향후 12개월 수출 증가율은 10%입니다.',[digest])[0])
        self.assertFalse(check_sentence('예측 수출액은 100달러입니다.',[digest])[0])
        self.assertFalse(check_sentence('향후 12개월 수출액은 과거 12개월과 같은 100달러입니다.',[digest])[0])
        self.assertTrue(check_sentence('과거 12개월 수출액은 100달러로 집계됐고 향후 12개월 수출 증가율은 10%입니다.',[digest])[0])
        for connector in ('달러고','달러였고'):
            self.assertTrue(check_sentence(f'과거 12개월 수출액은 100{connector} 향후 12개월 수출 증가율은 10%입니다.',[digest])[0])

    def test_duration_year_and_single_month_totals_use_fact_bounds(self):
        digest={'period':{'start':'2025-01','end':'2025-03'},
                'compare_period':{'start':'2024-01','end':'2024-12'},
                'rows':[{'month':'2025-02','amount_usd':10,'total_usd':100,'compare_total_usd':200}]}
        self.assertFalse(check_sentence('12개월 수출 총액은 100달러입니다.',[digest])[0])
        self.assertTrue(check_sentence('12개월 수출 총액은 200달러입니다.',[digest])[0])
        self.assertFalse(check_sentence('2025년 수출액은 10달러입니다.',[digest])[0])
        single={'period':{'start':'2025-02','end':'2025-02'},'rows':[{'export_amount_usd':100}]}
        self.assertTrue(check_sentence('2025년 2월 수출 총액은 100달러입니다.',[single])[0])

    def test_single_month_and_comparison_use_the_actual_month(self):
        digest={'period':{'start':'2025-03','end':'2025-04'},
                'compare_period':{'start':'2024-03','end':'2024-04'},'rows':[
            {'month':'2025-03','amount_usd':100,'total_usd':180,'compare_total_usd':130,
             'compare_month':'2024-03','compare_amount_usd':70},
            {'month':'2025-04','amount_usd':80,'compare_month':'2024-04','compare_amount_usd':60}]}
        for sentence, expected in [('3월 수출액은 80달러입니다.', False),
                                   ('2025년 3월 수출액은 180달러입니다.', False),
                                   ('2024년 3월 수출액은 130달러입니다.', False),
                                   ('2024년 수출 총액은 180달러입니다.', False),
                                   ('2025년 3월부터 2025년 4월까지 수출 총액은 180달러입니다.', True),
                                   ('2025년 3월부터 2025년 4월까지 수출 총액은 130달러입니다.', False),
                                   ('2025년 3월 수출액은 70달러입니다.', False),
                                   ('2024년 3월 수출액은 70달러입니다.', True),
                                   ('2025년 3월 수출액은 100달러입니다.', True)]:
            with self.subTest(sentence=sentence):
                self.assertEqual(check_sentence(sentence,[digest])[0],expected)

    def test_comma_less_comparison_checks_each_month_claim(self):
        digest={'period':{'start':'2025-03','end':'2025-03'},'compare_period':{'start':'2024-03','end':'2024-03'},
                'targets':[{'total_usd':100,'compare_total_usd':80,'change_pct':25,'monthly':[
                    {'month':'2025-03','amount_usd':100,'compare_amount_usd':80}]}]}  # 예전 결과: compare_month 없음
        self.assertTrue(check_sentence('2025년 3월 수출액은 100달러로 2024년 3월 80달러보다 25% 늘었습니다.',[digest])[0])
        self.assertFalse(check_sentence('2025년 3월 수출액은 80달러로 2024년 3월 100달러보다 25% 늘었습니다.',[digest])[0])
        self.assertTrue(check_sentence('2024년 3월 수출액은 80달러입니다.',[digest])[0])  # 비교 월을 기간 차이로 복원

    def test_prediction_reference_month_and_anchor_year(self):
        digest=snapshot_evidence({'meta':{'kind':'PREDICTION','anchor':'2026-10'},
                                 'rows':[{'country_name':'베트남','rank':3,'growth_pct':5.2}]})
        self.assertTrue(check_sentence('최근 기준월 기준 향후 12개월 예측 증감률은 +5.2%입니다.',[digest])[0])
        self.assertTrue(check_sentence('2026년 전망에서 베트남은 3위입니다.',[digest],known_names=('베트남',))[0])
        self.assertFalse(check_sentence('2025년 전망에서 베트남은 3위입니다.',[digest],known_names=('베트남',))[0])

    def test_monthly_claims_cannot_exchange_amounts(self):
        digest={'period':{'start':'2025-01','end':'2025-02'},'rows':[
            {'month':'2025-01','amount_usd':1000}, {'month':'2025-02','amount_usd':2000}]}
        self.assertTrue(check_sentence('2025년 1월 수출은 1,000달러, 2025년 2월 수출은 2,000달러입니다.',[digest])[0])
        self.assertFalse(check_sentence('2025년 1월 수출은 2,000달러, 2025년 2월 수출은 1,000달러입니다.',[digest])[0])
        self.assertFalse(check_sentence('1월 수출은 2,000달러, 2월 수출은 1,000달러입니다.',[digest])[0])

    def test_period_ranking_and_annual_total_require_matching_bounds(self):
        digest={'period':{'start':'2025-01','end':'2025-03'},'rows':[
            {'country_name':'미국','rank':1,'share_pct':30,'export_amount_usd':100}]}
        self.assertTrue(check_sentence('2025년 1월부터 3월까지 미국은 1위입니다.',[digest])[0])
        self.assertFalse(check_sentence('2025년 2월 미국은 1위입니다.',[digest])[0])
        self.assertFalse(check_sentence('2025년 2월 미국 비중은 30%입니다.',[digest])[0])
        self.assertFalse(check_sentence('2025년 미국 수출 총액은 100달러입니다.',[digest])[0])

    def test_amount_has_to_match_country_metric_and_rounded_display(self):
        digest={'period':{'start':'2024-01','end':'2024-12'},'trade_flow':'EXPORT', 'targets':[
            {'country_name':'미국','total_usd':345000000,'monthly_avg_usd':None},
            {'country_name':'중국','total_usd':20000,'monthly_avg_usd':2000}]}
        def valid(text):return check_sentence(text,[digest],known_names=['미국','중국','일본'])[0]
        self.assertTrue(valid('2024년 미국 수출은 3.5억 달러입니다.'))
        for text in ['2023년 미국 수출은 3.5억 달러입니다.','미국 수입은 3.5억 달러입니다.',
                     '중국 수출은 3.5억 달러입니다.','미국 월평균은 0 USD입니다.',
                     '일본 수출은 3.5억 달러입니다.']:
            with self.subTest(text=text): self.assertFalse(valid(text))

    def test_ratio_and_percentage_points_are_distinct(self):
        digest={'items':[{'name':'미국','share_pct':3.5}]}
        self.assertTrue(check_sentence('미국 비중은 3.5%입니다. [1]',[digest],known_names=['미국'])[0])
        self.assertFalse(check_sentence('미국 비중은 3.5%p입니다.',[digest],known_names=['미국'])[0])

    def test_time_buckets_preserve_unverified_month(self):
        rows=[{'month':f'{2020+i//12}-{i%12+1:02d}','amount_usd':None if i==1 else 1} for i in range(61)]
        granularity, labels, values=time_series(rows,['amount_usd'])
        self.assertEqual((granularity,labels[0],values[0][:2]), ('QUARTER','2020-Q1',[None,3]))

    def test_different_length_comparison_chart_reads_full_snapshot_averages(self):
        task=SimpleNamespace(task_id='t1',conditions=Conditions(period={'start':'2025-01-01','end':'2025-03-01'},
            compare_period={'start':'2024-01-01','end':'2024-12-01'}))
        rows=[{'country_code':'US','country_name':'미국','hs_code':None,'hs_ver':None,'name':'전체 품목',
               'requested_months':3,'compare_requested_months':12,'compare_monthly_avg_usd':None,'monthly_avg_usd':20}]
        with patch('backend.app.agents.charts.read_snapshot', return_value={'meta':{'kind':'TREND'},'rows':rows}):
            result,full,_=build_chart(None,1,task,'r')
        self.assertEqual(result.chart.type,'grouped_bar')
        self.assertEqual(full,['미국'])
        self.assertEqual([s.values for s in result.chart.series],[[None],[20]])
