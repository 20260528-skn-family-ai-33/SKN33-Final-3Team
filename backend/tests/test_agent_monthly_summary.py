import unittest

from backend.app.agents.compose import assemble, compose_input
from backend.app.agents.digest import bounded
from backend.app.agents.monthly_summary import monthly_facts
from backend.app.agents.planner import build_plan
from backend.app.agents.state import AgentState, Conditions, Draft, PlannerOutput, ToolResult
from backend.app.agents.verify import judge_draft, snapshot_evidence, verify
from backend.tests.test_agent_graph import initial


class MonthlySummaryTest(unittest.TestCase):
    def example(self, rows, comparison=False):
        state = AgentState.model_validate(initial())
        state.plan, _ = build_plan(PlannerOutput(tasks=[
            dict(intent='STATISTICS', question='전년과 비교해줘' if comparison else '중국 화장품 수출 추이 알려줘'),
            dict(intent='CAUSE', question='수출 변화의 원인을 알려줘')]))
        conditions = Conditions(period={'start': rows[0]['month']+'-01', 'end': rows[-1]['month']+'-01'},
            compare_period={'start': rows[0]['compare_month']+'-01', 'end': rows[-1]['compare_month']+'-01'} if comparison else None,
            country_codes=['CN'], hs_items=[{'hs_code': '330499', 'hs_ver': 'HS2022'}])
        subject = dict(country_code='CN', country_name='중국', name='기초화장품', hs_code='330499', hs_ver='HS2022')
        target = dict(subject, requested_months=len(rows), total_usd=sum(r['amount_usd'] for r in rows),
                      compare_total_usd=sum(r['compare_amount_usd'] for r in rows) if comparison else None)
        source = snapshot_evidence({'meta': {'kind': 'TREND'}, 'conditions': conditions.model_dump(mode='json'),
                                    'rows': [dict(subject, **r) for r in rows]})
        snapshots = {}
        for task in state.plan.tasks:
            task.answer_status = 'ANSWERED'
            task.conditions = conditions
            task.slots.products = ['화장품']
            step = next(s for s in task.steps if s.tool == 'query_trade_data')
            state.results[step.step_id] = ToolResult(status='OK', result_id='123', digest=bounded(
                dict(kind='TREND', trade_flow='EXPORT', targets=[dict(target, monthly_facts=monthly_facts(rows), monthly=rows[-1:])])))
            snapshots[step.step_id] = source
        return state, snapshots

    @staticmethod
    def china_rows():
        amounts = [115750059,142614807,173221436,179832954,144505840,106797921,
                   109520546,113557306,164855720,111582593,101223467,122387836]
        return [dict(month=f'2025-{i:02}', amount_usd=a) for i,a in enumerate(amounts,1)]

    def writer_example(self):
        state, sources = self.example(self.china_rows())
        primary = state.plan.tasks[0]
        state.plan.tasks = [primary]
        state.input.question = primary.question = '2025년 중국 화장품 월별 수출의 최저와 최고를 알려줘'
        step = next(s.step_id for s in primary.steps if s.tool == 'query_trade_data')
        return state, {step: sources[step]}

    def check_writer(self, state, sources, body, summary, body_ok, summary_ok):
        # Feed Writer prose directly to verification, independently of fixed-answer routing.
        state.fixed_conclusion_ids = []
        state.draft = Draft(conclusion=[dict(task_id='t1', sentences=[body])],
                            explanation=[body], turn_summary=summary)
        verify(state, snapshots=sources)
        verdict = state.verification
        self.assertEqual([c.status for c in verdict.checks], [body_ok] * 2)
        self.assertEqual(verdict.summary_status, summary_ok)
        self.assertEqual(state.draft.turn_summary, summary if summary_ok == 'PASS' else '')
        failed = {f.section for f in verdict.feedback}
        self.assertEqual(failed, ({'conclusion', 'explanation'} if body_ok == 'MISMATCH' else set()) |
                         ({'summary'} if summary_ok == 'MISMATCH' else set()))

    def test_every_month_as_min_and_max_in_writer_body_and_summary(self):
        state, sources = self.writer_example()
        for i, row in enumerate(self.china_rows(), 1):
            for role, winner in [('최저', 11), ('최고', 4)]:
                with self.subTest(i=i, role=role):
                    line = f"2025년 {i}월 수출액 {row['amount_usd']:,}달러가 {role}입니다."
                    ok = 'PASS' if i == winner else 'MISMATCH'
                    self.check_writer(state, sources, line, line, ok, ok)

    def test_other_month_controls_minimum_but_not_monthly_amount(self):
        state, sources = self.writer_example()
        june = '2025년 6월 수출액은 106,797,921달러입니다.'
        lowest = '2025년 6월 수출액은 106,797,921달러로 가장 낮았습니다.'
        for november, expected in [(101223467, 'MISMATCH'), (116000000, 'PASS'),
                                   (106797921, 'PASS'), (0, 'MISMATCH'), (None, 'MISMATCH')]:
            with self.subTest(november=november):
                next(iter(sources.values()))['rows'][10]['amount_usd'] = november
                self.check_writer(state, sources, june, lowest, 'PASS', expected)
                self.check_writer(state, sources, lowest, june, expected, 'PASS')

    def test_extrema_without_month_or_amount_and_rounded_range(self):
        state, sources = self.writer_example()
        for wrong, right in [
            ('최저는 6월입니다.', '최저는 11월입니다.'),
            ('월별 수출액의 최솟값은 106,797,921달러입니다.', '월별 수출액의 최댓값은 179,832,954달러입니다.'),
            ('최저와 최고는 각각 6월과 4월입니다.', '최저와 최고는 각각 11월과 4월입니다.'),
            ('6월이 가장 낮고 4월이 가장 높았습니다.', '11월이 가장 낮고 4월이 가장 높았습니다.'),
            ('월별 수출액은 106,797,921~179,832,954달러였습니다.',
             '월별 수출액은 101,223,467~179,832,954달러였습니다.'),
            ('최고 수출액은 106,797,921달러입니다.', '최고 수출액은 179,832,954달러입니다.'),
            ('최저는 6월 106,797,921달러, 최고는 4월 179,832,954달러입니다.',
             '최저는 11월 101,223,467달러, 최고는 4월 179,832,954달러입니다.'),
            ('월별 수출액은 1억 680만 달러에서 1억 7,983만 달러 사이로 변동했습니다.',
             '월별 수출액은 1억 122만 달러에서 1억 7,983만 달러 사이로 변동했습니다.'),
        ]:
            with self.subTest(wrong=wrong):
                self.check_writer(state, sources, right, wrong, 'PASS', 'MISMATCH')
                self.check_writer(state, sources, wrong, right, 'MISMATCH', 'PASS')

    def test_assembled_writer_summary_cannot_reintroduce_june_minimum(self):
        state, sources = self.writer_example()
        _, fixed = compose_input(state, lambda _: {}, [])
        correct = '2025년 11월 수출액 101,223,467달러가 최저입니다.'
        wrong = '2025년 6월 수출액 106,797,921달러가 최저입니다.'
        generated = Draft(conclusion=[dict(task_id='t1', sentences=[correct])], turn_summary=wrong)
        state.draft = assemble(state, generated, fixed, lambda _: {})
        state.fixed_conclusion_ids = list(fixed)
        verify(state, snapshots=sources)
        self.assertEqual(state.verification.checks[0].status, 'PASS')
        self.assertEqual(state.verification.summary_status, 'MISMATCH')
        self.assertEqual(state.verification.status, 'NEEDS_REVIEW')
        self.assertEqual(state.verification.failed_summary, wrong)
        self.assertEqual(state.draft.turn_summary, '')

    def test_extremum_uses_original_precision_before_formatting(self):
        state, sources = self.writer_example()
        rows = next(iter(sources.values()))['rows']
        rows[5]['amount_usd'] = rows[10]['amount_usd'] + 1
        right = '11월 수출액 1억 122만 달러가 최저입니다.'
        wrong = '6월 수출액 1억 122만 달러가 최저입니다.'
        self.check_writer(state, sources, right, wrong, 'PASS', 'MISMATCH')

    def test_winner_must_belong_to_one_complete_saved_series(self):
        state, sources = self.writer_example()
        table = next(iter(sources.values()))
        ordinary = '6월 수출액은 106,797,921달러입니다.'
        lowest = '중국의 월별 최저 수출액은 11월 101,223,467달러입니다.'
        # Japan has a lower month. It neither disproves China's minimum nor
        # supplies a minimum for a statement with no identifiable country.
        table['rows'] += [dict(r, country_code='JP', country_name='일본', amount_usd=0)
                          for r in table['rows']]
        self.check_writer(state, sources, lowest, '최저는 11월입니다.', 'PASS', 'MISMATCH')
        table['rows'].pop(4)
        self.check_writer(state, sources, ordinary, lowest, 'PASS', 'MISMATCH')
        # The Writer digest still contains the correct extrema after loss of the snapshot.
        self.check_writer(state, {}, lowest, lowest, 'MISMATCH', 'MISMATCH')

    def test_monthly_threshold_remains_an_ordinary_amount_claim(self):
        state, sources = self.writer_example()
        self.check_writer(state, sources, '6월 수출액은 최소 100달러 이상입니다.',
                          '6월 수출액은 최소 2억 달러 이상입니다.', 'PASS', 'MISMATCH')

    def test_tied_and_zero_extrema(self):
        state, sources = self.writer_example()
        rows = next(iter(sources.values()))['rows']
        rows[5]['amount_usd'] = rows[10]['amount_usd'] = 0
        paired = '6월과 11월이 공동 최저입니다.'
        zero = '11월 수출액 0달러가 최저입니다.'
        self.check_writer(state, sources, paired, zero, 'PASS', 'PASS')
        rows[5]['amount_usd'] = 1
        self.check_writer(state, sources, paired, zero, 'MISMATCH', 'PASS')

    def test_range_uses_full_table_and_cause_cannot_reintroduce_wrong_minimum(self):
        rows = self.china_rows()
        state, snapshots = self.example(rows)
        _, fixed = compose_input(state, lambda _: {}, [])
        wrong = '월별 수출액은 1억 680만 달러에서 1억 7,983만 달러 사이로 변동했습니다.'
        article = '제조시설의 신규 등록 요건이 변경되었습니다.'
        generated = Draft(conclusion=[dict(task_id='t2', sentences=[wrong, article])],
                          explanation=[wrong,article], turn_summary=wrong)
        state.draft = assemble(state, generated, fixed, lambda _: {})
        state.fixed_conclusion_ids = list(fixed)
        self.assertIn('2025년 11월 101,223,467달러', state.draft.turn_summary)
        self.assertIn('2025년 4월 179,832,954달러', state.draft.turn_summary)
        self.assertNotIn(wrong, state.draft.sentences())
        self.assertIn(article, state.draft.conclusion[1].sentences)
        self.assertEqual(state.draft.explanation, [article])
        verdict = judge_draft(state, snapshots=snapshots)
        self.assertEqual(verdict.summary_status, 'PASS')
        self.assertEqual([c.status for c in verdict.checks[:2]], ['PASS','PASS'])
        for snapshot in snapshots.values():
            snapshot['rows'][10]['amount_usd'] = 116000000
        self.assertEqual(judge_draft(state, snapshots=snapshots).summary_status, 'MISMATCH')

    def test_yoy_counts_all_increased_months_and_retains_zero_baseline(self):
        rows = [dict(month=f'2025-{m:02}', compare_month=f'2024-{m:02}',
                     amount_usd=3 if m in (9,12) else 0, compare_amount_usd=0) for m in range(1,13)]
        state, snapshots = self.example(rows, comparison=True)
        _, fixed = compose_input(state, lambda _: {}, [])
        generated = Draft(conclusion=[dict(task_id='t2', sentences=['기사 근거가 없습니다.'])],
                          explanation=['9월에만 수출액이 증가했습니다.'], turn_summary='9월에만 증가했습니다.')
        state.draft = assemble(state, generated, fixed, lambda _: {})
        self.assertIn('9월, 12월', state.draft.turn_summary)
        self.assertIn('증감률을 계산할 수 없습니다', state.draft.conclusion[0].sentences[0])
        self.assertFalse(state.draft.explanation)
        self.assertEqual(judge_draft(state, snapshots=snapshots).summary_status, 'PASS')

    def test_other_requested_metrics_and_cited_article_quantities_survive(self):
        rows = [dict(month='2025-01', amount_usd=20), dict(month='2025-02', amount_usd=40)]
        state, _ = self.example(rows)
        primary, _ = state.plan.tasks
        for question in ('중국 화장품 월별 추이와 월평균 알려줘', '1월과 2월 수출액 차액을 비교해줘'):
            primary.question = question
            _, fixed = compose_input(state, lambda _: {}, [])
            self.assertNotIn(primary.task_id, fixed)
        primary.question = '중국 화장품 수출 추이 알려줘'
        _, fixed = compose_input(state, lambda _: {}, [])
        article = '업체가 발표한 2월 투자액은 200달러에서 300달러 사이였습니다.'
        cited = article + ' [1]'
        generated = Draft(conclusion=[dict(task_id='t2', sentences=[cited])],
                          explanation=[cited], turn_summary='조회 결과입니다.')
        cards = [dict(n=1, task_id='t2', summary=[dict(text=article)])]
        draft = assemble(state, generated, fixed, lambda _: {}, articles=cards)
        self.assertEqual(draft.conclusion[1].sentences, [cited])
        self.assertEqual(draft.explanation, [cited])

    def test_verified_zero_ties_missing_and_non_yoy_months(self):
        rows = [dict(month='2025-01', amount_usd=None, compare_month='2024-01', compare_amount_usd=1),
                dict(month='2025-02', amount_usd=0, compare_month='2023-02', compare_amount_usd=5),
                dict(month='2025-03', amount_usd=0, compare_month='2024-03', compare_amount_usd=0)]
        facts = monthly_facts(rows)
        self.assertEqual((facts['min_usd'],facts['min_month'],facts['min_count']), (0,'2025-02',2))
        self.assertEqual((facts['increased_months'],facts['yoy_months']), ([],1))

    def test_cited_monthly_trade_claim_survives_assembly_then_uses_article_evidence(self):
        rows = [dict(month='2025-01', amount_usd=20), dict(month='2025-02', amount_usd=40)]
        state, snapshots = self.example(rows)
        _, fixed = compose_input(state, lambda _: {}, [])
        fact = '2024년 2월 화장품 수출액은 전년 동월보다 3% 감소했다.'
        cards = [dict(n=1, task_id='t2', summary=[dict(text=fact)])]
        evidence = [dict(article_id='1', task_id='t2', evidence_sufficient=True,
                         sentences=[dict(text=fact, quotes=[fact])])]
        quoted = '기사에 따르면 ' + fact.replace('감소했다.', '감소했습니다. [1]')
        for line, status in [(quoted, 'PASS'), (quoted.replace('3%', '30%'), 'MISMATCH'),
                             (quoted + ' 월별 수출액의 최저는 40달러입니다.', 'MISMATCH'),
                             (quoted + ' 월별 수출액의 최저는 20달러입니다.', 'PASS'),
                             (quoted + ' 월별 수출액의 최저는 900달러입니다.', 'MISMATCH')]:
            with self.subTest(line=line):
                generated = Draft(conclusion=[dict(task_id='t2', sentences=[line])],
                                  explanation=[line], turn_summary='')
                state.draft = assemble(state, generated, fixed, lambda _: {}, articles=cards)
                self.assertEqual(state.draft.conclusion[-1].sentences, [line])
                self.assertEqual(state.draft.explanation, [line])
                verdict = judge_draft(state, snapshots=snapshots, articles=evidence)
                self.assertEqual([(c.basis, c.status) for c in verdict.checks[-2:]],
                                 [('ARTICLE', status), ('ARTICLE', status)])
