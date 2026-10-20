"""조회 범위 제한 후에도 누락·잔여 행·부모 키 검증이 유지되는지 확인합니다."""
import unittest
from datetime import date
from sqlalchemy import event
from backend.tests import test_un_data as fixture
from backend.app.models.un_data import UnData
from backend.app.db.session import session_scope
from backend.app.db.crud import CRUD
from backend.app.scheduler.jobs import un_data_load as loader

class QueryScopeTest(unittest.TestCase):
    setUp=fixture.UnDataIntegrationTest.setUp
    tearDown=fixture.UnDataIntegrationTest.tearDown

    def test_verify_only_input_keys_without_orm_objects(self):
        rows=[self.row,{**self.row,'period_start':date(2025,2,1)}]
        with session_scope(self.engine) as s:CRUD(UnData).upsert_many(s,rows)
        statements=[]
        def audit(c,cur,sql,params,ctx,many):
            if sql.lstrip().upper().startswith('SELECT'):statements.append(sql)
        event.listen(self.engine,'before_cursor_execute',audit)
        with session_scope(self.engine) as s:
            loader.verify(s,[self.row]);self.assertEqual(len(s.identity_map),0)
        self.assertEqual(len(statements),1)
        self.assertIn('WHERE',statements[0]);self.assertIn(' IN ',statements[0])

    def test_verify_missing_and_changed_rows_rejected(self):
        with session_scope(self.engine) as s:
            with self.assertRaises(ValueError):loader.verify(s,[self.row])
            CRUD(UnData).upsert_many(s,[self.row])
            with self.assertRaises(ValueError):loader.verify(s,[{**self.row,'total_import_usd':200}])

    def test_scope_verify_still_detects_unexpected_remaining_row(self):
        extra={**self.row,'period_start':date(2025,2,1)}
        with session_scope(self.engine) as s:
            CRUD(UnData).upsert_many(s,[self.row,extra])
            with self.assertRaises(ValueError):loader.verify(s,[self.row],[UnData.country_code=='KR'])

    def test_verify_multiple_key_batches(self):
        rows=[{**self.row,'freq_code':'A','period_start':date(2022+i,1,1)} for i in range(205)]
        with session_scope(self.engine) as s:
            CRUD(UnData).upsert_many(s,rows);loader.verify(s,rows)
            with self.assertRaises(ValueError):loader.verify(s,rows+[{**self.row,'period_start':date(2500,1,1)}])

    def test_parent_queries_filtered_and_exact_version_required(self):
        statements=[]
        def audit(c,cur,sql,params,ctx,many):
            if sql.lstrip().upper().startswith('SELECT') and ('FROM country' in sql or 'FROM hs_code' in sql):statements.append(sql)
        event.listen(self.engine,'before_cursor_execute',audit)
        with session_scope(self.engine) as s:
            loader.parents(s,self.engine,[self.row])
            with self.assertRaisesRegex(ValueError,'HS2017'):
                loader.parents(s,self.engine,[{**self.row,'hs_ver':'HS2017'}])
            with self.assertRaisesRegex(ValueError,'ZZ'):
                loader.parents(s,self.engine,[{**self.row,'country_code':'ZZ'}])
        self.assertTrue(statements)
        self.assertTrue(all('WHERE' in sql and ' IN ' in sql for sql in statements))

    def test_parent_batch_boundary_reports_all_missing_keys(self):
        rows=[{**self.row,'hs6_code':f'{i:06d}'} for i in range(205)]
        with session_scope(self.engine) as s:
            with self.assertRaises(ValueError) as error:loader.parents(s,self.engine,rows)
        self.assertIn('000000',str(error.exception));self.assertIn('000204',str(error.exception))

if __name__=='__main__':unittest.main()
