"""Replay the recorded annual comparison questions, claims, and SQL projections."""
import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path

from backend.app.agents.sql import annual_sum_sources, validate_sql
from backend.app.agents.state import Conditions, SqlDraft
from backend.app.agents.verify import inspect_sentence, snapshot_evidence

RECORDED = json.loads((Path(__file__).parent / 'fixtures' / 'annual_comparison_replay.json').read_text())
LABELS = {'country_labels': {'US': '미국'}, 'product_labels': {('854232', 'HS2022'): '메모리 집적회로'}}


def inspect(line, payload):
    source = snapshot_evidence(payload, **LABELS)
    source['_country_mentions'] = [('대미', '미국')]
    return inspect_sentence(line, [source], countries=['미국', '일본'], known_names=['미국', '일본', '메모리 집적회로', '의약품'])


class AnnualComparisonReplayTest(unittest.TestCase):
    def setUp(self):
        self.payload = deepcopy(RECORDED['sql_payload'])

    def test_nationwide_difference_uses_date_index_without_changing_the_saved_query(self):
        draft = SqlDraft.model_validate(RECORDED['timed_out_draft'])
        conditions = Conditions.model_validate({**self.payload['conditions'], 'country_codes': [], 'hs_items': []})
        original = validate_sql(draft)
        planned = validate_sql(draft, conditions)
        self.assertEqual(planned.count('FORCE INDEX (`PRIMARY`)'), 2)
        self.assertEqual(planned.replace(' FORCE INDEX (`PRIMARY`)', ''), original)
        # Replay the unchanged SELECT with a total row, another edition and a
        # third year. Only the two in-scope sums may contribute to the difference.
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE country (country_code TEXT, country_role TEXT);
            INSERT INTO country VALUES ('US','COUNTRY'), ('TOTAL','TOTAL');
            CREATE TABLE export_monthly (trade_month TEXT, country_code TEXT, hs_ver TEXT, amount_usd INTEGER);
            INSERT INTO export_monthly VALUES
                ('2024-01-01','US','HS2022',100), ('2025-01-01','US','HS2022',125),
                ('2025-01-01','TOTAL','HS2022',125), ('2025-01-01','US','HS2017',10),
                ('2023-01-01','US','HS2022',75);
        """)
        self.assertEqual(db.execute(original).fetchone(), (100,125,25))
        db.execute("UPDATE export_monthly SET amount_usd=80 WHERE trade_month='2025-01-01' AND country_code='US' AND hs_ver='HS2022'")
        self.assertEqual(db.execute(original).fetchone(), (100,80,-20))
        different_scope = conditions.model_copy(update={'country_codes': ['US']})
        with self.assertRaises(ValueError):
            validate_sql(draft, different_scope)
        different_year = draft.model_copy(update={'sql': draft.sql.replace('2024-', '2023-')})
        with self.assertRaises(ValueError):
            validate_sql(different_year, conditions)
        scoped_sql = RECORDED['sql_cases'][0]['sql']
        scoped = SqlDraft(sql=scoped_sql, columns=[{**column, 'type':'number'} for column in self.payload['columns']],
                         summary='연간 비교', unsupported=None)
        self.assertNotIn('FORCE INDEX', validate_sql(scoped, Conditions.model_validate(self.payload['conditions'])))
        amount_only = draft.model_copy(update={'sql':draft.sql.replace(
            'y25.amount_usd - y24.amount_usd', 'y24.amount_usd')})
        self.assertNotIn('FORCE INDEX', validate_sql(amount_only, conditions))

    def test_rate_word_order_requires_correct_years_target_and_value(self):
        line = RECORDED['sql_cases'][0]['sentences'][1]
        self.payload['sql'] = [RECORDED['sql_cases'][0]['sql']]
        ok, (check,) = inspect(line, self.payload)
        self.assertTrue(ok)
        self.assertEqual(check.matches, ((0, ('rows','0','change_pct')),))
        for wrong in (line.replace('2024','2023'), line.replace('2025','2024').replace('2024년을','2025년을'),
                      line.replace('22.7%', '18.5%'), line.replace('수출액', '일본 수출액'),
                      '2024년과 2025년의 수출액 차이는 22.7%입니다.'):
            with self.subTest(wrong=wrong):
                self.assertFalse(inspect(wrong, self.payload)[0])
        self.assertTrue(inspect(RECORDED['other_rate_sentence'], self.payload)[0])

    def test_saved_sql_annual_amounts_are_bound_to_their_own_filters(self):
        for case in RECORDED['sql_cases']:
            sql = case['sql']
            self.payload['sql'] = [sql]
            cells = annual_sum_sources(self.payload)
            for year in ('2024','2025'):
                self.assertEqual(cells['export_'+year+'_usd'], (year,'US','854232','HS2022','EXPORT'))
            # The independent HS label + code group check still rejects the original.
            line = case['sentences'][0]
            self.assertFalse(inspect(line, self.payload)[0])
            single = line.replace('(HS 854232)', '')
            self.assertTrue(inspect(single, self.payload)[0])
            self.assertTrue(inspect(case['sentences'][1], self.payload)[0])
            for old, new in [('3억 542만', '3억 543만'), ('2024년','2023년'),
                             ('대미','일본'), ('메모리 집적회로','의약품')]:
                with self.subTest(old=old):
                    self.assertFalse(inspect(single.replace(old,new), self.payload)[0])
            for old,new in [("'US'", "'JP'"), ("'854232'", "'123456'"), ("'HS2022'", "'HS2017'"),
                            ('2024-01-01', '2024-02-01'), ('SUM(', 'AVG('),
                            ('JOIN country','LEFT JOIN country')]:
                with self.subTest(old=old):
                    self.payload['sql'] = [sql.replace(old,new)]
                    self.assertNotIn('export_2024_usd', annual_sum_sources(self.payload))
        self.payload['sql'] = [RECORDED['sql_cases'][1]['sql'].replace("< '2025-01-01'", "< '2024-12-01'")]
        self.assertNotIn('export_2024_usd', annual_sum_sources(self.payload))
        joined = RECORDED['sql_cases'][0]['sql']
        for invalid in [joined.replace("AND c.country_role", "OR c.country_role"),
                        joined.replace("c.country_code = e.country_code", "c.country_code = c.country_code")]:
            self.payload['sql'] = [invalid]
            self.assertEqual(annual_sum_sources(self.payload), {})

    def test_monthly_list_matches_each_comparison_month_and_amount(self):
        self.payload.update(meta={'kind':'TREND'}, rows=RECORDED['monthly_rows'])
        self.payload['conditions']['hs_items'] = []
        line = RECORDED['monthly_sentence']
        ok, checks = inspect(line, self.payload)
        self.assertTrue(ok)
        self.assertEqual([c.matches for c in checks],
                         [((0, ('rows',str(i),'compare_amount_usd')),) for i in range(6)])
        for wrong in (line.replace('2024년','2025년'), line.replace('1월','7월',1),
                      line.replace('5,044만','5,045만'), '일본 '+line):
            with self.subTest(wrong=wrong):
                self.assertFalse(inspect(wrong, self.payload)[0])

    def test_date_claims_match_both_exact_boundaries_and_roles(self):
        self.payload['conditions'].update(period={'start':'2024-01-01','end':'2024-12-31'},
                                         compare_period={'start':'2023-01-01','end':'2023-12-31'})
        line = RECORDED['range_sentence']
        self.assertTrue(inspect(line, self.payload)[0])
        for wrong in (line.replace('2023년','2022년'), line.replace('1월 1일','1월 2일',1),
                      line.replace('12월 31일','12월 30일',1),
                      line.replace('비교 기간','TEMP').replace('대상 기간','비교 기간').replace('TEMP','대상 기간')):
            with self.subTest(wrong=wrong):
                self.assertFalse(inspect(wrong, self.payload)[0])
        self.payload['conditions'] = RECORDED['sql_payload']['conditions']
        end = RECORDED['end_sentence']
        self.assertTrue(inspect(end, self.payload)[0])
        self.assertFalse(inspect(end.replace('2024년 12월','2025년 12월'), self.payload)[0])
