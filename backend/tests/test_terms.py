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

    def actions(self, changes):
        return {c['code']: c['action'] for c in changes}

    def agree_notification(self, csrf, version='TERMS_V1'):
        r = self.client.post('/api/v1/users/me/consents', headers={'X-CSRF-Token': csrf}, json={
            'terms_type': 'NOTIFICATION', 'terms_version': version, 'action': 'AGREE', 'enable_notifications': True})
        self.assertEqual(r.status_code, 200, r.text)

    def option(self, code, kind, order, body='본문'):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['service_option'].insert(), {'option_code': code, 'option_type': kind, 'label_ko': code,
                                                                    'enabled': True, 'sort_order': order, 'value_json': {'body': body}})

    def test_register_is_idempotent_and_switches_unconsented_notification_terms(self):
        """동의자가 없는 알림 약관은 새 버전으로 바꿔도 알림이 끊기는 회원이 없다."""
        self.assertEqual(len(register(self.engine, write=False)), 4)
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_V1')
        self.assertEqual(len(register(self.engine)), 4)
        self.assertEqual(register(self.engine), [])
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_NOTIFICATION_V1')

    def test_register_keeps_notification_terms_that_members_already_agreed_to(self):
        """기존 알림 동의자가 있으면 알림 약관을 바꾸지 않는다. 바꾸면 동의자가 안내 없이 발송 대상에서 빠진다."""
        csrf = self.signup().json()['csrf_token']
        self.agree_notification(csrf)
        # 테스트 가입 약관 본문은 문서와 달라 정정용 force로 맞춘다. 알림 약관 유지 여부는 force와 무관하다.
        planned = self.actions(register(self.engine, write=False, force=True))
        self.assertEqual(planned['TERMS_NOTIFICATION_V1'], 'KEEP_EXISTING')
        register(self.engine, force=True)
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_V1')
        self.assertTrue(self.client.get('/api/v1/users/me/notification-settings').json()['settings']['consent_current'])
        self.assertEqual(self.actions(register(self.engine)), {'TERMS_NOTIFICATION_V1': 'KEEP_EXISTING'})

    def test_replace_notification_switches_on_purpose_and_asks_consenters_again(self):
        """의도적인 알림 약관 전환(--replace-notification): 기존 알림 약관을 끄고 새 버전을 현재 약관으로 둔다.
        계획에 기존 동의자 수를 보여 주고, 적용 뒤 기존 동의자는 재동의 안내를 받는다. 다시 실행해도 바뀌는 것이 없다."""
        csrf = self.signup().json()['csrf_token']
        self.agree_notification(csrf)
        planned = register(self.engine, write=False, force=True, replace_notification=True)
        self.assertEqual(self.actions(planned)['TERMS_V1'], 'DISABLE')
        self.assertEqual(self.actions(planned)['TERMS_NOTIFICATION_V1'], 'INSERT')
        self.assertEqual(next(c for c in planned if c['code'] == 'TERMS_V1')['consents'], 1)
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_V1')  # 계획만으로는 바뀌지 않음
        register(self.engine, force=True, replace_notification=True)
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_NOTIFICATION_V1')
        settings = self.client.get('/api/v1/users/me/notification-settings').json()['settings']
        self.assertEqual((settings['consent_current'], settings['consent_required']), (False, True))
        self.assertEqual(register(self.engine, force=True), [])
        with self.engine.connect() as c:  # 기존 약관 행과 동의 기록은 지우지 않는다(이력 보존)
            table = self.meta.tables['service_option']
            self.assertFalse(c.scalar(select(table.c.enabled).where(table.c.option_code == 'TERMS_V1')))

    def test_replace_counts_only_consenters_of_the_current_notification_terms(self):
        """이미 현재 약관이 아닌 옛 알림 약관의 동의자는 재동의 안내를 받고 있으므로, 전환 계획의 영향 인원에 넣지 않는다."""
        csrf = self.signup().json()['csrf_token']
        self.agree_notification(csrf)  # TERMS_V1 동의
        register(self.engine, force=True, replace_notification=True)  # 현재 약관 = TERMS_NOTIFICATION_V1
        self.option('TERMS_V1_COPY', 'NOTIFICATION_TERMS', 1)  # 현재보다 낮은 옛 약관(사용 중)
        planned = {c['code']: c for c in register(self.engine, write=False, force=True, replace_notification=True)}
        self.assertEqual((planned['TERMS_V1_COPY']['action'], planned['TERMS_V1_COPY']['consents']), ('DISABLE', 0))

    def test_notification_tie_suggests_replace_and_replace_resolves_it(self):
        """동의자 없는 기존 알림 약관과 정렬값이 같으면 등록을 거부하되 전환 방법을 알려 준다. 전환 옵션으로는 등록된다."""
        self.option('TERMS_OLD_NOTIFICATION', 'NOTIFICATION_TERMS', 10)
        with self.assertRaisesRegex(ValueError, '--replace-notification'):
            register(self.engine, write=False)
        changes = self.actions(register(self.engine, replace_notification=True))
        self.assertEqual(changes['TERMS_OLD_NOTIFICATION'], 'DISABLE')
        self.assertEqual(changes['TERMS_NOTIFICATION_V1'], 'INSERT')
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_NOTIFICATION_V1')

    def test_replace_option_does_not_touch_other_terms_types(self):
        """전환 옵션은 알림 약관에만 쓴다. 가입 약관의 정렬값 충돌은 그대로 거부한다."""
        self.option('TERMS_SERVICE_V0', 'SERVICE_TERMS', 10)
        with self.assertRaisesRegex(ValueError, '정렬값'):
            register(self.engine, write=False, replace_notification=True)

    def test_register_refuses_to_rewrite_terms_members_agreed_to(self):
        """가입자가 동의한 약관 본문을 같은 버전으로 덮어쓰지 않는다. 바꾸려면 새 버전 코드를 쓰거나 --force가 필요하다."""
        self.assertEqual(self.signup().status_code, 201)  # TERMS_SERVICE_V1·TERMS_PRIVACY_V1 동의(테스트 본문)
        with self.assertRaisesRegex(ValueError, '이미 동의받은 약관'):
            register(self.engine, write=False)
        with self.engine.connect() as c:
            table = self.meta.tables['service_option']
            body = c.scalar(select(table.c.value_json).where(table.c.option_code == 'TERMS_SERVICE_V1'))['body']
        self.assertEqual(body, '테스트용 이용약관')
        self.assertEqual(self.actions(register(self.engine, force=True))['TERMS_SERVICE_V1'], 'UPDATE')

    def test_register_rejects_versions_that_only_a_code_string_would_order(self):
        """같은 종류에 정렬값이 같은 다른 버전이 있으면 문자열 비교(V10 < V2)로 현재 버전이 정해지므로 등록을 거부한다."""
        self.option('TERMS_SERVICE_V0', 'SERVICE_TERMS', 10)
        with self.assertRaisesRegex(ValueError, '정렬값'):
            register(self.engine, write=False)

    def test_current_terms_follow_sort_order_not_code_string(self):
        self.option('TERMS_X_V10', 'NOTIFICATION_TERMS', 10)
        self.option('TERMS_X_V2', 'NOTIFICATION_TERMS', 2)
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'], 'TERMS_X_V10')

    def test_admin_terms_view_shows_the_same_current_version_as_members(self):
        self.option('TERMS_X_V10', 'NOTIFICATION_TERMS', 10)
        self.option('TERMS_X_V2', 'NOTIFICATION_TERMS', 2)
        self.assertEqual(self.signup().status_code, 201)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['users'].update().values(role='ADMIN'))
        r = self.client.get('/api/v1/admin/notification-terms')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['terms']['version'], 'TERMS_X_V10')

    def test_signup_and_me_return_the_same_user_shape_with_latest_consent_first(self):
        signup = self.signup().json()['user']
        self.assertEqual({c['terms_type'] for c in signup['terms_consents']}, {'SERVICE', 'PRIVACY'})
        table = self.meta.tables['user_consent']
        with self.engine.begin() as c:
            first = c.scalar(select(table.c.acted_at).limit(1))
            c.execute(table.insert(), {'user_id': int(signup['user_id']), 'terms_version': 'TERMS_SERVICE_V1', 'action': 'AGREE',
                                       'acted_at': first.replace(year=first.year + 1)})
        me = self.client.get('/api/v1/users/me').json()['user']
        self.assertEqual(set(me) - {'terms_consents'}, set(signup) - {'terms_consents'})
        service = [c for c in me['terms_consents'] if c['terms_type'] == 'SERVICE']
        self.assertEqual(len(service), 2)
        self.assertGreater(service[0]['acted_at'], service[1]['acted_at'])

    def test_stale_notification_consent_is_reported_to_the_member(self):
        """알림을 켠 회원의 동의가 현재 약관이 아니면 설정 응답이 재동의가 필요하다고 알려 준다."""
        csrf = self.signup().json()['csrf_token']
        self.agree_notification(csrf)
        self.assertFalse(self.client.get('/api/v1/users/me/notification-settings').json()['settings']['consent_required'])
        self.option('TERMS_X_V2', 'NOTIFICATION_TERMS', 20)
        settings = self.client.get('/api/v1/users/me/notification-settings').json()['settings']
        self.assertTrue(settings['notifications_enabled'])
        self.assertFalse(settings['consent_current'])
        self.assertTrue(settings['consent_required'])

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
