import io
import unittest
from datetime import datetime
from openpyxl import load_workbook
from backend.app.api.results import display_columns, result_filename
from backend.app.api.files import xlsx_bytes
from backend.app.api.database import Store
from backend.tests.api_support import test_engine, reference_data
from types import SimpleNamespace


class ResultPresentationTest(unittest.TestCase):
    def test_monthly_display_and_excel_keep_source_values(self):
        row={'month':'2026-08','hs_code':'330499','hs_ver':'HS2022','name':'화장품',
             'country_code':'VN','country_name':'베트남','amount_usd':123,
             'verified_months':6,'total_usd':900,'compare_amount_usd':None}
        columns=[{'key':key,'label':key} for key in row]
        visible=display_columns(columns,[row])
        self.assertEqual([c['label'] for c in visible],['기준월','HS 코드','품목','국가','수출액'])
        data=xlsx_bytes([{'columns':columns,'rows':[row],'total_rows':1}],{},[])
        sheet=load_workbook(io.BytesIO(data)).worksheets[0]
        self.assertEqual([c.value for c in sheet[1]],[c['label'] for c in visible])
        self.assertEqual(row['total_usd'],900)
        self.assertEqual(columns[0]['label'],'month')

    def test_country_filename_uses_reference_name(self):
        engine,meta=test_engine()
        try:
            reference_data(engine,meta)
            with engine.connect() as connection:
                store=Store(SimpleNamespace(table=lambda name, _:meta.tables[name]),connection)
                name=result_filename({'conditions':{'country_codes':['US']},
                    'snapshot_created_at':datetime(2026,11,2)},store)
            self.assertEqual(name,'미국_20261102_090000.xlsx')
        finally:
            engine.dispose()
