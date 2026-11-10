"""Report Agent 실제 LLM 평가기의 채점·안전장치를 네트워크 없이 검증한다(실제 호출은 하지 않는다)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app.agents.state import PlannerOutput
from backend.scripts import evaluate_report_agent_live as live


def task(intent, sources=(), flow=None, countries=None, products=None, recent=None, period=None):
    return {'intent': intent, 'question': 'q', 'sources': list(sources), 'trade_flow': flow, 'countries': countries,
            'products': products, 'recent_months': recent, 'period': period}


def case(intents, report, category='condition_report'):
    return {'id': 'X', 'category': category, 'question': 'q', 'expected': {'intents': intents, 'report': report}}


REPORT = {'sources': [], 'trade_flow': 'EXPORT', 'countries': ['미국'], 'products': ['화장품'], 'period': {'recent_months': 6}}


class LiveScoringTest(unittest.TestCase):
    def test_all_applicable_fields_must_match(self):
        good = [task('REPORT', flow=None, countries=['미국'], products=['기타 화장품'], recent=6)]
        self.assertTrue(live.score_case(case([['REPORT']], REPORT), good)['correct'])   # 수출입 미지정 = 기본 수출
        for wrong, field in ((task('REPORT', countries=['중국'], products=['화장품'], recent=6), 'countries'),
                             (task('REPORT', countries=['미국'], products=['화장품', '세제'], recent=6), 'products'),
                             (task('REPORT', countries=['미국'], products=['화장품'], recent=3), 'period'),
                             (task('REPORT', flow='IMPORT', countries=['미국'], products=['화장품'], recent=6), 'trade_flow')):
            with self.subTest(field=field):
                scored = live.score_case(case([['REPORT']], REPORT), [wrong])
                self.assertFalse(scored['correct'])
                self.assertFalse(scored['marks'][field])

    def test_result_based_report_is_scored_on_the_source_task(self):
        expected = {**REPORT, 'sources': [1]}
        plan = [task('STATISTICS', countries=['미국'], products=['화장품'], recent=6), task('REPORT', sources=[1])]
        self.assertTrue(live.score_case(case([['STATISTICS', 'REPORT']], expected), plan)['correct'])
        unlinked = [plan[0], task('REPORT')]
        self.assertFalse(live.score_case(case([['STATISTICS', 'REPORT']], expected), unlinked)['marks']['sources'])

    def test_not_report_questions_must_not_create_a_report(self):
        expected = case([['CONVERSATION'], ['OUT_OF_SCOPE']], None, 'not_report')
        self.assertTrue(live.score_case(expected, [task('OUT_OF_SCOPE')])['correct'])
        made = live.score_case(expected, [task('REPORT')])
        self.assertFalse(made['correct'])
        self.assertFalse(made['marks']['report_present'])

    def test_inherit_requires_empty_slots_and_null_is_not_scored(self):
        inherit = {'sources': [], 'trade_flow': 'EXPORT', 'countries': 'INHERIT', 'products': 'INHERIT', 'period': 'INHERIT'}
        self.assertTrue(live.score_case(case([['REPORT']], inherit), [task('REPORT')])['correct'])
        self.assertFalse(live.score_case(case([['REPORT']], inherit), [task('REPORT', countries=['미국'])])['correct'])
        loose = {**REPORT, 'countries': None, 'period': None}
        self.assertTrue(live.score_case(case([['REPORT']], loose), [task('REPORT', countries=['일본'], products=['화장품'])])['correct'])

    def test_summary_denominators_and_detection_rates(self):
        records = [{'correct': True, 'category': 'a', 'expects_report': True, 'marks': {'intent': True, 'report_present': True}, 'latency_seconds': 1},
                   {'correct': False, 'category': 'a', 'expects_report': True, 'marks': {'intent': False, 'report_present': False}, 'latency_seconds': 3},
                   {'correct': True, 'category': 'b', 'expects_report': False, 'marks': {'intent': True, 'report_present': True}, 'latency_seconds': 2}]
        summary = live.summarize(records, [{'category': 'a'}, {'category': 'b'}, {'category': 'b'}])
        self.assertEqual(summary['overall_accuracy'], {'passed': 2, 'total': 3, 'rate_pct': 66.67})
        self.assertEqual(summary['report_detection']['recall']['passed'], 1)
        self.assertEqual(summary['report_detection']['false_positive_avoided']['passed'], 1)
        self.assertEqual(summary['planned'], 3)


class LiveHarnessTest(unittest.TestCase):
    def test_production_planner_path_runs_with_a_fake_model(self):
        """실제 호출 없이도 운영 플래너 경로(Services.planner → build_plan)가 평가 입력으로 돈다."""
        client = Mock()
        client.parse.return_value = PlannerOutput(tasks=[
            {'intent': 'STATISTICS', 'question': '조회', 'slots': {'recent_months': 3, 'countries': [{'mention': '미국', 'name': '미국'}]}},
            {'intent': 'REPORT', 'question': '리포트', 'source_tasks': [1]}])
        state = live.run_planner('미국 최근 3개월 조회 후 리포트', None, client, SimpleNamespace(get=lambda k, d=None: d, values={}))
        view = live.plan_view(state.plan)
        self.assertEqual([t['intent'] for t in view], ['STATISTICS', 'REPORT'])
        self.assertEqual(view[1]['sources'], [1])
        self.assertEqual(view[0]['countries'], ['미국'])
        self.assertNotIn('expected', json.dumps(client.parse.call_args.kwargs['payload'], ensure_ascii=False))  # 정답을 모델에 보내지 않음

    def test_changed_questions_and_holdout_rerun_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            questions = root / 'q.jsonl'
            questions.write_text('\n'.join(json.dumps(case([['REPORT']], None) | {'id': f'Q{i}'}) for i in range(20)) + '\n', encoding='utf-8')
            target = {'questions': questions, 'manifest': root / 'm.json', 'review': root / 'r.md', 'result': root / 'out.json',
                      'lock': root / 'lock.json', 'role': 'holdout'}
            live.freeze(target)
            args = SimpleNamespace(allow_unreviewed_gold=False, max_cost_usd=0.1, set='holdout', output=None)
            with self.assertRaisesRegex(ValueError, '사람이 검토한 정답'):
                live.run(target, args)
            args.allow_unreviewed_gold = True
            target['lock'].write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '한 번만'):
                live.run(target, args)
            questions.write_text(questions.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            questions.write_text(questions.read_text(encoding='utf-8').replace('"Q0"', '"Q00"'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '바뀌었습니다'):
                live.run(target, args)

    def test_holdout_refuses_changed_prompt_or_schema_before_key_and_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            questions = root / 'q.jsonl'
            questions.write_text('\n'.join(json.dumps(case([['REPORT']], None) | {'id': f'Q{i}'}) for i in range(20)) + '\n', encoding='utf-8')
            target = {'questions': questions, 'manifest': root / 'm.json', 'review': root / 'r.md', 'result': root / 'out.json',
                      'lock': root / 'lock.json', 'role': 'holdout'}
            live.freeze(target)
            frozen = json.loads(target['manifest'].read_text(encoding='utf-8'))
            args = SimpleNamespace(allow_unreviewed_gold=True, max_cost_usd=0.1, set='holdout', output=None)
            for name in ('prompt_sha256', 'schema_sha256'):
                target['manifest'].write_text(json.dumps(frozen | {name: '0' * 64}), encoding='utf-8')
                with patch('backend.app.api.config.Settings') as settings, self.assertRaisesRegex(ValueError, name):
                    live.run(target, args)
                settings.assert_not_called()   # API 키를 읽기 전에 멈춘다
                self.assertFalse(target['lock'].exists())   # 잠금 파일도 만들지 않는다
            # 개발 세트는 플래너 수정 전후 비교용이라 거부하지 않고 바뀐 항목을 결과에 남긴다
            self.assertEqual(live.changed_inputs(frozen | {'prompt_sha256': '0' * 64}), ['prompt_sha256'])
            self.assertEqual(live.changed_inputs(frozen), [])

    def test_cost_cap_stops_before_the_call(self):
        recorder = live.Recorder(max_cost=0.000001)
        with patch('openai.OpenAI') as factory:
            context = recorder.factory(api_key='test', timeout=1, max_retries=0)
            with context as client:
                with self.assertRaises(live.EvaluationStopped):
                    client.responses.parse(model=live.MODEL, instructions='x' * 1000, input='y', max_output_tokens=2000)
            factory.return_value.responses.parse.assert_not_called()


if __name__ == '__main__':
    unittest.main()
