import unittest

from backend.app.agents.verify import inspect_sentence, snapshot_evidence


class AnnualTrendEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            dict(name='부품', country_name='미국', hs_code='123456', hs_ver='HS2022', country_code='US',
                 month=f'2025-{month:02}', total_usd=125, compare_total_usd=100)
            for month in range(1, 13)
        ]
        self.payload = dict(meta={'kind':'TREND'}, rows=self.rows, conditions={
            'trade_flow':'EXPORT', 'period':{'start':'2025-01','end':'2025-12'},
            'compare_period':{'start':'2024-01','end':'2024-12'}})
        self.forward = '수출액은 2024년 100달러에서 2025년 125달러로 증가했습니다.'

    def inspect(self, line):
        return inspect_sentence(line, [snapshot_evidence(self.payload)], countries=['미국','일본'])

    def test_topic_years_do_not_assign_roles_to_the_dollar_difference(self):
        for row in self.rows:
            row['period_difference_usd'] = 25
        topic = '2024년과 2025년 수출액 비교에서 '
        claim = '2025년 수출액은 2024년보다 25달러 많습니다.'
        for text in (topic + claim, topic.replace('2024년과 2025년', '2025년과 2024년') + claim,
                     topic + '2025년보다 2024년 수출액이 25달러 적습니다.'):
            with self.subTest(text=text):
                ok, (check,) = self.inspect(text)
                self.assertTrue(ok)
                self.assertEqual({path[-1] for _, path in check.matches}, {'period_difference_usd'})
        for text in (topic + '2024년 수출액은 2025년보다 25달러 많습니다.',
                     topic + claim.replace('2024년', '2023년'),
                     topic.replace('2024년', '2023년') + claim,
                     topic + claim.replace('많습니다', '적습니다'),
                     topic + claim.replace('25달러', '26달러'),
                     topic + claim.replace('2024년보다', '2024년과'),
                     topic.replace('과 ', '보다 ') + claim):
            with self.subTest(text=text):
                self.assertFalse(self.inspect(text)[0])

    def test_saved_period_difference_keeps_both_years_and_its_sign(self):
        for row in self.rows:
            row['period_difference_usd'] = 25
        sentence = '미국 부품 수출액은 2025년이 2024년보다 25달러 많습니다.'
        ok, (check,) = self.inspect(sentence)
        self.assertTrue(ok)
        self.assertEqual({path[-1] for _, path in check.matches}, {'period_difference_usd'})
        for invalid in (sentence.replace('2024년', '2023년'), sentence.replace('많습니다', '적습니다'),
                        sentence.replace('25달러', '26달러'), sentence.replace('미국', '일본')):
            with self.subTest(sentence=invalid):
                ok, _ = inspect_sentence(invalid, [snapshot_evidence(self.payload)],
                                         known_names=['미국', '일본'], countries=['미국', '일본'])
                self.assertFalse(ok)

    def test_labeled_years_bind_the_rate_to_both_snapshot_periods(self):
        for row in self.rows:
            row.update(total_usd=374661069, compare_total_usd=305417685, change_pct=22.671701, share_pct=22.7)
        for roles in ('2024년을 비교 기간, 2025년을 대상 기간',
                      '2025년을 대상 기간, 2024년을 비교 기간'):
            with self.subTest(roles=roles):
                ok, (check,) = self.inspect(roles + '으로 보면 변화율은 22.7%입니다.')
                self.assertTrue(ok)
                self.assertEqual(check.claim.time.subject_year, '2025')
                self.assertTrue(check.claim.time.comparison_reference)
                self.assertEqual(check.claim.metric.family, 'CHANGE')
                self.assertEqual({path[-1] for _, path in check.matches}, {'change_pct'})
        # A share equal to the displayed rate cannot prove a change.
        for row in self.rows:
            del row['change_pct']
        self.assertFalse(self.inspect('2024년을 비교 기간, 2025년을 대상 기간으로 보면 변화율은 22.7%입니다.')[0])

    def test_reverse_reference_requires_the_saved_reverse_rate(self):
        conditions = self.payload['conditions']
        conditions['period'], conditions['compare_period'] = conditions['compare_period'], conditions['period']
        for row in self.rows:
            row.update(month=row['month'].replace('2025', '2024'), total_usd=305417685,
                       compare_total_usd=374661069, change_pct=-18.481606)
        roles = '2025년을 비교 기간, 2024년을 대상 기간으로 보면 변화율은 '
        self.assertTrue(self.inspect(roles + '-18.5%입니다.')[0])
        self.assertFalse(self.inspect(roles + '22.7%입니다.')[0])
        self.assertFalse(self.inspect(roles + '18.5%입니다.')[0])
        self.assertFalse(self.inspect('2024년을 비교 기간, 2025년을 대상 기간으로 보면 변화율은 -18.5%입니다.')[0])

    def test_ambiguous_rates_and_plain_period_statements(self):
        for row in self.rows:
            row['change_pct'] = 22.671701
        self.assertTrue(self.inspect('2024년과 2025년의 수출액을 비교했습니다.')[0])
        self.assertFalse(self.inspect('2023년과 2025년의 수출액을 비교했습니다.')[0])
        for statement in ('2024년과 2025년의 수출액 차이는 22.7%입니다.',
                          '2024년을 비교 기간, 2025년을 비교 기간으로 보면 변화율은 22.7%입니다.',
                          '2025년을 대상 기간, 2024년을 대상 기간으로 보면 변화율은 22.7%입니다.',
                          '2024년을 비교 기간, 2025년을 대상 기간으로 보지 않으면 변화율은 22.7%입니다.'):
            with self.subTest(statement=statement):
                ok, (check,) = self.inspect(statement)
                self.assertFalse(ok)
                self.assertIsNone(check.claim.time.subject_year)
                self.assertFalse(check.matches)

    def test_verified_totals_used_in_spoken_order(self):
        old = (0, ('rows','0','compare_total_usd'))
        new = (0, ('rows','0','total_usd'))
        for line, cells in [(self.forward, (old,new)),
                            ('수출액은 2025년 125달러에서 2024년 100달러로 감소했습니다.', (new,old))]:
            with self.subTest(line=line):
                ok, claims = self.inspect(line)
                self.assertTrue(ok)
                self.assertEqual(claims[-1].matches, cells)
        for invalid in [self.forward.replace('증가','감소'), self.forward.replace('125달러','100달러')]:
            self.assertFalse(self.inspect(invalid)[0])

    def test_dollar_difference_keeps_both_cells_in_spoken_order(self):
        old = (0, ('rows', '0', 'compare_total_usd'))
        new = (0, ('rows', '0', 'total_usd'))
        forward = '미국 부품 수출액은 2024년 100달러에서 2025년 125달러로 25달러 증가했습니다.'
        reverse = '미국 부품 수출액은 2025년 125달러에서 2024년 100달러로 25달러 감소했습니다.'
        for sentence, expected in [(forward, (old, new)), (reverse, (new, old))]:
            with self.subTest(sentence=sentence):
                ok, checks = self.inspect(sentence)
                self.assertTrue(ok)
                self.assertEqual(checks[-1].matches, expected)
        for ending in ('24달러 증가', '25달러 감소', '-25달러 증가', '25유로 증가'):
            with self.subTest(ending=ending):
                self.assertFalse(self.inspect(forward.replace('25달러 증가', ending))[0])
        self.rows[-1]['total_usd'] = None
        self.assertFalse(self.inspect(forward)[0])

    def test_difference_uses_unrounded_operands(self):
        for row in self.rows:
            row.update(compare_total_usd=14499, total_usd=14501)
        sentence = '수출액은 2024년 1.4만 달러에서 2025년 1.5만 달러로 2달러 증가했습니다.'
        self.assertTrue(self.inspect(sentence)[0])
        self.assertFalse(self.inspect(sentence.replace('2달러 증가', '1,000달러 증가'))[0])

    def test_all_repeated_rows_must_agree(self):
        for wrong in [126, None]:
            with self.subTest(wrong=wrong):
                self.rows[-1]['total_usd'] = wrong
                self.assertFalse(self.inspect(self.forward)[0])
        self.rows[-1].pop('name')
        self.assertFalse(self.inspect('미국 부품 '+self.forward)[0])
        del self.rows[-1]['total_usd']
        self.assertFalse(self.inspect(self.forward)[0])

    def test_other_series_rate_cannot_validate_cross_target_increase(self):
        for other in [dict(country_name='일본', country_code='JP'), dict(name='가구', hs_code='654321')]:
            name = other.get('country_name', '미국') + ' ' + other.get('name', '부품')
            with self.subTest(name=name):
                self.payload['rows'] = self.rows + [
                    {**row, **other, 'total_usd':250, 'compare_total_usd':200, 'change_pct':25}
                    for row in self.rows]
                line = f'2024년 미국 부품 수출액 100달러에서 2025년 {name} 수출액 250달러로 증가했습니다.'
                ok, claims = self.inspect(line)
                self.assertTrue(all(c.matches for c in claims[:2]))
                self.assertFalse(ok)
                self.assertFalse(claims[-1].matches)
                self.assertFalse(self.inspect(line.replace('로 증가', '로 150달러 증가'))[0])

    def test_ambiguous_targets_and_incomplete_period_are_not_proof(self):
        for field, other in [('country_code','JP'), ('hs_code','654321'), ('hs_ver','HS2017')]:
            with self.subTest(field=field):
                self.rows.append({**self.rows[0], field:other})
                self.assertFalse(self.inspect(self.forward)[0])
                self.rows.pop()
        self.payload['conditions']['period']['end'] = '2025-11'
        self.assertFalse(self.inspect(self.forward)[0])
