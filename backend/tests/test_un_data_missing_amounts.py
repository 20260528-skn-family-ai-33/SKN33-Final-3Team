import json
import unittest
from datetime import date
from backend.tests import test_un_data_refresh as fixtures
from backend.app.scheduler.collectors.un_data.transform import transform, scalar
from backend.app.scheduler.jobs import un_data_refresh as job


class MissingAmountsTest(unittest.TestCase):
    def convert(self, korea):
        raw=[fixtures.raw()]+([] if korea is None else [korea])
        missing=[]
        result=transform([{k:scalar(v) for k,v in row.items()} for row in raw],fixtures.CFG,missing=missing)
        return result,missing

    def test_korea_row_missing(self):
        result,missing=self.convert(None)
        self.assertIsNone(result[0][0][6]);self.assertIsNone(result[0][0][7])
        self.assertEqual(missing[0]['reason'],'KOREA_ROW_MISSING')
        self.assertEqual(result[3],[])

    def test_same_basis_missing_no_fallback(self):
        row=fixtures.raw(partner=410,amount='25');row.update(cifvalue=None,fobvalue='25')
        result,missing=self.convert(row)
        self.assertIsNone(result[0][0][6]);self.assertIsNone(result[0][0][7])
        self.assertEqual(missing[0]['reason'],'KOREA_SAME_BASIS_AMOUNT_MISSING')
        self.assertEqual(missing[0]['fob_value'],'25');self.assertEqual(result[3],[])

    def test_observed_zero_is_not_missing(self):
        result,missing=self.convert(fixtures.raw(partner=410,amount='0'))
        self.assertEqual(result[0][0][6],0);self.assertEqual(missing,[])


class MissingFileTest(unittest.TestCase):
    setUp=fixtures.MonthlyRefreshTest.setUp
    tearDown=fixtures.MonthlyRefreshTest.tearDown

    def test_run_writes_missing_reasons(self):
        client=fixtures.FakeClient([fixtures.catalog()],[fixtures.raw()])
        job.run(self.engine,client,self.scope,self.state,date(2024,2,2))
        paths=list(self.state.glob('runs/*/missing_amounts.json'))
        self.assertEqual(len(paths),1)
        self.assertEqual(json.loads(paths[0].read_text(encoding='utf-8'))[0]['reason'],'KOREA_ROW_MISSING')
