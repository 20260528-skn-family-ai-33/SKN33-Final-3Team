"""HS2022 전용 입력·발표 제외·초기 스냅샷을 검증한다."""
import csv
import hashlib
import json
import unittest
from datetime import date
from pathlib import Path
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.collectors.un_data.api import discover
from backend.app.scheduler.collectors.un_data.transform import transform,scalar
from backend.app.scheduler.collectors.un_data.pipeline import validate_records
from backend.app.scheduler.jobs import un_data_refresh as job


class HsPolicyTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown

    def test_each_old_edition_excluded_and_not_collected(self):
        for index,edition in enumerate(('H0','H1','H2','H3','H4','H5')):
            row=fixture.catalog(edition=edition)
            row['lastReleased']=f'2024-02-01T{12+index}:00:00'
            client=fixture.FakeClient([row])
            result=job.run(self.engine,client,self.scope,self.state,date(2024,2,2))
            self.assertEqual(result['excluded_hs_policy'],1)
            self.assertTrue(all(path.startswith('getDa') for path,_ in client.calls))
            self.assertEqual(result['succeeded'],0)
        self.assertTrue(next(self.state.glob('runs/*/excluded_hs_policy.json')).exists())

    def test_raw_and_csv_reject_old_editions(self):
        for edition,ver in [('H2','HS2002'),('H3','HS2007'),('H4','HS2012'),('H5','HS2017')]:
            raw={k:scalar(v) for k,v in fixture.raw(edition=edition).items()}
            with self.assertRaisesRegex(ValueError,'HS2022'):transform([raw],fixture.CFG)
            record=dict(zip(job.HEAD,['M','2024-01-01','AL','010121',ver,'100','','','CIF']))
            with self.assertRaisesRegex(ValueError,'HS2022'):validate_records([record])

    def test_old_scope_checkpoint_cannot_start_new_policy(self):
        before=(self.state/'checkpoint.json').read_bytes()
        cfg=json.loads(self.scope.read_text());cfg['classification_policy']='HS2022_original_only'
        self.scope.write_text(json.dumps(cfg),encoding='utf-8')
        client=fixture.FakeClient()
        with self.assertRaisesRegex(ValueError,'범위 변경'):job.run(self.engine,client,self.scope,self.state,date(2024,2,2))
        self.assertEqual(client.calls,[])
        self.assertEqual((self.state/'checkpoint.json').read_bytes(),before)

    def test_snapshot_hashes_and_parent_lists_exact(self):
        folder=job.DEFAULT_CSV.parent
        with job.DEFAULT_CSV.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),67550);self.assertEqual({r['hs_ver'] for r in rows},{'HS2022'})
        self.assertEqual(hashlib.sha256(job.DEFAULT_CSV.read_bytes()).hexdigest(),job.SNAPSHOT['csv_sha256'])
        self.assertEqual(hashlib.sha256(job.DEFAULT_SCOPE.read_bytes()).hexdigest(),job.SNAPSHOT['scope_sha256'])
        self.assertEqual(job.SNAPSHOT['last_successful_check_date'],'2026-10-15')
        with (folder/'required_hs.csv').open(encoding='utf-8-sig') as f:hs={(r['hs_code'],r['hs_ver']) for r in csv.DictReader(f)}
        self.assertEqual(hs,{(r['hs6_code'],r['hs_ver']) for r in rows});self.assertEqual(len(hs),15)
        with (folder/'required_country.csv').open(encoding='utf-8-sig') as f:countries={r['country_code'] for r in csv.DictReader(f)}
        self.assertEqual(countries,{r['country_code'] for r in rows});self.assertEqual(len(countries),137)
