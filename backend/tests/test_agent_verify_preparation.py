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

    def test_mixed_claims_use_original_offsets_for_conclusion_explanation_and_summary(self):
        sentence = '미국 수출액은 1억 2,346만 달러이며 전년 동월 대비 1.5% 감소했습니다.[1, 2]'
        rows = [dict(country_name='미국', amount_usd=123460000, yoy_pct=3, mom_pct=-1.5)]
        state, snapshots = self.fixture([rows], [[sentence]], [sentence], sentence)
        verdict = verification.judge_draft(state, snapshots=snapshots, countries=['미국'])
        self.assertEqual(state.draft.turn_summary, sentence)
        self.assertEqual([c.index for c in verdict.confirmed_claims], [0, 1, None])
        self.assertEqual([c.section for c in verdict.feedback], ['conclusion', 'explanation', 'summary'])
        for keep, fix in zip(verdict.confirmed_claims, verdict.feedback, strict=True):
            self.assertEqual((keep.action, keep.cell), ('PRESERVE', ('rows', '0', 'amount_usd')))
            self.assertEqual(keep.passage, '미국 수출액은 1억 2,346만 달러')
            self.assertEqual((fix.action, fix.reason, fix.cell), ('CORRECT', 'COMPARISON', ('rows', '0', 'mom_pct')))
            self.assertEqual(fix.passage, '전년 동월 대비 1.5% 감소했습니다')
            self.assertLessEqual(keep.span[1], fix.span[0])
            for item in (keep, fix):
                self.assertEqual(sentence[slice(*item.span)], item.passage)

    def test_repeated_amount_after_article_aside_has_its_own_offset(self):
        sentence = '수요 감소에도 미국 수출액은 10달러이고 일본 수출액은 10달러입니다.'
        rows = [dict(country_name='미국', amount_usd=10), dict(country_name='일본', amount_usd=11)]
        state, snapshots = self.fixture([rows], [[sentence]], [], '')
        verdict = verification.judge_draft(state, snapshots=snapshots, countries=['미국', '일본'])
        keep, = verdict.confirmed_claims
        fix, = verdict.feedback
        self.assertEqual(keep.passage, '미국 수출액은 10달러')
        self.assertEqual(fix.span, (sentence.rindex('10달러'), sentence.rindex('10달러') + 4))
        self.assertEqual(fix.action, 'CORRECT')

    def test_unknown_or_expired_evidence_is_review_only(self):
        sentence = '미국 수출은 전년 동월 대비 1.5% 감소했습니다.'
        state, snapshots = self.fixture([[dict(country_name='미국', mom_pct=-1.5)]], [[sentence]], [], '')
        step = next(iter(snapshots))
        for expired, expected in [(False, 'NOT_CONFIRMED'), (True, 'SOURCE_UNAVAILABLE')]:
            if expired:
                snapshots[step] = {'_verification_source': 'SNAPSHOT', '_source_unavailable': True}
            result = verification.judge_draft(state, snapshots=snapshots, countries=['미국'])
            item, = result.feedback
            self.assertEqual((item.action, item.reason, item.cell), ('REVIEW', expected, None))
            self.assertFalse(result.confirmed_claims)

    def test_saved_task_period_fills_only_its_own_sql_digest(self):
        from backend.app.agents.state import Conditions
        a = '9월 미국 수출액은 10달러입니다.'
        rows = [dict(country_name='미국', month='2026-09', amount_usd=10)]
        state, snapshots = self.fixture([rows, rows], [[a], [a]], [a], a)
        first = next(iter(snapshots))
        september = {'start': '2026-09-01', 'end': '2026-09-01'}
        august = {'start': '2026-08-01', 'end': '2026-08-01'}
        for task, step, window in zip(state.plan.tasks, snapshots, [september, august], strict=True):
            task.conditions = Conditions(period=window)
            state.results[step].digest = {'rows': rows}
        result = verification.judge_draft(state)
        self.assertEqual([c.status for c in result.checks], ['PASS', 'MISMATCH', 'PASS'])
        self.assertNotIn('period', state.results[first].digest)
        state.results[first].digest['period'] = august
        self.assertEqual(verification.judge_draft(state).checks[0].status, 'MISMATCH')
        state.results[first].digest['period'] = september
        # A supplied snapshot is authoritative even when it lacks a window.
        self.assertEqual(verification.judge_draft(state, snapshots=snapshots).checks[0].status, 'MISMATCH')

    def test_shared_quantity_is_protected_for_one_target_and_not_editable_for_the_other(self):
        sentence = '미국과 일본 수출액은 모두 10달러입니다.'
        rows = [dict(country_name='미국', amount_usd=10), dict(country_name='일본', amount_usd=11)]
        state, snapshots = self.fixture([rows], [[sentence]], [], '')
        verdict = verification.judge_draft(state, snapshots=snapshots, countries=['미국', '일본'])
        keep, = verdict.confirmed_claims
        review, = verdict.feedback
        self.assertEqual((keep.targets, keep.passage), (('미국',), '10달러'))
        self.assertEqual((review.targets, review.span, review.action), (('일본',), keep.span, 'REVIEW'))

    def test_article_date_and_caveat_preserve_the_measured_amount_and_direction(self):
        source = dict(trade_flow='EXPORT', period=dict(start='2026-09', end='2026-09'),
                      compare_period=dict(start='2026-08', end='2026-08'), rows=[
                          dict(country_name='일본', month='2026-09', amount_usd=80, mom_pct=-20)])
        sentence = '9월 15일 기사에 따르면 일본 수출은 전월 대비 감소했지만 원인은 확인되지 않았습니다.'
        for text, good in [(sentence, True), (sentence.replace('감소', '증가'), False),
                           ('9월 15일 일본 수출액은 80달러입니다.', True),
                           ('9월 15일 일본 수출액은 81달러입니다.', False),
                           ('일본 수출 증가: 9월 수출액은 80달러입니다.', False)]:
            with self.subTest(text=text):
                self.assertEqual(verification.check_sentence(text, [source])[0], good)

    def test_outlook_requires_the_selected_market_anchor_and_stored_category(self):
        source = dict(kind='PREDICTION', anchor='2026-08', trade_flow='EXPORT', rows=[
            dict(country_name='일본', hs_name='사과', growth_outlook='증가 예상', exp_12m=60),
            dict(country_name='미국', hs_name='홍차', growth_outlook=None, exp_12m=50)])
        tests = [
            ('2026년 8월 기준 일본 사과 시장은 증가 예상 시장입니다.', True),
            ('2026년 7월 기준 일본 사과 시장은 증가 예상 시장입니다.', False),
            ('2026년 8월 기준 일본 사과 시장은 감소 예상 시장입니다.', False),
            ('일본 홍차 시장은 증가 예상 시장입니다.', False),
            ('미국 홍차 시장은 증가 예상 시장입니다.', False),
            ('미국 홍차 시장은 유형을 판정하지 못했습니다.', True),
            ('일본 사과 시장은 유형을 판정하지 못했습니다.', False),
            ('미국 홍차 시장은 유형을 판정하지 못한 것은 아닙니다.', False),
            ('미국 홍차는, 증가 예상 시장입니다.', False),
            ('증가 예상 시장입니다.', False),
            ('일본 사과 수출이 전월 대비 증가했습니다.', False),
            ('일본 사과 과거 12개월 수출액은 61달러이며 증가 예상 시장입니다.', False)]
        for text, good in tests:
            with self.subTest(text=text):
                self.assertEqual(verification.check_sentence(text, [source])[0], good)
        del source['rows'][1]['growth_outlook']
        self.assertFalse(verification.check_sentence('미국 홍차 시장은 유형을 판정하지 못했습니다.', [source])[0])

    def test_monthly_null_zero_and_period_totals_use_separate_cells(self):
        source = dict(trade_flow='EXPORT', period={'start':'2026-07','end':'2026-09'}, targets=[
            dict(country_name='미국', verified_subtotal_usd=90, monthly=[
                dict(month='2026-07', amount_usd=None), dict(month='2026-08', amount_usd=0),
                dict(month='2026-09', amount_usd=90)])])
        for text, good in [('미국 수출에서 0인 달은 8월, 미집계인 달은 7월입니다.', True),
                           ('미국 수출에서 0인 달은 7월, 미집계인 달은 8월입니다.', False),
                           ('미국 수출에서 미집계인 달은 9월입니다.', False),
                           ('태국 수출에서 0인 달은 8월입니다.', False),
                           ('미국 수출에서 8월은 1달러입니다.', False),
                           ('3분기 미국 수출 소계는 90달러입니다.', True),
                           ('2분기 미국 수출 소계는 90달러입니다.', False)]:
            with self.subTest(text=text):
                self.assertEqual(verification.check_sentence(text, [source])[0], good)

    def test_share_denominator_must_come_from_its_own_cell(self):
        source = dict(kind='SHARE', trade_flow='EXPORT', denominator_usd=1000,
                      items=[dict(country_name='일본', amount_usd=100, share_pct=10)])
        sentence = '일본 비중은 10%이며, 분모인 한국 전체 수출액은 1,000달러입니다.'
        self.assertTrue(verification.check_sentence(sentence, [source])[0])
        self.assertFalse(verification.check_sentence(sentence.replace('1,000달러','100달러'), [source])[0])

    def test_other_task_support_labels_review_without_clearing_summary_failure(self):
        text = '미국 수출액은 200달러입니다.'
        state, sources = self.fixture([[dict(country_name='일본', amount_usd=100)],
                                       [dict(country_name='미국', amount_usd=200)]],
                                      [['일본 수출액은 100달러입니다.'], [text]], [], text)
        result = verification.judge_draft(state, snapshots=sources)
        self.assertEqual(result.summary_status, 'MISMATCH')
        self.assertEqual(result.status, 'NEEDS_REVIEW')
        item, = result.feedback
        self.assertEqual((item.action, item.reason, item.cell), ('REVIEW', 'OUTSIDE_SUMMARY_SCOPE', None))
