"""발표일 수집/변환/교체/체크포인트 통합 검증. API는 모의 응답, DB는 SQLite."""
import json,tempfile,unittest,hashlib
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock
from sqlalchemy import Table,Column,String,select,event
from backend.app.models.base import Base
from backend.app.models.hs_code import HsCodeMst
from backend.app.models.un_data import UnData
from backend.app.db.session import make_engine,session_scope
from backend.app.db.crud import CRUD
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler.jobs import un_data_load as loader
from backend.app.scheduler.collectors.un_data import api
if 'country' not in Base.metadata.tables:
    Table('country',Base.metadata,Column('country_code',String(12),primary_key=True))
CFG={'start_year':2022,'reporters':{'M':['8'],'A':['40']},'products':['010121','010129'],
     'country_map':{'8':'AL','40':'AT'},'target_pairs':[['8','010121'],['8','010129'],['40','010121'],['40','010129']],
     'partners':[0,410],'flow':'M'}
def catalog(freq='M',period=202401,reporter=8,edition='H6',released='2024-02-01'):
    return dict(freqCode=freq,period=period,reporterCode=reporter,classificationCode=edition,
                isOriginalClassification=True,lastReleased=released+'T12:00:00')
def raw(freq='M',period='202401',reporter=8,hs='010121',partner=0,amount='100',edition='H6'):
    return dict(freqCode=freq,period=period,reporterCode=reporter,cmdCode=hs,classificationCode=edition,
                refYear=period[:4],refMonth=int(period[4:]) if freq=='M' else 52,
                refPeriodId=period+'01' if freq=='M' else period+'0101',typeCode='C',flowCode='M',
                partnerCode=partner,partner2Code=0,customsCode='C00',motCode=0,mosCode=0,aggrLevel=6,
                isLeaf=True,isOriginalClassification=True,classificationSearchCode='HS',primaryValue=amount,cifvalue=amount,fobvalue=None)
class FakeClient:
    api_key=None
    def __init__(self,datasets=None,rows=None):
        self.datasets=[catalog(),catalog('A',2023,40)] if datasets is None else datasets
        self.rows=[raw(),raw(partner=410,amount='25'),raw(hs='010129'),raw('A','2023',40)] if rows is None else rows
        self.calls=[];self.request_count=0
    def get(self,path,params,out):
        self.calls.append((path,params));self.request_count+=1;freq=path.split('/')[2]
        if path.startswith('getDa'):
            return [r for r in self.datasets if r['freqCode']==freq and str(r['period']) in params['period'].split(',')]
        return [r for r in self.rows if r['freqCode']==freq and r['period']==params['period'] and r['cmdCode']==params['cmdCode']
                and str(r['reporterCode']) in params['reporterCode'].split(',')]
class MonthlyRefreshTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.scope=self.path/'scope.json'
        self.scope.write_text(json.dumps(CFG));self.state=self.path/'state';self.engine=make_engine('sqlite://')
        @event.listens_for(self.engine,'connect')
        def fk(connection,record):connection.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        with session_scope(self.engine) as s:
            s.execute(Base.metadata.tables['country'].insert(),[{'country_code':'AL'},{'country_code':'AT'}])
            for h in CFG['products']:
                for v in ('HS2002','HS2022'):
                    CRUD(HsCodeMst).create(s,hs_code=h,hs_ver=v,name_ko='시험',original_name_ko='시험',source_edition='TEST')
        job.save(self.state/'checkpoint.json', {'last_successful_check_date':'2022-01-01', 'scope_sha256':hashlib.sha256(self.scope.read_bytes()).hexdigest(), 'target_sha256':hashlib.sha256(self.engine.url.render_as_string(hide_password=True).encode()).hexdigest()})
    def tearDown(self):self.engine.dispose();self.temp.cleanup()
    def run_job(self,client=None,today=date(2024,2,2)):
        return job.run(self.engine,client or FakeClient(),self.scope,self.state,today)
    def snapshot(self):
        with session_scope(self.engine) as s:return sorted(tuple(getattr(r,k) for k in loader.HEAD) for r in s.scalars(select(UnData)))
    def checkpoint(self):return (self.state/'checkpoint.json').read_bytes()
    def test_window_revision_deletion_and_idempotence(self):
        self.run_job();baseline=self.snapshot()
        client=FakeClient([catalog(released='2024-02-03')],[raw(amount='200'),raw(partner=410,amount='50')])
        self.run_job(client,date(2024,2,4))
        self.assertTrue(all(p['publishedDateFrom']=='2024-02-02' and p['publishedDateTo']=='2024-02-04' for path,p in client.calls if path.startswith('getDa')))
        self.assertEqual({p.split('/')[2] for p,_ in client.calls if p.startswith('getDa')},{'M','A'})
        now=self.snapshot();self.assertEqual(len(now),2)
        self.assertEqual([r for r in baseline if r[0]=='A'],[r for r in now if r[0]=='A'])
        self.assertEqual(next(r for r in now if r[0]=='M')[5:8],(Decimal('200'),Decimal('50'),Decimal('25')))
        self.run_job(client,date(2024,2,4));self.assertEqual(now,self.snapshot())
    def test_edition_change_removes_old_key(self):
        self.run_job();self.run_job(FakeClient([catalog(edition='H2',released='2024-02-03')],[raw(edition='H2')]),date(2024,2,4))
        self.assertEqual([r[4] for r in self.snapshot() if r[0]=='M'],['HS2002'])
    def test_filters(self):
        self.run_job();old=self.snapshot()
        client=FakeClient([catalog(released='2024-01-01'),catalog(reporter=999,released='2024-02-03')],[])
        self.run_job(client,date(2024,2,4));self.assertEqual(old,self.snapshot())
        self.assertFalse(any(not p.startswith('getDa') for p,_ in client.calls))
    def test_no_release_no_writes(self):
        statements=[]
        @event.listens_for(self.engine,'before_cursor_execute')
        def audit(c,cur,stmt,params,ctx,many):statements.append(stmt)
        self.run_job(FakeClient([],[]))
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for s in statements))
        self.assertEqual(json.loads(self.checkpoint())['last_successful_check_date'],'2024-02-02')
    def test_only_un_data_written(self):
        statements=[]
        @event.listens_for(self.engine,'before_cursor_execute')
        def audit(c,cur,stmt,params,ctx,many):
            if stmt.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):statements.append(stmt)
        self.run_job();self.assertTrue(statements);self.assertTrue(all('un_data' in s for s in statements))
        self.assertFalse(any('batch_run' in s or 'service_option' in s for s in statements))
    def test_api_failure_preserves_state(self):
        self.run_job();old=self.snapshot();checkpoint=self.checkpoint();client=FakeClient()
        with mock.patch.object(client,'get',side_effect=RuntimeError('HTTP 429')):
            with self.assertRaises(RuntimeError):self.run_job(client,date(2024,3,1))
        self.assertEqual(old,self.snapshot());self.assertEqual(checkpoint,self.checkpoint())
    def test_invalid_raw_preserves_state(self):
        self.run_job();old=self.snapshot();checkpoint=self.checkpoint()
        with self.assertRaises(ValueError):self.run_job(FakeClient([catalog(released='2024-02-03')],[raw(amount='-1')]),date(2024,2,4))
        self.assertEqual(old,self.snapshot());self.assertEqual(checkpoint,self.checkpoint())
    def test_sql_failure_rolls_back(self):
        self.run_job();old=self.snapshot();checkpoint=self.checkpoint()
        with mock.patch.object(loader,'verify',side_effect=ValueError('injected')):
            with self.assertRaises(ValueError):self.run_job(FakeClient([catalog(released='2024-02-03')],[raw(amount='200')]),date(2024,2,4))
        self.assertEqual(old,self.snapshot());self.assertEqual(checkpoint,self.checkpoint())
    def test_checkpoint_failure_replay(self):
        self.run_job();checkpoint=self.checkpoint();save=job.save
        def fail(path,obj):
            if Path(path).name=='checkpoint.json':raise OSError('disk full')
            save(path,obj)
        client=FakeClient([catalog(released='2024-02-03')],[raw(amount='200')])
        with mock.patch.object(job,'save',side_effect=fail):
            with self.assertRaises(OSError):self.run_job(client,date(2024,2,4))
        self.assertEqual(checkpoint,self.checkpoint());committed=self.snapshot()
        self.run_job(client,date(2024,2,4));self.assertEqual(committed,self.snapshot())
    def test_empty_dataset_blocked(self):
        self.run_job();old=self.snapshot();checkpoint=self.checkpoint()
        with self.assertRaisesRegex(ValueError,'전체가 빈'):self.run_job(FakeClient([catalog(released='2024-02-03')],[]),date(2024,2,4))
        self.assertEqual(old,self.snapshot());self.assertEqual(checkpoint,self.checkpoint())
    def test_scope_change_blocked(self):
        self.run_job();checkpoint=self.checkpoint();self.scope.write_text(json.dumps(CFG,indent=2))
        with self.assertRaisesRegex(ValueError,'범위 변경'):self.run_job()
        self.assertEqual(checkpoint,self.checkpoint())
    def test_cap_and_wrong_query(self):
        client=FakeClient()
        for response in [[raw()]*500,[raw(reporter=999)]]:
            with mock.patch.object(client,'get',return_value=response):
                with self.assertRaises(ValueError):api.collect(client,CFG,[catalog()],self.path)
    def test_monthly_trigger(self):
        from backend.app.scheduler import main as scheduler
        trigger=scheduler.build_scheduler().get_job('un_data_monthly').trigger
        self.assertEqual(trigger.get_next_fire_time(None,datetime(2026,10,15,tzinfo=job.KST)).isoformat(),'2026-11-01T03:00:00+09:00')
    def test_http_retry_and_key_not_saved(self):
        session=mock.Mock();session.get.side_effect=[mock.Mock(status_code=503),mock.Mock(status_code=200,text='{"count":0,"data":[]}')]
        client=api.Client('secret-test-value',session,interval=0);out=self.path/'payload.json'
        self.assertEqual(client.get('getDa/C/A/HS',{},out),[]);self.assertEqual(client.request_count,2)
        self.assertNotIn('secret-test-value',out.read_text())
        self.assertEqual(session.get.call_args.kwargs['headers']['Ocp-Apim-Subscription-Key'],'secret-test-value')
    def test_periods_start_at_2022_and_use_both_frequencies(self):
        client=FakeClient([],[]);self.run_job(client)
        calls=[(path,params) for path,params in client.calls if path.startswith('getDa')]
        self.assertTrue(all(int(period[:4])>=2022 for _,params in calls for period in params['period'].split(',')))
        self.assertEqual({path.split('/')[2] for path,_ in calls},{'M','A'})
        self.assertTrue(all(params['publishedDateFrom']=='2022-01-01' for _,params in calls))
    def test_same_state_concurrent_lock_blocked(self):
        from filelock import FileLock,Timeout
        before=self.checkpoint()
        with FileLock(str(self.state/'refresh.lock'),timeout=0):
            with self.assertRaises(Timeout):self.run_job()
        self.assertEqual(before,self.checkpoint())
    def test_non_target_product_preserved_in_same_dataset(self):
        self.run_job()
        with session_scope(self.engine) as s:
            CRUD(HsCodeMst).create(s,hs_code='999999',hs_ver='HS2022',name_ko='시험',original_name_ko='시험',source_edition='TEST')
            original=dict(zip(loader.HEAD,next(r for r in self.snapshot() if r[0]=='M')))
            original['hs6_code']='999999';CRUD(UnData).upsert_many(s,[original])
        self.run_job(FakeClient([catalog(released='2024-02-03')],[raw(amount='200')]),date(2024,2,4))
        self.assertTrue(any(r[3]=='999999' for r in self.snapshot()))
if __name__=='__main__':unittest.main()
