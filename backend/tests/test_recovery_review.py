"""#63 리뷰 회귀: 원자적 복구 제한, 전체 SMTP 상한, 정확한 집합 기반 이력 조회."""
import socket
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock
from types import SimpleNamespace

from redis.exceptions import ConnectionError
from sqlalchemy import select, update, event, inspect
from sqlalchemy.dialects import mysql
from backend.app.api.common import ApiError, now
from backend.app.api.recovery import limit_request, send_temporary_password
from backend.app.api.security import hash_token, password_matches
from backend.app.api.redis_store import RedisState
from backend.app.api.keyword_admin import latest_reviews
from backend.app.api.database import Store
from backend.app.api.runtime_schema import RUNTIME_TABLES, EXTENSIONS
from backend.scripts.migrate_integration import plan
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


class KeywordReviewQueryTest(unittest.TestCase):
    setUp = fixture.RecoveryKeywordAdminTest.setUp
    seed_admin = fixture.RecoveryKeywordAdminTest.seed_admin

    def test_latest_history_filter_pagination_and_count_without_correlated_scans(self):
        self.seed_admin()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['keyword_topic'].insert(), [
                {'keyword_text': f'query-{i:03}', 'status': 'PENDING'} for i in range(30)])
            c.execute(self.meta.tables['api_audit_event'].insert(), [
                {'audit_id': f'query-{i:03}-{n:02}', 'actor_id': self.uid, 'action': 'KEYWORD_HOLD',
                 'target_type': 'KEYWORD', 'target_id': f'query-{i:03}', 'reason': 'query test',
                 'before_json': {}, 'after_json': {'review_status': 'HELD' if n == 9 else 'UNREVIEWED'},
                 'created_at': now()} for i in range(30) for n in range(10)])
            self.app.state.database.table('api_audit_event', c)
            self.app.state.database.table('keyword_topic', c)
            self.app.state.database.table('keyword_category', c)
            self.app.state.database.table('article_tag', c)
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            response = self.client.get('/api/v1/admin/keyword-topics?review_status=HELD&q=query-&sort=KEYWORD_ASC&page=2&page_size=7')
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['page']['total'], 30)
        self.assertEqual([row['keyword_text'] for row in response.json()['items']], [f'query-{i:03}' for i in range(7, 14)])
        queries = [sql for sql in statements if 'ranked_reviews' in sql]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(sql.lower().count('row_number()') == 1 for sql in queries))
        self.assertNotIn('article_tag', next(sql for sql in queries if sql.startswith('SELECT count(')))
        with self.engine.connect() as c:
            store = Store(self.app.state.database, c)
            with patch.object(c.dialect, 'name', 'mysql'):
                sql = str(latest_reviews(store).select().compile(dialect=mysql.dialect()))
            self.assertNotIn('CAST(', sql)
            self.assertIn('utf8mb4_bin', sql)
        indexes = inspect(self.engine).get_indexes('api_audit_event')
        self.assertEqual(next(i['column_names'] for i in indexes if i['name'] == 'ix_api_audit_target_time'),
                         ['target_type', 'target_id', 'created_at', 'audit_id'])

    def test_index_migration_is_repeatable_and_preserves_existing_rows(self):
        with self.engine.begin() as c:
            c.exec_driver_sql('DROP INDEX ix_api_audit_target_time')
            statements = plan(c)
            self.assertEqual(len(statements), 1)
            self.assertIn('CREATE INDEX ix_api_audit_target_time', statements[0])
            c.exec_driver_sql(statements[0])
            self.assertEqual(plan(c), [])

    def test_mysql_collation_plan_uses_effective_value_and_is_idempotent(self):
        dialect = mysql.dialect()
        tables = {table.name: table for table in RUNTIME_TABLES}
        schema = Mock()
        schema.get_table_names.return_value = list(EXTENSIONS) + list(tables)
        def columns(name):
            source = EXTENSIONS[name] if name in EXTENSIONS else tables[name].c
            return [{'name': column.name, 'type': mysql.VARCHAR(120) if name == 'api_audit_event' and column.name == 'target_id'
                     else column.type.dialect_impl(dialect), 'nullable': column.nullable} for column in source]
        schema.get_columns.side_effect = columns
        schema.get_pk_constraint.side_effect = lambda name: {'constrained_columns': list(tables[name].primary_key.columns.keys())}
        schema.get_indexes.side_effect = lambda name: [{'name': index.name, 'column_names': list(index.columns.keys())} for index in tables[name].indexes]
        schema.get_foreign_keys.side_effect = lambda name: [
            {'constrained_columns': list(fk.columns.keys()), 'referred_table': 'users', 'referred_columns': ['user_id'],
             'options': {'ondelete': 'CASCADE'}} for fk in tables[name].foreign_key_constraints]
        connection = SimpleNamespace(dialect=dialect, scalar=Mock(return_value='utf8mb4_0900_ai_ci'))
        with patch('backend.scripts.migrate_integration.inspect', return_value=schema):
            statements = plan(connection)
            self.assertEqual(len(statements), 1)
            self.assertIn('COLLATE utf8mb4_bin', statements[0])
            # 열 type에 collation이 생략되어도 실제 테이블 기본값이 binary면 ALTER를 반복하지 않는다.
            connection.scalar.return_value = 'utf8mb4_bin'
            self.assertEqual(plan(connection), [])


if __name__ == '__main__':
    unittest.main()
