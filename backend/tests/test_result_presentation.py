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
    def test_legacy_prediction_excel_uses_korean_labels_and_relevant_rank(self):
        row = {'anchor': '2026-09-01', 'country_name': '베트남', 'hs6': '330499',
               'product_name': '화장품', 'growth_pct': 2.1, 'exp_12m': 123456,
               'country_rank': 3, 'product_rank': 12, 'country_size': 100, 'name': '화장품'}
        table = {'columns': [{'key': key, 'label': key} for key in row], 'rows': [row],
                 'total_rows': 1, 'meta': {'kind': 'PREDICTION', 'group_by': 'COUNTRY'}}
        sheet = load_workbook(io.BytesIO(xlsx_bytes([table], {}, [], unit_headers=True))).worksheets[0]
        # 모델 A의 예측 증감률은 순위용이라 보이지 않는다(증감 방향은 모델 B의 '증감 예상').
        self.assertEqual([cell.value for cell in sheet[1]], [
            '품목 순위', '국가', 'HS 코드', '품목명', '최근 12개월 수출액 (USD)', '예측 기준월',
        ])
        self.assertEqual(sheet['A2'].value, 3)
        row['growth_outlook'] = '증가 예상'
        table['columns'] = [{'key': key, 'label': key} for key in row]
        sheet = load_workbook(io.BytesIO(xlsx_bytes([table], {}, [], unit_headers=True))).worksheets[0]
        self.assertIn('증감 예상', [cell.value for cell in sheet[1]])

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

    def test_llm_sql_columns_are_shown_as_generated(self):
        # 판본별 품목 수처럼 자유 SQL이 고른 hs_ver 열은 숨기지 않는다.
        columns = [{'key': 'hs_ver', 'label': 'HS 판본', 'type': 'string', 'unit': None},
                   {'key': 'item_count', 'label': '품목 수', 'type': 'number', 'unit': 'COUNT'}]
        rows = [{'hs_ver': 'HS2017', 'item_count': 5387}, {'hs_ver': 'HS2022', 'item_count': 5612}]
        self.assertEqual([c['label'] for c in display_columns(columns, rows, {'kind': 'LLM_SQL'})], ['HS 판본', '품목 수'])

    def test_different_length_comparison_keeps_period_totals(self):
        # 3개월과 12개월 비교(D-84)는 월별 비교 금액이 없고 양쪽 기간 합계·월평균이 비교 근거다.
        row = {'month': '2026-08', 'country_code': 'VN', 'country_name': '베트남', 'amount_usd': 100,
               'compare_month': None, 'compare_amount_usd': None, 'total_usd': 300, 'compare_total_usd': 1000,
               'monthly_avg_usd': 100, 'compare_monthly_avg_usd': 83.3, 'change_pct': -70.0,
               'verified_months': 3, 'requested_months': 3, 'compare_verified_months': 12, 'compare_requested_months': 12}
        labels = [c['label'] for c in display_columns([{'key': k, 'label': k} for k in row], [row])]
        for label in ('기간 합계', '비교 기간 합계', '월평균 금액', '비교 기간 월평균', '집계된 개월 수', '비교 집계 개월 수'):
            self.assertIn(label, labels)
        self.assertNotIn('비교 기간 금액', labels)
        # 같은 길이 비교에서 비교 월이 아직 집계되지 않았을 뿐이면 월별 표 그대로 둔다.
        same = {**row, 'requested_months': 12, 'compare_requested_months': 12}
        self.assertNotIn('기간 합계', [c['label'] for c in display_columns([{'key': k, 'label': k} for k in same], [same])])

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
