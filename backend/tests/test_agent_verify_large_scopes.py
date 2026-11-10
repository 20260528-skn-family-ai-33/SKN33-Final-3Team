"""Verify the maximum task/row shape with disjoint product dictionaries."""
import unittest
from time import perf_counter

from backend.app.agents.state import Draft, ToolResult
from backend.app.agents.verify import verify
from backend.tests import test_agent_compose as compose_tests


class LargeScopesTest(unittest.TestCase):
    def test_three_disjoint_snapshots_fit_verify_budget(self):
        state = compose_tests.ComposeTest().state(['STATISTICS'] * 3)
        snapshots, conclusions, explanation = {}, [], []
        for i, task in enumerate(state.plan.tasks):
            task.answer_status = 'ANSWERED'
            step = next(s.step_id for s in task.steps if s.tool == 'query_trade_data')
            state.results[step] = ToolResult(status='OK', result_id=step, digest={'rows': []})
            rows = [dict(country_name='미국', product_name=f'품목{i * 1000 + j:04d}', amount_usd=j + 1)
                    for j in range(1000)]
            snapshots[step] = dict(_verification_source='SNAPSHOT', rows=rows, trade_flow='EXPORT')
            sentence = f"미국 {rows[0]['product_name']} 수출액은 1달러입니다."
            conclusions.append(dict(task_id=task.task_id, sentences=[sentence]))
            if i > 0:
                explanation.append(f"미국 {rows[-1]['product_name']} 수출액은 1000달러입니다.")
        summary = conclusions[0]['sentences'][0]
        state.draft = Draft(conclusion=conclusions, explanation=explanation, turn_summary=summary)
        start = perf_counter()
        verify(state, snapshots=snapshots, countries=('미국',))
        elapsed = perf_counter() - start
        self.assertEqual(state.verification.status, 'PASS')
        self.assertEqual(len(state.verification.checks), 5)
        self.assertEqual(state.draft.turn_summary, summary)
        self.assertLess(elapsed, 5.0)
