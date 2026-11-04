import io
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select, update, delete, text, inspect
from backend.tests.api_support import test_engine, reference_data
from backend.tests.api_support import create_test_app as create_app
from backend.app.api.config import Settings
from backend.app.api.common import now
from backend.app.api.worker import run_once
from backend.app.api.files import xlsx_bytes
from backend.app.api.database import Store
from backend.app.api import jobs


class BackendApiTest(unittest.TestCase):
    def test_worker_claims_jobs_when_report_wait_scan_fails(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        engine = MagicMock()
        scan, claim = MagicMock(), MagicMock()
        engine.begin.side_effect = [scan, claim]
        database = SimpleNamespace(engine=engine)
        with patch('backend.app.agents.report.wake_ready', side_effect=ConnectionError), \
             patch('backend.app.api.worker.jobs.claim', return_value=None) as claim_job, \
             self.assertLogs('backend.app.api.worker', level='WARNING'):
            self.assertFalse(run_once(database, {'API_CHAT_AGENT_ENABLED':'false'}))
        self.assertIs(scan.__exit__.call_args.args[0], ConnectionError)
        self.assertIs(claim_job.call_args.args[0].c, claim.__enter__.return_value)

    def setUp(self):
        Path('tmp/backend_build').mkdir(parents=True,exist_ok=True)
        self.directory=tempfile.TemporaryDirectory(dir='tmp/backend_build',ignore_cleanup_errors=True)
        self.engine,self.meta=test_engine(); reference_data(self.engine,self.meta)
        self.settings=Settings(values={'API_COOKIE_SECURE':'false','API_REPORT_DIRECTORY':self.directory.name,'API_HS_VECTOR_ENABLED':'false'})
        self.app=create_app(self.settings,self.engine); self.client=TestClient(self.app)
        self.csrf=None
        from backend.tests.intent_fixtures import extracted_slots
        intent_patch=patch('backend.app.api.intent.OpenAIIntentProvider.extract',side_effect=extracted_slots)
        self.intent_mock=intent_patch.start()
        self.addCleanup(intent_patch.stop)

    def tearDown(self):
        self.client.close(); self.engine.dispose(); self.directory.cleanup()

    def signup(self,email='alice@example.org'):
        response=self.client.post('/api/v1/auth/signup',json={'name':'홍길동','email':email,'password':'Sample!123','password_confirm':'Sample!123'})
        self.assertEqual(response.status_code,201,response.text)
        self.csrf=response.json()['csrf_token']; return response.json()['user']['user_id']

    def post(self,path,body,**kwargs):
        return self.client.post('/api/v1/'+path,json=body,headers={'X-CSRF-Token':self.csrf,**kwargs})

    def test_auth_csrf_logout_and_validation(self):
        self.assertEqual(self.client.get('/api/v1/users/me').status_code,401)
        uid=self.signup()
        self.assertEqual(self.client.get('/api/v1/users/me').json()['user']['user_id'],uid)
        self.assertEqual(self.client.patch('/api/v1/users/me',json={'landing_page':'GLOBAL'}).status_code,403)
        self.assertEqual(self.post('auth/logout',{}).status_code,204)
        self.assertEqual(self.client.get('/api/v1/users/me').status_code,401)
        result=self.client.post('/api/v1/auth/signup',json={'name':'x','email':'bad','password':'secret','password_confirm':'secret'})
        self.assertEqual(result.status_code,400); self.assertNotIn('secret',result.text)
        self.assertEqual(set(result.json()),{'code','message','details','retryable','request_id'})

    def test_origin_admin_and_removed_routes(self):
        r=self.client.post('/api/v1/auth/login',json={'email':'x@example.org','password':'Sample!123'},headers={'Origin':'https://evil.invalid'})
        self.assertEqual(r.status_code,403)
        self.signup(); self.assertEqual(self.client.get('/api/v1/admin/users').status_code,403)
        self.assertEqual(self.client.get('/api/v1/admin/reports').status_code,404)
        self.assertEqual(self.client.get('/api/v1/articles?page=0').status_code,400)
        self.assertEqual(self.client.get('/api/v1/articles?page=1&page=2').status_code,400)
        self.assertEqual(self.client.get('/api/v1/notifications?unread_only=no').status_code,400)

    def test_interest_version_and_hs_depth(self):
        self.signup(); url='/api/v1/users/me/interests'
        version=self.client.get(url).json()['interests']['version']
        body={'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],'country_codes':['US'],'expected_version':version,'landing_page':'INTERESTED'}
        r=self.client.put(url,json=body,headers={'X-CSRF-Token':self.csrf}); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['interests']['country_codes'],['US'])
        self.assertEqual(self.client.put(url,json=body,headers={'X-CSRF-Token':self.csrf}).status_code,409)
        body['expected_version']=r.json()['interests']['version']; body['hs_items'][0]['hs_code']='3304'
        self.assertEqual(self.client.put(url,json=body,headers={'X-CSRF-Token':self.csrf}).status_code,400)

    def test_snapshot_ownership_and_global_denominator(self):
        self.signup()
        r=self.client.get('/api/v1/trade/kpis?month=2025-12'); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['kpi']['amount_usd'],3460000)
        rid=r.json()['result_id']
        body={'conditions':{'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],'country_codes':['US'],'period':{'start':'2025-12-01','end':'2025-12-31'}}}
        r=self.post('analysis/market-shares',body); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['denominator_usd'],3460000)
        self.signup('bob@example.org')
        self.assertEqual(self.client.get('/api/v1/results/'+rid).status_code,404)
        self.assertEqual(self.client.get('/api/v1/results/'+rid+'/file').status_code,404)

    def test_verified_period_and_no_zero_fill(self):
        self.signup()
        t=self.meta.tables['batch_run']
        with self.engine.begin() as c:
            c.execute(t.insert().values(source_code='CUSTOMS',job_name='HS6_AGGREGATION',target_table='export_summary',period_start=date(2025,12,1),period_end=date(2025,12,31),status='FAILED',detail_json={'schema_version':1}))
        r=self.client.get('/api/v1/trade/kpis?month=2025-12')
        self.assertEqual(r.status_code,422,r.text)
        self.assertEqual(self.client.get('/api/v1/data/availability').json()['trade_end_month'],'2025-11')

    def test_notification_consent_and_popup_ack_not_read(self):
        uid=self.signup()
        r=self.client.patch('/api/v1/users/me/notification-settings',json={'notifications_enabled':True},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(r.status_code,409)
        r=self.post('users/me/consents',{'terms_type':'NOTIFICATION','terms_version':'TERMS_V1','action':'AGREE','enable_notifications':True})
        self.assertEqual(r.status_code,200,r.text)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['alert_event'].insert(),{'event_id':1,'event_type':'MAJOR_ISSUE','event_key':'a'*64,'payload_json':{'target':{},'article_ids':[]},'created_at':now()})
            c.execute(self.meta.tables['user_alert'].insert(),{'user_id':int(uid),'event_id':1,'is_interest_match':True,'created_at':now()})
        r=self.client.get('/api/v1/notifications/popups'); self.assertEqual(r.status_code,200,r.text)
        delivery=r.json()['groups'][0]['delivery_id']
        self.assertEqual(self.post('notifications/popups/'+delivery+'/ack',{}).status_code,204)
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'],1)
        self.assertEqual(self.client.get('/api/v1/notifications/popups').json()['groups'],[])

    def test_report_idempotency_worker_files_and_frozen_content(self):
        self.signup()
        conditions={'period':{'start':'2025-12-01','end':'2025-12-31'},'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}]}
        preview=self.post('reports/preview',{'conditions':conditions}); self.assertEqual(preview.status_code,200,preview.text)
        body={'preview_id':preview.json()['preview_id'],'confirm_conditions':True,'sections':['KPI','TOP_COUNTRIES'],'formats':['PDF','DOCX']}
        response=self.post('reports',body,**{'Idempotency-Key':'request-1'}); self.assertEqual(response.status_code,202,response.text)
        rid=response.json()['job']['report_id']
        self.assertEqual(self.post('reports',body,**{'Idempotency-Key':'request-1'}).json()['job']['report_id'],rid)
        other={**body,'formats':['PDF']}; self.assertEqual(self.post('reports',other,**{'Idempotency-Key':'request-1'}).status_code,409)
        with self.engine.connect() as connection:
            store=Store(self.app.state.database,connection)
            report=store.t('report')
            record=store.one(select(report).where(report.c.report_id==rid))
            self.assertEqual(jobs.report_execution(store,record)['version'],jobs.REPORT_QUEUE_VERSION)
            legacy_ids=connection.execute(select(report.c.report_id).where(report.c.content_json[jobs.EXECUTION]['version'].as_integer()==1)).scalars().all()
            self.assertNotIn(int(rid),legacy_ids)
        self.assertTrue(run_once(self.app.state.database,self.settings))
        with self.engine.connect() as connection:
            store=Store(self.app.state.database,connection)
            record=store.one(select(store.t('report')).where(store.t('report').c.report_id==rid))
            self.assertEqual(jobs.report_execution(store,record)['worker']['report_protocol'],jobs.REPORT_QUEUE_VERSION)
        state=self.client.get(f'/api/v1/reports/{rid}/status'); self.assertEqual(state.json()['job']['status'],'READY',state.text)
        old=self.client.get(f'/api/v1/reports/{rid}').json()
        for fmt in ('pdf','docx','xlsx'):
            file=self.client.get(f'/api/v1/reports/{rid}/files/{fmt}'); self.assertEqual(file.status_code,200,file.text[:100] if fmt=='xlsx' else '')
            self.assertTrue(file.content.startswith(b'%PDF' if fmt=='pdf' else b'PK'))
            from urllib.parse import unquote
            filename=unquote(file.headers['content-disposition'])
            self.assertIn('전체국가_2025-12-01~2025-12-31_',filename)
            self.assertRegex(filename,r'_수출액분석_리포트_\d{8}_\d{6}\.'+fmt+r'$')
            Path('tmp/backend_build').mkdir(parents=True,exist_ok=True)
            Path(f'tmp/backend_build/report-test.{fmt}').write_bytes(file.content)
        with self.engine.begin() as c: c.execute(update(self.meta.tables['export_summary']).values(amount_usd=1))
        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}').json(),old)
        with self.engine.connect() as c: self.assertEqual(len(c.execute(select(self.meta.tables['alert_event'])).all()),0)

    def test_cancelled_job_cannot_be_claimed_and_session_accepts_next_question(self):
        self.signup()
        sid = self.post('chat/sessions', {}).json()['session']['session_id']
        path = f'chat/sessions/{sid}/messages'
        body = {'question': '미국 수출액', 'client_message_id': 'cancel-test'}
        job = self.post(path, body).json()['job']['job_id']
        response = self.post(f'chat/jobs/{job}/cancel', {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['job']['status'], 'CANCELLED')
        self.assertEqual(self.post(f'chat/jobs/{job}/cancel', {}).json()['code'], 'JOB_ALREADY_FINISHED')
        self.assertFalse(run_once(self.app.state.database, self.settings))
        self.assertEqual(self.post(path, {**body, 'client_message_id': 'next'}).status_code, 202)

    def test_cancelling_queued_agent_job_deletes_checkpoint_after_commit(self):
        from unittest.mock import Mock
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        self.signup()
        db=self.app.state.database; db.agent_checkpointer=Mock()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        job=self.post(f'chat/sessions/{sid}/messages',{'question':'미국 수출액','client_message_id':'cleanup'}).json()['job']['job_id']
        def run_now(target,args,daemon):  # run the background cleanup inline so the test can observe it
            return Mock(start=lambda:target(*args))
        with patch('threading.Thread',side_effect=run_now):
            response=self.post(f'chat/jobs/{job}/cancel',{})
        self.assertEqual(response.json()['job']['status'],'CANCELLED')
        db.agent_checkpointer.delete_thread.assert_called_once_with(str(job))

    def test_legacy_worker_cannot_claim_agent_and_mixed_interest_resumes_same_job(self):
        from backend.app.api import chat
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        uid=self.signup()
        db=self.app.state.database
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        path=f'chat/sessions/{sid}/messages'
        jid=self.post(path,{'question':'수출 추이를 알려줘','client_message_id':'mixed'}).json()['job']['job_id']
        with self.engine.begin() as conn:
            store=Store(db,conn)
            m=store.t('chat_message')
            state=m.c.result_json[jobs.EXECUTION]
            # Same protocol discriminator as main's worker, including expired lease recovery.
            v1_candidate=select(m.c.message_id).where(m.c.message_id==jid,state['version'].as_integer()==1)
            self.assertIsNone(conn.scalar(v1_candidate))
            old=jobs.owned_job(store,uid,jid)
            old['version']=1  # admission before the protocol fix
            jobs._save_chat(store,old)
            self.assertIsNotNone(conn.scalar(v1_candidate))
            claimed=jobs.claim(store)
            with patch.object(chat,'interests',return_value={'country_codes':['US'],'hs_items':[]}):
                waiting=chat.execute(store,claimed)
            self.assertEqual(waiting['clarification']['type'],'INTEREST_CONFIRM')
            jobs.finish(store,claimed,waiting)  # unmodified legacy finish defaults to SUCCEEDED
        url=f'chat/jobs/{jid}/clarifications'
        body={'clarification_id':waiting['clarification']['clarification_id'],'choice_ids':['GLOBAL']}
        self.assertEqual(self.post(url,{**body,'clarification_id':'stale'}).status_code,409)
        accepted=self.post(url,body)
        self.assertEqual(accepted.status_code,202,accepted.text)
        self.assertEqual(accepted.json()['job']['job_id'],jid)
        with self.engine.connect() as conn: self.assertIsNone(conn.scalar(v1_candidate))
        self.assertTrue(run_once(db,self.settings))
        done=self.post(url,body).json()['job']
        self.assertEqual(done['status'],'SUCCEEDED',done)
        self.assertTrue(done['answer']['tables'])
        self.assertEqual(done['answer']['conditions']['country_codes'],[])
        self.assertEqual(self.post(url,{**body,'choice_ids':['APPLY']}).status_code,409)

    def _run_numeric_review(self, correction):
        from langgraph.checkpoint.memory import InMemorySaver
        from backend.app.agents.state import PlannerOutput, Draft
        from unittest.mock import Mock
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        database=self.app.state.database
        database.agent_checkpointer=InMemorySaver()
        database.agent_client=Mock()
        wrong='수출액은 98765432198765 USD입니다.'
        intro='수출 통계를 조회했습니다.'
        original=Draft(conclusion=[{'task_id':'t1','sentences':[intro]}],explanation=[wrong],turn_summary='')
        revised=Draft(conclusion=[{'task_id':'t1','sentences':[wrong]}],turn_summary='')
        database.agent_client.parse.side_effect=[
            PlannerOutput(tasks=[{'intent':'STATISTICS','question':'수출 통계','slots':{'recent_months':3}}]),
            original, revised if correction else ValueError('invalid draft')]
        self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'수출 통계','client_message_id':'numeric-review'}).json()['job']['job_id']
        run_once(database,self.settings)
        job=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        self.assertEqual(job['status'],'SUCCEEDED',job)
        answer=job['answer']
        self.assertIn(wrong,answer['text'])
        verification=answer['verification']
        self.assertEqual((verification['status'],verification['regeneration_count']),('NEEDS_REVIEW',1))
        self.assertEqual([(c['index'],c['sentence']) for c in verification['sentence_checks'] if c['status']=='MISMATCH'],
                         [(0 if correction else 1,wrong)])
        if not correction: self.assertEqual(verification['sentence_checks'][0]['status'],'PASS')
        self.assertEqual(database.agent_client.parse.call_count,3)  # planner, draft, one correction

    def test_failed_numeric_correction_keeps_original_marked_sentence(self):
        self._run_numeric_review(False)

    def test_reverified_shorter_draft_marks_only_final_sentence_index(self):
        self._run_numeric_review(True)

    def test_agent_worker_resumes_then_publishes_ranked_table(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from backend.app.agents.state import Draft, PlannerOutput
        from unittest.mock import Mock
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        db=self.app.state.database
        db.agent_checkpointer=InMemorySaver()
        db.agent_client=Mock()
        db.agent_client.parse.side_effect=[PlannerOutput(tasks=[{'intent':'RANKING','question':'수출 순위',
            'slots':{'recent_months':3}}]), Draft(conclusion=[{'task_id':'t1','sentences':['중국이 1위입니다.']}],
                                                   turn_summary='중국이 1위입니다.',title='수출 순위')]
        self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        path=f'chat/sessions/{sid}/messages'
        response=self.post(path,{'question':'수출 순위','client_message_id':'agent-start'})
        self.assertEqual(response.status_code,202,response.text)
        jid=response.json()['job']['job_id']
        self.assertTrue(run_once(db,self.settings))
        def poll(): return self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        waiting=poll()
        self.assertEqual(waiting['status'],'NEEDS_INPUT',waiting)
        plan=waiting['progress']['plan']
        self.assertEqual([s['id'] for s in plan],['t1.check','t1.sql','t1.chart','answer'])
        self.assertEqual(plan[0]['status'],'WAITING')
        self.assertEqual((waiting['progress']['done'],waiting['progress']['total']),(0,4))
        body={'clarification_id':waiting['clarification']['clarification_id'],'choice_ids':['COUNTRY']}
        answer=self.post(f'chat/jobs/{jid}/clarifications',body)
        self.assertEqual(answer.status_code,202,answer.text)
        self.assertEqual(answer.json()['job']['progress']['total'],4)
        self.assertEqual(self.post(f'chat/jobs/{jid}/clarifications',body).json()['job']['status'],'QUEUED')
        self.assertTrue(run_once(db,self.settings))
        done=poll()
        self.assertEqual(done['progress']['done'],done['progress']['total'])
        self.assertTrue(all(s['status']=='DONE' for s in done['progress']['plan']))
        self.assertEqual(done['status'],'SUCCEEDED',done)
        self.assertEqual(done['answer']['answer_status'],'ANSWERED')
        self.assertEqual(done['answer']['tables'][0]['rows'][0]['country_code'],'CN')
        table=done['answer']['tables'][0]
        self.assertNotIn('hs_item',[c['key'] for c in table['columns']])
        messages=self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']
        history=next(m['job'] for m in messages if m.get('job'))['clarification_history']
        self.assertEqual([(h['message'],h['answer']) for h in history],[(waiting['clarification']['message'],'국가별')])
        exported=self.client.get(f"/api/v1/results/{table['result_id']}/file")
        self.assertEqual(exported.status_code,200)
        from urllib.parse import unquote
        from openpyxl import load_workbook
        self.assertRegex(unquote(exported.headers['content-disposition']),r'2025-10~2025-12_전체국가_수출_\d{8}_\d{6}\.xlsx$')
        wb=load_workbook(io.BytesIO(exported.content),read_only=True)
        self.assertNotIn('품목',next(wb['결과표1'].values))
        wb.close()
        self.assertEqual(done['answer']['charts'][0]['labels'][0],'중국')
        self.assertEqual(done['answer']['verification']['status'],'PASS')
        self.assertIsNone(db.redis.chat_active(sid))

    def test_agent_report_needs_explicit_approval_and_ready_before_completion(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from backend.app.agents.state import PlannerOutput
        from unittest.mock import Mock
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        db=self.app.state.database
        db.agent_checkpointer=InMemorySaver()
        db.agent_client=Mock()
        db.agent_client.parse.return_value=PlannerOutput(tasks=[{'intent':'REPORT','question':'수출 리포트 생성'}])
        self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        response=self.post(f'chat/sessions/{sid}/messages',{'question':'수출 리포트 생성','client_message_id':'report'})
        jid=response.json()['job']['job_id']
        def poll(): return self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        self.assertTrue(run_once(db,self.settings))
        waiting=poll(); self.assertEqual(waiting['status'],'NEEDS_INPUT',waiting)
        prompt=waiting['clarification']; cid=prompt['clarification_id']
        self.assertTrue(prompt['allow_text']); self.assertIn('expires_at',prompt)  # 화면의 만료 시각 표시(API-37)
        url=f'chat/jobs/{jid}/clarifications'
        self.assertEqual(self.post(url,{'clarification_id':cid,'text':'네'}).json()['job']['status'],'NEEDS_INPUT')
        confirmation={'preview_id':prompt['report_preview']['preview_id'],'confirm':True,'formats':['DOCX']}
        self.assertEqual(self.post(url,{'clarification_id':cid,'report_confirmation':confirmation}).status_code,202)
        self.assertTrue(run_once(db,self.settings))
        self.assertEqual(poll()['status'],'WAITING_REPORT')
        self.settings.values['API_CHAT_AGENT_ENABLED']='false'
        # File generation has its own worker tests. Simulate its durable completion signal.
        with self.engine.begin() as connection:
            table=self.meta.tables['report']
            rid=connection.scalar(select(table.c.report_id))
            connection.execute(update(table).where(table.c.report_id==rid).values(status='READY'))
        self.assertTrue(run_once(db,self.settings))
        done=poll(); self.assertEqual(done['status'],'SUCCEEDED',done)
        self.assertEqual(done['answer']['report_id'],str(rid))
        self.assertEqual(db.agent_client.parse.call_count,1)

    def test_agent_report_uses_approved_result_after_source_expires(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from backend.app.agents.state import PlannerOutput
        from backend.app.api import reports
        from unittest.mock import Mock
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        db=self.app.state.database; db.agent_checkpointer=InMemorySaver(); db.agent_client=Mock()
        db.agent_client.parse.return_value=PlannerOutput(tasks=[{'intent':'STATISTICS','question':'수출액 조회'},
            {'intent':'REPORT','question':'조회 결과로 리포트','source_tasks':[1]}])
        uid=self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        response=self.post(f'chat/sessions/{sid}/messages',{'question':'수출액 조회 후 리포트','client_message_id':'freeze'})
        jid=response.json()['job']['job_id']; run_once(db,self.settings)
        job=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        self.assertEqual(job['status'],'NEEDS_INPUT',job)
        prompt=job['clarification']; preview=prompt['report_preview']; source=preview['source_results'][0]['result_id']
        with self.engine.begin() as conn:
            snapshot=self.meta.tables['api_snapshot']
            conn.execute(update(snapshot).where(snapshot.c.snapshot_id==source).values(expires_at=now()-timedelta(hours=1)))
        accepted=self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':prompt['clarification_id'],
            'report_confirmation':{'confirm':True,'formats':['PDF'],'preview_id':preview['preview_id']}})
        self.assertEqual(accepted.status_code,202,accepted.text)
        run_once(db,self.settings)
        job=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        self.assertEqual(job['status'],'WAITING_REPORT',job)
        with self.engine.begin() as conn:
            store=Store(db,conn); record=store.one(select(store.t('report')))
            with patch('backend.app.api.trade.aggregate',side_effect=AssertionError('unexpected requery')):
                document=reports.build_document(store,uid,record)
        self.assertEqual(document['sections'][1]['tables'][0]['result_id'],source)
        self.assertIn('포함되지 않음',document['sections'][-1]['text'])

    def test_agent_expired_clarification_unblocks_session_and_old_id_cannot_resume(self):
        from langgraph.checkpoint.memory import InMemorySaver
        from backend.app.agents.state import PlannerOutput
        from unittest.mock import Mock
        self.settings.values['API_CHAT_AGENT_ENABLED']='true'
        db=self.app.state.database; db.agent_checkpointer=InMemorySaver(); db.agent_client=Mock()
        db.agent_client.parse.return_value=PlannerOutput(tasks=[{'intent':'RANKING','question':'순위'}])
        self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        path=f'chat/sessions/{sid}/messages'
        jid=self.post(path,{'question':'순위','client_message_id':'old'}).json()['job']['job_id']
        run_once(db,self.settings)
        pending=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['clarification']
        db.agent_checkpointer.delete_thread(jid)
        response=self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':pending['clarification_id'],'text':'국가'})
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['code'],'CLARIFICATION_EXPIRED')
        self.assertEqual(self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['status'],'EXPIRED')
        fresh=self.post(path,{'question':'다음 질문','client_message_id':'new'})
        self.assertEqual(fresh.status_code,202,fresh.text)
        self.assertNotEqual(fresh.json()['job']['job_id'],jid)

    def test_chat_idempotency_ownership_and_worker(self):
        self.signup()
        r=self.post('chat/sessions',{}); self.assertEqual(r.status_code,201,r.text); sid=r.json()['session']['session_id']
        body={'question':'2025년 12월 전체 품목 수출 추이','client_message_id':'message-1'}
        r=self.post(f'chat/sessions/{sid}/messages',body); self.assertEqual(r.status_code,202,r.text); job=r.json()['job']['job_id']
        self.assertEqual(self.post(f'chat/sessions/{sid}/messages',body).json()['job']['job_id'],job)
        run_once(self.app.state.database,self.settings)
        r=self.client.get('/api/v1/chat/jobs/'+job); self.assertEqual(r.json()['job']['status'],'SUCCEEDED',r.text)
        self.assertEqual(len(self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']),2)
        self.signup('other@example.org'); self.assertEqual(self.client.get('/api/v1/chat/jobs/'+job).status_code,404)

    def chat_answer(self, question, key):
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        response=self.post(f'chat/sessions/{sid}/messages',{'question':question,'client_message_id':key})
        self.assertEqual(response.status_code,202,response.text)
        jid=response.json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        job=self.client.get('/api/v1/chat/jobs/'+jid).json()['job']
        self.assertEqual(job['status'],'SUCCEEDED',job)
        return job['answer']

    def seed_review_articles(self):
        days=[date(2024,12,31),date(2025,1,1),date(2025,1,31),date(2025,2,1),date(2025,10,1),date(2025,12,15)]
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article'].insert(),[
                {'article_id':i,'source_code':'KOTRA_MARKET','external_id':str(i),'title':'검수 기사',
                 'published_date':day,'source_url':f'https://example.org/news/{i}','body_masked':'검수 본문',
                 'category':'경제·무역','visibility_status':'VISIBLE','admin_meta_json':{}}
                for i,day in enumerate(days,1)])

    def test_clarification_resumes_without_job_table_or_duplicate_question(self):
        self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'2025년 12월 화장품 수출','client_message_id':'null-check'}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        job=self.client.get('/api/v1/chat/jobs/'+jid).json()['job']
        self.assertEqual(job['status'],'NEEDS_INPUT')
        with self.engine.begin() as c:
            c.execute(text("""CREATE TRIGGER enforce_message_result BEFORE UPDATE OF result_json ON chat_message
                WHEN NEW.result_json IS NOT NULL AND json_type(NEW.result_json) != 'object'
                BEGIN SELECT RAISE(ABORT, 'message_result_object'); END"""))
        response=self.post(f'chat/jobs/{jid}/clarifications',{
            'clarification_id':job['clarification']['clarification_id'],'choice_ids':['3304']})
        self.assertEqual(response.status_code,202,response.text)
        with self.engine.connect() as c:
            self.assertNotIn('async_job',inspect(c).get_table_names())
            t=self.meta.tables['chat_message']
            self.assertEqual(len(c.execute(select(t).where(t.c.session_id==int(sid))).all()),1)
        run_once(self.app.state.database,self.settings)
        job=self.client.get('/api/v1/chat/jobs/'+jid).json()['job']
        self.assertEqual(job['status'],'SUCCEEDED',job)
        self.assertEqual(job['answer']['conditions']['hs_items'],[{'hs_code':'3304','hs_ver':'HS2022'}])
        self.assertEqual(len(self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']),2)

    def test_chat_articles_respect_period_boundaries(self):
        self.signup(); self.seed_review_articles()
        answer=self.chat_answer('2025년 1월 전체 품목 기사','article-period')
        self.assertEqual(answer['conditions']['period'],{'start':'2025-01-01','end':'2025-01-31'})
        self.assertEqual([a['published_date'] for a in answer['articles']],['2025-01-31','2025-01-01'])

    def test_chat_articles_without_trade_data(self):
        self.signup(); self.seed_review_articles()
        with self.engine.begin() as c:
            for name in ('export_summary','import_summary','batch_run'):
                c.execute(delete(self.meta.tables[name]))
        cases=[('2025년 1월 전체 품목 기사','2025-01-01',['2025-01-31','2025-01-01']),
               ('전체 품목 기사','2025-10-01',['2025-12-15','2025-10-01']),
               ('최근 2개월 전체 품목 기사','2025-11-01',['2025-12-15'])]
        for i,(question,start,days) in enumerate(cases):
            with self.subTest(question=question):
                answer=self.chat_answer(question,f'news-only-{i}')
                self.assertEqual(answer['intent'],'EXPLAIN')
                self.assertEqual(answer['conditions']['period']['start'],start)
                self.assertEqual([a['published_date'] for a in answer['articles']],days)

    def seed_quarterly_subscription(self, uid, baseline='2025-05'):
        with self.engine.begin() as c:
            for code,kind in [('QUARTERLY','REPORT_PERIOD'),('RPT_QUARTERLY','REPORT_TYPE')]:
                c.execute(self.meta.tables['service_option'].insert(),{'option_code':code,'option_type':kind,
                    'label_ko':'분기','enabled':True,'sort_order':1,'value_json':{}})
            c.execute(self.meta.tables['report_subscription'].insert(),{'user_id':int(uid),'period_code':'QUARTERLY','enabled':True,
                'filters_json':{'enabled_since':('2025-07-20T00:00:00Z' if baseline=='2025-06' else '2025-05-01T00:00:00Z'),'last_observed_month':baseline,
                                'hs_items':[],'countries':['US'],'formats':['PDF']}})
            for name in ('export_summary','import_summary'):
                t=self.meta.tables[name]
                c.execute(delete(t).where(t.c.trade_month>date(2025,10,1)))
                c.execute(update(self.meta.tables['batch_run']).where(self.meta.tables['batch_run'].c.target_table==name).values(period_end=date(2025,10,31)))

    def test_quarterly_reports_catch_up_once_after_later_month(self):
        from backend.app.api.subscriptions import enqueue_due
        uid=self.signup(); self.seed_quarterly_subscription(uid)
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(enqueue_due(store),2)
            self.assertEqual(enqueue_due(store),0)
            records=store.rows(select(store.t('report')).order_by(store.t('report').c.report_id))
            self.assertEqual([r['conditions_json']['period'] for r in records],[
                {'start':'2025-04-01','end':'2025-06-30'}, {'start':'2025-07-01','end':'2025-09-30'}])
            self.assertEqual(records[1]['conditions_json']['compare_period'],{'start':'2024-07-01','end':'2024-09-30'})
            self.assertTrue(all(jobs.report_execution(store,r)['status']=='QUEUED' for r in records))

    def test_quarterly_reports_wait_for_all_three_months(self):
        from backend.app.api.subscriptions import enqueue_due
        uid=self.signup(); self.seed_quarterly_subscription(uid)
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            for name in ('export_summary','import_summary'):
                t=self.meta.tables[name]
                c.execute(delete(t).where(t.c.trade_month==date(2025,8,1)))
                c.execute(self.meta.tables['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':name,
                    'period_start':date(2025,8,1),'period_end':date(2025,8,31),'status':'FAILED','detail_json':{}})
            self.assertEqual(enqueue_due(store),1)
            self.assertEqual(enqueue_due(store),0)
            for name in ('export_summary','import_summary'):
                c.execute(self.meta.tables[name].insert(),{'trade_month':date(2025,8,1),
                    'country_code':'US','hs6_code':'330499','hs_ver':'HS2022','amount_usd':100})
                c.execute(self.meta.tables['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':name,
                    'period_start':date(2025,8,1),'period_end':date(2025,8,31),'status':'SUCCESS','detail_json':{}})
            self.assertEqual(enqueue_due(store),1)
            self.assertEqual(enqueue_due(store),0)

    def test_quarterly_reports_do_not_backfill_before_subscription_date(self):
        from backend.app.api.subscriptions import enqueue_due
        uid=self.signup(); self.seed_quarterly_subscription(uid,baseline='2025-06')
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(enqueue_due(store),1)
            record=store.one(select(store.t('report')))
            self.assertEqual(record['conditions_json']['period'],{'start':'2025-07-01','end':'2025-09-30'})

    def test_xlsx_formula_and_limit(self):
        from openpyxl import load_workbook
        t={'total_rows':1,'columns':[{'key':'text','label':'텍스트'}],'rows':[{'text':'=1+1'}]}
        wb=load_workbook(io.BytesIO(xlsx_bytes([t],{},['SELECT 1'])))
        self.assertEqual(wb['결과표1']['A2'].data_type,'s'); self.assertIn('정보',wb.sheetnames)
        from backend.app.api.common import ApiError
        t['total_rows']=1001
        with self.assertRaises(ApiError): xlsx_bytes([t],{},[])

    def test_un_comparisons_separate_valuation_and_missing_observations(self):
        self.signup(); t=self.meta.tables['un_data']
        base={'country_code':'US','hs6_code':'330499','hs_ver':'HS2022','freq_code':'M'}
        with self.engine.begin() as c:
            c.execute(t.insert(),[
                {**base,'period_start':date(2024,1,1),'valuation_basis':'FOB','total_import_usd':100,'korea_import_usd':10,'korea_share_pct':10},
                {**base,'period_start':date(2025,1,1),'valuation_basis':'CIF','total_import_usd':200,'korea_import_usd':20,'korea_share_pct':10}])
        response=self.post('analysis/import-market-shares',{'hs6_code':'330499','hs_ver':'HS2022','country_codes':['US'],'freq_code':'M','period_start':'2025-01-01','period_end':'2025-02-01'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(response.json()['items'][0]['market_yoy_pct'])
        self.assertEqual(len(response.json()['items']),1)
        self.assertTrue(any('2025-02-01' in w for w in response.json()['warnings']))

    def test_hs_search_finds_six_digit_items_without_joining_word_fragments(self):
        url='/api/v1/reference/hs-codes'
        table=self.meta.tables['hs_code']
        with self.engine.begin() as c:
            c.execute(table.insert(),[
                {'hs_code':'220890','depth':6,'name_ko':'기타 증류주','hs_ver':'HS2022','source_edition':'test'},
                {'hs_code':'2208904000','depth':10,'name_ko':'소주','hs_ver':'HS2022','source_edition':'test'},
                {'hs_code':'2009','depth':4,'name_ko':'채소 주스','hs_ver':'HS2022','source_edition':'test'},
                {'hs_code':'854232','depth':6,'name_ko':'메모리 집적회로','hs_ver':'HS2022','source_edition':'test'}])
        for query in ('854232','메모리'):
            result=self.client.get(url,params={'q':query,'depth':6})
            self.assertEqual([r['hs_code'] for r in result.json()['items']],['854232'])
        self.assertEqual(self.client.get(url,params={'q':'소주'}).json()['items'],[])
        spirits=self.client.get(url,params={'q':'소주','depth':6}).json()['items']
        self.assertEqual([r['hs_code'] for r in spirits],['220890'])
        from backend.app.api.reference import chat_hs_search
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            result=chat_hs_search(store,'소주')
            self.assertNotEqual(result['match_type'],'EXACT')
            c.execute(table.insert().values(hs_code='8542',depth=4,name_ko='전자 집적 회로',hs_ver='HS2022',source_edition='test'))
            self.assertIn('8542',[r['hs_code'] for r in chat_hs_search(store,'집적회로')['items']])
        # HS4 이름은 띄어쓰기를 합쳐 맞추지 않지만, 하위 HS6 이름에 있는 말이면 부모 HS4를 후보로 낸다
        for query in ('집적회로','메모리'):
            found=self.client.get(url,params={'q':query,'depth':4}).json()
            self.assertEqual([r['hs_code'] for r in found['items']],['8542'])
            self.assertEqual(found['match_type'],'SIMILAR')
        self.assertEqual(self.client.get(url,params={'q':'없는품목','depth':4}).json()['items'],[])

    def test_cancel_legacy_duplicates_and_resubscribe(self):
        uid=int(self.signup())
        headers={'X-CSRF-Token':self.csrf}
        url='/api/v1/users/me/subscriptions'
        table=self.meta.tables['report_subscription']
        with self.engine.begin() as c:
            for _ in range(2):
                c.execute(table.insert().values(user_id=uid,period_code='MONTHLY',enabled=True,filters_json={}))
        before=self.client.get(url).json()
        first=before['items'][0]['subscription_id']
        self.assertEqual(self.client.delete(f'{url}/{first}',headers=headers).status_code,204)
        cancelled=self.client.get(url).json()
        self.assertFalse(any(row['enabled'] for row in cancelled['items']))
        version=self.client.get('/api/v1/users/me/interests').json()['interests']['version']
        self.client.put('/api/v1/users/me/interests',headers=headers,json={'hs_items':[],'country_codes':['US'],'expected_version':version})
        body={'period_codes':['MONTHLY'],'formats':['DOCX'],'use_saved_interests':True,'expected_version':cancelled['version']}
        saved=self.client.put(url,json=body,headers=headers)
        self.assertEqual(saved.status_code,200,saved.text)
        active=[row for row in saved.json()['items'] if row['enabled']]
        self.assertEqual([(r['subscription_id'],r['formats']) for r in active],[(first,['DOCX'])])
        self.assertEqual(self.client.put(url,json=body,headers=headers).status_code,409)

    def test_retrying_consent_preserves_history_after_revocation(self):
        self.signup()
        url='users/me/consents'
        body={'terms_type':'NOTIFICATION','terms_version':'TERMS_V1','action':'AGREE','enable_notifications':True}
        for action in ('AGREE','AGREE','REVOKE','AGREE'):
            response=self.post(url,{**body,'action':action,'enable_notifications':action=='AGREE'})
            self.assertEqual(response.status_code,200,response.text)
        table=self.meta.tables['user_consent']
        with self.engine.connect() as c:
            self.assertEqual(list(c.scalars(select(table.c.action).order_by(table.c.consent_id))),['AGREE','REVOKE','AGREE'])

    def test_subscription_validation_and_publication_trigger(self):
        uid=self.signup(); url='/api/v1/users/me/subscriptions'
        version=self.client.get(url).json()['version']
        body={'period_codes':['MONTHLY'],'formats':['PDF'],'use_saved_interests':True,'expected_version':version}
        self.assertEqual(self.client.put(url,json=body,headers={'X-CSRF-Token':self.csrf}).status_code,409)
        interest=self.client.get('/api/v1/users/me/interests').json()['interests']
        self.client.put('/api/v1/users/me/interests',json={'hs_items':[],'country_codes':['US'],'expected_version':interest['version']},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(self.client.put(url,json={**body,'use_saved_interests':False},headers={'X-CSRF-Token':self.csrf}).status_code,400)
        self.assertEqual(self.client.put(url,json={**body,'period_codes':[]},headers={'X-CSRF-Token':self.csrf}).status_code,400)
        with patch('backend.app.api.reports.now',return_value=datetime(2026,1,1)):
            response=self.client.put(url,json=body,headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,200,response.text)
        from backend.app.api.subscriptions import enqueue_due
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(enqueue_due(store,datetime(2026,1,14)),0)  # 발행일 이전에는 생성하지 않는다.
            c.execute(self.meta.tables['service_option'].insert(),{'option_code':'RPT_MONTHLY','option_type':'REPORT_TYPE','label_ko':'월간','enabled':True,'sort_order':1,'value_json':{}})
            for target in ('export_summary','import_summary'):
                c.execute(self.meta.tables[target].insert(),{'trade_month':date(2026,1,1),'hs6_code':'330499','hs_ver':'HS2022','country_code':'US','amount_usd':1500000})
                c.execute(self.meta.tables['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':target,'period_start':date(2026,1,1),'period_end':date(2026,1,31),'status':'SUCCESS','detail_json':{'schema_version':1}})
            self.assertEqual(enqueue_due(store,datetime(2026,1,15)),1)  # 가입 전 준비된 12월도 예정일에 발행한다.
            self.assertEqual(enqueue_due(store,datetime(2026,2,14)),0)
            self.assertEqual(enqueue_due(store,datetime(2026,2,15)),1)
            self.assertEqual(enqueue_due(store,datetime(2026,2,15)),0)
        self.assertFalse(self.client.get('/api/v1/users/me/notification-settings').json()['settings']['notifications_enabled'])

    def test_articles_visibility_and_admin_audit(self):
        uid=self.signup(); m=self.meta.tables
        with self.engine.begin() as c:
            c.execute(m['article'].insert(),{'article_id':1,'source_code':'KOTRA_MARKET','external_id':'test-news','title':'화장품 무역 정책',
                'published_date':date(2025,12,1),'source_url':'https://example.org/news/1','body_masked':'검증용 기사 내용','category':'통상·규제',
                'visibility_status':'VISIBLE','admin_meta_json':{'schema_version':1,'visibility':{},'country_tags_manual':False,'hs_tags_manual':False,'classification_input':None,'classification_state':{'status':'PENDING','revision':0}}})
        response=self.client.get('/api/v1/articles?tab=MAJOR_ISSUE'); self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['items']),1)
        with self.engine.begin() as c: c.execute(update(m['users']).where(m['users'].c.user_id==int(uid)).values(role='ADMIN'))
        detail=self.client.get('/api/v1/admin/articles/1'); self.assertEqual(detail.status_code,200,detail.text)
        response=self.client.patch('/api/v1/admin/articles/1/visibility',json={'visibility':'EXCLUDED','confirmed':True,'reason':'검수 중','expected_version':detail.json()['version']},headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.get('/api/v1/articles/1').status_code,404)
        self.assertEqual(self.client.get('/api/v1/admin/audit-events').json()['page']['total'],1)
        self.assertEqual(self.client.get('/api/v1/admin/notification-events/999/diagnosis?user_id='+uid).status_code,404)

    def test_product_ranking_includes_hs_name_and_formatted_excel(self):
        self.signup()
        response=self.client.get('/api/v1/trade/rankings?period_start=2025-12&period_end=2025-12&group_by=HS6')
        self.assertEqual(response.status_code,200,response.text)
        item=response.json()['items'][0]
        self.assertEqual(item['hs_item']['hs_code'],'330499')
        self.assertTrue(item['hs_name'])
        workbook=self.client.get('/api/v1/results/'+response.json()['result_id']+'/file')
        self.assertEqual(workbook.status_code,200,workbook.text)
        from openpyxl import load_workbook
        sheet=load_workbook(io.BytesIO(workbook.content))['결과표1']
        headers=[c.value for c in sheet[1]]
        self.assertIn('수출액 (USD)',headers); self.assertIn('품목명',headers)
        self.assertEqual(sheet.cell(2,headers.index('품목')+1).value,'330499')
        self.assertEqual(sheet.cell(2,headers.index('수출액 (USD)')+1).number_format,'#,##0')
        countries=self.client.get('/api/v1/trade/rankings?period_start=2025-12&period_end=2025-12').json()
        self.assertIsNone(countries['items'][0]['hs_name'])
        table=self.client.get('/api/v1/results/'+countries['result_id']).json()['table']
        self.assertNotIn('hs_name',[c['key'] for c in table['columns']])

    def test_admin_member_search_follows_selected_field(self):
        uid=self.signup(); m=self.meta.tables
        with self.engine.begin() as c:
            c.execute(update(m['users']).where(m['users'].c.user_id==int(uid)).values(role='ADMIN',name='검색이름'))
            email=c.execute(select(m['users'].c.email).where(m['users'].c.user_id==int(uid))).scalar()
        part=email.split('@')[0]
        found=lambda q,by: self.client.get('/api/v1/admin/users',params={'q':q,'by':by}).json()['page']['total']
        self.assertEqual(found(part,'id'),1)
        self.assertEqual(found(part,'name'),0)
        self.assertEqual(found('검색이름','name'),1)
        self.assertEqual(found('검색이름','id'),0)
        self.assertEqual(self.client.get('/api/v1/admin/users',params={'q':part,'by':'other'}).status_code,400)

    def test_admin_runs_search_and_status_filter(self):
        uid=self.signup(); m=self.meta.tables
        found=lambda **p: self.client.get('/api/v1/admin/data/runs',params=p).json()['page']['total']
        with self.engine.begin() as c:
            c.execute(update(m['users']).where(m['users'].c.user_id==int(uid)).values(role='ADMIN'))
        base,failed=found(),found(status='FAILED')
        with self.engine.begin() as c:
            for job,status in (('HS6_AGGREGATION','SUCCESS'),('HS6_AGGREGATION','FAILED'),('ECOS_RATE','FAILED')):
                c.execute(m['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':job,'target_table':'hs_code_correlation',
                    'period_start':date(2025,8,1),'period_end':date(2025,8,31),'status':status,'detail_json':{}})
        self.assertEqual(found(),base+3)
        self.assertEqual(found(status='FAILED'),failed+2)
        self.assertEqual(found(q='ecos'),1)
        self.assertEqual(found(q='HS6',status='FAILED'),1)
        # 숫자처럼 보이는 유니코드 문자나 범위를 넘는 번호도 500 없이 0건으로 끝난다.
        for q in ('²','１２','99999999999999999999999','1'*4301,'0'*30+'7','9223372036854775808'):
            response=self.client.get('/api/v1/admin/data/runs',params={'q':q})
            self.assertEqual(response.status_code,200,response.text)

    def test_admin_audit_search_and_sort(self):
        from datetime import datetime
        uid=self.signup(); m=self.meta.tables
        with self.engine.begin() as c:
            c.execute(update(m['users']).where(m['users'].c.user_id==int(uid)).values(role='ADMIN'))
            email=c.execute(select(m['users'].c.email).where(m['users'].c.user_id==int(uid))).scalar()
            for i,(action,target,reason) in enumerate((('USER_ROLE','USER','권한 부여 검수'),('ARTICLE_VISIBILITY','ARTICLE','정정 확인 전 숨김'))):
                c.execute(m['api_audit_event'].insert(),{'audit_id':f'audit_t{i}','actor_id':int(uid),'action':action,'target_type':target,
                    'target_id':str(100+i),'reason':reason,'before_json':{},'after_json':{},'created_at':datetime(2026,1,1+i)})
        get=lambda **p: self.client.get('/api/v1/admin/audit-events',params=p).json()
        self.assertEqual([x['audit_id'] for x in get()['items']][:2],['audit_t1','audit_t0'])
        self.assertEqual([x['audit_id'] for x in get(sort='oldest')['items']][-2:],['audit_t0','audit_t1'])
        self.assertEqual([x['audit_id'] for x in get(q='정정')['items']],['audit_t1'])
        self.assertEqual(get(q=email.split('@')[0])['page']['total'],2)
        self.assertEqual(get(q='정정')['items'][0]['actor_email'],email)
        # 화면에 보이는 한글 작업 이름(띄어쓰기 무시)으로도 찾는다.
        self.assertEqual([x['audit_id'] for x in get(q='기사 노출 변경')['items']],['audit_t1'])
        self.assertEqual([x['audit_id'] for x in get(q='권한변경')['items']],['audit_t0'])
        self.assertEqual(self.client.get('/api/v1/admin/audit-events',params={'sort':'other'}).status_code,400)

    def test_admin_alerts_follow_category_tab(self):
        uid=self.signup(); m=self.meta.tables
        with self.engine.begin() as c:
            c.execute(update(m['users']).where(m['users'].c.user_id==int(uid)).values(role='ADMIN'))
            c.execute(m['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':'export_summary',
                'period_start':date(2025,8,1),'period_end':date(2025,8,31),'status':'FAILED','detail_json':{}})
        for category,count in (('ALL',1),('DATA',1),('REPORT',0),('MEMBER',0)):
            response=self.client.get('/api/v1/admin/alerts?category='+category)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len(response.json()['items']),count,category)
            self.assertEqual(response.json()['page']['total'],count,category)
        self.assertEqual(self.client.get('/api/v1/admin/alerts?category=OTHER').status_code,400)

    def test_worker_fencing_and_duplicate_message_after_context_change(self):
        uid=self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        body={'question':'2025년 12월 전체 품목 수출 추이','client_message_id':'same'}
        job=self.post(f'chat/sessions/{sid}/messages',body).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        self.assertEqual(self.post(f'chat/sessions/{sid}/messages',body).json()['job']['job_id'],job)
        sid2=self.post('chat/sessions',{}).json()['session']['session_id']
        self.assertEqual(self.post(f'chat/sessions/{sid2}/messages',body).status_code,409)
        from backend.app.api.common import ApiError
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            old=jobs.owned_job(store,int(uid),job)
            with self.assertRaises(ApiError): jobs.finish(store,{**old,'claim_token':0},{})

    def test_chat_claim_recovery_and_stale_worker_rejection(self):
        from backend.app.api import chat
        from backend.app.api.common import ApiError
        uid=self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        self.post(f'chat/sessions/{sid}/messages',{'question':'2025년 12월 전체 품목 수출','client_message_id':'recover-chat'})
        with self.engine.begin() as c: old=jobs.claim(Store(self.app.state.database,c))
        with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=11)):
            with self.engine.begin() as c: recovered=jobs.claim(Store(self.app.state.database,c))
        self.assertEqual(old['job_id'],recovered['job_id'])
        self.assertGreater(recovered['claim_token'],old['claim_token'])
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            with self.assertRaises(ApiError): jobs.finish(store,old,{})
            result=chat.execute(store,recovered); jobs.finish(store,recovered,result)
        self.assertFalse(run_once(self.app.state.database,self.settings))
        self.assertEqual(len(self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']),2)

    def test_deleted_pending_chat_is_not_executed(self):
        self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'전체 품목 수출','client_message_id':'delete-queued'}).json()['job']['job_id']
        self.assertEqual(self.client.delete('/api/v1/chat/sessions/'+sid,headers={'X-CSRF-Token':self.csrf}).status_code,204)
        self.assertFalse(run_once(self.app.state.database,self.settings))
        self.assertEqual(self.client.get('/api/v1/chat/jobs/'+jid).status_code,404)

    def queued_report(self, key):
        preview=self.post('reports/preview',{'conditions':{'period':{'start':'2025-12-01','end':'2025-12-31'}}})
        self.assertEqual(preview.status_code,200,preview.text)
        response=self.post('reports',{'preview_id':preview.json()['preview_id'],'confirm_conditions':True,
            'sections':['KPI'],'formats':['PDF']},**{'Idempotency-Key':key})
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['job']['report_id']

    def test_report_retry_round_and_restart_without_job_table(self):
        from backend.app.api.database import Database
        self.signup(); rid=self.queued_report('retry-report')
        with patch('backend.app.api.worker.reports.generate',side_effect=OSError('test failure')):
            self.assertTrue(run_once(self.app.state.database,self.settings))
            self.assertTrue(run_once(self.app.state.database,self.settings))
        status=self.client.get(f'/api/v1/reports/{rid}/status').json()['job']
        self.assertEqual(status['status'],'DELAYED'); self.assertEqual(status['retry_count'],1)
        self.assertEqual(status['error']['code'],'JOB_FAILED')
        restarted=Database(self.engine)
        self.assertFalse(run_once(restarted,self.settings))
        with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=6)):
            self.assertTrue(run_once(restarted,self.settings))
        status=self.client.get(f'/api/v1/reports/{rid}/status').json()['job']
        self.assertEqual(status['status'],'READY'); self.assertEqual(status['retry_count'],2)
        self.assertIsNone(status['error'])
        self.assertFalse(run_once(restarted,self.settings))

    def test_report_expired_claim_recovers_after_restart(self):
        from backend.app.api.database import Database
        self.signup(); rid=self.queued_report('recover-report')
        with self.engine.begin() as c: old=jobs.claim(Store(self.app.state.database,c))
        restarted=Database(self.engine)
        with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=11)):
            self.assertTrue(run_once(restarted,self.settings))
        status=self.client.get(f'/api/v1/reports/{rid}/status').json()['job']
        self.assertEqual(status['status'],'READY'); self.assertEqual(status['retry_count'],1)
        with self.engine.begin() as c:
            from backend.app.api.common import ApiError
            with self.assertRaises(ApiError): jobs.finish(Store(restarted,c),old,{})

    def test_report_recovers_when_db_commit_fails_after_file_log(self):
        from backend.app.api import reports
        self.signup(); rid=self.queued_report('rollback-report')
        with self.engine.begin() as c: job=jobs.claim(Store(self.app.state.database,c))
        with self.assertRaisesRegex(RuntimeError,'simulated rollback'):
            with self.engine.begin() as c:
                store=Store(self.app.state.database,c)
                result=reports.generate(store,job,self.settings)
                jobs.finish(store,job,result)
                raise RuntimeError('simulated rollback')
        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/status').json()['job']['status'],'RUNNING')
        with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=11)):
            self.assertTrue(run_once(self.app.state.database,self.settings))
        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/status').json()['job']['status'],'READY')
        self.assertFalse(run_once(self.app.state.database,self.settings))
        with self.engine.connect() as c:
            record=c.execute(select(self.meta.tables['report']).where(self.meta.tables['report'].c.report_id==int(rid))).mappings().one()
            self.assertEqual(record['content_json']['_execution']['status'],'SUCCEEDED')

    def test_older_report_is_not_starved_by_chat(self):
        self.signup(); rid=self.queued_report('older-report')
        with self.engine.begin() as c:
            t=self.meta.tables['report']
            c.execute(update(t).where(t.c.report_id==int(rid)).values(created_at=now()-timedelta(minutes=1)))
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        self.post(f'chat/sessions/{sid}/messages',{'question':'전체 품목 수출','client_message_id':'later-chat'})
        with self.engine.begin() as c:
            job=jobs.claim(Store(self.app.state.database,c))
            self.assertEqual(job['job_type'],'API_REPORT')
            self.assertEqual(str(job['job_id']),rid)

    def test_report_preview_conflicts_and_ml_gate(self):
        self.signup(); conditions={'period':{'start':'2025-12-01','end':'2025-12-31'}}
        self.assertEqual(self.post('reports/preview',{'conditions':conditions,'result_id':'bad'}).status_code,400)
        self.assertEqual(self.post('reports/preview',{'conditions':conditions,'period_preset':'LAST_1M'}).status_code,503)
        self.assertEqual(self.post('reports/preview',{'conditions':{**conditions,'compare_period':conditions['period']}}).status_code,400)
        self.assertEqual(self.post('analysis/promising-markets',{'conditions':conditions}).status_code,422)
        self.assertEqual(self.post('analysis/market-patterns',{'conditions':conditions}).status_code,422)

    def test_report_preview_accepts_korea_option_through_http_validation(self):
        self.signup()
        for selected in (True,False):
            response=self.post('reports/preview',{'conditions':{
                'period':{'start':'2025-01-01','end':'2025-03-31'},'compare':'NONE',
                'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],
                'country_codes':['US','CN'],'include_korea_total':selected}})
            self.assertEqual(response.status_code,200,response.text)
            conditions=response.json()['conditions']
            self.assertEqual(bool(conditions.get('include_korea_total')),selected)
            self.assertEqual(conditions['country_codes'],['US','CN'])
        rejected=self.post('reports/preview',{'conditions':{
            'period':{'start':'2025-01-01','end':'2025-03-31'},'unknown_option':True}})
        self.assertEqual(rejected.status_code,400)

    def test_chat_delete_cleans_results_and_blocks_job_read(self):
        self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'2025년 12월 전체 품목 수출 추이','client_message_id':'del-1'}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        result=self.client.get('/api/v1/chat/jobs/'+jid).json()['job']['answer']['tables'][0]['result_id']
        self.assertEqual(self.client.delete('/api/v1/chat/sessions/'+sid,headers={'X-CSRF-Token':self.csrf}).status_code,204)
        self.assertEqual(self.client.get('/api/v1/results/'+result).status_code,404)
        self.assertEqual(self.client.get('/api/v1/chat/jobs/'+jid).status_code,404)

    def test_saved_interests_require_confirmation(self):
        self.signup(); scope=self.client.get('/api/v1/users/me/interests').json()['interests']
        self.client.put('/api/v1/users/me/interests',json={'hs_items':[],'country_codes':['US'],'expected_version':scope['version']},headers={'X-CSRF-Token':self.csrf})
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'수출 추이를 알려줘','client_message_id':'confirm-1'}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        job=self.client.get('/api/v1/chat/jobs/'+jid).json()['job']; self.assertEqual(job['status'],'NEEDS_INPUT')
        response=self.post(f'chat/jobs/{jid}/clarifications',{'clarification_id':job['clarification']['clarification_id'],'choice_ids':['APPLY']})
        self.assertEqual(response.status_code,202,response.text)
        run_once(self.app.state.database,self.settings)
        answer=self.client.get('/api/v1/chat/jobs/'+jid).json()['job']['answer']
        self.assertEqual(answer['conditions']['country_codes'],['US'])

    def test_delivery_is_idempotent_and_respects_notification_consent(self):
        uid=self.signup(); m=self.meta.tables
        from backend.app.api.event_delivery import deliver_event
        with self.engine.begin() as c:
            c.execute(m['alert_event'].insert(),{'event_id':1,'event_type':'MAJOR_ISSUE','event_key':'b'*64,'payload_json':{},'created_at':now()})
            self.assertEqual(deliver_event(Store(self.app.state.database,c),1),0)
        self.post('users/me/consents',{'terms_type':'NOTIFICATION','terms_version':'TERMS_V1','action':'AGREE','enable_notifications':True})
        with self.engine.begin() as c:
            self.assertEqual(deliver_event(Store(self.app.state.database,c),1),1)
            self.assertEqual(deliver_event(Store(self.app.state.database,c),1),0)

    def test_rankings_mark_selected_country_without_rows(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['country'].insert(),{'country_code':'JP','name_ko':'일본','country_role':'COUNTRY'})
        response=self.client.get('/api/v1/trade/rankings?period_start=2025-12&period_end=2025-12&country_codes=US&country_codes=JP')
        self.assertEqual(response.status_code,200,response.text)
        items={item['country_code']:item for item in response.json()['items']}
        self.assertTrue(items['US']['has_data'])
        self.assertFalse(items['JP']['has_data'])
        self.assertEqual(float(items['JP']['export_amount_usd']),0)
        result=self.client.get('/api/v1/results/'+response.json()['result_id']).json()['table']
        self.assertNotIn('has_data',[column['key'] for column in result['columns']])
        # 엑셀·결과표에서는 실적 없는 행의 금액을 0이 아니라 빈 값으로 둔다(화면의 '—'와 같게).
        row={r['country_code']:r for r in result['rows']}['JP']
        self.assertIsNone(row['export_amount_usd']); self.assertIsNone(row['import_amount_usd'])
        self.assertIsNone(row['rank']); self.assertIsNone(row['share_pct'])

    def test_rankings_return_applied_interest_scope(self):
        # 관심 범위 조회는 최신 관심 품목·국가를 실제 적용 조건으로 돌려준다(파일명·AI 질문이 이 값을 쓴다).
        self.signup(); url='/api/v1/users/me/interests'
        version=self.client.get(url).json()['interests']['version']
        body={'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],'country_codes':['US'],'expected_version':version,'landing_page':'INTERESTED'}
        self.assertEqual(self.client.put(url,json=body,headers={'X-CSRF-Token':self.csrf}).status_code,200)
        response=self.client.get('/api/v1/trade/rankings?scope=INTERESTED&group_by=HS6&period_start=2025-12&period_end=2025-12')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['conditions'],{'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],'country_codes':['US']})
        # 월별 추이도 관심 범위에서 상위 코드(3304)를 주면 실제 적용된 관심 HS6를 돌려준다.
        response=self.client.get('/api/v1/trade/trends?scope=INTERESTED&hs_code=3304&hs_ver=HS2022&country_codes=US&period_start=2025-12&period_end=2025-12')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['conditions']['hs_items'],[{'hs_code':'330499','hs_ver':'HS2022'}])

    def test_excel_usd_keeps_cents(self):
        from openpyxl import load_workbook
        from backend.app.api.files import xlsx_bytes
        table={'columns':[{'key':'amount_usd','label':'금액','unit':'USD'}],'rows':[{'amount_usd':1234.56},{'amount_usd':1230000}],'total_rows':2}
        ws=load_workbook(io.BytesIO(xlsx_bytes([table],{},[]))).active
        self.assertEqual((ws['A2'].value,ws['A2'].number_format),(1234.56,'#,##0.00'))
        self.assertEqual(ws['A3'].number_format,'#,##0')

    def test_rankings_put_rows_with_data_before_empty_rows(self):
        self.signup(); m=self.meta.tables
        with self.engine.begin() as c:
            for code,name in (('AE','아랍에미리트'),('VN','베트남')):
                c.execute(m['country'].insert(),{'country_code':code,'name_ko':name,'country_role':'COUNTRY'})
            c.execute(m['import_summary'].insert(),{'trade_month':date(2025,12,1),'hs6_code':'330499','hs_ver':'HS2022','country_code':'VN','amount_usd':1230000})
        # 수출 기준: VN은 수입만 있고 AE는 실적이 없다. 금액(수출 0)이 같아도 실적 있는 VN이 AE보다 앞에 와야 한다.
        response=self.client.get('/api/v1/trade/rankings?period_start=2025-12&period_end=2025-12&country_codes=AE&country_codes=VN&sort_flow=EXPORT&page_size=1')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([i['country_code'] for i in response.json()['items']],['VN'])
        self.assertTrue(response.json()['items'][0]['has_data'])

    def test_rankings_and_changes_use_saved_measurements(self):
        self.signup()
        response=self.client.get('/api/v1/trade/rankings?period_start=2025-12&period_end=2025-12&page_size=5')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['items'][0]['country_code'],'CN')
        self.assertEqual(response.json()['items'][0]['one_off']['status'],'NOT_EVALUATED')
        result=self.client.get('/api/v1/results/'+response.json()['result_id']).json()['table']
        self.assertEqual(result['rows'],[{k:v for k,v in item.items() if k not in ('has_data','hs_name')} for item in response.json()['items']])
        self.assertEqual(response.json()['items'][0]['rank_status'],'SAME')
        response=self.post('analysis/changes',{'month':'2025-12','compare':'YOY'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['surges'],[])
        self.assertEqual(response.json()['drops'],[])
        self.assertEqual(self.client.get('/api/v1/trade/fast-growing-products?month=2025-12').status_code,200)

    def test_future_terms_do_not_invalidate_current_consent(self):
        self.signup()
        self.assertIsNone(self.client.get('/api/v1/users/me/notification-settings').json()['settings']['terms_version'])
        self.post('users/me/consents',{'terms_type':'NOTIFICATION','terms_version':'TERMS_V1','action':'AGREE','enable_notifications':True})
        with self.engine.begin() as c:
            c.execute(self.meta.tables['service_option'].insert(),{'option_code':'TERMS_FUTURE','option_type':'NOTIFICATION_TERMS','label_ko':'미래 약관','enabled':True,'sort_order':100,'value_json':{'body':'미래 시행 약관','effective_at':'2099-01-01T00:00:00Z'}})
        settings=self.client.get('/api/v1/users/me/notification-settings').json()['settings']
        self.assertTrue(settings['consent_current'])
        self.assertEqual(settings['terms_version'],'TERMS_V1')
        self.assertEqual(self.client.get('/api/v1/terms/notification').json()['terms']['version'],'TERMS_V1')

    def test_rate_limit_and_missing_schema(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/api/v1/auth/login',json={'email':'none@example.org','password':'fail'}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'email':'none@example.org','password':'fail'}).status_code,429)
        self.meta.tables['api_snapshot'].drop(self.engine)
        if 'api_snapshot' in self.app.state.database.metadata.tables:
            self.app.state.database.metadata.remove(self.app.state.database.metadata.tables['api_snapshot'])
        self.assertEqual(self.client.get('/health/ready').status_code,503)


    def test_sparse_verified_trade_returns_zero_across_apis(self):
        self.signup()
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article'].insert(),{'article_id':1,'source_code':'KOTRA_MARKET','external_id':'sparse-news','title':'기사',
                'published_date':date(2025,12,1),'source_url':'https://example.org/1','body_masked':'본문','category':'경제','visibility_status':'VISIBLE','admin_meta_json':{}})
            c.execute(delete(self.meta.tables['import_summary']).where(self.meta.tables['import_summary'].c.country_code=='US'))
            c.execute(delete(self.meta.tables['export_summary']).where(self.meta.tables['export_summary'].c.country_code=='US',self.meta.tables['export_summary'].c.trade_month==date(2025,12,1)))
        trend=self.client.get('/api/v1/trade/trends?period_start=2025-11&period_end=2025-12&country_codes=US')
        self.assertEqual(trend.status_code,200,trend.text)
        self.assertEqual(trend.json()['items'][-1]['export_amount_usd'],0)
        self.assertEqual(trend.json()['items'][0]['import_amount_usd'],0)
        for path in ('trade/rankings?period_start=2025-11&period_end=2025-12','trade/rankings/US/products?month=2025-11'):
            response=self.client.get('/api/v1/'+path)
            self.assertEqual(response.status_code,200,response.text)
        response=self.post('analysis/local-issues',{'conditions':{'period':{'start':'2025-12-01','end':'2025-12-31'},'country_codes':['US']}})
        self.assertEqual(response.status_code,200,response.text)
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'2025년 12월 미국 전체 품목 수출 추이','client_message_id':'zero'}).json()['job']['job_id']
        run_once(self.app.state.database,self.settings)
        self.assertEqual(self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['answer']['answer_status'],'ANSWERED')

    def test_wholly_empty_verified_month_and_request_cache(self):
        from backend.app.api import trade
        from sqlalchemy import event
        self.signup()
        with self.engine.begin() as c:
            for name in ('export_summary','import_summary'):
                c.execute(delete(self.meta.tables[name]).where(self.meta.tables[name].c.trade_month==date(2025,12,1)))
        response=self.client.get('/api/v1/trade/trends?period_start=2025-12&period_end=2025-12')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['items'][0]['export_amount_usd'],0)
        queries=[]
        def record(conn,cursor,statement,params,context,many): queries.append(statement)
        with self.engine.connect() as c:
            store=Store(self.app.state.database,c)
            trade.bounds(store)
            event.listen(self.engine,'before_cursor_execute',record)
            try:
                for _ in range(6): trade.bounds(store); trade.verified_months(store,'export_summary')
                self.assertEqual(queries,[])
            finally: event.remove(self.engine,'before_cursor_execute',record)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION','target_table':'export_summary','period_start':date(2025,12,1),'period_end':date(2025,12,31),'status':'FAILED','detail_json':{}})
        self.assertNotEqual(self.client.get('/api/v1/trade/trends?period_start=2025-12&period_end=2025-12').status_code,200)

    def test_worker_retries_loop_after_database_failure(self):
        from backend.app.api.worker import run_loop
        from sqlalchemy.exc import OperationalError
        with patch('backend.app.api.subscriptions.enqueue_due',side_effect=[OperationalError('query',{},Exception()),0]) as schedule, \
             patch('backend.app.api.worker.run_once',return_value=False) as run, \
             patch('backend.app.api.worker.time.monotonic',side_effect=[0,0,5,5]), \
             patch('backend.app.api.worker.time.sleep',side_effect=[None,KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt): run_loop(self.app.state.database,self.settings)
        self.assertEqual(schedule.call_count,2); self.assertEqual(run.call_count,2)

    def test_scheduler_failure_does_not_block_real_chat_job(self):
        from backend.app.api.worker import run_loop
        self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'2025년 12월 전체 품목 수출','client_message_id':'scheduler-failure'}).json()['job']['job_id']
        with patch('backend.app.api.subscriptions.enqueue_due',side_effect=RuntimeError('scheduler failure')) as schedule, \
             patch('backend.app.api.worker.time.monotonic',side_effect=[0,0,1]), \
             patch('backend.app.api.worker.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): run_loop(self.app.state.database,self.settings)
        self.assertEqual(schedule.call_count,1)  # 5초 재시도 시각 전에는 예약만 건너뛴다.
        self.assertEqual(self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']['status'],'SUCCEEDED')

    def test_worker_continues_during_persistent_scheduler_failure(self):
        from backend.app.api.worker import run_loop
        with patch('backend.app.api.subscriptions.enqueue_due',side_effect=RuntimeError('scheduler failure')) as schedule, \
             patch('backend.app.api.worker.run_once',return_value=False) as run, \
             patch('backend.app.api.worker.time.monotonic',side_effect=[0,0,5,5,10,10]), \
             patch('backend.app.api.worker.time.sleep',side_effect=[None,None,KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt): run_loop(self.app.state.database,self.settings)
        self.assertEqual(schedule.call_count,3); self.assertEqual(run.call_count,3)

    def test_worker_once_processes_queue_and_reports_scheduler_failure(self):
        from backend.app.api.worker import run_loop
        with patch('backend.app.api.subscriptions.enqueue_due',side_effect=RuntimeError('scheduler failure')), \
             patch('backend.app.api.worker.run_once',return_value=False) as run:
            with self.assertRaisesRegex(RuntimeError,'scheduler failure'):
                run_loop(self.app.state.database,self.settings,once=True)
        run.assert_called_once()

    def test_worker_recovers_from_queue_failure_independently(self):
        from backend.app.api.worker import run_loop
        with patch('backend.app.api.subscriptions.enqueue_due',return_value=0) as schedule, \
             patch('backend.app.api.worker.run_once',side_effect=[OSError('DB failure'),False]) as run, \
             patch('backend.app.api.worker.time.monotonic',side_effect=[0,0,5]), \
             patch('backend.app.api.worker.time.sleep',side_effect=[None,KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt): run_loop(self.app.state.database,self.settings)
        self.assertEqual(schedule.call_count,1); self.assertEqual(run.call_count,2)

    def test_worker_connection_failure_before_store_does_not_mask_error(self):
        from contextlib import contextmanager
        self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'전체 품목 수출','client_message_id':'db-failure'}).json()['job']['job_id']
        original=self.engine.begin; count=0
        @contextmanager
        def flaky():
            nonlocal count
            count+=1
            if count==3: raise OSError('temporary connection failure')  # 1: wake_ready 검사, 2: claim, 3: 실행 시작
            with original() as c: yield c
        with patch.object(self.engine,'begin',side_effect=flaky):
            self.assertTrue(run_once(self.app.state.database,self.settings))
        state=self.client.get(f'/api/v1/chat/jobs/{jid}').json()['job']
        self.assertEqual(state['status'],'FAILED'); self.assertEqual(state['error']['code'],'JOB_FAILED')

    def test_not_found_inside_existing_job_is_terminal(self):
        from backend.app.api.common import ApiError
        self.signup(); rid=self.queued_report('missing-reference')
        with patch('backend.app.api.worker.reports.generate',side_effect=ApiError(404,'NOT_FOUND','자료 없음')):
            self.assertTrue(run_once(self.app.state.database,self.settings))
        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/status').json()['job']['status'],'FAILED')
        with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=11)):
            self.assertFalse(run_once(self.app.state.database,self.settings))

    def test_report_retry_budget_and_separate_execution_directories(self):
        from backend.app.api.database import Database
        self.signup(); rid=self.queued_report('bounded-retry')
        other=Settings(values={**self.settings.values,'API_REPORT_DIRECTORY':self.directory.name+'/worker'})
        db=Database(self.engine)
        with patch('backend.app.api.worker.reports.generate',side_effect=OSError('permanent failure')):
            run_once(db,other); run_once(db,other)
            with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=6)): run_once(db,other)
        state=self.client.get(f'/api/v1/reports/{rid}/status').json()['job']
        self.assertEqual(state['status'],'FAILED'); self.assertEqual(state['retry_count'],2)
        self.assertFalse(state['error']['retryable'])
        with patch('backend.app.api.jobs.now',return_value=now()+timedelta(days=1)):
            self.assertFalse(run_once(db,other))

    def test_queued_report_without_file_or_execution_json_is_claimed(self):
        self.signup(); rid=self.queued_report('legacy-queued')
        from sqlalchemy import null
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['report']).where(self.meta.tables['report'].c.report_id==int(rid)).values(content_json=null()))
        self.assertTrue(run_once(self.app.state.database,self.settings))
        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/status').json()['job']['status'],'READY')

    def test_worker_claims_new_chat_immediately_after_history(self):
        uid=self.signup(); sid=self.post('chat/sessions',{}).json()['session']['session_id']
        with self.engine.begin() as c:
            c.execute(self.meta.tables['chat_message'].insert(),[{'session_id':int(sid),'role':'USER','content_masked':'old','created_at':now(),'result_json':{}} for _ in range(1000)])
        jid=self.post(f'chat/sessions/{sid}/messages',{'question':'전체 품목 수출','client_message_id':'after-history'}).json()['job']['job_id']
        self.app.state.database.job_scan_size=100
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(str(jobs.claim(store)['job_id']),jid)
        sid2=self.post('chat/sessions',{}).json()['session']['session_id']
        jid2=self.post(f'chat/sessions/{sid2}/messages',{'question':'전체 품목 수출','client_message_id':'after-first-claim'}).json()['job']['job_id']
        with self.engine.begin() as c:
            self.assertEqual(str(jobs.claim(Store(self.app.state.database,c))['job_id']),jid2)

    def test_worker_claims_report_past_history_and_future_retries(self):
        self.signup(); rid=self.queued_report('history-report')
        t=self.meta.tables['report']
        with self.engine.begin() as c:
            original=dict(c.execute(select(t).where(t.c.report_id==int(rid))).mappings().one())
            original.pop('report_id')
            c.execute(update(t).where(t.c.report_id==int(rid)).values(status='READY'))
            records=[]
            for i in range(1000):
                state={**original['content_json']['_execution'],'available_at':(now()+timedelta(days=1)).isoformat()}
                key=f'review-{i:058}'
                state['generation_key']=key
                records.append({**original,'generation_key':key,'status':'READY' if i<900 else 'DELAYED','content_json':{'_execution':state}})
            c.execute(t.insert(),records)
        ready=self.queued_report('ready-after-history')
        self.app.state.database.job_scan_size=5
        with self.engine.begin() as c:
            job=jobs.claim(Store(self.app.state.database,c))
            self.assertEqual(job['job_type'],'API_REPORT'); self.assertEqual(str(job['job_id']),ready)

    def test_worker_rotates_past_locked_candidates_and_revisits_old_jobs(self):
        self.signup(); ids=[]
        for i in range(3):
            sid=self.post('chat/sessions',{}).json()['session']['session_id']
            ids.append(self.post(f'chat/sessions/{sid}/messages',{'question':'전체 품목 수출','client_message_id':f'locked-{i}'}).json()['job']['job_id'])
        self.app.state.database.job_scan_size=2
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            with patch.object(store,'one',return_value=None): self.assertIsNone(jobs.claim(store))
            self.assertEqual(str(jobs.claim(store)['job_id']),ids[2])
            self.assertEqual(str(jobs.claim(store)['job_id']),ids[0])

    def test_worker_revisits_expired_jobs_before_new_arrivals(self):
        self.signup(); ids=[]
        for i in range(3):
            sid=self.post('chat/sessions',{}).json()['session']['session_id']
            ids.append(self.post(f'chat/sessions/{sid}/messages',{'question':'전체 품목 수출','client_message_id':f'expired-{i}'}).json()['job']['job_id'])
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(str(jobs.claim(store)['job_id']),ids[0])
            self.assertEqual(str(jobs.claim(store)['job_id']),ids[1])
            with patch('backend.app.api.jobs.now',return_value=now()+timedelta(minutes=11)):
                self.assertEqual(str(jobs.claim(store)['job_id']),ids[0])

    def test_proxy_trust_and_independent_account_limits(self):
        from starlette.requests import Request
        from backend.app.api.security import client_ip
        settings=Settings(values={'API_TRUSTED_PROXY_CIDRS':'10.0.0.0/8'})
        def request(peer,forwarded):
            return Request({'type':'http','method':'POST','path':'/','headers':[(b'x-forwarded-for',forwarded.encode())],'client':(peer,123)})
        self.assertEqual(client_ip(request('10.0.0.2','192.0.2.5, 10.0.0.1'),settings),'192.0.2.5')
        self.assertEqual(client_ip(request('198.51.100.4','192.0.2.5'),settings),'198.51.100.4')
        self.assertEqual(client_ip(request('10.0.0.2','bad-address'),settings),'10.0.0.2')
        self.assertEqual(client_ip(request('10.0.0.2','203.0.113.8, 192.0.2.5'),settings),'192.0.2.5')
        for n in range(12):
            self.assertEqual(self.client.post('/api/v1/auth/login',json={'email':f'user{n}@example.org','password':'fail'}).status_code,401)
        for _ in range(10):
            self.assertEqual(self.client.post('/api/v1/auth/login',json={'email':'same@example.org','password':'fail'}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'email':'same@example.org','password':'fail'}).status_code,429)

    def test_notification_sql_filter_count_and_page_queries(self):
        from backend.app.api import notifications
        from sqlalchemy import event
        uid=int(self.signup()); m=self.meta.tables
        with self.engine.begin() as c:
            for i in range(120):
                eid=c.execute(m['alert_event'].insert().values(event_type='MAJOR_ISSUE',event_key=f'{i:064}',payload_json={'target':{'kw_topic':'blocked' if i%2 else 'allowed'}},created_at=now())).inserted_primary_key[0]
                c.execute(m['user_alert'].insert().values(user_id=uid,event_id=eid,is_interest_match=True,created_at=now()))
            c.execute(update(m['notification_setting']).where(m['notification_setting'].c.user_id==uid).values(keyword_blocks_json=[{'kw_topic':'blocked','selected_terms':['blocked']}]))
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(notifications.unread_count(store,uid),60)
            queries=[]
            def record(conn,cursor,stmt,params,ctx,many): queries.append(stmt)
            event.listen(self.engine,'before_cursor_execute',record)
            try:
                self.assertEqual(notifications.unread_count(store,uid),60)
                self.assertEqual(len(queries),1)
                result=notifications.listing(store,uid,page=2,size=5)
                self.assertEqual(len(result['items']),5)
                self.assertTrue(any('LIMIT' in q.upper() for q in queries))
            finally: event.remove(self.engine,'before_cursor_execute',record)
            out=notifications.read_all(store,uid,'2099-01-01T00:00:00Z','ALL')
            self.assertEqual(out['updated_count'],60); self.assertEqual(out['unread_count'],0)

    def test_hs_exact_vector_fallback_and_database_verification(self):
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        with patch('backend.app.api.hs_vectors.candidates',return_value=[('9999',0.1),('3304',0.2)]) as search:
            self.assertEqual(self.client.get('/api/v1/reference/hs-codes?q=화장품').json()['match_type'],'EXACT')
            search.assert_not_called()
            result=self.client.get('/api/v1/reference/hs-codes?q=스킨케어')
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual([x['hs_code'] for x in result.json()['items']],['3304'])
        with patch('backend.app.api.hs_vectors.candidates',side_effect=RuntimeError('offline')) as search:
            # 품목 선택 검색은 등록명 부분 일치가 있으면 임베딩을 부르지 않는다. 부르지 않았으니 연결 경고도 없다.
            result=self.client.get('/api/v1/reference/hs-codes?q=화장')
            self.assertEqual(result.json()['items'][0]['hs_code'],'3304')
            self.assertNotIn('warnings',result.json())
            search.assert_not_called()
            # 등록명에 없는 말만 임베딩으로 찾고, 그 연결이 실패하면 경고를 붙인다(위 '스킨케어'는 캐시에 남아 다른 말을 쓴다).
            result=self.client.get('/api/v1/reference/hs-codes?q=보습크림')
            self.assertEqual(result.status_code,200,result.text)
            self.assertTrue(result.json()['warnings'])

    def test_notification_sql_visibility_matches_article_and_array_rules(self):
        from backend.app.api import notifications
        uid=int(self.signup()); m=self.meta.tables
        with self.engine.begin() as c:
            for i,visibility in [(1,'VISIBLE'),(2,'HIDDEN'),(3,'VISIBLE')]:
                c.execute(m['article'].insert(),{'article_id':i,'source_code':'KOTRA_MARKET','external_id':str(i),'title':'기사',
                    'published_date':date(2025,12,1),'source_url':'https://example.org/'+str(i),'body_masked':'본문','category':'경제',
                    'visibility_status':visibility,'admin_meta_json':{}})
            c.execute(m['keyword_topic'].insert(),{'keyword_text':'blocked','status':'UNCLASSIFIED'})
            c.execute(m['article_tag'].insert(),{'article_id':3,'keyword_text':'blocked'})
            c.execute(update(m['notification_setting']).where(m['notification_setting'].c.user_id==uid).values(keyword_blocks_json=[{'kw_topic':'blocked','selected_terms':['blocked']}]))
            for i,(article,ids) in enumerate([(1,[]),(2,[]),(3,[]),(None,[1,2]),(None,['1']), (None,[2,3])],1):
                c.execute(m['alert_event'].insert(),{'event_id':i,'event_type':'MAJOR_ISSUE','article_id':article,'event_key':f'{i:064}',
                    'payload_json':{'article_ids':ids},'created_at':now()})
                c.execute(m['user_alert'].insert(),{'user_id':uid,'event_id':i,'is_interest_match':True,'created_at':now()})
            store=Store(self.app.state.database,c)
            rows=notifications.notification_rows(store,uid)
            self.assertEqual({r['event_id'] for r in rows},{1,4,5})
            self.assertEqual(notifications.unread_count(store,uid),3)
            views=notifications.notification_views(store,rows,uid)
            self.assertTrue(all([a['article_id'] for a in x['issue']['articles']]==['1'] for x in views))

    def test_hs_vector_threshold_rejects_unrelated_results(self):
        self.settings.values['API_HS_VECTOR_ENABLED']='true'
        with patch('backend.app.api.hs_vectors.candidates',return_value=[('3304',0.99)]):
            result=self.client.get('/api/v1/reference/hs-codes?q=전혀다른검색어').json()
            self.assertEqual(result['match_type'],'NONE'); self.assertEqual(result['items'],[])

    def test_fx_loaded_rates_units_previous_business_day_and_empty_currency(self):
        from sqlalchemy import event
        with self.engine.begin() as c:
            c.execute(self.meta.tables['currency'].insert(),[
                {'currency_code':code,'name_ko':code,'quote_unit':unit} for code,unit in [('USD',1),('JPY',100),('EUR',1)]])
            c.execute(self.meta.tables['fx_daily'].insert(),[
                {'currency_code':code,'base_date':day,'rate_krw':rate} for code,day,rate in
                [('USD',date(2026,10,23),1300),('USD',date(2026,10,26),1313),('JPY',date(2026,10,23),900),('JPY',date(2026,10,26),891)]])
        response=self.client.get('/api/v1/exchange-rates?currency_codes=USD&currency_codes=JPY&currency_codes=EUR')
        self.assertEqual(response.status_code,200,response.text)
        usd,jpy,eur=response.json()['items']
        self.assertEqual(usd['previous_base_date'],'2026-10-23'); self.assertEqual(usd['change_pct'],1)
        self.assertEqual(jpy['quote_unit'],100); self.assertEqual(jpy['rate_krw'],891); self.assertEqual(jpy['change_pct'],-1)
        self.assertEqual(eur['availability'],'UNAVAILABLE'); self.assertIsNone(eur['rate_krw'])



    def test_verified_answer_stream_requires_completion_and_ownership(self):
        import json
        uid=self.signup()
        sid=self.post('chat/sessions',{}).json()['session']['session_id']
        with self.engine.begin() as connection:
            store=Store(self.app.state.database,connection)
            job=jobs.enqueue(store,uid,'API_CHAT',sid,{'question':'q'},'stream-test')
        path=f"/api/v1/chat/jobs/{job['job_id']}/answer-stream"
        self.assertEqual(self.client.get(path).status_code,409)
        with self.engine.begin() as connection:
            store=Store(self.app.state.database,connection)
            job.update(status='SUCCEEDED',result_json={'answer':{'text':'검증된 결과 123 USD입니다.'}})
            jobs._save_chat(store,job)
        response=self.client.get(path)
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('text/event-stream',response.headers['content-type'])
        text=''.join(json.loads(block.split('data: ',1)[1])['text'] for block in response.text.split('\n\n') if block.startswith('event: delta'))
        self.assertEqual(text,job['result_json']['answer']['text'])
        self.assertIn('event: done',response.text)
        self.signup('stream-other@example.org')
        self.assertEqual(self.client.get(path).status_code,404)


    def test_country_comparison_pivots_full_snapshots_and_exports_same_columns(self):
        from backend.app.api.results import result_table
        from backend.app.api.common import shift_month
        from openpyxl import load_workbook
        uid=self.signup();ids=[]
        period={'start':'2024-01-01','end':'2026-01-31'}
        with self.engine.begin() as connection:
            store=Store(self.app.state.database,connection)
            for code,name,count in [('CN','중국',25),('US','미국',24)]:
                rows=[{'country_code':code,'country_name':name,'hs_code':'330499','hs_ver':'HS2022',
                    'name':'화장품','month':shift_month(date(2024,1,1),i).strftime('%Y-%m'),
                    'amount_usd':100+i if code=='CN' else i} for i in range(count)]
                conditions={'period':period,'country_codes':[code],'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],
                    'trade_flow':'EXPORT','metric':'AMOUNT'}
                ids.append(result_table(store,uid,rows,{'kind':'TREND'},conditions)['result_id'])
        body={'result_ids':ids}
        response=self.post('results/comparison',body)
        self.assertEqual(response.status_code,200,response.text)
        table=response.json()['table']
        self.assertEqual([c['label'] for c in table['columns']],['HS 코드','품목','기준월','중국 수출액','미국 수출액'])
        self.assertEqual(table['total_rows'],25)  # Not the 20-row public preview.
        self.assertEqual(table['rows'][0]['country_1_amount_usd'],0)
        self.assertEqual(table['rows'][-1]['country_0_amount_usd'],124)
        self.assertIsNone(table['rows'][-1]['country_1_amount_usd'])
        exported=self.post('results/comparison',{**body,'format':'xlsx'})
        self.assertEqual(exported.status_code,200)
        wb=load_workbook(io.BytesIO(exported.content));sheet=wb['결과표1']
        self.assertEqual([cell.value for cell in sheet[1]],[c['label']+(f" ({c['unit']})" if c.get('unit') else '') for c in table['columns']])
        self.assertEqual(sheet.max_row,26)
        with self.engine.begin() as connection:
            store=Store(self.app.state.database,connection)
            duplicate_conditions={**conditions,'compare_period':{'start':'2023-01-01','end':'2023-12-31'}}
            duplicate=result_table(store,uid,rows,{'kind':'TREND'},duplicate_conditions)['result_id']
        combined=self.post('results/comparison',{'result_ids':ids+[duplicate]})
        self.assertEqual(combined.status_code,200,combined.text)
        self.assertEqual(combined.json()['table']['rows'],table['rows'])
        with self.engine.begin() as connection:
            snapshot=self.meta.tables['api_snapshot']
            record=connection.execute(select(snapshot).where(snapshot.c.snapshot_id==ids[1])).mappings().one()
            payload=dict(record['payload_json']);payload['conditions']={**payload['conditions'],'trade_flow':'IMPORT'}
            connection.execute(update(snapshot).where(snapshot.c.snapshot_id==ids[1]).values(payload_json=payload))
        self.assertEqual(self.post('results/comparison',body).status_code,400)
        self.signup('comparison-other@example.org')
        self.assertEqual(self.post('results/comparison',body).status_code,404)

if __name__=='__main__': unittest.main()
