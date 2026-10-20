"""CSV 초기화와 API 체크포인트 연결. SQLite 및 작은 검증 스냅샷 사용."""
import hashlib
import json
import unittest
from datetime import date
from unittest import mock
from backend.tests import test_un_data_refresh as fixtures
from backend.app.scheduler.jobs import un_data_refresh as job


class BootstrapTest(unittest.TestCase):
    setUp = fixtures.MonthlyRefreshTest.setUp
    tearDown = fixtures.MonthlyRefreshTest.tearDown
    snapshot = fixtures.MonthlyRefreshTest.snapshot

    def prepare(self):
        (self.state/'checkpoint.json').unlink()
        self.csv = self.path/'snapshot.csv'
        job.write_csv(self.csv, job.HEAD, [['M','2024-01-01','AL','010121','HS2022','100','25','25','CIF']])
        metadata = {'last_successful_check_date':'2024-02-01', 'source_run':'test_only',
                    'csv_sha256':hashlib.sha256(self.csv.read_bytes()).hexdigest(),
                    'scope_sha256':hashlib.sha256(self.scope.read_bytes()).hexdigest(), 'rows':1}
        patcher=mock.patch.object(job,'SNAPSHOT',metadata);patcher.start();self.addCleanup(patcher.stop)

    def initialize(self):
        return job.initialize_csv(self.engine,self.csv,self.scope,self.state,date(2024,2,2))

    def test_initial_load_and_api_window(self):
        self.prepare();self.initialize();self.assertEqual(len(self.snapshot()),1)
        client=fixtures.FakeClient([],[])
        job.run(self.engine,client,self.scope,self.state,date(2024,3,1))
        self.assertEqual({p['publishedDateFrom'] for _,p in client.calls},{'2024-01-30'})
        self.assertEqual({path.split('/')[2] for path,_ in client.calls},{'M','A'})
        self.assertEqual(json.loads((self.state/'checkpoint.json').read_text())['last_successful_check_date'],'2024-03-01')
        with self.assertRaisesRegex(ValueError,'이미 존재'):self.initialize()

    def test_verification_failure_rolls_back(self):
        self.prepare()
        with mock.patch.object(job,'verify',side_effect=ValueError('injected')):
            with self.assertRaises(ValueError):self.initialize()
        self.assertEqual(self.snapshot(),[])
        self.assertFalse((self.state/'checkpoint.json').exists())

    def test_checkpoint_write_failure_retry(self):
        self.prepare()
        with mock.patch.object(job,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.initialize()
        self.assertEqual(len(self.snapshot()),1)
        self.assertFalse((self.state/'checkpoint.json').exists())
        self.initialize();self.assertTrue((self.state/'checkpoint.json').exists())

    def test_changed_csv_and_scope_rejected(self):
        self.prepare();original=self.csv.read_bytes();self.csv.write_bytes(original+b'\n')
        with self.assertRaisesRegex(ValueError,'CSV 해시'):self.initialize()
        self.csv.write_bytes(original);self.scope.write_text('{}')
        with self.assertRaisesRegex(ValueError,'범위 해시'):self.initialize()
        self.assertEqual(self.snapshot(),[])

    def test_no_checkpoint_no_api_call(self):
        self.prepare();client=mock.Mock()
        with self.assertRaisesRegex(ValueError,'초기 체크포인트'):
            job.run(self.engine,client,self.scope,self.state,date(2024,3,1))
        client.get.assert_not_called()

    def test_different_db_content_not_overwritten(self):
        self.prepare();self.initialize();(self.state/'checkpoint.json').unlink()
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE un_data SET total_import_usd=200'))
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'전수 대조'):self.initialize()
        self.assertEqual(before,self.snapshot());self.assertFalse((self.state/'checkpoint.json').exists())
