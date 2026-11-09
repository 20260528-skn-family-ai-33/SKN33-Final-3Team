"""Request-local evidence preparation, using the normal verify entry point."""
import unittest
from unittest.mock import patch

import backend.app.agents.verify as verification
from backend.app.agents.state import Draft, ToolResult
from backend.tests import test_agent_compose


class PreparationTest(unittest.TestCase):
    def fixture(self, row_groups, conclusions, explanations, summary):
        state = test_agent_compose.ComposeTest().state(['STATISTICS'] * len(row_groups))
        snapshots = {}
        blocks = []
        for task, rows, sentences in zip(state.plan.tasks, row_groups, conclusions, strict=True):
            task.answer_status = 'ANSWERED'
            step = next(s for s in task.steps if s.tool == 'query_trade_data')
            snapshots[step.step_id] = {'_verification_source': 'SNAPSHOT', 'rows': rows}
            state.results[step.step_id] = ToolResult(status='OK', result_id=step.step_id, digest={'rows': []})
            blocks.append(dict(task_id=task.task_id, sentences=sentences))
        state.draft = Draft(conclusion=blocks, explanation=explanations, turn_summary=summary)
        return state, snapshots

    def test_thousand_rows_six_sentences_and_summary(self):
        rows = [dict(country_name='미국', product_name=f'품목{i:04d}', amount_usd=i+1) for i in range(1000)]
        texts = [f'미국 품목{i:04d} 수출액은 {i+1}달러입니다.' for i in range(6)]
        state, snapshots = self.fixture([rows], [texts[:2]], texts[2:], texts[0])
        with patch.object(verification, 'build_facts', wraps=verification.build_facts) as builds:
            verification.verify(state, snapshots=snapshots, countries=('미국',))
        self.assertEqual(builds.call_count, 1)
        self.assertEqual([c.status for c in state.verification.checks], ['PASS'] * 6)
        self.assertEqual(state.draft.turn_summary, texts[0])
        rows[0]['amount_usd'] = 100
        verification.verify(state, snapshots=snapshots)
        self.assertEqual(state.verification.checks[0].status, 'MISMATCH')
        self.assertEqual(state.draft.turn_summary, '')

    def test_task_combined_and_primary_summary_scopes(self):
        rows = [[dict(country_name='미국', amount_usd=value)] for value in (100, 200)]
        a, b = [f'미국 수출액은 {value}달러입니다.' for value in (100, 200)]
        state, snapshots = self.fixture(rows, [[a,b],[a,b]], [a,b], b)
        paths = []
        inspect = verification._inspect_prepared
        def trace(sentence, prepared):
            ok, checks = inspect(sentence, prepared)
            paths.append(checks[0].matches)
            return ok, checks
        with patch.object(verification, '_inspect_prepared', trace), patch.object(
                verification, 'build_facts', wraps=verification.build_facts) as builds:
            verification.verify(state, snapshots=snapshots)
        cell = ('rows','0','amount_usd')
        first, second = ((0,cell),), ((1,cell),)
        self.assertEqual(paths, [first, (), (), first, first, second, ()])
        self.assertEqual(builds.call_count, 2)
        self.assertEqual(state.draft.turn_summary, '')

    def test_combined_scope_rechecks_ambiguous_aliases(self):
        short = '전동축'
        rows = [[dict(product_name=short+suffix, amount_usd=amount)]
                for suffix, amount in [(' 모델A', 100), (' 모델B', 200)]]
        a, b = [f'{short} 수출액은 {value}달러입니다.' for value in (100, 200)]
        state, snapshots = self.fixture(rows, [[a],[b]], [a,b], a)
        verification.verify(state, snapshots=snapshots, known_names=[short])
        self.assertEqual([c.status for c in state.verification.checks],
                         ['PASS', 'PASS', 'MISMATCH', 'MISMATCH'])
        self.assertEqual(state.draft.turn_summary, a)
