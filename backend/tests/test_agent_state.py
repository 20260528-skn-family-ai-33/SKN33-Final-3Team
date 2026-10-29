"""에이전트 state 계약: 체크포인트 왕복, 하위 질문 계획, 되묻기 일관성, 차트 모양, 크기 상한."""
import unittest
from datetime import datetime

from pydantic import ValidationError

from backend.app.agents.state import AgentState, ChartSpec, MAX_DIGEST_BYTES, SessionContext, TurnSummary

CAUSE = {'task_id': 't1', 'intent': 'CAUSE', 'question': '베트남 화장품 수출 왜 늘었어?', 'steps': [
    {'step_id': 't1.check', 'tool': 'check_conditions'},
    {'step_id': 't1.sql', 'tool': 'query_trade_data', 'depends_on': ['t1.check']},
    {'step_id': 't1.news', 'tool': 'search_articles', 'depends_on': ['t1.check']},
    {'step_id': 't1.chart', 'tool': 'build_chart', 'depends_on': ['t1.sql']}]}
RANKING = {'task_id': 't2', 'intent': 'RANKING', 'question': '화장품 수출 상위 10개국', 'steps': [
    {'step_id': 't2.check', 'tool': 'check_conditions'},
    {'step_id': 't2.sql', 'tool': 'query_trade_data', 'depends_on': ['t2.check']}]}
PENDING = {'clarification_id': 'c1', 'task_id': 't1', 'kind': 'HS_ITEM', 'condition_key': 't1:hs:화장품',
           'message': '품목을 고르세요', 'asked_at': datetime(2026, 10, 29, 9, 1),
           'choices': [{'choice_id': '330499', 'label': '기타 화장품',
                        'patch': {'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}]}}]}


def state(tasks=(CAUSE,), **extra):
    base = {'input': {'job_id': '11', 'session_id': '3', 'user_id': 7, 'question': '베트남 화장품 수출 왜 늘었어?',
                      'context': {'session_id': '3'}, 'received_at': datetime(2026, 10, 29, 9)},
            'plan': {'tasks': list(tasks)}}
    return AgentState.model_validate({**base, **extra})


def chart(**extra):
    return {'chart_id': 'c1', 'type': 'line', 'title': '베트남 화장품 수출 추이', 'unit': 'USD', 'source': '관세청',
            'result_id': 'r1', 'labels': ['2025-08', '2025-09'], 'series': [{'name': '수출액', 'values': [1.0, 2.0]}], **extra}


class AgentStateTest(unittest.TestCase):
    def test_checkpoint_round_trip(self):
        task = {**CAUSE, 'conditions': {'hs_items': [{'hs_code': '3304', 'hs_ver': 'HS2022'}], 'country_codes': ['VN'],
                                        'period': {'start': '2024-10-01', 'end': '2025-09-01'}}}
        s = state([task], results={'t1.sql': {'status': 'OK', 'result_id': 'r1', 'digest': {'total_usd': 1.5}},
                                   't1.chart': {'status': 'OK', 'chart': chart()}})
        self.assertEqual(AgentState.model_validate_json(s.model_dump_json()), s)

    def test_ready_steps_across_tasks(self):
        s = state([CAUSE, RANKING])
        self.assertEqual([x.step_id for x in s.ready_steps()], ['t1.check', 't2.check'])
        s.plan.tasks[0].steps[0].status = 'DONE'
        self.assertEqual([x.step_id for x in s.ready_steps()], ['t1.sql', 't1.news', 't2.check'])

    def test_plan_rules(self):
        with self.assertRaises(ValidationError):  # 하위 질문 사이 의존
            state([CAUSE, {**RANKING, 'steps': [RANKING['steps'][0], {**RANKING['steps'][1], 'depends_on': ['t1.sql']}]}])
        with self.assertRaises(ValidationError):  # check_conditions로 시작하지 않음
            state([{**RANKING, 'steps': RANKING['steps'][1:]}])
        with self.assertRaises(ValidationError):  # 하위 질문은 3개까지
            state([CAUSE, RANKING, {**RANKING, 'task_id': 't3'}, {**RANKING, 'task_id': 't4'}])
        # 시장 유형은 지원 예정 안내만, 단계 없음
        self.assertEqual(state([{'task_id': 't1', 'intent': 'MARKET_TYPE', 'question': '베트남 화장품 시장 유형'}])
                         .plan.tasks[0].steps, [])

    def test_pending_only_while_waiting(self):
        self.assertEqual(state(status='NEEDS_INPUT', pending=PENDING).pending.kind, 'HS_ITEM')
        with self.assertRaises(ValidationError): state(status='NEEDS_INPUT')
        with self.assertRaises(ValidationError): state(status='RUNNING', pending=PENDING)
        with self.assertRaises(ValidationError): state(status='NEEDS_INPUT', pending={**PENDING, 'task_id': 't9'})

    def test_ask_limit_per_condition(self):
        self.assertEqual(state(ask_counts={'t1:hs:화장품': 2}).ask_counts['t1:hs:화장품'], 2)
        with self.assertRaises(ValidationError): state(ask_counts={'t1:hs:화장품': 3})

    def test_chart_shape(self):
        self.assertEqual(ChartSpec.model_validate(chart()).type, 'line')
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(series=[{'name': 'a', 'values': [1.0]}]))
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(type='grouped_bar'))
        with self.assertRaises(ValidationError): ChartSpec.model_validate(chart(labels=[str(n) for n in range(61)]))

    def test_rows_do_not_go_into_state(self):
        rows = [{'month': f'2025-{m:02}', 'usd': 1.0, 'country': 'VN' * 50} for m in range(1, 13)] * 10
        with self.assertRaises(ValidationError):
            state(results={'t1.sql': {'status': 'OK', 'result_id': 'r1', 'digest': {'rows': rows}}})
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
        with self.assertRaises(ValidationError): state(results={'t9.sql': {'status': 'OK'}})
        with self.assertRaises(ValidationError): state(results={'t1.sql': {'status': 'FAILED'}})


if __name__ == '__main__':
    unittest.main()
