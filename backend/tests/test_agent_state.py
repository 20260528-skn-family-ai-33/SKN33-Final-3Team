"""에이전트 state 계약: Redis 왕복, 계획 순서, 되묻기 일관성, 크기 상한."""
import unittest
from datetime import datetime

from pydantic import ValidationError

from backend.app.agents.state import AgentState, MAX_DIGEST_BYTES, SessionContext, TurnSummary


def state(**extra):
    base = {'input': {'job_id': '11', 'session_id': '3', 'user_id': 7, 'question': '베트남 화장품 수출 왜 늘었어?',
                      'context': {'session_id': '3'}, 'received_at': datetime(2026, 10, 29, 9)},
            'plan': {'intent': 'CAUSE', 'steps': [
                {'step_id': 's1', 'tool': 'check_conditions'},
                {'step_id': 's2', 'tool': 'query_trade_data', 'depends_on': ['s1']},
                {'step_id': 's3', 'tool': 'search_articles', 'depends_on': ['s2']},
                {'step_id': 's4', 'tool': 'build_chart', 'depends_on': ['s2']}]}}
    return AgentState.model_validate({**base, **extra})


class AgentStateTest(unittest.TestCase):
    def test_redis_round_trip(self):
        s = state(conditions={'hs_items': [{'hs_code': '3304', 'hs_ver': 'HS2022'}], 'country_codes': ['VN'],
                              'period': {'start': '2024-10-01', 'end': '2025-09-01'}},
                  results={'s2': {'status': 'OK', 'result_id': 'r1', 'digest': {'total_usd': 1.5}}})
        self.assertEqual(AgentState.model_validate_json(s.model_dump_json()), s)

    def test_ready_steps_follow_dependencies(self):
        s = state()
        self.assertEqual([x.step_id for x in s.ready_steps()], ['s1'])
        s.plan.steps[0].status = 'DONE'; s.plan.steps[1].status = 'DONE'
        self.assertEqual([x.step_id for x in s.ready_steps()], ['s3', 's4'])

    def test_plan_rejects_forward_dependency_and_missing_check(self):
        with self.assertRaises(ValidationError):
            state(plan={'intent': 'STATISTICS', 'steps': [{'step_id': 's1', 'tool': 'check_conditions', 'depends_on': ['s2']},
                                                         {'step_id': 's2', 'tool': 'query_trade_data'}]})
        with self.assertRaises(ValidationError):
            state(plan={'intent': 'STATISTICS', 'steps': [{'step_id': 's1', 'tool': 'query_trade_data'}]})

    def test_pending_only_while_waiting(self):
        pending = {'clarification_id': 'c1', 'kind': 'HS_ITEM', 'message': '품목을 고르세요',
                   'choices': [{'choice_id': '330499', 'label': '기타 화장품',
                                'patch': {'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}]}}],
                   'asked_at': datetime(2026, 10, 29, 9, 1)}
        self.assertEqual(state(status='NEEDS_INPUT', pending=pending).pending.kind, 'HS_ITEM')
        with self.assertRaises(ValidationError): state(status='NEEDS_INPUT')
        with self.assertRaises(ValidationError): state(status='RUNNING', pending=pending)

    def test_rows_do_not_go_into_state(self):
        rows = [{'month': f'2025-{m:02}', 'usd': 1.0, 'country': 'VN' * 50} for m in range(1, 13)] * 10
        with self.assertRaises(ValidationError):
            state(results={'s2': {'status': 'OK', 'result_id': 'r1', 'digest': {'rows': rows}}})
        self.assertGreater(len(str(rows)), MAX_DIGEST_BYTES)

    def test_context_keeps_last_five_turns_and_latest_conditions(self):
        context = SessionContext(session_id='3')
        vn = {'country_codes': ['VN'], 'period': {'start': '2025-01-01', 'end': '2025-09-01'}}
        for n in range(7):
            turn = TurnSummary.model_validate({'question': f'q{n}', 'intent': 'STATISTICS',
                                               'conditions': vn if n == 5 else None, 'answered_at': datetime(2026, 10, 29, 9, n)})
            context = context.remember(turn)
        self.assertEqual([t.question for t in context.recent], ['q2', 'q3', 'q4', 'q5', 'q6'])
        self.assertEqual(context.conditions.country_codes, ['VN'])  # 조건 없는 답변(q6)은 최근 조건을 지우지 않는다
        self.assertEqual(SessionContext.model_validate_json(context.model_dump_json()), context)

    def test_result_must_belong_to_plan_and_failure_needs_reason(self):
        with self.assertRaises(ValidationError): state(results={'s9': {'status': 'OK'}})
        with self.assertRaises(ValidationError): state(results={'s2': {'status': 'FAILED'}})


if __name__ == '__main__':
    unittest.main()
