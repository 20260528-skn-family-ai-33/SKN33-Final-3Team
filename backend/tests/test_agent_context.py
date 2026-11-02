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
    def setUp(self):
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.redis = RedisState({'API_REDIS_PREFIX': 'test:agent'}, self.client)
        self.context = ChatContext(self.redis)

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
