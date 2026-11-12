"""Annual totals projected from saved, independently filtered SQL aggregates."""
import unittest

from backend.app.agents.verify import inspect_sentence, snapshot_evidence


class AnnualSqlEvidenceTest(unittest.TestCase):
    def setUp(self):
        def annual_sum(year):
            return f"""SELECT SUM(t.amount_usd) AS total FROM export_monthly AS t
                WHERE t.country_code = 'US' AND t.hs6_code = '123456' AND t.hs_ver = 'HS2022'
                  AND t.trade_month BETWEEN '{year}-01-01' AND '{year}-12-31'"""

        self.sql = f"""WITH earlier AS ({annual_sum(2021)}), later AS ({annual_sum(2022)})
            SELECT a.total AS old_usd, b.total AS new_usd FROM earlier a CROSS JOIN later b"""
        self.payload = {
            'meta': {'kind': 'LLM_SQL'}, 'sql': [self.sql],
            'conditions': {
                'country_codes': ['US'], 'hs_items': [{'hs_code': '123456', 'hs_ver': 'HS2022'}],
                'period': {'start': '2022-01-01', 'end': '2022-12-31'},
                'compare_period': {'start': '2021-01-01', 'end': '2021-12-31'}, 'trade_flow': 'EXPORT'},
            'columns': [{'key': 'old_usd', 'label': '2021년 수출액', 'unit': 'USD'},
                        {'key': 'new_usd', 'label': '2022년 수출액', 'unit': 'USD'}],
            'rows': [{'old_usd': 80, 'new_usd': 100}],
        }
        self.line = '미국 부품 수출액은 2021년 80달러에서 2022년 100달러로 증가했습니다.'

    def inspect(self, line):
        source = snapshot_evidence(self.payload, country_labels={'US': '미국'},
                                   product_labels={('123456', 'HS2022'): '부품'})
        return inspect_sentence(line, [source], known_names=['미국','일본','부품','가구'], countries=['미국','일본'])

    def test_two_year_levels_and_direction_point_to_saved_cells(self):
        ok, checks = self.inspect(self.line)
        old = (0, ('rows', '0', 'old_usd'))
        new = (0, ('rows', '0', 'new_usd'))
        self.assertTrue(ok)
        self.assertEqual([c.matches for c in checks], [(old,), (new,), (old, new)])
        reverse = '미국 부품 수출액은 2022년 100달러에서 2021년 80달러로 감소했습니다.'
        self.assertTrue(self.inspect(reverse)[0])

    def test_wrong_direction_year_partner_or_product_is_not_supported(self):
        for old, new in [('증가', '감소'), ('80달러', '100달러'), ('2021년', '2020년'),
                         ('미국', '일본'), ('부품', '가구')]:
            with self.subTest(new=new):
                self.assertFalse(self.inspect(self.line.replace(old, new))[0])
        _, checks = self.inspect(self.line.replace('증가', '감소'))
        self.assertTrue(all(c.matches for c in checks[:2]))
        self.assertFalse(checks[-1].matches)

    def test_alias_and_value_alone_cannot_prove_the_year_or_scope(self):
        for old, new in [('a.total AS old_usd', 'b.total AS old_usd'),
                         ("t.country_code = 'US'", "t.country_code = 'JP'"),
                         ("t.hs6_code = '123456'", "t.hs6_code = '123455'"),
                         ('2021-12-31', '2021-06-30'), ('SUM(t.amount_usd)', 'AVG(t.amount_usd)')]:
            with self.subTest(new=new):
                self.payload['sql'] = [self.sql.replace(old, new, 1)]
                self.assertFalse(self.inspect(self.line)[0])
