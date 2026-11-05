"""#63 리뷰 회귀: 원자적 복구 제한과 전체 SMTP 상한."""
import socket
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from redis.exceptions import ConnectionError
from sqlalchemy import select, update
from backend.app.api.common import ApiError
from backend.app.api.recovery import limit_request, send_temporary_password
from backend.app.api.security import hash_token, password_matches
from backend.app.api.redis_store import RedisState
from backend.tests import test_recovery_keyword_admin as fixture


class RecoveryReviewTest(unittest.TestCase):
    setUp = fixture.RecoveryKeywordAdminTest.setUp
    call = fixture.RecoveryKeywordAdminTest.call
    reset = fixture.RecoveryKeywordAdminTest.reset
    expire_recovery_cooldown = fixture.RecoveryKeywordAdminTest.expire_recovery_cooldown

    def recovery_keys(self):
        redis = self.app.state.database.redis
        prefix = redis.prefix + 'recovery:{' + hash_token('alice@example.org') + '}'
        return prefix+':cooldown', prefix+':daily'

    def test_cooldown_is_checked_before_database_and_normalizes_email(self):
        with patch('backend.app.api.recovery.send_temporary_password') as send:
            self.assertEqual(self.reset().status_code, 200)
            with patch.object(self.engine, 'begin', side_effect=AssertionError('must not acquire DB')):
                result = self.reset(email='ALICE@example.org', name='다른이름', action='REISSUE')
            self.assertEqual(result.status_code, 429, result.text)
            self.assertEqual(result.json()['code'], 'RECOVERY_RATE_LIMITED')
            self.assertTrue(590 <= int(result.headers['Retry-After']) <= 600)
            self.assertEqual(send.call_count, 1)
        self.assertTrue(all('alice' not in key for key in self.recovery_keys()))

    def test_parallel_instances_share_one_atomic_reservation(self):
        database = self.app.state.database
        shared = RedisState(self.app.state.settings, database.redis.client)
        def attempt(i):
            try:
                (database.redis if i % 2 else shared).limit_recovery(hash_token('alice@example.org'))
                return 200
            except ApiError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(attempt, range(20)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(429), 19)
        self.assertEqual(database.redis.client.zcard(self.recovery_keys()[1]), 1)

    def test_daily_limit_uses_rolling_window_and_rejections_do_not_extend_it(self):
        db, settings = self.app.state.database, self.app.state.settings
        body = {'action': 'ISSUE', 'email': 'alice@example.org', 'name': '홍길동'}
        cooldown, daily = self.recovery_keys()
        for _ in range(5):
            db.redis.client.delete(cooldown)
            limit_request(db, body, settings)
        db.redis.client.delete(cooldown)
        ttl = db.redis.client.pttl(daily)
        with self.assertRaises(ApiError) as denied:
            limit_request(db, body, settings)
        self.assertEqual(denied.exception.status, 429)
        self.assertGreater(denied.exception.retry_after, 86000)
        self.assertEqual(db.redis.client.zcard(daily), 5)
        self.assertLessEqual(db.redis.client.pttl(daily), ttl)
        seconds, micros = db.redis.client.time()
        oldest = db.redis.client.zrange(daily, 0, 0)[0]
        db.redis.client.zadd(daily, {oldest: seconds*1000 + micros//1000 - 86400001})
        limit_request(db, body, settings)
        self.assertEqual(db.redis.client.zcard(daily), 5)
        self.assertGreater(db.redis.client.ttl(cooldown), 0)

    def test_failure_keeps_quota_and_redis_outage_fails_closed(self):
        with patch('backend.app.api.recovery.send_temporary_password', side_effect=ApiError(503, 'MAIL_DELIVERY_FAILED', '실패')) as send:
            self.assertEqual(self.reset().status_code, 503)
            self.assertEqual(self.reset().status_code, 429)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code, 200)
        with patch.object(self.app.state.database.redis.client, 'eval', side_effect=ConnectionError('secret endpoint')):
            with patch.object(self.engine, 'begin', side_effect=AssertionError('must not acquire DB')):
                result = self.reset()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['code'], 'REDIS_UNAVAILABLE')
        self.assertNotIn('secret endpoint', result.text)

    def test_unavailable_accounts_have_identical_status_code_and_message(self):
        responses = []
        users = self.meta.tables['users']
        with patch('backend.app.api.recovery.send_temporary_password') as send:
            responses.append(self.reset(email='missing@example.org'))
            responses.append(self.reset(name='다른이름'))
            for changes in ({'account_status': 'RESTRICTED'}, {'account_status': 'WITHDRAWN'},
                            {'account_status': 'ACTIVE', 'password_hash': ''}):
                self.expire_recovery_cooldown()
                with self.engine.begin() as c:
                    c.execute(update(users).where(users.c.user_id == self.uid).values(**changes))
                responses.append(self.reset())
            send.assert_not_called()
        for result in responses:
            self.assertEqual(result.status_code, 403, result.text)
            self.assertEqual(result.json()['code'], 'RECOVERY_NOT_AVAILABLE')
            self.assertEqual(result.json()['message'], responses[0].json()['message'])
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(users.c.auth_version).where(users.c.user_id == self.uid)), 0)

    def test_total_smtp_deadline_kills_blocked_sender_and_rolls_back(self):
        accepted, disconnected = threading.Event(), threading.Event()
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(8)
        def silent_server():
            try:
                connection, _ = listener.accept()
                with connection:
                    accepted.set()
                    connection.settimeout(8)
                    try:
                        closed = connection.recv(1) == b''
                    except ConnectionResetError:
                        closed = True  # Windows는 강제 종료 시 EOF 대신 RST를 반환할 수 있다.
                    if closed:
                        disconnected.set()
            except (OSError, TimeoutError):
                pass
        server = threading.Thread(target=silent_server, daemon=True)
        server.start()
        self.app.state.settings.values.update(API_SMTP_HOST='127.0.0.1', API_SMTP_PORT=listener.getsockname()[1],
                                             API_SMTP_FROM='service@example.org', API_SMTP_TIMEOUT_SECONDS=10,
                                             API_SMTP_TOTAL_TIMEOUT_SECONDS=3)
        started = time.monotonic()
        try:
            result = self.reset()
            self.assertEqual(result.status_code, 503, result.text)
            self.assertEqual(result.json()['code'], 'MAIL_DELIVERY_FAILED')
            self.assertLess(time.monotonic()-started, 7)
            self.assertTrue(accepted.wait(1), 'child never connected to local fixture')
            self.assertTrue(disconnected.wait(2), 'timed-out sender was left running')
        finally:
            listener.close()
            server.join(timeout=9)
        users = self.meta.tables['users']
        with self.engine.connect() as c:
            hashed = c.scalar(select(users.c.password_hash).where(users.c.user_id == self.uid))
        self.assertTrue(password_matches('Sample!123', hashed))
        self.assertEqual(self.client.get('/api/v1/users/me').status_code, 200)

    def test_sender_passes_secrets_only_via_pipe_and_reaps_child_on_timeout(self):
        self.app.state.settings.values.update(API_SMTP_HOST='smtp.example.org', API_SMTP_FROM='service@example.org',
                                             API_SMTP_PASSWORD='not-a-real-app-password')
        with patch('backend.app.api.recovery.subprocess.Popen') as popen:
            child = popen.return_value
            child.communicate.side_effect = [subprocess.TimeoutExpired('smtp_delivery.py', 15), (None, None)]
            child.poll.return_value = None
            with self.assertRaises(ApiError) as failed:
                send_temporary_password(self.app.state.settings, 'alice@example.org', 'SyntheticSecret123')
            self.assertEqual(failed.exception.code, 'MAIL_DELIVERY_FAILED')
            child.kill.assert_called_once()
            self.assertEqual(child.communicate.call_count, 2)
            args = str(popen.call_args.args)
            self.assertNotIn('not-a-real-app-password', args)
            self.assertNotIn('SyntheticSecret123', args)
            self.assertLessEqual(child.communicate.call_args_list[0].kwargs['timeout'], 15)


if __name__ == '__main__':
    unittest.main()
