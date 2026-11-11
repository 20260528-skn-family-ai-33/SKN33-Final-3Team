"""Redis admission and cache contracts; fakeredis only, no external services."""
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import fakeredis
from redis.exceptions import ConnectionError

from backend.app.agents.context import ChatContext, restore_conditions
from backend.app.agents.state import SessionContext, TurnSummary, CONTEXT_TTL_SECONDS, ACTIVE_TTL_SECONDS
from backend.app.api.redis_store import RedisState
from backend.app.api.common import ApiError


class ChatContextTest(unittest.TestCase):
    def test_followup_keeps_paired_trade_flows_after_cache_round_trip(self):
        from backend.app.agents.context import completed_context
        from backend.app.agents.planner import build_plan, planner_input, postprocess
        from backend.app.agents.state import Conditions, Draft, PlannerOutput
        from backend.tests.test_agent_state import state

        current = state()
        question = '2025년 베트남 화장품 수출액과 수입액을 보여줘'
        current.input.question = question
        output = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question,
            'slots': {'trade_flow': 'EXPORT'}}])
        current.plan, _ = build_plan(postprocess(output, question))
        self.assertEqual([task.slots.trade_flow for task in current.plan.tasks], ['EXPORT', 'IMPORT'])
        for task in current.plan.tasks:
            task.conditions = Conditions(country_codes=['VN'], trade_flow=task.slots.trade_flow,
                period={'start': '2025-01-01', 'end': '2025-12-01'})
            task.answer_status = 'ANSWERED'
        current.draft = Draft(conclusion=[
            {'task_id': task.task_id, 'sentences': ['조회했습니다.']} for task in current.plan.tasks
        ], turn_summary='')
        completed = completed_context(current)
        self.redis.acquire_chat('3', '11')
        self.context.publish(completed, '11')
        cached = self.context.load('3', {}, '11')
        durable = {'conditions': completed.conditions.model_dump(mode='json'), 'both_trade_flows': True}
        restored = self.context.load('3', durable, '12')
        for previous in (cached, restored):
            self.assertTrue(previous.both_trade_flows)
            self.assertEqual(previous.conditions.country_codes, ['VN'])
            current.input.context = previous
            self.assertTrue(planner_input(current)['previous_both_trade_flows'])
            question = '2024년도 보여줘'
            followup = PlannerOutput(tasks=[{'intent': 'STATISTICS', 'question': question,
                'slots': {'period': {'start': '2024-01-01', 'end': '2024-12-01'}}}])
            plan, _ = build_plan(postprocess(followup, question, previous_both_trade_flows=previous.both_trade_flows))
            self.assertEqual([task.slots.trade_flow for task in plan.tasks], ['EXPORT', 'IMPORT'])
            self.assertTrue(all(task.slots.period.start.year == 2024 for task in plan.tasks))
            plan, _ = build_plan(postprocess(followup, '2024년 수입만 보여줘', previous_both_trade_flows=True))
            self.assertEqual([task.slots.trade_flow for task in plan.tasks], ['IMPORT'])
        current.plan.tasks[1].answer_status = 'NO_DATA'
        self.assertFalse(completed_context(current).both_trade_flows)

    def test_rejected_summary_keeps_question_and_conditions_for_followup(self):
        from backend.app.agents.context import completed_context
        from backend.app.agents.planner import planner_input
        from backend.app.agents.state import Conditions, Draft
        from backend.tests.test_agent_state import state
        current=state()
        task=current.plan.tasks[0]
        task.conditions=Conditions(country_codes=['VN'])
        task.answer_status='ANSWERED'
        current.draft=Draft(conclusion=[{'task_id':task.task_id,'sentences':['조회했습니다.']}],turn_summary='')
        context=completed_context(current)
        self.redis.acquire_chat('3','11')
        self.context.publish(context,'11')
        loaded=self.context.load('3',{},'11')
        self.assertEqual(len(loaded.recent),1)
        self.assertEqual(loaded.recent[0].summary,'')
        current.input.context=loaded
        current.input.question='수입은?'
        payload=planner_input(current)
        self.assertEqual(payload['recent'][0]['question'],context.recent[0].question)
        self.assertEqual(payload['recent'][0]['conditions']['country_codes'],['VN'])

    def test_term_and_hs_answers_stay_in_recent_turns(self):
        # 조건 없는 용어·HS 조회 답변도 "아까 뭐 물어봤지?"에 답할 수 있게 남긴다. 이전 검색 조건은 그대로 둔다
        from backend.app.agents.context import completed_context
        from backend.app.agents.state import Conditions, Draft
        from backend.tests.test_agent_state import state
        current=state()
        previous=Conditions(country_codes=['VN'])
        current.input.context=current.input.context.model_copy(update={'conditions':previous})
        task=current.plan.tasks[0]
        task.intent='CONVERSATION'; task.conditions=None; task.answer_status='ANSWERED'
        current.draft=Draft(conclusion=[{'task_id':task.task_id,'sentences':['HS 코드는 품목 분류 번호입니다.']}],turn_summary='HS 코드 설명')
        context=completed_context(current)
        self.assertEqual([(t.intent,t.summary) for t in context.recent[-1:]],[('CONVERSATION','HS 코드 설명')])
        self.assertEqual(context.conditions,previous)

    def setUp(self):
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.redis = RedisState({'API_REDIS_PREFIX': 'test:agent'}, self.client)
        self.context = ChatContext(self.redis)

    def test_late_publish_after_clear_is_ignored_until_next_answer(self):
        from backend.app.agents.state import Conditions
        old = SessionContext(session_id='3', conditions=Conditions(country_codes=['CN']))
        self.redis.acquire_chat('3', '11')
        self.context.publish(old, '11')   # 초기화(API-39) 뒤에 늦게 도착한 답변 11의 컨텍스트
        cleared = {'conditions': {}, 'interest_action': 'CLEARED', 'cleared_after_job_id': '11'}
        self.assertIsNone(self.context.load('3', cleared, '11').conditions)
        self.assertEqual(self.context.load('3', {}, '11').conditions.country_codes, ['CN'])

    def test_prefix_and_exclusive_acquisition(self):
        self.assertTrue(self.redis.acquire_chat('s', 'old'))
        self.assertFalse(self.redis.acquire_chat('s', 'new'))
        self.assertEqual(self.client.get('test:agent:chat:session:s:active'), 'old')
        self.assertAlmostEqual(self.client.ttl('test:agent:chat:session:s:active'), ACTIVE_TTL_SECONDS, delta=1)

    def test_old_owner_cannot_delete_renew_or_publish(self):
        self.redis.acquire_chat('s', 'new')
        key = 'test:agent:chat:session:s:active'
        self.client.expire(key, 10)
        self.assertFalse(self.redis.release_chat('s', 'old'))
        self.assertFalse(self.redis.renew_chat('s', 'old'))
        self.assertFalse(self.context.publish(SessionContext(session_id='s'), 'old'))
        self.assertLessEqual(self.client.ttl(key), 10)
        self.assertTrue(self.redis.renew_chat('s', 'new'))
        self.assertTrue(self.redis.release_chat('s', 'new'))

    def test_mysql_active_job_blocks_even_with_lost_redis(self):
        for status in ('QUEUED', 'RUNNING', 'NEEDS_INPUT', 'WAITING_REPORT'):
            with self.subTest(status=status), self.assertRaises(ApiError) as exc:
                self.context.acquire_locked('s', 'new', {'status': status}, Mock())
            self.assertEqual(exc.exception.code, 'JOB_IN_PROGRESS')
        self.assertIsNone(self.redis.chat_active('s'))

    def test_orphan_cleanup_only_after_mysql_lookup(self):
        for record in (None, {'status': 'SUCCEEDED'}, {'status': 'CANCELLED'}, {'status': 'EXPIRED'}):
            self.client.flushall()
            self.redis.acquire_chat('s', 'orphan')
            lookup = Mock(return_value=record)
            self.context.acquire_locked('s', 'new', None, lookup)
            lookup.assert_called_once_with('orphan')
            self.assertEqual(self.redis.chat_active('s'), 'new')

    def test_live_owner_and_acquisition_race_are_not_overwritten(self):
        self.redis.acquire_chat('s', 'old')
        with self.assertRaises(ApiError):
            self.context.acquire_locked('s', 'new', None, lambda _: {'status': 'RUNNING'})
        self.assertEqual(self.redis.chat_active('s'), 'old')
        # A changed owner between orphan lookup and compare-delete survives.
        def changed(_):
            self.client.set('test:agent:chat:session:s:active', 'successor')
            return None
        with self.assertRaises(ApiError):
            self.context.acquire_locked('s', 'new', None, changed)
        self.assertEqual(self.redis.chat_active('s'), 'successor')

    def test_legacy_restore_trims_scope_and_normalizes_month_end(self):
        raw = {'conditions': {'country_codes': [f'C{i}' for i in range(25)],
            'hs_items': [{'hs_code': f'3304{i:02}', 'hs_ver': 'HS2022'} for i in range(6)],
            'period': {'start': '2025-01-01', 'end': '2025-03-31'}, 'compare': 'NONE', 'metric': 'AMOUNT',
            'compare_period': {'start': '2024-01-01', 'end': '2024-03-31'}}, 'interest_action': 'DECLINED'}
        restored = self.context.load('s', raw, 'j')
        self.assertEqual((len(restored.conditions.country_codes), len(restored.conditions.hs_items)), (20, 5))
        self.assertEqual(restored.conditions.period.end.day, 1)
        self.assertIsNone(restored.conditions.compare_period)
        self.assertEqual(restored.interest_action, 'DECLINED')
        self.assertEqual(restored.recent, [])
        self.assertIsNone(restore_conditions({}))

    def test_stale_corrupt_or_wrong_session_context_falls_back(self):
        key = 'test:agent:chat:session:s:context'
        values = ['broken', SessionContext(session_id='s', last_job_id='old').model_dump_json(),
                  SessionContext(session_id='other', last_job_id='new').model_dump_json()]
        for raw in values:
            self.client.set(key, raw)
            actual = self.context.load('s', {'conditions': {'country_codes': ['US']}}, 'new')
            self.assertEqual(actual.conditions.country_codes, ['US'])
            self.assertEqual(actual.recent, [])
            self.assertEqual(self.client.get(key), raw)  # no racing stale deletion

    def test_published_context_keeps_five_turns_and_refreshes_on_open(self):
        self.redis.acquire_chat('s', 'j')
        context = SessionContext(session_id='s')
        for i in range(7):
            context = context.remember(TurnSummary(question=str(i), intent='STATISTICS', answered_at=datetime(2026, 11, 2)))
        self.assertTrue(self.context.publish(context, 'j'))
        key = 'test:agent:chat:session:s:context'
        self.client.expire(key, 20)
        self.context.load('s', {}, 'j')
        self.assertLessEqual(self.client.ttl(key), 20)
        actual = self.context.load('s', {}, 'j', opened=True)
        self.assertEqual([t.question for t in actual.recent], ['2', '3', '4', '5', '6'])
        self.assertEqual(actual.last_job_id, 'j')
        self.assertAlmostEqual(self.client.ttl(key), CONTEXT_TTL_SECONDS, delta=1)

    def test_redis_failure_is_retryable_503(self):
        with patch.object(self.client, 'get', side_effect=ConnectionError()):
            with self.assertRaises(ApiError) as exc:
                self.context.load('s', {}, None)
        self.assertEqual((exc.exception.status, exc.exception.code, exc.exception.retryable),
                         (503, 'REDIS_UNAVAILABLE', True))


if __name__ == '__main__':
    unittest.main()
