"""가입 약관 동의 저장, 약관 조회, 알림 동의 판단 분리, 약관 문서 등록."""
import unittest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from backend.app.api.config import Settings
from backend.tests.api_support import test_engine, reference_data, create_test_app, SIGNUP_CONSENTS
from backend.scripts.migrate_integration import option_type_step
from backend.scripts.register_terms import parse, register, rows


class TermsTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.meta = test_engine()
        reference_data(self.engine, self.meta)
        self.app = create_test_app(Settings(values={'API_COOKIE_SECURE': 'false'}), self.engine)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)

    def signup(self, **changes):
        body = {'name': '홍길동', 'email': 'alice@example.org', 'password': 'Sample!123',
                'password_confirm': 'Sample!123', **SIGNUP_CONSENTS, **changes}
        return self.client.post('/api/v1/auth/signup', json=body)

    def consents(self):
        table = self.meta.tables['user_consent']
        with self.engine.connect() as connection:
            return sorted(connection.scalars(select(table.c.terms_version)).all())

    def users(self):
        with self.engine.connect() as connection:
            return connection.scalars(select(self.meta.tables['users'].c.email)).all()

    def test_signup_saves_both_required_consents(self):
        response = self.signup()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.consents(), ['TERMS_PRIVACY_V1', 'TERMS_SERVICE_V1'])
        me = self.client.get('/api/v1/users/me').json()['user']
        self.assertEqual({c['terms_type'] for c in me['terms_consents']}, {'SERVICE', 'PRIVACY'})

    def test_signup_without_consent_creates_nothing(self):
        cases = [({'age_confirmed': False}, 422, 'AGE_CONFIRMATION_REQUIRED'),
                 ({'service_terms_version': 'OLD'}, 409, 'TERMS_VERSION_CHANGED'),
                 ({'privacy_terms_version': 'OLD'}, 409, 'TERMS_VERSION_CHANGED')]
        for changes, status, code in cases:
            response = self.signup(**changes)
            self.assertEqual((response.status_code, response.json()['code']), (status, code))
        missing = {'name': '홍길동', 'email': 'alice@example.org', 'password': 'Sample!123', 'password_confirm': 'Sample!123'}
        self.assertEqual(self.client.post('/api/v1/auth/signup', json=missing).status_code, 400)
        self.assertEqual(self.users(), [])
        self.assertEqual(self.consents(), [])

    def test_terms_lookup_is_public_and_rejects_unknown_type(self):
        for kind, version in (('service', 'TERMS_SERVICE_V1'), ('privacy', 'TERMS_PRIVACY_V1'),
                              ('privacy-policy', 'PRIVACY_POLICY_V1'), ('notification', 'TERMS_V1')):
            response = self.client.get('/api/v1/terms/' + kind)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['terms']['version'], version)
        response = self.client.get('/api/v1/terms/marketing')
        self.assertEqual((response.status_code, response.json()['code']), (422, 'UNSUPPORTED_TERMS_TYPE'))

    def test_signup_consents_do_not_count_as_notification_consent(self):
        csrf = self.signup().json()['csrf_token']
        headers = {'X-CSRF-Token': csrf}
        settings = self.client.get('/api/v1/users/me/notification-settings').json()['settings']
        self.assertFalse(settings['consent_current'])
        self.assertIsNone(settings['terms_version'])
        blocked = self.client.patch('/api/v1/users/me/notification-settings', json={'notifications_enabled': True}, headers=headers)
        self.assertEqual(blocked.json()['code'], 'CONSENT_REQUIRED')
        agreed = self.client.post('/api/v1/users/me/consents', headers=headers, json={
            'terms_type': 'NOTIFICATION', 'terms_version': 'TERMS_V1', 'action': 'AGREE', 'enable_notifications': True})
        self.assertEqual(agreed.status_code, 200, agreed.text)
        settings = self.client.get('/api/v1/users/me/notification-settings').json()['settings']
        self.assertTrue(settings['consent_current'])
        self.assertEqual(settings['terms_version'], 'TERMS_V1')

    def test_parse_removes_author_notes_and_keeps_tables(self):
        title, body = parse('# 제목 (필수)\n\n> 초안입니다.\n\n## 항목\n| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n참고: 내부 메모')
        self.assertEqual(title, '제목 (필수)')
        self.assertEqual(body, '## 항목\n| a | b |\n|---|---|\n| 1 | 2 |')

    def test_registered_documents_are_complete_without_future_effective_date(self):
        self.assertEqual({r['option_type'] for r in rows()},
                         {'SERVICE_TERMS', 'PRIVACY_TERMS', 'PRIVACY_POLICY', 'NOTIFICATION_TERMS'})
        for row in rows():
            self.assertTrue(row['value_json']['body'].strip())
            self.assertNotIn('초안', row['value_json']['body'])
            self.assertNotIn('effective_at', row['value_json'])

    def test_register_is_idempotent_and_updates_notification_terms(self):
        self.assertEqual(len(register(self.engine, write=False)), 4)
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_V1')
        self.assertEqual(len(register(self.engine)), 4)
        self.assertEqual(register(self.engine), [])
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_NOTIFICATION_V1')

    def test_migration_step_extends_only_missing_enum_values(self):
        class Inspector:
            def __init__(self, values): self.values = values
            def get_columns(self, table):
                column_type = type('Enum', (), {'enums': self.values})()
                return [{'name': 'option_type', 'type': column_type, 'comment': '종류'}]
        step = option_type_step(Inspector(['SOURCE', 'NOTIFICATION_TERMS']))
        self.assertIn("ENUM('SOURCE','NOTIFICATION_TERMS','SERVICE_TERMS','PRIVACY_TERMS','PRIVACY_POLICY')", step)
        self.assertIn("NOT NULL COMMENT '종류'", step)
        self.assertIsNone(option_type_step(Inspector(['SOURCE', 'SERVICE_TERMS', 'PRIVACY_TERMS', 'PRIVACY_POLICY'])))
        self.assertIsNone(option_type_step(inspect(self.engine)))


if __name__ == '__main__':
    unittest.main()
