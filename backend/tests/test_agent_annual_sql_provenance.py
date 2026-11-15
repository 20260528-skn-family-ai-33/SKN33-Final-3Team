"""Immutable SQL projection proof for annual nationwide totals and differences."""
import unittest
from copy import deepcopy

from backend.app.agents.sql import annual_sql_sources
from backend.app.agents.verify import snapshot_evidence
from backend.app.agents.verify_claims import NameCatalog
from backend.app.agents.verify_evidence import build_facts


CONDITIONS = {
    'period': {'start': '2025-01-01', 'end': '2025-12-31'},
    'compare_period': {'start': '2024-01-01', 'end': '2024-12-31'},
    'country_codes': [], 'hs_items': [], 'trade_flow': 'EXPORT',
}
SUM = """SELECT SUM(e.amount_usd) AS value FROM export_monthly e
    JOIN country c ON c.country_code = e.country_code AND c.country_role = 'COUNTRY'
    WHERE e.hs_ver = 'HS2022' AND e.trade_month BETWEEN '{year}-01-01' AND '{year}-12-31'"""
CTE = """WITH older AS ({old}), newer AS ({new})
    SELECT o.value AS old_usd, n.value AS new_usd,
           n.value - o.value AS signed_usd FROM older o CROSS JOIN newer n""".format(
               old=SUM.format(year=2024), new=SUM.format(year=2025))
CASE = """SELECT SUM(CASE WHEN e.trade_month BETWEEN '2024-01-01' AND '2024-12-31'
                 THEN e.amount_usd END) AS old_usd,
               SUM(CASE WHEN e.trade_month BETWEEN '2025-01-01' AND '2025-12-31'
                 THEN e.amount_usd END) AS new_usd,
               SUM(CASE WHEN e.trade_month BETWEEN '2025-01-01' AND '2025-12-31'
                 THEN e.amount_usd END) -
               SUM(CASE WHEN e.trade_month BETWEEN '2024-01-01' AND '2024-12-31'
                 THEN e.amount_usd END) AS signed_usd
    FROM export_monthly e JOIN country c
      ON c.country_code = e.country_code AND c.country_role = 'COUNTRY'
    WHERE e.hs_ver = 'HS2022' AND
      (e.trade_month BETWEEN '2024-01-01' AND '2024-12-31' OR
       e.trade_month BETWEEN '2025-01-01' AND '2025-12-31')"""


def payload(sql):
    return {'meta': {'kind': 'LLM_SQL'}, 'conditions': deepcopy(CONDITIONS),
            'sql': [sql], 'rows': [{'old_usd': 80, 'new_usd': 100, 'signed_usd': 20}],
            'columns': [{'key': 'old_usd', 'label': '2024년 수출액', 'unit': 'USD'},
                        {'key': 'new_usd', 'label': '2025년 수출액', 'unit': 'USD'},
                        {'key': 'signed_usd', 'label': '수출액 차이', 'unit': 'USD'}]}


def facts(sql):
    source = snapshot_evidence(payload(sql))
    return {f.path[-1]: f for f in build_facts([source], NameCatalog([source]))}


