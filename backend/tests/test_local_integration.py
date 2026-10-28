"""Opt-in real Docker MySQL + Redis tests. Refuses production targets."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, update
from sqlalchemy.exc import OperationalError
from backend.scripts.migrate_integration_cleanup import inspect_changes
from backend.scripts.local_integration import local_settings
from backend.scripts.migrate_integration import plan
from backend.app.api.application import create_app
from backend.app.api.security import hash_token
from backend.app.api.common import ApiError
from backend.app.api.worker import run_once
from backend.tests.intent_fixtures import extracted_slots


@unittest.skipUnless(os.environ.get('RUN_LOCAL_INTEGRATION')=='1','Start compose.integration.yml and set RUN_LOCAL_INTEGRATION=1')
class LocalIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.settings=local_settings()
        self.settings.values['API_REDIS_PREFIX']='borderless:localtest:'+uuid.uuid4().hex+':'
        self.settings.values['API_REPORT_DIRECTORY']='tmp/local-test-reports'
        self.engine=self.settings.engine()
        assert self.engine.url.host=='127.0.0.1' and self.engine.url.port==3307 and self.engine.url.database=='trade_integration'
        self.app=create_app(self.settings,self.engine); self.client=TestClient(self.app)
        self.email='integration-'+uuid.uuid4().hex+'@example.org'
        r=self.client.post('/api/v1/auth/signup',json={'name':'로컬 검증','email':self.email,'password':'LocalTest!123','password_confirm':'LocalTest!123'})
        self.assertEqual(r.status_code,201,r.text)
        self.uid=r.json()['user']['user_id']; self.csrf=r.json()['csrf_token']

    def tearDown(self):
        # Only keys under this test's random namespace are removed; never FLUSHDB.
        redis=self.app.state.database.redis
        keys=list(redis.client.scan_iter(match=redis.prefix+'*'))
        if keys: redis.client.delete(*keys)
        self.client.close(); self.engine.dispose()

    def post(self,path,body,headers=None):
        return self.client.post('/api/v1/'+path,json=body,headers={'X-CSRF-Token':self.csrf,**(headers or {})})

    def test_schema_readiness_idempotency_and_shared_sessions(self):
        self.assertEqual(self.client.get('/health/ready').status_code,200)
        with self.engine.connect() as c: self.assertEqual(plan(c),[])
        self.assertEqual(sorted(t for t in inspect(self.engine).get_table_names() if t.startswith('api_')),['api_audit_event','api_snapshot'])
        other=TestClient(create_app(self.settings,self.engine)); other.cookies.update(self.client.cookies)
        self.assertEqual(other.get('/api/v1/users/me').json()['user']['user_id'],self.uid)
        self.assertEqual(self.post('auth/logout',{}).status_code,204)
        self.assertEqual(other.get('/api/v1/users/me').status_code,401); other.close()

    def test_atomic_redis_limit_and_expiry(self):
        state=self.app.state.database.redis
        def request(_):
            try: state.limit('burst',5,60); return 200
            except ApiError as e: return e.status
        with ThreadPoolExecutor(max_workers=12) as pool: status=list(pool.map(request,range(30)))
        self.assertEqual(status.count(200),5); self.assertEqual(status.count(429),25)
        key=state.prefix+'limit:burst'; self.assertGreater(state.client.ttl(key),0)
        state.client.pexpire(key,0); state.limit('burst',5,60)
        sessionkey=state.prefix+'session:'+hash_token(self.client.cookies.get('bs_session'))
        state.client.pexpire(sessionkey,0)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code,401)

    def test_cleanup_is_complete_and_database_rejects_third_role(self):
        with self.engine.connect() as c:
            users, _, changes = inspect_changes(c)
            self.assertEqual(changes, {'removed_columns': [], 'converted_roles': 0, 'files': [], 'ddl': []})
            with self.assertRaises(OperationalError) as rejected:
                c.execute(update(users).where(users.c.user_id==self.uid).values(role='SUPER_ADMIN'))
            self.assertEqual(rejected.exception.orig.args[0],3819)
            c.rollback()
        self.assertEqual(self.client.get('/api/v1/users/me').json()['user']['role'],'USER')

    def test_integrated_blocks_admin_audit_and_account_restriction(self):
        blocks=self.client.get('/api/v1/users/me/keyword-blocks').json()['settings']
        body={'keywords':['로컬검증'],'expected_version':blocks['version']}
        r=self.client.put('/api/v1/users/me/keyword-blocks',json=body,headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/api/v1/articles?q=로컬&mode=SEARCH').json()['items'],[])
        t=self.app.state.database.table('notification_setting')
        with self.engine.connect() as c:
            saved=c.scalar(select(t.c.keyword_blocks_json).where(t.c.user_id==self.uid))
        self.assertEqual(saved[0]['kw_topic'],'로컬검증')
        users=self.app.state.database.table('users')
        with self.engine.begin() as c: c.execute(update(users).where(users.c.user_id==self.uid).values(role='ADMIN'))
        other=TestClient(self.app)
        try:
            r=other.post('/api/v1/auth/signup',json={'name':'제한 검증','email':uuid.uuid4().hex+'@example.org','password':'LocalTest!123','password_confirm':'LocalTest!123'})
            self.assertEqual(r.status_code,201,r.text); target=r.json()['user']['user_id']
            detail=self.client.get(f'/api/v1/admin/users/{target}').json()
            r=self.client.patch(f'/api/v1/admin/users/{target}/status',headers={'X-CSRF-Token':self.csrf},json={'status':'RESTRICTED','reason':'로컬 통합 테스트','confirmed':True,'expected_version':detail['version']})
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(other.get('/api/v1/users/me').status_code,401)
            audit_id=r.json()['operation']['audit_id']
            r=self.client.get(f'/api/v1/admin/audit-events/{audit_id}')
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['audit']['after']['account_status'],'RESTRICTED')
        finally:
            other.close()
            with self.engine.begin() as c: c.execute(update(users).where(users.c.user_id==self.uid).values(role='USER'))

    def test_interests_version_real_trade_and_snapshot(self):
        data=self.client.get('/api/v1/users/me/interests').json()['interests']
        body={'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],'country_codes':['US'],'expected_version':data['version'],'landing_page':'INTERESTED'}
        r=self.client.put('/api/v1/users/me/interests',json=body,headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.put('/api/v1/users/me/interests',json=body,headers={'X-CSRF-Token':self.csrf}).status_code,409)
        r=self.client.get('/api/v1/trade/rankings?scope=INTERESTED')
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['items'][0]['country_code'],'US')
        identity=r.json()['result_id']
        self.assertTrue(self.client.get(f'/api/v1/results/{identity}/file').content.startswith(b'PK'))

    def test_report_worker_files_and_ownership(self):
        r=self.post('reports/preview',{'conditions':{'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],
            'country_codes':['US'],'period':{'start':'2025-12-01','end':'2025-12-31'}}})
        self.assertEqual(r.status_code,200,r.text)
        body={'preview_id':r.json()['preview_id'],'confirm_conditions':True,'sections':['KPI','TOP_COUNTRIES'],'formats':['PDF','DOCX']}
        headers={'Idempotency-Key':uuid.uuid4().hex}
        r=self.post('reports',body,headers); self.assertEqual(r.status_code,202,r.text); rid=r.json()['job']['report_id']
        self.assertEqual(self.post('reports',body,headers).json()['job']['report_id'],rid)
        for _ in range(10):
            run_once(self.app.state.database,self.settings)
            job=self.client.get(f'/api/v1/reports/{rid}/status').json()['job']
            if job['status']=='READY': break
        self.assertEqual(job['status'],'READY',job)
        for fmt in ('pdf','docx','xlsx'):
            r=self.client.get(f'/api/v1/reports/{rid}/files/{fmt}')
            self.assertEqual(r.status_code,200,r.text[:100] if r.status_code!=200 else '')
            self.assertTrue(r.content.startswith(b'%PDF' if fmt=='pdf' else b'PK'))
        t=self.app.state.database.table('report')
        with self.engine.connect() as c: files=c.scalar(select(t.c.files_json).where(t.c.report_id==rid))
        self.assertTrue(all(set(meta)=={'storage','key','sha256','created_at'} for meta in files.values()));
        self.assertEqual(set(files),{'PDF','DOCX'}); self.assertEqual(files['PDF']['storage'],'local')
        other=TestClient(self.app)
        r=other.post('/api/v1/auth/signup',json={'name':'다른 회원','email':uuid.uuid4().hex+'@example.org','password':'LocalTest!123','password_confirm':'LocalTest!123'})
        self.assertEqual(r.status_code,201,r.text)
        self.assertEqual(other.get(f'/api/v1/reports/{rid}/files/pdf').status_code,404)
        other.close()

    def test_chat_worker_and_restored_job(self):
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        response=self.post(f'chat/sessions/{sid}/messages',{'question':'2025년 12월 전체 품목 수출 추이','client_message_id':uuid.uuid4().hex})
        self.assertEqual(response.status_code,202,response.text); job=response.json()['job']
        messages=self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']
        self.assertEqual(messages[-1]['job']['job_id'],job['job_id'])
        with patch('backend.app.api.intent.OpenAIIntentProvider.extract',side_effect=extracted_slots):
            run_once(self.app.state.database,self.settings)
        result=self.client.get('/api/v1/chat/jobs/'+job['job_id']).json()['job']
        self.assertEqual(result['status'],'SUCCEEDED',result)


if __name__=='__main__': unittest.main()
