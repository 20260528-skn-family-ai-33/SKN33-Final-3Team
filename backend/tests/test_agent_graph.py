import unittest
from datetime import datetime
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from backend.app.agents.graph import build_graph, graph_config
from backend.app.agents.planner import build_plan
from backend.app.agents.state import (AgentState, PlannerOutput, Pending, ToolResult,
                                      ReportWait, Draft, Verification)


class Services:
    def __init__(self, intent='STATISTICS', ask=False, mismatch=False, slots=None):
        self.intent, self.ask, self.mismatch, self.slots = intent, ask, mismatch, slots or {}
        self.events = []
        self.saved = []

    def guard(self, state, node): self.events.append(node)

    def planner(self, state):
        state.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': self.intent, 'question': 'q', 'slots': self.slots}]))

    def check(self, state, step):
        if self.ask and not state.plan.tasks[0].resolved:
            state.pending = Pending(clarification_id='c', task_id='t1', kind='COUNTRY',
                condition_key='country', message='국가?', choices=[], asked_at=datetime(2026, 11, 2))
            state.ask_counts['country'] = state.ask_counts.get('country', 0) + 1
            state.status = 'NEEDS_INPUT'
        else: step.status = 'DONE'

    def resume(self, state, answer):
        state.plan.tasks[0].resolved['country'] = {'country_codes': [answer]}
        state.pending, state.status = None, 'RUNNING'
        state.plan.tasks[0].steps[0].status = 'PENDING'

    def tool(self, state, step):
        if step.tool == 'generate_report':
            return ReportWait(step_id=step.step_id, report_id='r', started_at=datetime(2026, 11, 2))
        return ToolResult(status='OK', result_id='result')

    def report_ready(self, state): return ToolResult(status='OK', report_id='r')

    def compose(self, state):
        state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['완료했습니다.']}], turn_summary='')

    def compose_text(self, state):
        self.events.append('compose_text')
        state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['하나입니다.', '둘입니다.', '셋입니다.']}],
                            explanation=['설명입니다.'] * 6, turn_summary='')

    def verify(self, state):
        state.verification = Verification(status='NEEDS_REVIEW' if self.mismatch else 'PASS',
            regeneration_count=state.verification.regeneration_count)

    def finalize(self, state): self.saved.append(state.model_dump())


def initial():
    return AgentState(input={'job_id': 'j', 'session_id': 's', 'user_id': 1, 'question': 'q',
        'context': {'session_id': 's'}, 'received_at': datetime(2026, 11, 2)}).model_dump()


