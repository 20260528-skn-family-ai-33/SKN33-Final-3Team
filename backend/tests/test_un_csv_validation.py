import csv, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
from backend.app.scheduler.jobs import un_data_load as job

class ValidationTest(unittest.TestCase):
    def setUp(self):
        with job.DEFAULT_CSV.open(encoding='utf-8-sig',newline='') as f:
            self.row=next(csv.DictReader(f))
    def validate(self, rows, header=None):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'input.csv'
            with path.open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.DictWriter(f,fieldnames=header or job.HEAD);w.writeheader();w.writerows(rows)
            return job.read_rows(path)
    def test_bad_values(self):
        for key,value in [('hs6_code','１２３４５６'),('total_import_usd','-1'),('total_import_usd','1.0001'),('total_import_usd','Infinity'),('period_start','2024-01-02'),('freq_code','Q'),('valuation_basis','BAD'),('korea_share_pct','-1')]:
            with self.subTest(key=key,value=value),self.assertRaises(Exception):
                self.validate([{**self.row,key:value}])
    def test_duplicate(self):
        with self.assertRaises(ValueError):self.validate([self.row,self.row])
    def test_empty(self):
        with self.assertRaises(ValueError):self.validate([])
    def test_header_order(self):
        with self.assertRaises(ValueError):self.validate([self.row],list(reversed(job.HEAD)))
    def test_zero_null(self):
        r={**self.row,'total_import_usd':'100','korea_import_usd':'0','korea_share_pct':'0'}
        self.assertEqual(self.validate([r])[0]['korea_import_usd'],0)
        r.update(korea_import_usd='',korea_share_pct='')
        self.assertIsNone(self.validate([r])[0]['korea_import_usd'])
    def test_invalid_file_never_connects(self):
        with mock.patch.object(job,'make_engine') as connect:
            self.assertEqual(job.run('/nonexistent/un.csv'),1)
            connect.assert_not_called()

if __name__=='__main__':unittest.main()
