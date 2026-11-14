"""Check the rendered TREND conclusion and summary against the saved result."""
import unittest

from backend.app.agents.runtime import Services
from backend.app.agents.state import Conditions, ToolResult
from backend.app.agents.templates import months
from backend.app.agents.verify import inspect_sentence, judge_draft, snapshot_evidence
from backend.app.api.config import Settings
from backend.tests import test_agent_compose


class PeriodDifferenceTest(unittest.TestCase):
    def example(self, current, prior, delta):
        state = test_agent_compose.ComposeTest().state(['STATISTICS'])
        task, = state.plan.tasks
        task.answer_status = 'ANSWERED'
        task.slots.amount_difference_only = True
        def window(bounds):
            return dict(start=bounds[0] + '-01', end=bounds[1] + '-01')
        cond = task.conditions = Conditions(period=window(current), compare_period=window(prior),
                                            country_codes=['US'], hs_items=[dict(hs_code='123456', hs_ver='HS2022')])
        step = next(s for s in task.steps if s.tool == 'query_trade_data')
        target = dict(country_code='US', country_name='미국', hs_code='123456', hs_ver='HS2022', name='부품',
                      total_usd=100 + delta, compare_total_usd=100, period_difference_usd=delta,
                      requested_months=len(months(cond.period)), compare_requested_months=len(months(cond.compare_period)))
        rows = [dict(target, month=a.strftime('%Y-%m'), compare_month=b.strftime('%Y-%m'))
                for a,b in zip(months(cond.period), months(cond.compare_period))]
        state.results[step.step_id] = ToolResult(status='OK', result_id='saved',
                                                digest=dict(kind='TREND', targets=[target]))
        source = snapshot_evidence(dict(meta={'kind':'TREND'}, conditions=cond.model_dump(mode='json'), rows=rows))
        services = Services(None, Settings({}), None)
        services.compose(state)
        return state, services, {step.step_id:source}

    def test_rendered_difference_before_and_after_recomposition(self):
        annual = ('2025-01','2025-12'), ('2024-01','2024-12')
        monthly = ('2025-02','2025-02'), ('2025-01','2025-01')
        quarterly = ('2025-04','2025-06'), ('2025-01','2025-03')
        for window in (annual, monthly, quarterly):
            for delta in (25, 0, -25):
                with self.subTest(window=window, delta=delta):
                    state, services, snapshots = self.example(*window, delta)
                    for attempt in range(2):
                        state.verification.regeneration_count = attempt
                        services.compose(state)
                        verdict = judge_draft(state, snapshots=snapshots)
                        self.assertEqual(([c.status for c in verdict.checks], verdict.summary_status, verdict.status),
                                         (['PASS','PASS'], 'PASS', 'PASS'))
                        difference = state.draft.turn_summary
                        self.assertEqual(difference, state.draft.sentences()[-1])
                        ok, (check,) = inspect_sentence(difference, list(snapshots.values()))
                        self.assertTrue(ok)
                        self.assertEqual({path[-1] for _,path in check.matches}, {'period_difference_usd'})

    def test_changed_claims_fail_in_both_body_and_summary(self):
        for current, prior in [(('2025-01','2025-12'), ('2024-01','2024-12')),
                               (('2025-02','2025-02'), ('2025-01','2025-01')),
                               (('2025-04','2025-06'), ('2025-01','2025-03'))]:
            state, _, snapshots = self.example(current, prior, 25)
            original = state.draft.turn_summary
            zero, _, _ = self.example(current, prior, 0)
            for wrong in (original.replace('25달러','26달러'), original.replace('많습니다','적습니다'),
                          original.replace('2025년','2023년',1), zero.draft.turn_summary):
                with self.subTest(line=wrong):
                    state.draft.conclusion[0].sentences[-1] = state.draft.turn_summary = wrong
                    verdict = judge_draft(state, snapshots=snapshots)
                    self.assertEqual((verdict.checks[-1].status, verdict.summary_status, verdict.status),
                                     ('MISMATCH','MISMATCH','NEEDS_REVIEW'))

    def test_period_boundaries_and_reversed_operands(self):
        state, _, snapshots = self.example(('2025-04','2025-06'), ('2025-01','2025-03'), 25)
        for line, expected in (
            ('2025년 2분기 미국 부품 수출액은 2025년 1분기보다 25달러 많습니다.', True),
            ('2025년 1분기 미국 부품 수출액은 2025년 2분기보다 25달러 적습니다.', True),
            ('2025년 1분기 미국 부품 수출액은 2025년 2분기보다 25달러 많습니다.', False),
            (state.draft.turn_summary.replace('6월','5월'), False),
            (state.draft.turn_summary.replace('1월','2월'), False),
        ):
            with self.subTest(line=line):
                ok, _ = inspect_sentence(line, list(snapshots.values()))
                self.assertEqual(ok, expected)

    def test_zero_requires_a_stored_difference_even_when_amount_cells_are_zero(self):
        state, _, snapshots = self.example(('2025-02','2025-02'), ('2025-01','2025-01'), 0)
        source, = snapshots.values()
        line = state.draft.turn_summary
        for row in source['rows']:
            row.update(total_usd=0, compare_total_usd=0)
            del row['period_difference_usd']
        self.assertFalse(inspect_sentence(line, [source])[0])