class AgentGraphTest(unittest.TestCase):
    def test_success_round_trip_and_node_order(self):
        services = Services()
        graph = build_graph(services, InMemorySaver())
        result = graph.invoke(initial(), graph_config('j'))
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertEqual(result['plan']['tasks'][0]['answer_status'], 'ANSWERED')
        self.assertEqual(services.events, ['planner', 'dispatch', 'check_conditions', 'dispatch',
            'query_trade_data', 'dispatch', 'build_chart', 'dispatch', 'compose', 'verify', 'finalize'])
        self.assertEqual(len(services.saved), 1)
        self.assertEqual(graph.get_state(graph_config('j')).values['status'], 'SUCCEEDED')

    def test_text_answer_skips_chart_and_uses_text_node(self):
        services = Services(slots={'answer_format': 'TEXT'})
        result = build_graph(services, InMemorySaver()).invoke(initial(), graph_config('j'))
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertNotIn('build_chart', services.events)
        self.assertEqual(services.events[-4:], ['compose', 'compose_text', 'verify', 'finalize'])
        self.assertEqual(len(result['draft']['conclusion'][0]['sentences']), 3)

    def test_conversation_has_no_steps_and_uses_text_node(self):
        services = Services(intent='CONVERSATION')
        result = build_graph(services, InMemorySaver()).invoke(initial(), graph_config('j'))
        self.assertEqual(result['plan']['tasks'][0]['steps'], [])
        self.assertEqual(result['plan']['tasks'][0]['answer_status'], 'ANSWERED')
        self.assertNotIn('query_trade_data', services.events)
        self.assertIn('compose_text', services.events)

    def test_conversation_numbers_must_come_from_recent_turns(self):
        from types import SimpleNamespace
        from backend.app.agents.verify import check_history
        recent = [SimpleNamespace(question='중국 화장품 수출 추이', summary='2025년 중국 화장품 수출은 2,610만 달러입니다.')]
        self.assertTrue(check_history('이전 질문은 중국 화장품 수출 추이였고 2,610만 달러였습니다.', recent)[0])
        self.assertFalse(check_history('이전 결과는 3,000만 달러였습니다.', recent)[0])
        self.assertTrue(check_history('HS코드는 품목 분류 번호입니다.', [])[0])
        # 값이 같아도 단위가 다르거나, 요약에 없는 날짜를 쓰면 통과하지 않는다
        self.assertFalse(check_history('이전 결과는 2,610만%였습니다.', recent)[0])
        self.assertFalse(check_history('2024년 결과는 2,610만 달러였습니다.', recent)[0])
        self.assertTrue(check_history('2025년 결과는 2,610만이었습니다.', recent)[0])   # 단위 생략은 값·배율만 본다
        # 기간 길이(개월)는 날짜가 아니라 대조하지 않는다. 이전 질문의 확정 조건 기간 달은 날짜로 허용한다
        self.assertTrue(check_history('아까 최근 12개월 추이를 물으셨습니다.', recent)[0])
        from datetime import date
        period = SimpleNamespace(period=SimpleNamespace(start=date(2024, 1, 1), end=date(2024, 12, 31)))
        asked = [SimpleNamespace(question='중국 화장품 수출', summary='', conditions=period)]
        self.assertTrue(check_history('2024년 1월부터 12월까지 물으셨습니다.', asked)[0])
        self.assertFalse(check_history('2023년을 물으셨습니다.', asked)[0])

    def test_hs_lookup_plan_has_only_lookup_step(self):
        plan, _ = build_plan(PlannerOutput(tasks=[{'intent': 'HS_LOOKUP', 'question': '면도기 HS코드', 'slots': {'products': ['면도기']}}]))
        self.assertEqual([s.tool for s in plan.tasks[0].steps], ['lookup_hs'])
        services = Services(intent='HS_LOOKUP', slots={'products': ['면도기']})
        result = build_graph(services, InMemorySaver()).invoke(initial(), graph_config('j'))
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertIn('lookup_hs', services.events)
        self.assertIn('compose_text', services.events)

    def test_hs_lookup_sentence_numbers_must_be_found_codes(self):
        from backend.app.agents.verify import check_codes
        digests = [{'kind': 'HS_CODES', 'items': [{'code': '851010', 'name': '면도기', 'level': 'HS6'}]}]
        self.assertTrue(check_codes('면도기는 HS 851010입니다.', digests)[0])
        self.assertFalse(check_codes('면도기는 HS 851020입니다.', digests)[0])
        # HS를 붙여 쓴 코드도 대조한다. 판본 표기(HS2022)는 코드가 아니다
        digests[0]['hs_ver'] = 'HS2022'
        self.assertTrue(check_codes('HS2022 기준 면도기는 HS851010입니다.', digests)[0])
        self.assertFalse(check_codes('면도기는 HS851020입니다.', digests)[0])

    def test_text_sentence_limits_apply_only_to_text_tasks(self):
        from backend.app.agents.state import draft_limit_error
        draft = Draft(conclusion=[{'task_id': 't1', 'sentences': ['가.', '나.', '다.']}], turn_summary='')
        for slots, expected in (({}, True), ({'answer_format': 'TEXT'}, False)):
            plan, _ = build_plan(PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': 'q', 'slots': slots}]))
            self.assertEqual(bool(draft_limit_error(plan, draft)), expected)

    def test_interrupt_resume_does_not_repeat_planner_or_ask_counter(self):
        services = Services(ask=True)
        graph = build_graph(services, InMemorySaver())
        config = graph_config('j')
        graph.invoke(initial(), config)
        snapshot = graph.get_state(config)
        self.assertTrue(snapshot.tasks[0].interrupts)
        self.assertEqual(snapshot.values['status'], 'NEEDS_INPUT')
        self.assertEqual(services.saved, [])
        graph.invoke(Command(resume='US'), config)
        self.assertEqual(services.events.count('planner'), 1)
        self.assertEqual(services.saved[0]['ask_counts']['country'], 1)

    def test_report_wait_releases_graph_and_resumes_with_none(self):
        services = Services(intent='REPORT')
        graph = build_graph(services, InMemorySaver())
        config = graph_config('j')
        graph.invoke(initial(), config)
        snapshot = graph.get_state(config)
        self.assertEqual(snapshot.next, ('report_ready',))
        self.assertEqual(snapshot.values['status'], 'WAITING_REPORT')
        self.assertFalse(snapshot.tasks[0].interrupts)
        self.assertEqual(services.saved, [])
        graph.invoke(None, config)
        self.assertEqual(len(services.saved), 1)
        self.assertEqual(services.events.count('generate_report'), 1)

    def test_numeric_regeneration_happens_at_most_once(self):
        services = Services(mismatch=True)
        graph = build_graph(services, InMemorySaver())
        result = graph.invoke(initial(), graph_config('j'))
        self.assertEqual(services.events.count('compose'), 2)
        self.assertEqual(result['verification']['status'], 'NEEDS_REVIEW')
        self.assertEqual(result['verification']['regeneration_count'], 1)

    def test_regeneration_does_not_spend_reserved_finish_budget(self):
        services = Services(mismatch=True)
        data = initial()
        data['run_budget']['charged_seconds'] = 146
        graph = build_graph(services, InMemorySaver())
        graph.invoke(data, graph_config('j'))
        self.assertEqual(services.events.count('compose'), 1)

    def test_guard_stops_before_durable_save(self):
        services = Services()
        original = services.guard
        def guard(state, node):
            if node == 'finalize': raise RuntimeError('cancelled')
            original(state, node)
        services.guard = guard
        graph = build_graph(services, InMemorySaver())
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            graph.invoke(initial(), graph_config('j'))
        self.assertEqual(services.saved, [])

    def test_saved_draft_and_graph_verify_same_comparison_feedback(self):
        from backend.app.agents.verify import judge_draft, verify

        sentence = '미국 수출은 전년 동월 대비 1.5% 감소했다 [1, 2].'
        snapshot = {'_verification_source': 'SNAPSHOT', 'rows': [
            {'country_name': '미국', 'yoy_pct': 3, 'mom_pct': -1.5}]}

        class SavedDraftServices(Services):
            def compose(self, state):
                state.draft = Draft(conclusion=[{'task_id': 't1', 'sentences': [sentence]}],
                                    turn_summary=sentence)

            def verify(self, state):
                evidence = {'t1.sql': snapshot}
                self.before = state.model_dump()
                self.standalone = judge_draft(state, snapshots=evidence, countries=['미국'])
                verify(state, snapshots=evidence, countries=['미국'])

        services = SavedDraftServices()
        graph = build_graph(services, InMemorySaver())
        result = graph.invoke(initial(), graph_config('j'))
        self.assertEqual(result['verification'], services.standalone.model_dump())
        self.assertEqual(services.before['draft']['turn_summary'], sentence)
        self.assertEqual(result['draft']['turn_summary'], '')
        self.assertEqual(services.standalone.failed_summary, sentence)
        body, summary = services.standalone.feedback
        self.assertEqual((body.index, summary.index), (0, None))
        self.assertEqual((body.reason, body.cell), ('COMPARISON', ('rows', '0', 'mom_pct')))
        self.assertEqual(services.standalone.checks[0].numbers, ['1.5%'])
