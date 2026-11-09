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
    def test_user_excel_info_keeps_conditions_warnings_and_limit_but_not_internal_fields(self):
        from backend.app.api.files import result_xlsx
        from copy import deepcopy
        meta={'export_profile':'TRADE_SCREEN','period':{'start':'2025-10-01','end':'2025-12-31'},
              'sources':[{'code':'CUSTOMS','label':'관세청','url':None}],
              'generated_at':'2026-11-09T00:00:00Z','data_version':'internal-1',
              'freshness':'STALE','truncated':True,'rows_total':1001,'warnings':['비교 자료 미집계']}
        payload={'meta':meta,'conditions':{'period':meta['period'],'hs_items':[{'hs_code':'010129','hs_ver':'HS2022'}],
                   'country_codes':['US'],'trade_flow':'IMPORT'},'sql':['SELECT private_column FROM internal_table'],
                 'columns':[{'key':'amount','label':'수입액','unit':'USD'}],'rows':[{'amount':0}],'total_rows':1}
        original=deepcopy(payload)
        workbook=load_workbook(io.BytesIO(result_xlsx(payload)))
        info=dict(list(workbook['정보'].values)[1:])
        self.assertEqual(info['조회 품목 (HS 코드)'],'010129')
        self.assertEqual(info['조회 국가 코드'],'US')
        self.assertEqual(info['조회 기준'],'수입')
        self.assertEqual(info['조회 기간'],'2025-10-01 ~ 2025-12-31')
        self.assertEqual(info['생성 시각'],'2026-11-09 09:00 KST')
        self.assertEqual(info['출처'],'관세청')
        self.assertEqual(info['주의사항'],'비교 자료 미집계')
        self.assertIn('상위 1,000행',info['안내'])
        self.assertNotIn('실행 SQL',info)
        self.assertNotIn('export_profile',info)
        self.assertNotIn('data_version',info)
        self.assertEqual(workbook['결과표1']['A2'].value,0)
        self.assertEqual(payload,original)
        # 표의 모양으로 출처를 추정하지 않는다. 표시가 없는 에이전트/과거 스냅샷은 원본 유지.
        del payload['meta']['export_profile']
        workbook=load_workbook(io.BytesIO(result_xlsx(payload)))
        info=dict(list(workbook['정보'].values)[1:])
        self.assertEqual(info['실행 SQL'],payload['sql'][0])
        self.assertEqual(info['data_version'],'internal-1')

    def test_periodic_excel_distinguishes_no_comparison_from_unavailable_data(self):
        from backend.app.api.reports import report_xlsx
        from copy import deepcopy
        row={'amount_usd':0,'previous_amount_usd':None,'comparison_pct':None,'mom_pct':None,'yoy_pct':None}
        table={'columns':[{'key':key,'label':key,'unit':'USD' if key.endswith('_usd') else '%' if key.endswith('_pct') else None} for key in row],
               'rows':[row],'total_rows':1}
        doc={'sections':[{'tables':[table]}],'meta':{'warnings':['비교 기간 자료 미집계']},'sql':['SELECT 1']}
        original=deepcopy(doc)
        for comparison in ('NONE','YOY_PERIOD'):
            record={'report_type':'RPT_MONTHLY','conditions_json':{'compare':comparison,
                    'compare_period':{'start':'2024-01-01','end':'2024-12-31'}}}
            workbook=load_workbook(io.BytesIO(report_xlsx(record,doc)))
            headers=[cell.value for cell in workbook['결과표1'][1]]
            self.assertEqual('비교 기간 수출액 (USD)' in headers,comparison!='NONE')
            self.assertEqual('비교 증감률 (%)' in headers,comparison!='NONE')
            self.assertEqual(workbook['결과표1']['A2'].value,0)
            info=dict(list(workbook['정보'].values)[1:])
            self.assertEqual('비교 기간' in info,comparison!='NONE')
            self.assertEqual(info['주의사항'],'비교 기간 자료 미집계')
            self.assertNotIn('실행 SQL',info)
        self.assertEqual(doc,original)
        row.update(monthly_avg_change_pct=12.5,compare_verified_months=3,compare_requested_months=12)
        table['columns'].extend([{'key':key,'label':key,'unit':'%' if key.endswith('_pct') else None}
                                for key in ('monthly_avg_change_pct','compare_verified_months','compare_requested_months')])
        workbook=load_workbook(io.BytesIO(report_xlsx(record,doc)))
        headers=[c.value for c in workbook['결과표1'][1]]
        self.assertIn('월평균 증감률 (%)',headers)
        self.assertIn('비교 집계 개월 수',headers)
        self.assertIn('비교 조회 개월 수',headers)
        # 생성 후 source_tables가 빠진 과거 에이전트 문서도 FROZEN 출처를 보존한다.
        doc['meta']['freshness']='FROZEN'
        workbook=load_workbook(io.BytesIO(report_xlsx({'report_type':'RPT_MONTHLY'},doc)))
        self.assertIn('전월 대비',[c.value for c in workbook['결과표1'][1]])
        self.assertIn('실행 SQL',dict(list(workbook['정보'].values)[1:]))

    def test_periodic_excel_removes_duplicate_rates_without_changing_agent_or_pdf_tables(self):
        from copy import deepcopy
        from backend.app.api.reports import report_xlsx
        row={'amount_usd':125.5,'previous_amount_usd':100,'comparison_pct':25.5,
             'mom_pct':None,'yoy_pct':25.5,'export_country_count':2,'top_country_share_pct':80}
        columns=[{'key':key,'label':key,'unit':'USD' if key.endswith('_usd') else '%' if key.endswith('_pct') else None} for key in row]
        table={'columns':columns,'rows':[row],'total_rows':1}
        document={'sections':[{'tables':[table]}],'meta':{},'sql':[]}
        original=deepcopy(document)
        for kind in ('RPT_MONTHLY','RPT_QUARTERLY'):
            sheet=load_workbook(io.BytesIO(report_xlsx({'report_type':kind},document))).worksheets[0]
            self.assertEqual([c.value for c in sheet[1]],[
                '수출액 (USD)','비교 기간 수출액 (USD)','비교 증감률 (%)','수출 국가 수','최대 국가 비중 (%)'])
            self.assertEqual(sheet['A2'].value,125.5)
            self.assertEqual(sheet['C2'].value,25.5)
        for record in ({'report_type':'RPT_ON_DEMAND'},
                       {'report_type':'RPT_MONTHLY','content_json':{'source_tables':[table]}}):
            actual=load_workbook(io.BytesIO(report_xlsx(record,document)))
            expected=load_workbook(io.BytesIO(xlsx_bytes([table],{},[])))
            self.assertEqual(actual.sheetnames,expected.sheetnames)
            for actual_sheet,expected_sheet in zip(actual,expected):
                self.assertEqual(list(actual_sheet.values),list(expected_sheet.values))
        self.assertEqual(document,original)
        empty={'sections':[{'tables':[]}],'meta':{},'sql':[]}
        self.assertEqual(load_workbook(io.BytesIO(report_xlsx({'report_type':'RPT_WEEKLY'},empty))).sheetnames,['정보'])

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
