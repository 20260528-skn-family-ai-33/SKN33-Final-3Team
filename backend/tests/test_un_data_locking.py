import unittest
from unittest import mock
from backend.app.scheduler.collectors.un_data.locking import Guard, db_lock
from backend.tests import test_un_data_refresh as fixtures
from backend.app.scheduler.jobs import un_data_refresh as job
from datetime import date


class LockingTest(unittest.TestCase):
    def test_owner_change_is_rejected(self):
        connection=mock.Mock(closed=False,invalidated=False)
        connection.execute.return_value.one.return_value=(10,11)
        with self.assertRaisesRegex(RuntimeError,'소유권 유실'):
            Guard(mock.Mock(),connection,10).check()

    def test_invalidated_connection_never_reconnects(self):
        connection=mock.Mock(closed=False,invalidated=True)
        with self.assertRaisesRegex(RuntimeError,'자동 재연결 금지'):
            Guard(mock.Mock(),connection,10).check()
        connection.execute.assert_not_called()

    def test_cleanup_does_not_replace_original_failure(self):
        engine=mock.Mock();engine.dialect.name='mysql'
        connection=engine.connect.return_value
        connection.closed=False;connection.invalidated=False
        connection.scalar.side_effect=[10,1,OSError('release failed')]
        connection.execute.return_value.one.return_value=(10,10)
        with self.assertRaisesRegex(ValueError,'original'):
            with db_lock(engine):raise ValueError('original')
        connection.invalidate.assert_called_once()

    def test_release_failure_is_reported_without_original_failure(self):
        engine=mock.Mock();engine.dialect.name='mysql'
        connection=engine.connect.return_value
        connection.closed=False;connection.invalidated=False
        connection.scalar.side_effect=[10,1,OSError('release failed')]
        connection.execute.return_value.one.return_value=(10,10)
        with self.assertRaisesRegex(OSError,'release failed'):
            with db_lock(engine):pass


class FailureRecordingTest(unittest.TestCase):
    setUp=fixtures.MonthlyRefreshTest.setUp
    tearDown=fixtures.MonthlyRefreshTest.tearDown

    def test_manifest_failure_keeps_api_error(self):
        client=fixtures.FakeClient()
        save=job.save
        def failed_manifest(path,obj):
            if obj.get('status')=='FAILED_OR_CHECKPOINT_PENDING':raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(client,'get',side_effect=ValueError('original API error')), mock.patch.object(job,'save',side_effect=failed_manifest):
            with self.assertRaisesRegex(ValueError,'original API error'):
                job.run(self.engine,client,self.scope,self.state,date(2024,2,2))
