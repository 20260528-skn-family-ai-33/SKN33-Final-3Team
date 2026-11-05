import unittest
from datetime import date
from backend.tests.api_support import test_engine, reference_data
from backend.app.api.database import Database, Store
from backend.app.api import reports
from backend.app.services.report_scope_comparison import comparison_table
from backend.app.services.report_context import build_context
from backend.app.services.report_templates import render


class ReportScopeComparisonTest(unittest.TestCase):
    def setUp(self):
        engine,meta=test_engine()
        self.addCleanup(engine.dispose)
        reference_data(engine,meta)
        self.connection=engine.connect()
        self.addCleanup(self.connection.close)
        self.store=Store(Database(engine),self.connection)
        self.conditions={'country_codes':['US','CN'],'hs_items':[],
            'period':{'start':'2025-01-01','end':'2025-03-31'},'trade_flow':'EXPORT',
            'compare':'NONE','compare_period':None,'include_korea_total':True,'metric':'AMOUNT'}

    def test_total_is_independent_of_selected_partners_and_excludes_total_rows(self):
        table=comparison_table(self.store,1,self.conditions,date(2025,1,1),date(2025,3,1))
        self.assertEqual(table['rows'][0]['korea_total_usd'],3240000)
        self.assertEqual(table['rows'][0]['partner_0_usd'],1120000)
        self.assertEqual(table['rows'][0]['partner_1_usd'],2120000)
        self.assertEqual(len(table['rows']),3)

    def test_preview_document_and_pdf_context_preserve_korea_and_partners(self):
        preview=reports.preview(self.store,1,{'conditions':self.conditions})
        self.assertTrue(preview['conditions']['include_korea_total'])
        self.assertEqual(preview['conditions']['country_codes'],['US','CN'])
        saved={**preview['conditions'],'sections':['KPI']}
        record={'report_id':1,'report_type':'RPT_ON_DEMAND','conditions_json':saved,'content_json':{}}
        doc=reports.build_document(self.store,1,record)
        block=next(s for s in doc['sections'] if s['section_code']=='KPI')
        comparison=next(t for t in block['tables'] if t['meta'].get('kind')=='REPORT_SCOPE_COMPARISON')
        self.assertEqual(block['kpi']['amount_usd'],sum(r['korea_total_usd'] for r in comparison['rows']))
        context=build_context(self.store,1,record,doc)
        self.assertEqual(len(context['scope_comparison']['series']),3)
        html=render('RPT_ON_DEMAND',context)
        self.assertIn('한국 전체 · 상대국 비교',html)
        self.assertIn('한국(대한민국) 전체 수출액',html)
