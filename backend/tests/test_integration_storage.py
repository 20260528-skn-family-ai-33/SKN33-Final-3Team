"""Storage/security regression tests; fake Redis is injected only by the test fixture."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from redis.exceptions import ConnectionError
from sqlalchemy import inspect, select, update
from backend.tests import test_backend_api as base
from backend.app.api.common import ApiError
from backend.app.api.security import hash_token
from backend.app.api.report_storage import LocalReportStorage


class IntegrationStorageTest(unittest.TestCase):
    setUp=base.BackendApiTest.setUp
    tearDown=base.BackendApiTest.tearDown
    signup=base.BackendApiTest.signup
    post=base.BackendApiTest.post

    def test_only_approved_app_tables_and_extended_columns(self):
        tables=inspect(self.engine).get_table_names()
        self.assertEqual(sorted(t for t in tables if t.startswith('api_')),['api_audit_event','api_snapshot'])
        self.assertFalse({'password_change_advised','scheduled_delete_at'} & {c['name'] for c in inspect(self.engine).get_columns('users')})
        uid=self.signup()
        self.assertNotIn('password_change_advised',self.client.get('/api/v1/users/me').json()['user'])
        with self.engine.connect() as c:
            user=c.execute(select(self.meta.tables['users']).where(self.meta.tables['users'].c.user_id==int(uid))).mappings().one()
            self.assertEqual(user['name'],'홍길동')
            self.assertEqual(user['auth_version'],0)

    def test_sessions_expire_and_all_devices_revoke_on_password_change(self):
        uid=self.signup(); db=self.app.state.database
        token=self.client.cookies.get('bs_session'); key=db.redis.prefix+'session:'+hash_token(token)
        self.assertGreater(db.redis.client.ttl(key),0)
        original_cookie=token
        r=self.client.post('/api/v1/auth/login',json={'email':'alice@example.org','password':'Sample!123'})
        self.csrf=r.json()['csrf_token']; second=self.client.cookies.get('bs_session')
        self.assertNotEqual(original_cookie,second)
        self.assertEqual(self.post('users/me/password',{'email':'alice@example.org','current_password':'Sample!123',
            'new_password':'Changed!123','new_password_confirm':'Changed!123'}).status_code,204)
        for token in (original_cookie,second):
            self.client.cookies.set('bs_session',token)
            self.assertEqual(self.client.get('/api/v1/users/me').status_code,401)
        self.client.cookies.clear()
        r=self.client.post('/api/v1/auth/login',json={'email':'alice@example.org','password':'Changed!123'})
        self.assertEqual(r.status_code,200)
        key=db.redis.prefix+'session:'+hash_token(self.client.cookies.get('bs_session'))
        # A past timestamp avoids fake Redis's same-clock-tick expiry boundary.
        db.redis.client.pexpireat(key,1)
        self.assertEqual(db.redis.client.exists(key),0)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code,401)

    def test_redis_outage_is_explicit_and_does_not_bypass_auth(self):
        self.signup()
        with patch.object(self.app.state.database.redis.client,'get',side_effect=ConnectionError('hidden endpoint')):
            response=self.client.get('/api/v1/users/me')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['code'],'REDIS_UNAVAILABLE')
            self.assertNotIn('hidden endpoint',response.text)
        with patch.object(self.app.state.database.redis.client,'eval',side_effect=ConnectionError()):
            response=self.client.post('/api/v1/auth/login',json={'email':'alice@example.org','password':'Sample!123'})
            self.assertEqual(response.status_code,503)

    def test_atomic_limiter_and_ttl(self):
        redis=self.app.state.database.redis
        def request(_):
            try: redis.limit('concurrent',7,60); return 200
            except ApiError as e: return e.status
        with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(request,range(30)))
        self.assertEqual(results.count(200),7); self.assertEqual(results.count(429),23)
        self.assertGreater(redis.client.ttl(redis.prefix+'limit:concurrent'),0)
        # A past absolute deadline avoids fake Redis's same-clock-tick expiry boundary.
        redis.client.pexpireat(redis.prefix+'limit:concurrent',1)
        self.assertEqual(redis.client.exists(redis.prefix+'limit:concurrent'),0)
        redis.limit('concurrent',7,60)

    def test_file_storage_traversal_and_tampering(self):
        storage=LocalReportStorage(self.directory.name)
        record=storage.write('1/2/report.pdf',b'%PDF-test')
        self.assertEqual(set(record),{'storage','key','sha256','created_at'})
        self.assertEqual(storage.read(record),b'%PDF-test')
        self.assertFalse(Path(record['key']).is_absolute())
        with self.assertRaises(ApiError): storage.read({**record,'key':'../outside.pdf'})
        (Path(self.directory.name)/record['key']).write_bytes(b'tampered')
        with self.assertRaises(ApiError): storage.read(record)

    def test_empty_report_charts_render_without_invalid_minus_glyphs(self):
        import warnings
        from backend.app.api.files import chart_image
        for kind in ('LINE','HORIZONTAL_BAR'):
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter('always')
                image=chart_image({'tables':[{'result_id':'empty','rows':[]}]},
                    {'result_id':'empty','x_key':'month','series_keys':['amount_usd'],
                     'chart_type':kind,'title':'자료 없음'},self.settings)
            self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertFalse(any('Glyph 8722' in str(w.message) for w in captured))

    def test_admin_can_manage_other_roles_but_not_self_or_unknown_role(self):
        from fastapi.testclient import TestClient
        uid=self.signup(); users=self.meta.tables['users']
        with self.engine.begin() as c:
            c.execute(update(users).where(users.c.user_id==int(uid)).values(role='ADMIN'))
        other=TestClient(self.app)
        self.addCleanup(other.close)
        r=other.post('/api/v1/auth/signup',json={'name':'대상 회원','email':'target@example.org',
            'password':'Sample!123','password_confirm':'Sample!123'})
        self.assertEqual(r.status_code,201,r.text); target=r.json()['user']['user_id']
        self.assertEqual(other.get('/api/v1/admin/users').status_code,403)
        def change(identity, role):
            detail=self.client.get(f'/api/v1/admin/users/{identity}')
            self.assertEqual(detail.status_code,200,detail.text)
            self.assertNotIn('scheduled_delete_at',detail.json()['member'])
            return self.client.patch(f'/api/v1/admin/users/{identity}/role',
                headers={'X-CSRF-Token':self.csrf},json={'role':role,'confirmed':True,
                'reason':'역할 검증','expected_version':detail.json()['version']})
        self.assertEqual(change(uid,'USER').status_code,403)
        self.assertEqual(change(target,'SUPER_ADMIN').status_code,400)
        result=change(target,'ADMIN'); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(other.get('/api/v1/users/me').status_code,401)
        result=other.post('/api/v1/auth/login',json={'email':'target@example.org','password':'Sample!123'})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['user']['role'],'ADMIN')
        self.assertEqual(other.get('/api/v1/admin/users').status_code,200)
        result=change(target,'USER'); self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(other.get('/api/v1/users/me').status_code,401)
        self.assertEqual(self.client.get('/api/v1/admin/audit-events').json()['page']['total'],2)

    def test_job_is_restored_with_messages_for_refresh(self):
        self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        job=self.post(f'chat/sessions/{sid}/messages',{'question':'미국 수출','client_message_id':'refresh'}).json()['job']
        messages=self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']
        self.assertEqual(messages[-1]['job']['job_id'],job['job_id'])
        self.assertEqual(messages[-1]['job']['status'],'QUEUED')


if __name__=='__main__': unittest.main()
