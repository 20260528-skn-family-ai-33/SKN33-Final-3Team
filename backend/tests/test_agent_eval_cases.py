"""이슈 86 평가셋 형식과 채점 함수. LLM·DB 없이 돈다(평가 실행은 backend/evals/agent/run.py)."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.evals.agent.run import (AREAS, agent_state, compose_case, load, score_conditions,
                                     score_context_turn, score_planner, summarize)
from backend.app.agents.planner import build_plan
from backend.app.agents.state import Draft, PlannerOutput


class EvalCasesTest(unittest.TestCase):
    def test_report_followup_scoring_distinguishes_result_and_conditions(self):
        for case in load('planner'):
            if case['id'] not in {'p042', 'p043', 'h030', 'h031'}:
                continue
            expected = case['expect']['tasks'][0]['previous_turn_result']
            with self.subTest(case=case['id']):
                for marked in (False, True):
                    output = PlannerOutput(tasks=[{'intent': 'REPORT', 'question': case['question'],
                                                   'previous_turn_result': marked}])
                    self.assertEqual(score_planner(case, output, build_plan(output)[0], {})['pass'],
                                     marked == expected)

    def test_every_area_has_unique_ids_and_both_splits(self):
        for area in AREAS:
            with self.subTest(area=area):
                cases = load(area)
                ids = [c['id'] for c in cases]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual({c['split'] for c in cases}, {'dev', 'holdout'})
                for case in cases:   # 맥락이 AgentInput 스키마를 통과해야 실행 때 깨지지 않는다
                    for question in [t['question'] for t in case.get('turns', [])] or [case['question']]:
                        agent_state(question, case.get('context') or {'recent': case.get('recent', [])})

    def test_planner_scoring_accepts_alternatives_and_catches_wrong_slot(self):
        case = next(c for c in load('planner') if c['id'] == 'p004')
        good = PlannerOutput(tasks=[{'intent': 'RANKING', 'question': 'q', 'slots': {
            'products': None, 'countries': [], 'period': {'start': '2025-01-01', 'end': '2025-12-01'},
            'trade_flow': 'EXPORT', 'ranking_group': 'COUNTRY', 'ranking_limit': 5}}])
        self.assertTrue(score_planner(case, good, build_plan(good)[0], {})['pass'])
        bad = good.model_copy(deep=True)
        bad.tasks[0].slots.ranking_limit = 10
        result = score_planner(case, bad, build_plan(bad)[0], {})
        self.assertFalse(result['pass'])
        self.assertFalse(result['fields']['t1.ranking_limit'])
        self.assertTrue(score_planner({'expect': {'error': 'QUESTION_TOO_COMPLEX'}}, None, None,
                                      {'error_code': 'QUESTION_TOO_COMPLEX'})['pass'])

    def test_planner_rejects_missing_selection_and_country_without_source_evidence(self):
        case = next(c for c in load('planner') if c['id'] == 'p027')
        specs = case['expect']['tasks']
        output = PlannerOutput(tasks=[
            {'intent': 'RANKING', 'question': '순위', 'slots': {
                **specs[0]['slots'], 'countries': None}},
            {'intent': 'PROMISING_MARKET', 'question': '유망 품목', 'slots': {'products': []},
             'source_tasks': [1]}])
        self.assertFalse(score_planner(case, output, build_plan(output)[0], {})['fields']['t2.target_selections'])
        tasks = output.model_dump()['tasks']
        tasks[1]['target_selections'] = specs[1]['target_selections']
        selected = PlannerOutput(tasks=tasks)
        self.assertTrue(score_planner(case, selected, build_plan(selected)[0], {})['pass'])

        case = next(c for c in load('planner') if c['id'] == 'p001')
        spec = case['expect']['tasks'][0]
        output = PlannerOutput(tasks=[{'intent': spec['intent'], 'question': case['question'],
            'slots': {**spec['slots'], 'countries': [{'mention': '일본', 'name': '베트남'}]}}])
        score = score_planner(case, output, build_plan(output)[0], {})
        self.assertFalse(score['fields']['t1.country_mentions'])

    def test_conditions_scoring_flags_wrong_confirm_and_hs_candidates(self):
        base = {'status': 'DONE', 'pending_kind': None, 'choices': [], 'country_codes': ['US'], 'hs_codes': ['330499'],
                'period': None, 'trade_flow': 'EXPORT', 'compare_period': None, 'latest_month': None, 'error': None}
        expect = {'status_in': ['DONE', 'NEEDS_INPUT'], 'country_codes': ['US'], 'hs_codes': ['330499'],
                  'pending_kind': 'HS_ITEM', 'hs_candidate_prefixes': ['330499'], 'trade_flow': 'EXPORT'}
        self.assertTrue(score_conditions(expect, base)['pass'])
        asked = {**base, 'status': 'NEEDS_INPUT', 'pending_kind': 'HS_ITEM', 'choices': ['330410', '330499'],
                 'country_codes': [], 'hs_codes': [], 'trade_flow': None}
        self.assertTrue(score_conditions(expect, asked)['pass'])
        self.assertFalse(score_conditions(expect, {**asked, 'choices': ['330410']})['pass'])
        self.assertTrue(score_conditions(expect, {**base, 'hs_codes': ['330410', '330499']})['wrong_confirm'])
        wrong = score_conditions(expect, {**base, 'country_codes': ['CN']})
        self.assertTrue(wrong['wrong_confirm'])
        silent = score_conditions({'status': 'NEEDS_INPUT', 'pending_kind': 'COUNTRY'}, base)
        self.assertTrue(silent['wrong_confirm'])

    def test_country_clarification_requires_expected_candidate_in_top_five(self):
        expect = {'status': 'NEEDS_INPUT', 'pending_kind': 'COUNTRY', 'country_candidate': 'US'}
        out = {'status': 'NEEDS_INPUT', 'pending_kind': 'COUNTRY', 'choices': ['CN', 'JP', 'DE', 'VN', 'GB', 'US']}
        missed = score_conditions(expect, out)
        self.assertFalse(missed['pass'])
        self.assertFalse(missed['country_candidate_recall'])
        out['choices'] = ['US']
        self.assertTrue(score_conditions(expect, out)['pass'])

    def test_conditions_quality_gate_requires_ninety_percent_and_zero_wrong_confirmations(self):
        passed = {'pass': True, 'fields': {}, 'wrong_confirm': False, 'unnecessary_ask': False}
        failed = {**passed, 'pass': False}
        rows = [{'id': str(i), 'ms': 1, 'score': passed if i < 9 else failed} for i in range(10)]
        self.assertTrue(summarize('conditions', rows)['quality_gate_pass'])
        rows[-1]['score'] = {**failed, 'wrong_confirm': True}
        report = summarize('conditions', rows)
        self.assertEqual(report['wrong_confirm_count'], 1)
        self.assertFalse(report['quality_gate_pass'])
        rows[-2]['score'] = rows[-1]['score'] = failed
        self.assertFalse(summarize('conditions', rows)['quality_gate_pass'])

    def test_context_rejects_missing_step_for_analysis_turn(self):
        case = next(c for c in load('context') if c['id'] == 's002')
        expect = case['turns'][1]['expect']
        task = SimpleNamespace(intent='STATISTICS')
        self.assertFalse(score_context_turn(expect, task, {'status': 'NO_STEP'}, 1)['pass'])

    def test_compose_references_pass_and_review_counterexamples_fail(self):
        wrong = {
            'k001': ['베트남의 수출은 감소했습니다.'],
            'k002': ['미국입니다.'],
            'k003': ['이전 질문이 없다는 답은 사실이 아닙니다.'],
            'k004': ['품목 분류와 무관합니다.'],
            'k005': ['851010입니다.'],
            'k006': ['330499는 기초화장품에 해당하지 않습니다.'],
            'k007': ['일본이 가장 많고 미국과 중국이 그 뒤를 이었습니다.'],
            'k008': ['베트남입니다.'],
            'k009': ['FTA입니다.', '베트남입니다.'],
            'k010': ['2,840만 달러가 아닙니다.'],
            'k101': ['일본 라면 수출은 감소했습니다.'],
            'k102': ['일본입니다.'],
            'k103': ['190230입니다.'],
            'k104': ['원산지 규정은 원산지와 무관합니다.'],
            'k105': ['승용차와 메모리가 상위 품목입니다.'],
            'k106': ['이전 질문이 없다는 답은 사실이 아닙니다.'],
            'k107': ['인코텀즈는 무역과 무관합니다.'],
            'k108': ['851010입니다.'],
            'k109': ['HS코드입니다.', '일본이 가장 많고 미국과 중국이 그 뒤를 이었습니다.'],
            'k110': ['미국입니다.', '일본 라면 수출은 감소했습니다.'],
        }
        for case in load('compose_text'):
            def score(sentences, summary=''):
                draft = Draft(conclusion=[
                    {'task_id': f't{i}', 'sentences': [sentence]} for i, sentence in enumerate(sentences, 1)
                ], turn_summary=summary)
                runner = Mock()
                runner.parse.return_value = draft, 1, None
                return compose_case(runner, case)[0]

            with self.subTest(case=case['id']):
                references = [rule['reference'] for rule in case['expect']['conclusions']]
                result = score(references)
                self.assertTrue(result['pass'], result['fields'])
                self.assertFalse(score(wrong[case['id']])['pass'])
                if case['id'] == 'k010':
                    self.assertTrue(score(['12월 수출액은 2840만 달러였습니다.'])['pass'])
                    self.assertTrue(score(['12월 수출액은 28,400,000달러였습니다.'], references[0])['pass'])
                    self.assertFalse(score(references, '12월 수출액은 2,610만 달러였습니다.')['pass'])
                if case['id'] == 'k005':
                    self.assertTrue(score(references, 'HS2022 기준 코드는 8510과 851010입니다.')['pass'])
                    self.assertFalse(score(references, 'HS2022 기준 코드는 851020입니다.')['pass'])
                if case['id'] == 'k001':
                    mixed = '베트남 기초화장품 수출은 전반적으로 증가했지만 10월만 감소했고 12월이 가장 컸습니다.'
                    self.assertTrue(score([mixed])['pass'])
                    # 기간 추세·월 증감·두 달 금액 비교 모두 허용하되, 방향이 틀리거나 근거가 모호하면 거부한다.
                    for sentence, ok in [
                        ('베트남 기초화장품 수출은 7월 2,100만 달러에서 12월 2,840만 달러로 늘었습니다.', True),
                        ('베트남 기초화장품 수출은 7월 2,100만 달러에서 12월 2,840만 달러로 줄었습니다.', False),
                        # 10월은 9월보다 줄었지만 7월보다는 늘었다. 두 달 금액이 있으면 그 비교로만 판정한다.
                        ('베트남 기초화장품 수출은 7월 2,100만 달러에서 10월 2,380만 달러로 줄었습니다.', False),
                        ('베트남 기초화장품 수출의 분모는 1억 4,580만 달러입니다.', False),
                        ('베트남 기초화장품 수출은 전반적으로 감소했습니다.', False),
                        ('베트남 기초화장품 수출은 증가했습니다.', False),
                        ('베트남 기초화장품 수출은 10월에 증가했습니다.', False),
                        ('베트남 기초화장품 수출은 7월에 증가했습니다.', False),
                    ]:
                        self.assertEqual(score([sentence])['grounded'], ok, sentence)
                if case['id'] == 'k007':
                    self.assertTrue(score(['2025년 1~12월 기준으로 ' + references[0]])['pass'])
                    self.assertFalse(score(['2025년 1~13월 기준으로 ' + references[0]])['pass'])


if __name__ == '__main__':
    unittest.main()


class BareMonthAcrossYearsTest(unittest.TestCase):
    def test_bare_month_in_two_year_trend_is_not_guessed(self):
        from backend.app.agents.verify import inspect_sentence
        monthly = [{'month': f'{y}-{m:02d}', 'amount_usd': v} for y, m, v in
                   ((2024, 11, 10), (2024, 12, 20), (2025, 11, 30), (2025, 12, 5))]
        source = {'kind': 'TREND', 'period': {'start': '2024-11', 'end': '2025-12'}, 'trade_flow': 'EXPORT',
                  'unit': 'USD', 'targets': [{'name': '베트남', 'country_code': 'VN', 'monthly': monthly}]}
        good = lambda sentence: inspect_sentence(sentence, [source], known_names=['베트남'], countries=['베트남'])[0]
        self.assertFalse(good('베트남 수출은 12월에 증가했습니다.'))
        self.assertTrue(good('베트남 수출은 2024년 12월에 증가했습니다.'))
        self.assertTrue(good('베트남 수출은 2025년 12월에 감소했습니다.'))
