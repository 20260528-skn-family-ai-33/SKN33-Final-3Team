"""오래된 발표를 저장된 성공·대기 버전보다 우선하지 않는지 검증합니다."""
import json
import unittest
from datetime import date
from unittest import mock
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.jobs import un_data_refresh as job

class VersionOrderTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    run_job=fixture.MonthlyRefreshTest.run_job
    snapshot=fixture.MonthlyRefreshTest.snapshot
    checkpoint=fixture.MonthlyRefreshTest.checkpoint

    def journal(self):return json.loads((self.state/'pending_datasets.json').read_text())
    def run_at(self,client):return self.run_job(client,date(2024,2,4))
    def initial_partial(self):
        original=job.process_dataset
        def fail_month(*args):
            if args[3]['freqCode']=='M':raise job.ApiRequestError('quota',kind='http',status_code=429)
            return original(*args)
        client=fixture.FakeClient([fixture.catalog('A',2023,40,released='2024-02-03'),fixture.catalog(released='2024-02-03')],
                                 [fixture.raw('A','2023',40,amount='300'),fixture.raw()])
        with mock.patch.object(job,'process_dataset',side_effect=fail_month):
            with self.assertRaises(job.ApiRequestError):self.run_at(client)

    def test_older_annual_cannot_overwrite_successful_value(self):
        self.initial_partial()
        client=fixture.FakeClient([fixture.catalog('A',2023,40),fixture.catalog(released='2024-02-03')],
                                 [fixture.raw('A','2023',40,amount='100'),fixture.raw()])
        result=self.run_at(client)
        self.assertEqual(next(r[5] for r in self.snapshot() if r[0]=='A'),300)
        self.assertEqual(result['skipped_stale'],1)
        self.assertFalse(any('/A/' in p for p,_ in client.calls if not p.startswith('getDa')))
        self.assertEqual(self.journal()['completed_versions']['A_2023_40'][1],'2024-02-03T12:00:00')

    def test_older_catalog_keeps_newer_retry_descriptor(self):
        self.initial_partial();original=job.process_dataset;versions=[]
        def capture(*args):
            versions.append(args[3]['lastReleased']);return original(*args)
        with mock.patch.object(job,'process_dataset',side_effect=capture):
            result=self.run_at(fixture.FakeClient([fixture.catalog()]))
        self.assertEqual(versions,['2024-02-03T12:00:00'])
        self.assertEqual(result['skipped_stale'],1)

    def test_newer_edition_still_updates(self):
        self.initial_partial()
        result=self.run_at(fixture.FakeClient([fixture.catalog('A',2023,40,'H2',released='2024-02-04')],
                           [fixture.raw('A','2023',40,amount='400',edition='H2'),fixture.raw()]))
        self.assertEqual(result['succeeded'],2)
        annual=next(r for r in self.snapshot() if r[0]=='A');self.assertEqual((annual[4],annual[5]),('HS2002',400))

    def test_equal_time_different_edition_blocks_without_changing_db(self):
        self.initial_partial();before=self.checkpoint()
        with self.assertRaisesRegex(ValueError,'발표 목록 오류'):
            self.run_at(fixture.FakeClient([fixture.catalog('A',2023,40,'H2',released='2024-02-03')]))
        self.assertEqual(next(r[5] for r in self.snapshot() if r[0]=='A'),300)
        self.assertIn('A_2023_40',self.journal()['catalog_errors']);self.assertEqual(before,self.checkpoint())

    def test_error_then_older_correction_cannot_lower_known_version(self):
        self.initial_partial()
        with self.assertRaises(ValueError):self.run_at(fixture.FakeClient([fixture.catalog('A',2023,40,'H9',released='2024-02-03')]))
        client=fixture.FakeClient([fixture.catalog('A',2023,40)])
        with self.assertRaises(ValueError):self.run_at(client)
        self.assertIn('A_2023_40',self.journal()['catalog_errors'])
        self.assertEqual(self.journal()['known_versions']['A_2023_40'][1],'2024-02-03T12:00:00')
        self.assertTrue(all(p.startswith('getDa') for p,_ in client.calls))

    def test_equivalent_timezone_notation_skips(self):
        self.initial_partial();annual=fixture.catalog('A',2023,40,released='2024-02-03')
        annual['lastReleased']='2024-02-03T21:00:00+09:00'
        client=fixture.FakeClient([annual]);result=self.run_at(client)
        self.assertEqual(result['skipped'],1)
        self.assertFalse(any('/A/' in p for p,_ in client.calls if not p.startswith('getDa')))

    def test_old_state_migrates_watermark_from_success_and_retry(self):
        self.initial_partial();state=self.journal();del state['known_versions'];job.save(self.state/'pending_datasets.json',state)
        result=self.run_at(fixture.FakeClient([fixture.catalog('A',2023,40),fixture.catalog()]))
        self.assertEqual(result['skipped_stale'],2)
        self.assertEqual(self.journal()['known_versions']['M_202401_8'][1],'2024-02-03T12:00:00')

    def test_invalid_known_version_fails_before_requests(self):
        self.initial_partial();state=self.journal();state['known_versions']['A_2023_40']=['H6','invalid']
        job.save(self.state/'pending_datasets.json',state)
        with mock.patch.object(job,'discover') as discover:
            with self.assertRaises(ValueError):self.run_at(fixture.FakeClient())
        discover.assert_not_called()

if __name__=='__main__':unittest.main()
