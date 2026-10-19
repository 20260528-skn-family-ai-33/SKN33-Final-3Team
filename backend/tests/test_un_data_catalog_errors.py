"""발표 목록 오류의 범위 격리·지속 보류·교정 후 복구를 검증합니다."""
import json
import unittest
from unittest import mock
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler.collectors.un_data import api

class CatalogErrorsTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    run_job=fixture.MonthlyRefreshTest.run_job
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def journal(self):
        return json.loads((self.state/'pending_datasets.json').read_text(encoding='utf-8'))

    def bad_client(self):
        return fixture.FakeClient([fixture.catalog(edition='H9'),fixture.catalog('A',2023,40)])

    def start_bad(self):
        with self.assertRaisesRegex(ValueError,'발표 목록 오류'):self.run_job(self.bad_client())

    def test_unsupported_edition_keeps_good_scope_and_checkpoint(self):
        before=self.checkpoint();self.start_bad()
        self.assertEqual({r[0] for r in self.snapshot()},{'A'})
        self.assertEqual(set(self.journal()['catalog_errors']),{'M_202401_8'})
        self.assertEqual(before,self.checkpoint())
        manifest=json.loads(next(self.state.glob('runs/*/manifest.json')).read_text())
        self.assertEqual((manifest['status'],manifest['succeeded'],manifest['catalog_failed']),('PARTIAL_FAILED',1,1))
        self.assertTrue(next(self.state.glob('runs/*/catalog_errors.json')).is_file())

    def test_conflicting_editions_never_choose_either(self):
        for editions in [('H6','H2'),('H2','H6')]:
            client=fixture.FakeClient([fixture.catalog(edition=v) for v in editions]+[fixture.catalog('A',2023,40)])
            with self.assertRaises(ValueError):self.run_job(client)
            self.assertFalse(any('/M/' in p for p,_ in client.calls if not p.startswith('getDa')))
        self.assertEqual({r[0] for r in self.snapshot()},{'A'})

    def test_existing_retry_does_not_bypass_bad_catalog(self):
        with self.assertRaises(ValueError):
            self.run_job(fixture.FakeClient([fixture.catalog()],[fixture.raw(amount='-1')]))
        self.assertIn('M_202401_8',self.journal()['datasets'])
        client=self.bad_client()
        with self.assertRaises(ValueError):self.run_job(client)
        self.assertNotIn('M_202401_8',self.journal()['datasets'])
        self.assertIn('M_202401_8',self.journal()['catalog_errors'])
        self.assertFalse(any('/M/' in p for p,_ in client.calls if not p.startswith('getDa')))

    def test_disappeared_error_stays_pending_and_good_success_not_repeated(self):
        self.start_bad();before=self.checkpoint();client=fixture.FakeClient([fixture.catalog('A',2023,40)])
        with self.assertRaises(ValueError):self.run_job(client)
        self.assertTrue(all(p.startswith('getDa') for p,_ in client.calls))
        self.assertEqual(before,self.checkpoint());self.assertIn('M_202401_8',self.journal()['catalog_errors'])

    def test_corrected_catalog_recovers_and_advances(self):
        self.start_bad();client=fixture.FakeClient();result=self.run_job(client)
        self.assertEqual((result['succeeded'],result['skipped'],result['catalog_failed']),(1,1,0))
        self.assertEqual(self.journal()['catalog_errors'],{})
        self.assertEqual(json.loads(self.checkpoint())['last_successful_check_date'],'2024-02-02')
        self.assertEqual(len(self.snapshot()),3)

    def test_corrected_catalog_but_bad_data_still_prevents_checkpoint(self):
        self.start_bad();before=self.checkpoint()
        with self.assertRaises(ValueError):self.run_job(fixture.FakeClient([fixture.catalog()],[fixture.raw(amount='-1')]))
        self.assertEqual(self.journal()['catalog_errors'],{})
        self.assertIn('M_202401_8',self.journal()['datasets']);self.assertEqual(before,self.checkpoint())

    def test_transport_failure_preserves_existing_error_state(self):
        self.start_bad();before=(self.state/'pending_datasets.json').read_bytes();checkpoint=self.checkpoint()
        with mock.patch.object(job,'discover',side_effect=api.ApiRequestError('down',kind='http',status_code=503)):
            with self.assertRaises(api.ApiRequestError):self.run_job()
        self.assertEqual(before,(self.state/'pending_datasets.json').read_bytes());self.assertEqual(checkpoint,self.checkpoint())

    def test_unidentifiable_row_stops_before_processing(self):
        row=fixture.catalog();del row['reporterCode']
        with mock.patch.object(job,'process_dataset') as process:
            with self.assertRaises(KeyError):self.run_job(fixture.FakeClient([row]))
        process.assert_not_called();self.assertEqual(self.snapshot(),[])

    def test_bad_release_timestamp_is_scoped(self):
        row=fixture.catalog();row['lastReleased']='invalid'
        with self.assertRaises(ValueError):self.run_job(fixture.FakeClient([row,fixture.catalog('A',2023,40)]))
        self.assertEqual({r[0] for r in self.snapshot()},{'A'})
        self.assertIn('M_202401_8',self.journal()['catalog_errors'])

    def test_latest_same_edition_is_order_independent(self):
        from datetime import date
        early=fixture.catalog();late=dict(early,lastReleased='2024-02-01T14:00:00')
        for rows in ([early,late],[late,early]):
            errors=[];result=api.discover(fixture.FakeClient(rows),fixture.CFG,date(2022,1,1),date(2024,2,2),self.path,errors)
            self.assertEqual(result,[late]);self.assertEqual(errors,[])

    def test_independent_discover_without_error_sink_still_raises(self):
        from datetime import date
        with self.assertRaisesRegex(ValueError,'발표 목록 범위 오류'):
            api.discover(self.bad_client(),fixture.CFG,date(2022,1,1),date(2024,2,2),self.path)

    def test_error_state_write_failure_blocks_all_db_changes(self):
        save=job.save
        def fail(path,obj):
            if path.name=='pending_datasets.json':raise OSError('disk full')
            return save(path,obj)
        with mock.patch.object(job,'save',side_effect=fail),mock.patch.object(job,'process_dataset') as process:
            with self.assertRaises(OSError):self.run_job(self.bad_client())
        process.assert_not_called();self.assertEqual(self.snapshot(),[])

if __name__=='__main__':unittest.main()
