"""외부 DB·Redis·메일 없이 계정 복구와 관리자 저장 흐름을 검증한다."""
import unittest
import smtplib
from unittest.mock import patch
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select, update, String, event
from sqlalchemy.exc import SQLAlchemyError
from backend.tests.api_support import test_engine, reference_data, create_test_app
from backend.app.api.config import Settings
from backend.app.api.common import ApiError
from backend.app.api.security import password_matches, hash_token
from backend.app.api.smtp_delivery import deliver
from backend.app.services.news_keywords.category_rules import TAX


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

    def expire_recovery_cooldown(self):
        redis = self.app.state.database.redis
        redis.client.delete(redis.prefix+'recovery:{'+hash_token('alice@example.org')+'}:cooldown')

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
            self.expire_recovery_cooldown()
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
        def fail_commit(connection):
            raise SQLAlchemyError('synthetic commit failure')
        event.listen(self.engine, 'commit', fail_commit)
        try:
            with patch('backend.app.api.recovery.send_temporary_password') as send:
                result = self.reset()
                self.assertEqual(result.status_code, 503, result.text)
                self.assertEqual(result.json()['code'], 'DATABASE_UNAVAILABLE')
                send.assert_not_called()
        finally:
            event.remove(self.engine, 'commit', fail_commit)
        self.expire_recovery_cooldown()
        with patch('backend.app.api.recovery.send_temporary_password', side_effect=ApiError(503, 'MAIL_DELIVERY_FAILED', '실패')) as send:
            self.assertEqual(self.reset(name='다른이름').status_code, 403)
            send.assert_not_called()
            self.expire_recovery_cooldown()
            self.assertEqual(self.reset().status_code, 503)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code, 200)
        self.assertEqual(self.login('Sample!123').status_code, 200)
        self.expire_recovery_cooldown()
        self.assertEqual(self.reset().json()['code'], 'MAIL_NOT_CONFIGURED')
        self.assertEqual(self.login('Sample!123').status_code, 200)

    def test_recovery_rate_limit(self):
        self.app.state.settings.values['API_AUTH_EMAIL_LIMIT'] = '2'
        with patch('backend.app.api.recovery.send_temporary_password') as send:
            self.assertEqual(self.reset().status_code, 200)
            self.expire_recovery_cooldown()
            self.assertEqual(self.reset().status_code, 200)
            self.assertEqual(self.reset().status_code, 429)
            self.assertEqual(send.call_count, 2)

    def test_collation_equivalent_email_cannot_confirm_or_reset_another_account(self):
        # MySQL users.email은 accent-insensitive다. 조회 성공 후에도 입력 이메일을 대조해야 한다.
        users = self.meta.tables['users']
        with self.engine.connect() as c:
            user = dict(c.execute(select(users).where(users.c.user_id == self.uid)).mappings().one())
        with patch('backend.app.api.recovery.accounts.get_user', return_value=user), \
                patch('backend.app.api.recovery.send_temporary_password') as send:
            for email in ('álice@example.org', 'alice@exámple.org'):
                with self.subTest(email=email):
                    confirmation = self.call('POST', 'auth/account-recovery/candidates', {'name': user['name'], 'email': email})
                    self.assertEqual(confirmation.status_code, 200, confirmation.text)
                    self.assertFalse(confirmation.json()['matches'])
                    result = self.reset(email=email)
                    self.assertEqual(result.status_code, 403, result.text)
                    self.assertEqual(result.json()['code'], 'RECOVERY_NOT_AVAILABLE')
            send.assert_not_called()
        with self.engine.connect() as c:
            after = c.execute(select(users).where(users.c.user_id == self.uid)).mappings().one()
        self.assertEqual(after['password_hash'], user['password_hash'])
        self.assertEqual(after['auth_version'], user['auth_version'])

    def test_smtp_tls_content_and_accepted_mail_does_not_wait_for_quit(self):
        data = {'host': 'smtp.example.org', 'port': 587, 'mode': 'starttls', 'sender': 'service@example.org',
                'recipient': 'alice@example.org', 'username': 'service', 'password': 'synthetic-test-only',
                'temporary_password': 'Abc123Synthetic', 'timeout': 10}
        with patch('backend.app.api.smtp_delivery.smtplib.SMTP') as smtp:
            client = smtp.return_value
            client.send_message.return_value = {}
            deliver(data)
            client.starttls.assert_called_once()
            client.login.assert_called_once()
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'alice@example.org')
            self.assertIn('Abc123Synthetic', message.get_content())
            client.close.assert_called_once()
            client.quit.assert_not_called()
            client.send_message.side_effect = smtplib.SMTPRecipientsRefused({'alice@example.org': (550, b'rejected')})
            with self.assertRaises(smtplib.SMTPRecipientsRefused):
                deliver(data)

    def seed_admin(self):
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['users']).where(self.meta.tables['users'].c.user_id == self.uid).values(role='ADMIN'))
            c.execute(self.meta.tables['keyword_category'].insert(), [{'category_id': i, 'major_name': major, 'minor_name': minor} for i, (major, minor, _) in enumerate(TAX, 1)])
            c.execute(self.meta.tables['keyword_topic'].insert(), {'keyword_text': '검수키워드', 'status': 'PENDING'})
            c.execute(self.meta.tables['article'].insert(), {'article_id': 1, 'source_code': 'KOTRA_MARKET', 'external_id': 'test', 'title': '실제 근거 기사', 'published_date': date(2026, 11, 1), 'source_url': 'https://example.org/1', 'body_masked': '본문', 'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
            c.execute(self.meta.tables['article_tag'].insert(), {'article_id': 1, 'keyword_text': '검수키워드'})

    def review(self, action, category=None, version=None, **extra):
        if version is None: version = self.client.get('/api/v1/admin/keyword-topics/detail', params={'keyword_text': '검수키워드'}).json()['keyword']['version']
        return self.call('PATCH', 'admin/keyword-topics/review', {'keyword_text': '검수키워드', 'action': action, 'category_id': category, 'expected_version': version, 'confirmed': True, 'reason': '기사 근거 확인', 'apply_scope': 'NEW_ONLY', **extra})

    def test_keyword_review_persists_filters_history_and_version_conflicts(self):
        self.assertEqual(self.client.get('/api/v1/admin/keyword-topics').status_code, 403)
        self.seed_admin()
        categories = self.client.get('/api/v1/admin/keyword-categories').json()
        self.assertEqual(len(categories['base_majors']), 8)
        self.assertEqual(len(categories['items']), 23)
        detail = self.client.get('/api/v1/admin/keyword-topics/detail', params={'keyword_text': '검수키워드'}).json()
        self.assertEqual(detail['articles'][0]['title'], '실제 근거 기사')
        version = detail['keyword']['version']
        self.assertEqual(self.review('HOLD').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/admin/keyword-topics?review_status=HELD').json()['items'][0]['keyword_text'], '검수키워드')
        self.assertEqual(self.review('ASSIGN', 1, version).status_code, 409)
        self.assertEqual(self.review('ASSIGN', 1).status_code, 200)
        classified = self.client.get('/api/v1/admin/keyword-topics?review_status=REVIEWED').json()['items']
        self.assertEqual(classified[0]['category_id'], 1)
        self.assertEqual(classified[0]['article_count'], 1)
        self.assertEqual(self.review('ASSIGN', 2).status_code, 200)
        article = self.client.get('/api/v1/admin/articles/1').json()
        self.assertEqual(article['moderation']['categories'], [])
        history = self.client.get('/api/v1/admin/audit-events?target_type=KEYWORD').json()['items']
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]['after']['category_id'], 2)
        # 새 기사는 최초 적재 정책을 고정하고, 재분류·재적재가 이전 분류를 바꾸지 않는다.
        import tempfile
        from pathlib import Path
        from backend.app.services import news_schema
        with tempfile.TemporaryDirectory() as directory, patch.object(news_schema, 'SNAPSHOT_DIR', Path(directory)):
            with self.engine.begin() as c:
                a = self.meta.tables['article']
                rows = news_schema.article_insert_rows(c, a, [{'article_id': 2, 'source_code': 'KOTRA_MARKET',
                    'external_id': 'new-only', 'title': '새 기사', 'published_date': date(2026, 11, 2),
                    'source_url': 'https://example.org/2', 'body_masked': '새 기사 본문'}])
                c.execute(a.insert(), rows)
                c.execute(self.meta.tables['article_tag'].insert(), {'article_id': 2, 'keyword_text': '검수키워드'})
                news_schema.freeze_keyword_categories(c, a, [2], ingestion_policy=True)
            self.assertEqual(self.review('ASSIGN', 1).status_code, 200)
            with self.engine.begin() as c:
                news_schema.freeze_keyword_categories(c, a, [2], ingestion_policy=True)
            self.assertEqual(self.client.get('/api/v1/admin/articles/2').json()['moderation']['categories'][0]['category_id'], 2)
            self.assertEqual(self.client.get('/api/v1/admin/articles/1').json()['moderation']['categories'], [])
            from backend.app.api.database import Store
            from backend.app.api.news import article_statement
            from sqlalchemy.dialects import mysql
            with self.engine.begin() as c:
                # 더 최근인 미분류 기사보다 과거에 분야 2로 고정된 기사를 우선한다.
                c.execute(update(a).where(a.c.article_id == 1).values(published_date=date(2026, 11, 3)))
                store = Store(self.app.state.database, c)
                statement = article_statement(store, category_boost=[2])
                self.assertEqual([r.article_id for r in c.execute(statement)], [2, 1])
                with patch.object(c.dialect, 'name', 'mysql'):
                    sql = str(article_statement(store, category_boost=[2]).compile(dialect=mysql.dialect()))
                self.assertIn('json_contains', sql)
        self.assertEqual(self.review('RELEASE').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/admin/keyword-topics?review_status=UNREVIEWED').json()['page']['total'], 1)
        self.assertEqual(self.review('ASSIGN', 1, apply_scope='CURRENT_LOOKUPS').status_code, 422)
        self.assertEqual(self.review('ASSIGN', 9999).status_code, 404)

    def test_category_crud_guards_and_member_minimal_detail(self):
        self.seed_admin()
        body = {'major_name': '운영 분류', 'minor_name': '신규 분야', 'reason': '운영 검수', 'confirmed': True}
        r = self.call('POST', 'admin/keyword-categories', body)
        self.assertEqual(r.status_code, 200, r.text)
        cid = r.json()['category_id']
        path = f'admin/keyword-categories/{cid}'
        category = self.client.get('/api/v1/'+path).json()['category']
        self.assertTrue(category['editable'])
        self.assertEqual(self.review('ASSIGN', cid).status_code, 200)
        deletion = {'reason': '삭제', 'confirmed': True, 'expected_version': category['version']}
        self.assertEqual(self.call('DELETE', path, deletion).status_code, 422)
        self.assertEqual(self.review('RELEASE').status_code, 200)
        rename = self.call('PATCH', path, {**body, 'minor_name': '수정 분야', 'expected_version': category['version']})
        self.assertEqual(rename.status_code, 200, rename.text)
        self.assertEqual(self.call('DELETE', path, deletion).status_code, 422)
        deletion['expected_version'] = self.client.get('/api/v1/'+path).json()['category']['version']
        self.assertEqual(self.call('DELETE', path, deletion).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/'+path).status_code, 200)
        self.assertEqual(self.call('PATCH', 'admin/keyword-categories/1', {**body, 'expected_version': 'x'}).status_code, 409)
        member = self.client.get(f'/api/v1/admin/users/{self.uid}').json()
        self.assertTrue({'interests', 'settings', 'subscriptions'} <= member.keys())
        self.assertNotIn('password_hash', member['member'])
        self.assertNotIn('auth_version', member['member'])

    def test_delayed_keyword_results_use_original_policy_for_both_loaders(self):
        import tempfile
        from pathlib import Path
        from backend.app.db import news_lock
        from backend.app.services import news_schema
        from backend.app.scheduler.jobs import news_load, shortnews_load
        from backend.tests import test_news_load as market, test_shortnews_load as short
        self.seed_admin()
        with self.engine.begin() as c:
            c.exec_driver_sql('CREATE UNIQUE INDEX article_origin ON article(source_code, external_id)')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(news_schema, 'SNAPSHOT_DIR', Path(directory)/'snapshots'), \
                patch.object(news_lock, 'LOAD_LOCK_DIR', Path(directory)/'locks'):
            for loader, raw, clean, source in (
                (news_load.load, lambda key: market.raw(key, '2026-11-01'),
                 lambda key: market.clean(key, '2026-11-01', '검수키워드'), 'KOTRA_MARKET'),
                (shortnews_load.load, lambda key: short.raw(key),
                 lambda key: short.clean(key, keywords='검수키워드', hs=''), 'KOTRA_SHORT_NEWS'),
            ):
                with self.subTest(source=source):
                    with self.engine.begin() as c:
                        c.execute(update(self.meta.tables['keyword_topic']).values(category_id=1, status='CLASSIFIED'))
                    loader(self.engine, [raw('101')], [])
                    a = self.meta.tables['article']
                    with self.engine.connect() as c:
                        pending = c.execute(select(a.c.admin_meta_json).where(a.c.source_code==source, a.c.external_id=='101')).scalar_one()
                    self.assertNotIn('keyword_categories', pending)
                    with self.engine.begin() as c:
                        c.execute(update(self.meta.tables['keyword_topic']).values(category_id=2))
                    loader(self.engine, [raw('101'), raw('102')], [clean('101'), clean('102')])
                    with self.engine.connect() as c:
                        classified = dict(c.execute(select(a.c.external_id, a.c.admin_meta_json).where(a.c.source_code==source, a.c.external_id.in_(['101', '102']))).all())
                    self.assertEqual(classified['101']['keyword_categories'][0]['category_id'], 1)
                    self.assertEqual(classified['102']['keyword_categories'][0]['category_id'], 2)

    def test_keyword_history_keeps_case_sensitive_dictionary_keys(self):
        self.seed_admin()
        # 운영 DB의 기본 감사 테이블 정렬 규칙이 대소문자를 구분하지 않는 경우를 재현한다.
        # keyword_topic 키는 대소문자를 구분하므로 서로 다른 이력이 합쳐지면 안 된다.
        with self.engine.begin() as c:
            audit = self.meta.tables['api_audit_event']
            audit.drop(c)
            audit.c.target_id.type = String(120, collation='NOCASE')
            audit.create(c)
            c.execute(self.meta.tables['keyword_topic'].insert(), [
                {'keyword_text': 'AI', 'status': 'PENDING'},
                {'keyword_text': 'ai', 'status': 'PENDING'},
            ])
        detail_path = '/api/v1/admin/keyword-topics/detail'
        version = self.client.get(detail_path, params={'keyword_text': 'AI'}).json()['keyword']['version']
        result = self.review('HOLD', version=version, keyword_text='AI')
        self.assertEqual(result.status_code, 200, result.text)
        lower = self.client.get(detail_path, params={'keyword_text': 'ai'}).json()['keyword']
        self.assertEqual(lower['review_status'], 'UNREVIEWED')
        held = self.client.get('/api/v1/admin/keyword-topics?review_status=HELD').json()['items']
        self.assertEqual([row['keyword_text'] for row in held], ['AI'])


if __name__ == '__main__': unittest.main()