class AnnualSqlProvenanceTest(unittest.TestCase):
    def test_cte_and_conditional_sum_prove_each_year_and_signed_order(self):
        for sql in (CTE, CASE):
            with self.subTest(shape=sql[:10]):
                sums, differences, _ = annual_sql_sources(payload(sql))
                self.assertEqual(sums['old_usd'][0], '2024')
                self.assertEqual(sums['new_usd'][0], '2025')
                self.assertEqual(differences['signed_usd'][:2], ('2025', '2024'))
                found = facts(sql)
                self.assertEqual(found['old_usd'].period, ('2024-01', '2024-12'))
                self.assertEqual(found['new_usd'].period, ('2025-01', '2025-12'))
                self.assertEqual(found['signed_usd'].period, ('2025-01', '2025-12'))
                self.assertEqual(found['signed_usd'].compare_period, ('2024-01', '2024-12'))
                self.assertTrue(found['signed_usd'].period_difference)
                self.assertEqual(found['signed_usd'].metric.family, 'AMOUNT_CHANGE')

    def test_cte_mutations_do_not_fabricate_the_current_year(self):
        for sql in (CTE.replace("c.country_role = 'COUNTRY'", "c.country_role = 'TOTAL'"),
                    CTE.replace("e.hs_ver = 'HS2022'", "e.hs_ver = 'HS2017'"),
                    CTE.replace("2025-12-31", "2025-11-30"),
                    CTE.replace('n.value - o.value', 'o.value - n.value'),
                    CTE.replace('o.value AS old_usd', 'n.value AS old_usd'),
                    CTE.replace('c.country_code = e.country_code', 'c.country_code = c.country_code')):
            with self.subTest(sql=sql[-110:]):
                found = facts(sql)
                if 'o.value - n.value' in sql:
                    self.assertEqual(found['signed_usd'].period, ('2024-01', '2024-12'))
                    self.assertEqual(found['signed_usd'].compare_period, ('2025-01', '2025-12'))
                elif 'n.value AS old_usd' in sql:
                    self.assertNotIn('old_usd', found)
                else:
                    self.assertNotIn('signed_usd', found)

    def test_renaming_projections_keeps_the_same_operand_years(self):
        sql = f"""WITH saved AS ({CTE}), renamed(first_amount, second_amount, balance) AS (
                    SELECT s.old_usd AS second_amount, s.new_usd AS first_amount,
                           s.signed_usd FROM saved s)
                  SELECT first_amount AS old_usd, second_amount AS new_usd,
                         balance AS signed_usd FROM renamed"""
        found = facts(sql)
        self.assertEqual(found['old_usd'].period, ('2024-01', '2024-12'))
        self.assertEqual(found['new_usd'].period, ('2025-01', '2025-12'))
        self.assertEqual(found['signed_usd'].period, found['new_usd'].period)
        self.assertEqual(found['signed_usd'].compare_period, found['old_usd'].period)

    def test_conditional_predicate_and_alias_mutations_are_rejected(self):
        date_disjunction = ("(e.trade_month BETWEEN '2024-01-01' AND '2024-12-31' OR\n"
                            "       e.trade_month BETWEEN '2025-01-01' AND '2025-12-31')")
        for sql in (CASE.replace("c.country_role = 'COUNTRY'", "c.country_role = 'TOTAL'"),
                    CASE.replace("e.hs_ver = 'HS2022'", "e.hs_ver = 'HS2017'"),
                    CASE.replace("THEN e.amount_usd END) AS signed_usd", "THEN c.amount_usd END) AS signed_usd"),
                    CASE.rsplit("'2025-12-31'", 1)[0] + "'2026-12-31'" + CASE.rsplit("'2025-12-31'", 1)[1],
                    CASE.replace("e.trade_month BETWEEN '2024-01-01' AND '2024-12-31' OR",
                                 "e.trade_month BETWEEN '2024-01-01' AND '2024-12-31' OR 1=1 OR"),
                    CASE.replace(date_disjunction, date_disjunction + ' AND ' + date_disjunction),
                    CASE.replace("WHERE e.hs_ver", "WHERE c.country_role = 'COUNTRY' AND e.hs_ver")):
            with self.subTest(sql=sql[-110:]):
                if "WHERE c.country_role = 'COUNTRY'" in sql:
                    self.assertIn('signed_usd', facts(sql))  # Equivalent inner-join predicate.
                else:
                    self.assertNotIn('signed_usd', facts(sql))

    def test_country_cte_shadow_is_not_a_country_role_proof(self):
        shadow = CTE.replace('WITH older AS',
                             "WITH country AS (SELECT country_code, country_role FROM country), older AS")
        self.assertEqual(annual_sql_sources(payload(shadow))[:2], ({}, {}))

    def test_neutral_alias_is_structural_and_false_label_year_is_rejected(self):
        altered = payload(CTE)
        altered['columns'][2]['label'] = '2024년 - 2025년 수출액 차이'
        source = snapshot_evidence(altered)
        found = {f.path[-1]: f for f in build_facts([source], NameCatalog([source]))}
        self.assertNotIn('signed_usd', found)
        self.assertIn('signed_usd', facts(CTE))

    def test_dated_monthly_observation_survives_annual_request_windows(self):
        saved = payload(CTE)
        saved['rows'] = [{'month': '2025-03', 'amount_usd': 7}]
        saved['columns'] = [{'key': 'amount_usd', 'label': '월별 수출액', 'unit': 'USD'}]
        source = snapshot_evidence(saved)
        found = {f.path[-1]: f for f in build_facts([source], NameCatalog([source]))}
        self.assertEqual(found['amount_usd'].month, '2025-03')

    def test_scoped_single_year_and_direct_projection_remain_supported(self):
        scoped = payload("SELECT SUM(e.amount_usd) AS old_usd FROM export_monthly e "
                         "WHERE e.country_code = 'US' AND e.hs6_code = '123456' "
                         "AND e.hs_ver = 'HS2022' AND e.trade_month BETWEEN '2024-01-01' AND '2024-12-31'")
        scoped['conditions']['country_codes'] = ['US']
        scoped['conditions']['hs_items'] = [{'hs_code': '123456', 'hs_ver': 'HS2022'}]
        scoped['conditions']['period']['end'] = '2025-11-30'
        self.assertEqual(annual_sql_sources(scoped)[0]['old_usd'][0], '2024')
        direct = payload("SELECT SUM(e.amount_usd) AS new_usd FROM export_monthly e "
                         "JOIN country c ON c.country_code = e.country_code AND c.country_role = 'COUNTRY' "
                         "WHERE e.hs_ver = 'HS2022' AND e.trade_month BETWEEN '2025-01-01' AND '2025-12-31'")
        self.assertEqual(annual_sql_sources(direct)[0]['new_usd'][0], '2025')


if __name__ == '__main__':
    unittest.main()
