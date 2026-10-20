"""발표일 경계, DB 별칭 식별, 상태 백업·복원을 검증한다. 실제 API/운영 DB는 사용하지 않는다."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from filelock import FileLock, Timeout
from sqlalchemy.engine import make_url
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.collectors.un_data import api, identity
from backend.app.scheduler.jobs import un_data_state as state


class PublicationBoundaryTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown

    def test_previous_utc_day_is_included_and_repeat_skips(self):
        row=fixture.catalog(period=202609)
        row['lastReleased']='2026-10-31T20:00:00Z'
        cp=json.loads((self.state/'checkpoint.json').read_text())
        cp['last_successful_check_date']='2026-11-01'
        fixture.job.save(self.state/'checkpoint.json',cp)
        raw=[fixture.raw(period='202609')]
        first=fixture.job.run(self.engine,fixture.FakeClient([row],raw),self.scope,self.state,date(2026,11,2))
        self.assertEqual(first['succeeded'],1)
        client=fixture.FakeClient([row],raw)
        second=fixture.job.run(self.engine,client,self.scope,self.state,date(2026,11,2))
        self.assertEqual(second['skipped'],1)
        self.assertFalse(any(not path.startswith('getDa') for path,_ in client.calls))
        self.assertEqual(json.loads((self.state/'checkpoint.json').read_text())['last_successful_check_date'],'2026-11-02')

    def test_database_binding_survives_checkpoint_advance(self):
        path=self.state/'checkpoint.json'
        cp=json.loads(path.read_text())
        binding={'target_sha256':cp['target_sha256'],
                 'database_identity':{'version':1,'server_uuid':'fixture','database':'fixture'}}
        with mock.patch.object(fixture.job,'bind_target',return_value=binding):
            fixture.job.run(self.engine,fixture.FakeClient([],[]),self.scope,self.state,date(2024,2,2))
        saved=json.loads(path.read_text())
        self.assertEqual(saved['database_identity'],binding['database_identity'])
        self.assertEqual(saved['target_sha256'],cp['target_sha256'])

    def test_year_boundary_and_outside_window(self):
        row=fixture.catalog(period=202412);row['lastReleased']='2024-12-31T20:00:00Z'
        old=dict(row,lastReleased='2024-12-29T20:00:00Z')
        client=fixture.FakeClient([row,old])
        result=api.discover(client,fixture.CFG,date(2025,1,1),date(2025,1,2),self.path)
        self.assertEqual(result,[row])
        self.assertTrue(all(p['publishedDateFrom']=='2024-12-30' for _,p in client.calls))

    def test_annual_overlap_and_lower_bound(self):
        row=fixture.catalog('A',2023,40);row['lastReleased']='2024-01-31T20:00:00Z'
        client=fixture.FakeClient([row])
        self.assertEqual(api.discover(client,fixture.CFG,date(2024,2,1),date(2024,2,2),self.path),[row])
        client=fixture.FakeClient([])
        api.discover(client,fixture.CFG,date(2022,1,1),date(2022,1,2),self.path)
        self.assertTrue(all(p['publishedDateFrom']=='2022-01-01' for _,p in client.calls))


class IdentityTest(unittest.TestCase):
    def engine(self, host='localhost', database='test'):
        engine=mock.Mock();engine.dialect.name='mysql'
        engine.url=make_url(f'mysql+pymysql://user@{host}/{database}')
        return engine

    def conn(self, server='server-a', database='test'):
        conn=mock.Mock();conn.execute.return_value.one.return_value=(server,database)
        return conn

    def test_alias_keeps_existing_pending_identity(self):
        engine=self.engine();cp=identity.bind_target(engine,connection=self.conn())
        result=identity.bind_target(self.engine('127.0.0.1'),cp,self.conn())
        self.assertEqual(result,cp)

    def test_other_server_and_database_rejected(self):
        cp=identity.bind_target(self.engine(),connection=self.conn())
        for conn in (self.conn('server-b'),self.conn(database='another')):
            with self.assertRaises(ValueError):identity.bind_target(self.engine(),cp,conn)

    def test_legacy_requires_original_url_then_binds(self):
        engine=self.engine();cp={'target_sha256':identity.url_hash(engine)}
        with self.assertRaises(ValueError):identity.bind_target(self.engine('127.0.0.1'),cp,self.conn())
        bound=identity.bind_target(engine,cp,self.conn())
        self.assertEqual(bound['target_sha256'],cp['target_sha256'])
        self.assertIn('database_identity',bound)

    def test_missing_database_or_query_error_fails_closed(self):
        with self.assertRaises(ValueError):identity.bind_target(self.engine(),connection=self.conn(database=None))
        conn=self.conn();conn.execute.side_effect=RuntimeError('connection lost')
        with self.assertRaises(RuntimeError):identity.bind_target(self.engine(),connection=conn)

    def test_sqlite_legacy_behavior(self):
        engine=self.engine();engine.dialect.name='sqlite'
        cp=identity.bind_target(engine)
        self.assertEqual(identity.bind_target(engine,cp),cp)
        engine.connect.assert_not_called()


class StateBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'runtime';self.source.mkdir()
        (self.source/'checkpoint.json').write_text('{"last_successful_check_date":"2026-10-15"}')
        (self.source/'continuation.json').write_text('{"status":"CANCELLED"}')
        (self.source/'recovery_fence.json').write_text('{"attempt":"cancelled"}')
        (self.source/'continuations').mkdir()
        (self.source/'continuations'/'cached.json').write_text('{"data":"한글"}',encoding='utf-8')
        self.backup=self.root/'backup';self.restored=self.root/'restored'

    def tearDown(self):self.temp.cleanup()

    def test_round_trip_preserves_cancel_and_cache(self):
        self.assertEqual(state.backup_state(self.source,self.backup),4)
        self.assertEqual(state.restore_state(self.backup,self.restored),4)
        self.assertEqual(state.inventory(self.source),state.inventory(self.restored))
        self.assertFalse((self.restored/'continuation.lock').exists())
        self.assertEqual(json.loads((self.restored/'continuation.json').read_text())['status'],'CANCELLED')

    def test_mutation_and_missing_file_rejected(self):
        state.backup_state(self.source,self.backup)
        f=self.backup/'continuation.json';f.write_text('{}')
        with self.assertRaises(ValueError):state.restore_state(self.backup,self.restored)
        f.unlink()
        with self.assertRaises(ValueError):state.restore_state(self.backup,self.restored)
        self.assertFalse(self.restored.exists())

    def test_running_process_blocks_backup(self):
        for name in ('refresh.lock','continuation.lock'):
            with FileLock(str(self.source/name)):
                with self.assertRaises(Timeout):state.backup_state(self.source,self.backup)
        self.assertFalse(self.backup.exists())

    def test_existing_destination_and_nested_destination_rejected(self):
        self.backup.mkdir()
        with self.assertRaises(ValueError):state.backup_state(self.source,self.backup)
        with self.assertRaises(ValueError):state.backup_state(self.source,self.source/'backup')
        self.assertTrue((self.source/'checkpoint.json').exists())

    def test_symlink_rejected(self):
        (self.source/'linked').symlink_to(self.source/'checkpoint.json')
        with self.assertRaises(ValueError):state.backup_state(self.source,self.backup)

    def test_partial_copy_not_published(self):
        with mock.patch.object(state.shutil,'copyfile',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):state.backup_state(self.source,self.backup)
        self.assertFalse(self.backup.exists())
