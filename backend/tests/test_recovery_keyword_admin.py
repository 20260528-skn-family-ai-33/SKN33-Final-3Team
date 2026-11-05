"""외부 DB·Redis·메일 없이 계정 복구 흐름을 검증한다."""
import unittest
import smtplib
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.tests.api_support import test_engine, reference_data, create_test_app
from backend.app.api.config import Settings
from backend.app.api.common import ApiError
from backend.app.api.security import password_matches
from backend.app.api.recovery import send_temporary_password


class RecoveryKeywordAdminTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.meta = test_engine()
        reference_data(self.engine, self.meta)
        self.app = create_test_app(Settings(values={'API_COOKIE_SECURE': 'false'}), self.engine)
        self.client = TestClient(self.app)
        r = self.client.post('/api/v1/auth/signup', json={'name': '홍길동', 'email': 'alice@example.org', 'password': 'Sample!123', 'password_confirm': 'Sample!123'})
        self.assertEqual(r.status_code, 201, r.text)
        self.uid, self.csrf = int(r.json()['user']['user_id']), r.json()['csrf_token']
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)

    def call(self, method, path, body=None):
        return self.client.request(method, '/api/v1/'+path, json=body, headers={'X-CSRF-Token': self.csrf})

    def reset(self, **patches):
        return self.call('POST', 'auth/password-recovery/temporary-password', {'name': '홍길동', 'email': 'alice@example.org', 'action': 'ISSUE', **patches})

    def login(self, password):
        return self.call('POST', 'auth/login', {'email': 'alice@example.org', 'password': password})

    def test_account_confirmation_exact_pair_and_no_candidate_leak(self):
        for name, email, expected in [(' 홍길동 ', 'ALICE@example.org', True), ('다른이름', 'alice@example.org', False), ('홍길동', 'missing@example.org', False)]:
            r = self.call('POST', 'auth/account-recovery/candidates', {'name': name, 'email': email})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {'matches': expected, 'message': '가입된 계정입니다.' if expected else '해당정보로 가입된 계정이 없습니다'})
        self.assertEqual(self.call('POST', 'auth/account-recovery/candidates', {'name': '홍길동'}).status_code, 400)

    def test_temporary_password_persists_reissues_and_changes(self):
        with patch('backend.app.api.recovery.send_temporary_password') as send:
            r = self.reset()
            self.assertEqual(r.status_code, 200, r.text)
            first = send.call_args.args[2]
            self.assertRegex(first, r'^[A-Za-z0-9]{20}$')
            for pattern in ('[A-Z]', '[a-z]', '[0-9]'): self.assertRegex(first, pattern)
            self.assertNotIn(first, r.text)
            users = self.meta.tables['users']
            with self.engine.connect() as c:
                hashed = c.scalar(select(users.c.password_hash).where(users.c.user_id == self.uid))
            self.assertNotEqual(first, hashed)
            self.assertTrue(password_matches(first, hashed))
            self.assertEqual(self.client.get('/api/v1/users/me').status_code, 401)
            self.assertEqual(self.login('Sample!123').status_code, 401)
            self.assertEqual(self.login(first).status_code, 200)
            self.assertEqual(self.reset(action='REISSUE').status_code, 200)
            second = send.call_args.args[2]
            self.assertNotEqual(first, second)
            self.assertEqual(self.login(first).status_code, 401)
            login = self.login(second)
            self.assertEqual(login.status_code, 200, login.text)
            self.csrf = login.json()['csrf_token']
            r = self.call('POST', 'users/me/password', {'email': 'alice@example.org', 'current_password': second, 'new_password': 'Changed!456', 'new_password_confirm': 'Changed!456'})
            self.assertEqual(r.status_code, 204, r.text)
            self.assertEqual(self.login(second).status_code, 401)
            self.assertEqual(self.login('Changed!456').status_code, 200)

    def test_mail_failure_rolls_back_and_mismatch_sends_nothing(self):
        with patch('backend.app.api.recovery.send_temporary_password', side_effect=ApiError(503, 'MAIL_DELIVERY_FAILED', '실패')) as send:
            self.assertEqual(self.reset(name='다른이름').status_code, 404)
            send.assert_not_called()
            self.assertEqual(self.reset().status_code, 503)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code, 200)
        self.assertEqual(self.login('Sample!123').status_code, 200)
        self.assertEqual(self.reset().json()['code'], 'MAIL_NOT_CONFIGURED')
        self.assertEqual(self.login('Sample!123').status_code, 200)

    def test_recovery_rate_limit(self):
        self.app.state.settings.values['API_AUTH_EMAIL_LIMIT'] = '2'
        with patch('backend.app.api.recovery.send_temporary_password') as send:
            self.assertEqual(self.reset().status_code, 200)
            self.assertEqual(self.reset().status_code, 200)
            self.assertEqual(self.reset().status_code, 429)
            self.assertEqual(send.call_count, 2)

    def test_smtp_tls_content_and_accepted_mail_survives_quit_error(self):
        settings = Settings(values={'API_SMTP_HOST': 'smtp.example.org', 'API_SMTP_FROM': 'service@example.org', 'API_SMTP_USERNAME': 'service', 'API_SMTP_PASSWORD': 'synthetic-test-only'})
        with patch('backend.app.api.recovery.smtplib.SMTP') as smtp:
            client = smtp.return_value
            client.send_message.return_value = {}
            client.quit.side_effect = smtplib.SMTPServerDisconnected('after DATA acceptance')
            send_temporary_password(settings, 'alice@example.org', 'Abc123Synthetic')
            client.starttls.assert_called_once()
            client.login.assert_called_once()
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'alice@example.org')
            self.assertIn('Abc123Synthetic', message.get_content())
            client.close.assert_called_once()
            client.send_message.side_effect = smtplib.SMTPRecipientsRefused({'alice@example.org': (550, b'rejected')})
            with self.assertRaises(ApiError) as failed:
                send_temporary_password(settings, 'alice@example.org', 'SecretNotInError')
            self.assertEqual(failed.exception.code, 'MAIL_DELIVERY_FAILED')
            self.assertNotIn('SecretNotInError', failed.exception.message)


if __name__ == '__main__': unittest.main()
