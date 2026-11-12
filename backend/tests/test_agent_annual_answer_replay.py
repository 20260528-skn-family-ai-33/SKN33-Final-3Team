"""Replay the saved nationwide answers through the whole-answer Verify entry point."""
import json
import unittest
from pathlib import Path

from backend.app.agents.state import Conditions, Draft, ToolResult
from backend.app.agents.verify import judge_draft, snapshot_evidence
from backend.tests import test_agent_compose

CASES = json.loads((Path(__file__).parent / 'fixtures' / 'nationwide_annual_answers.json').read_text())


def verdict(case, conclusion, summary=''):
    snapshot = case['snapshot']
    state = test_agent_compose.ComposeTest().state(['STATISTICS'])
    task = state.plan.tasks[0]
    task.answer_status = 'ANSWERED'
    task.conditions = Conditions.model_validate(snapshot['conditions'])
    step = next(s for s in task.steps if s.tool == 'query_trade_data')
    state.results[step.step_id] = ToolResult(status='OK', result_id='stored', digest={})
    state.draft = Draft(conclusion=[dict(task_id=task.task_id, sentences=conclusion)],
                        explanation=case['explanation'], turn_summary=summary)
    return judge_draft(state, ['미국'], countries=['미국'], snapshots={step.step_id: snapshot_evidence(snapshot)})


class AnnualAnswerReplayTest(unittest.TestCase):
    def test_summary_binds_reported_amounts_after_the_comparison_topic(self):
        case = CASES['pre']
        claim = '2025년 수출액은 2024년보다 256억 7,413만 달러 많습니다.'
        for opening in ('전체 수출액 비교에서', '전체 수출액 비교 결과', '전체 수출액을 비교해 보면'):
            topic = '2024년과 2025년 ' + opening + ' '
            for line, expected in (
                (claim, 'PASS'),
                (claim.replace('수출액', '수출 목표액'), 'MISMATCH'),
                (claim.replace('수출액', '예상 수출액'), 'MISMATCH'),
                (claim.replace('2025년 수출액은 2024년', '2024년 수출액은 2025년'), 'MISMATCH'),
                (claim.replace('2024년', '2023년'), 'MISMATCH'),
                (claim.replace('많습니다', '적습니다'), 'MISMATCH'),
                (claim.replace('7,413만', '7,414만'), 'MISMATCH'),
            ):
                with self.subTest(summary=topic + line):
                    result = verdict(case, case['conclusion'], summary=topic + line)
                    self.assertTrue(all(c.status == 'PASS' for c in result.checks))
                    self.assertEqual(result.summary_status, expected)
                    self.assertEqual(result.status, 'PASS' if expected == 'PASS' else 'NEEDS_REVIEW')

    def test_saved_conclusions_and_explanations(self):
        for name, case in CASES.items():
            with self.subTest(name=name):
                result = verdict(case, case['conclusion'])
                self.assertEqual(result.status, 'PASS')
                self.assertEqual(len(result.checks), len(case['conclusion']) + len(case['explanation']))
                self.assertTrue(all(c.status == 'PASS' and c.basis == 'SNAPSHOT' for c in result.checks))

    def test_change_year_direction_value_or_target_in_the_saved_comparisons(self):
        for name, case in CASES.items():
            statements = case['conclusion']
            original = statements[-1]
            swapped = original.replace(
                '2025년', 'SUBJECT').replace('2024년', '2025년').replace('SUBJECT', '2024년')
            for wrong in (swapped, original.replace('2024년', '2023년'), original.replace('많습니다', '적습니다'),
                          original.replace('7,413만', '7,414만'), '미국 ' + original):
                with self.subTest(name=name, sentence=wrong):
                    result = verdict(case, [*statements[:-1], wrong])
                    self.assertEqual(result.status, 'NEEDS_REVIEW')
                    self.assertEqual([c.index for c in result.checks if c.status == 'MISMATCH'], [len(statements)-1])
            with self.subTest(name=name, equivalent=True):
                self.assertEqual(verdict(case, [*statements[:-1], swapped.replace('많습니다', '적습니다')]).status, 'PASS')

    def test_each_level_is_bound_to_its_own_year(self):
        for name in ('pre', 'fixed-draft'):
            case = CASES[name]
            amounts, difference = case['conclusion']
            for wrong in (amounts.replace('2024년','2023년'), amounts.replace('2025년','2024년'),
                          amounts.replace('9,198만','9,199만'), amounts.replace('6,831억 9,198만','7,088억 6,611만')):
                with self.subTest(name=name, sentence=wrong):
                    result = verdict(case, [wrong, difference])
                    self.assertEqual(result.checks[0].status, 'MISMATCH')
                    self.assertEqual(result.checks[1].status, 'PASS')

    def test_summary_is_judged_independently_with_the_same_evidence(self):
        case = CASES['post']
        line, = case['conclusion']
        self.assertEqual(verdict(case, [line], summary=line).summary_status, 'PASS')
        result = verdict(case, [line], summary=line.replace('많습니다','적습니다'))
        self.assertEqual((result.checks[0].status, result.summary_status, result.status),
                         ('PASS', 'MISMATCH', 'NEEDS_REVIEW'))
