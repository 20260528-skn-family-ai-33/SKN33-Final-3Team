import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend.app.agents.charts import build_chart, time_series
from backend.app.agents.state import Conditions
from backend.app.agents.verify import check_sentence


class EvidenceTest(unittest.TestCase):
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
