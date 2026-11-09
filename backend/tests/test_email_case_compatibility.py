"""Exercise pre-migration case-insensitive UNIQUE lookup without external MySQL."""
import unittest
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from backend.tests import test_backend_api as base
from backend.tests.api_support import test_engine
from backend.app.api import accounts, admin
from backend.app.api.common import ApiError
from backend.app.api.database import Store


class EmailCaseCompatibilityTest(unittest.TestCase):
    def setUp(self):
        with patch.object(base, 'test_engine', side_effect=lambda: test_engine(email_collation='NOCASE')):
            base.BackendApiTest.setUp(self)

    tearDown = base.BackendApiTest.tearDown
    signup = base.BackendApiTest.signup

    def test_native_unique_lookup_rejects_different_case_before_migration(self):
        uid = self.signup()
        with self.engine.connect() as connection:
            store = Store(self.app.state.database, connection)
            with patch.object(store, 'one', wraps=store.one) as lookup:
                self.assertIsNone(accounts.get_user(store, email='Alice@example.org', lock=True))
            query = lookup.call_args.args[0]
            mysql_sql = str(query.compile(dialect=mysql.dialect()))
            self.assertIn('users.email = ', mysql_sql)
            self.assertIn('FOR UPDATE', mysql_sql)
            self.assertNotIn('COLLATE', mysql_sql)
            plan = connection.execute(text('EXPLAIN QUERY PLAN ' + str(query.compile(
                dialect=connection.dialect, compile_kwargs={'literal_binds': True})))).all()
            self.assertTrue(any('SEARCH users USING INDEX' in row[3] for row in plan), plan)
            self.assertEqual(str(accounts.get_user(store, email='alice@example.org')['user_id']), uid)
        for email, status in [('Alice@example.org', 401), ('alice@example.org', 200)]:
            response = self.client.post('/api/v1/auth/login', json={'email': email, 'password': 'Sample!123'})
            self.assertEqual(response.status_code, status, response.text)
        response = self.client.post('/api/v1/auth/account-recovery/candidates',
                                    json={'name': '홍길동', 'email': 'Alice@example.org'})
        self.assertFalse(response.json()['matches'])

    def test_case_conflict_and_concurrent_signup_are_email_errors(self):
        self.signup()
        for email in ('Alice@example.org', 'alice@example.org'):
            # Bypass the precheck to reproduce a concurrent insert too.
            with patch.object(accounts, 'get_user', return_value=None):
                response = self.client.post('/api/v1/auth/signup', json={
                    'name': '테스트', 'email': email, 'password': 'Sample!123', 'password_confirm': 'Sample!123'})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()['code'], 'EMAIL_ALREADY_EXISTS')
            self.assertFalse(response.json()['retryable'])
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text('SELECT count(*) FROM users')), 1)
            self.assertEqual(connection.scalar(text('SELECT count(*) FROM notification_setting')), 1)

    def test_mysql_duplicate_only_is_translated(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        store = MagicMock()
        store.c.dialect.name = 'mysql'
        data = SimpleNamespace(email='Alice@example.org', password='Sample!123',
                               password_confirm='Sample!123', name='테스트')
        for code in (1062, 1048):
            error = IntegrityError('INSERT users', {}, Exception(code, 'synthetic database error'))
            store.insert.side_effect = error
            with patch.object(accounts, 'get_user', return_value=None), patch.object(accounts, 'password_hash'):
                with self.assertRaises(ApiError if code == 1062 else IntegrityError) as raised:
                    accounts.signup(store, data, MagicMock(), self.settings)
            if code == 1062:
                self.assertEqual(raised.exception.code, 'EMAIL_ALREADY_EXISTS')
            else:
                self.assertIs(raised.exception, error)

    def test_admin_email_search_remains_case_insensitive(self):
        uid = int(self.signup('Alice@Example.org'))
        with self.engine.begin() as connection:
            # SQLite LIKE otherwise ignores ASCII case and could hide regressions.
            connection.exec_driver_sql('PRAGMA case_sensitive_like = ON')
            store = Store(self.app.state.database, connection)
            admin.audit(store, uid, 'USER_STATUS', 'USER', uid, {}, {}, '검수')
            for query in ('alice@example.org', 'ALICE@EXAMPLE.ORG'):
                self.assertEqual([row['user_id'] for row in admin.users(store, q=query, by='id')['items']], [str(uid)])
                self.assertEqual([row['actor_id'] for row in admin.audit_list(store, q=query)['items']], [str(uid)])
