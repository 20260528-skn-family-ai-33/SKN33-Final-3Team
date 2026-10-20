"""0/NULL 적재 의미와 정책 제외 인계 자료의 근거·범위를 검증합니다."""
import csv
import hashlib
import json
import unittest
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from backend.tests import test_un_data_refresh as fixture
from backend.app.scheduler.collectors.un_data import api
from backend.app.scheduler.jobs import un_data_refresh as job


class AmountStorageContractTest(unittest.TestCase):
    setUp=fixture.MonthlyRefreshTest.setUp
    tearDown=fixture.MonthlyRefreshTest.tearDown
    snapshot=fixture.MonthlyRefreshTest.snapshot
    run_job=fixture.MonthlyRefreshTest.run_job

    def load(self,rows):
        self.run_job(fixture.FakeClient([fixture.catalog()],rows))
        return self.snapshot()

    def test_world_and_korea_zero_keep_zero_share_null(self):
        rows=self.load([fixture.raw(amount='0'),fixture.raw(partner=410,amount='0')])
        self.assertEqual(rows[0][5:8],(Decimal('0'),Decimal('0'),None))

    def test_observed_korea_zero_keeps_zero_share(self):
        rows=self.load([fixture.raw(),fixture.raw(partner=410,amount='0')])
        self.assertEqual(rows[0][5:8],(Decimal('100'),Decimal('0'),Decimal('0')))

    def test_missing_korea_stays_null(self):
        rows=self.load([fixture.raw()])
        self.assertEqual(rows[0][5:8],(Decimal('100'),None,None))

    def test_missing_world_stays_null(self):
        rows=self.load([fixture.raw(partner=410,amount='25')])
        self.assertEqual(rows[0][5:8],(None,Decimal('25'),None))

    def test_absent_raw_does_not_fabricate_zero_rows(self):
        self.assertEqual(self.load([]),[])

    def test_other_valuation_amount_does_not_fill_null(self):
        korea=fixture.raw(partner=410,amount='25');korea.update(cifvalue=None,fobvalue='25')
        rows=self.load([fixture.raw(),korea])
        self.assertEqual(rows[0][5:8],(Decimal('100'),None,None))
        records=json.loads(next(self.state.glob('runs/*/missing_amounts.json')).read_text(encoding='utf-8'))
        self.assertEqual(records[0]['reason'],'KOREA_SAME_BASIS_AMOUNT_MISSING')

    def test_monthly_exclusion_file_identifies_target_not_observed_products(self):
        self.run_job(fixture.FakeClient([fixture.catalog(edition='H5')],[]))
        rows=json.loads(next(self.state.glob('runs/*/excluded_hs_policy.json')).read_text(encoding='utf-8'))
        row=rows[0]
        self.assertEqual((row['country_code'],row['freq_code'],row['period_start']),('AL','M','2024-01-01'))
        self.assertEqual((row['source_hs_ver'],row['reason']),('HS2017','HS2022_ONLY'))
        self.assertEqual(row['target_hs6_codes'],['010121','010129'])
        self.assertEqual(row['evidence_level'],'DATASET_ANNOUNCEMENT_ONLY')
        self.assertEqual(self.snapshot(),[])

    def test_annual_exclusions_use_only_country_target_pairs(self):
        cfg=dict(fixture.CFG,target_pairs=[['40','010121'],['8','010129']])
        excluded=[]
        api.discover(fixture.FakeClient([fixture.catalog('A',2023,40,edition='H4')]),
                     cfg,date(2022,1,1),date(2024,2,2),self.path,excluded=excluded)
        self.assertEqual(excluded[0]['target_hs6_codes'],['010121'])
        self.assertEqual((excluded[0]['country_code'],excluded[0]['period_start']),('AT','2023-01-01'))


class SnapshotExclusionLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=Path(__file__).resolve().parents[2]/'data/un_data'
        cls.summary=json.loads((cls.folder/'hs2022_exclusions_summary.json').read_text(encoding='utf-8'))
        with (cls.folder/'hs2022_exclusions.csv').open(encoding='utf-8-sig',newline='') as f:cls.excluded=list(csv.DictReader(f))
        with (cls.folder/'un_data.csv').open(encoding='utf-8-sig',newline='') as f:cls.included=list(csv.DictReader(f))

    def test_provenance_hashes_and_counts_match_snapshot(self):
        s=self.summary
        self.assertEqual(s['source']['sha256'],job.SNAPSHOT['parent_csv_sha256'])
        self.assertEqual(s['included']['sha256'],job.SNAPSHOT['csv_sha256'])
        for name,section in [('un_data.csv','included'),('hs2022_exclusions.csv','excluded')]:
            self.assertEqual(hashlib.sha256((self.folder/name).read_bytes()).hexdigest(),s[section]['sha256'])
        self.assertEqual(len(self.included),67550);self.assertEqual(len(self.excluded),8641)
        self.assertEqual(s['source']['rows'],len(self.included)+len(self.excluded))
        self.assertEqual(s['snapshot_date'],job.SNAPSHOT['last_successful_check_date'])

    def test_exact_excluded_keys_are_unique_and_not_current_2022_rows(self):
        columns=['freq_code','period_start','country_code','hs6_code']
        keys=lambda rows:{tuple(r[c] for c in columns) for r in rows}
        self.assertEqual(len(keys(self.excluded)),len(self.excluded))
        self.assertFalse(keys(self.excluded)&keys(self.included))
        for row in self.excluded:
            self.assertEqual(row['reason'],'HS2022_ONLY')
            self.assertIn(row['source_hs_ver'],('HS2002','HS2007','HS2012','HS2017'))
            self.assertEqual(len(row['hs6_code']),6)
            self.assertGreaterEqual(date.fromisoformat(row['period_start']).year,2022)

    def test_country_year_and_edition_summaries_match_ledger(self):
        s=self.summary['excluded'];countries={r['country_code'] for r in self.included}
        omitted={r['country_code'] for r in self.excluded}
        self.assertEqual(sorted(omitted-countries),s['fully_excluded_countries'])
        self.assertEqual(sorted(omitted&countries),s['partially_excluded_countries'])
        self.assertEqual(len(s['fully_excluded_countries']),16)
        self.assertEqual(len(s['partially_excluded_countries']),45)
        self.assertEqual(dict(Counter(r['period_start'][:4] for r in self.excluded)),s['rows_by_year'])
        self.assertEqual(dict(Counter(r['source_hs_ver'] for r in self.excluded)),s['rows_by_source_hs_ver'])

    def test_null_and_zero_counts_are_distinct(self):
        for field,counts in self.summary['included_value_counts'].items():
            self.assertEqual(sum(r[field]=='' for r in self.included),counts['null_or_blank'])
            self.assertEqual(sum(r[field]!='' and Decimal(r[field])==0 for r in self.included),counts['zero_value'])
        # 비중이 반올림으로 0인 행은 실제 한국 금액 0이 아닙니다.
        rounded=[r for r in self.included if r['korea_share_pct'] and Decimal(r['korea_share_pct'])==0]
        self.assertTrue(rounded)
        self.assertTrue(all(Decimal(r['korea_import_usd'])>0 for r in rounded))
