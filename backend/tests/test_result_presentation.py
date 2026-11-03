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


    def test_report_formats_share_condition_filename_and_sanitize_topic(self):
        from backend.app.api.reports import download_filename
        engine,meta=test_engine()
        try:
            reference_data(engine,meta)
            with engine.connect() as connection:
                store=Store(SimpleNamespace(table=lambda name,_:meta.tables[name]),connection)
                record={'created_at':datetime(2026,11,2,23,0,0),'completed_at':datetime(2026,11,3,0,1,2),'conditions_json':{'country_codes':['US'],'hs_items':[{'hs_code':'330499','hs_ver':'HS2022'}],
                    'period':{'start':'2025-10-01','end':'2025-12-31'},'trade_flow':'EXPORT','metric':'AMOUNT'}}
                names=[download_filename(store,record,fmt) for fmt in ('xlsx','pdf','docx')]
                self.assertEqual(len({n.rsplit('.',1)[0] for n in names}),1)
                self.assertTrue(all(n.startswith('미국_2025-10-01~2025-12-31_') for n in names))
                self.assertTrue(all('_수출액분석_리포트_20261103_090102.' in n for n in names))
                record['conditions_json']['topic']='비교/분석:*?<>|'+('장'*100)
                name=download_filename(store,record,'pdf')
                self.assertFalse(any(c in name for c in '/:*?<>|'))
                self.assertLess(len(name.encode('utf-8')),255)
                record['completed_at']=None
                self.assertTrue(download_filename(store,record,'pdf').endswith('_리포트_20261103_080000.pdf'))
        finally:
            engine.dispose()
