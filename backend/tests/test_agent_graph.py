import unittest
from datetime import datetime
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from backend.app.agents.graph import build_graph, graph_config
from backend.app.agents.planner import build_plan
from backend.app.agents.state import (AgentState, PlannerOutput, Pending, ToolResult,
                                      ReportWait, Draft, Verification)


class Services:
    def __init__(self, intent='STATISTICS', ask=False, mismatch=False):
        self.intent, self.ask, self.mismatch = intent, ask, mismatch
        self.events = []
        self.saved = []

    def guard(self, state, node): self.events.append(node)

    def planner(self, state):
        state.plan, _ = build_plan(PlannerOutput(tasks=[{'intent': self.intent, 'question': 'q'}]))

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
