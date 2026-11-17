import unittest

from backend.app.agents.compose import assemble, compose_input
from backend.app.agents.digest import bounded
from backend.app.agents.monthly_summary import monthly_facts
from backend.app.agents.planner import build_plan
from backend.app.agents.state import AgentState, Conditions, Draft, PlannerOutput, ToolResult
from backend.app.agents.verify import judge_draft, snapshot_evidence
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

    def test_range_uses_full_table_and_cause_cannot_reintroduce_wrong_minimum(self):
        amounts = [115750059,142614807,173221436,179832954,144505840,106797921,
                   109520546,113557306,164855720,111582593,101223467,122387836]
        rows = [dict(month=f'2025-{i:02}', amount_usd=a) for i,a in enumerate(amounts,1)]
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
