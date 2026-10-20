"""공통 모델 + UnData 통합 검사. country는 테스트 전용이며 운영 모델이 아닙니다."""
import unittest
from datetime import date
from decimal import Decimal
from sqlalchemy import Column, String, Table, event, inspect
from sqlalchemy.exc import IntegrityError
from backend.app.models.base import Base
from backend.app.models.un_data import UnData
from backend.app.models.hs_code import HsCodeMst
from backend.app.models.batch import BatchRun, ServiceOption
from backend.app.db.session import make_engine, session_scope
from backend.app.db.crud import CRUD
from backend.app.scheduler.jobs.un_data_load import load

# 공통 country 모델 도입 시 테스트 fixture를 해당 모델로 교체합니다.
if 'country' not in Base.metadata.tables:
    Table('country', Base.metadata, Column('country_code', String(12), primary_key=True))

class UnDataIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine('sqlite://')
        @event.listens_for(self.engine, 'connect')
        def enable_fk(connection, record):
            connection.execute('PRAGMA foreign_keys=ON')
        Base.metadata.create_all(self.engine)
        with session_scope(self.engine) as session:
            session.execute(Base.metadata.tables['country'].insert().values(country_code='KR'))
            CRUD(HsCodeMst).create(session, hs_code='010121', hs_ver='HS2022', name_ko='시험',
                                   original_name_ko='시험 원문', source_edition='TEST ONLY')
        self.row = dict(freq_code='M', period_start=date(2025,1,1), country_code='KR',
                        hs6_code='010121', hs_ver='HS2022', total_import_usd=Decimal('100.000'),
                        korea_import_usd=Decimal('25.000'), korea_share_pct=Decimal('25.000000'), valuation_basis='CIF')
    def tearDown(self):
        self.engine.dispose()
    def test_all_models_create_and_load(self):
        self.assertTrue(inspect(self.engine).has_table('un_data'))
        self.assertEqual(load(self.engine,[self.row]),1)
        self.assertEqual(load(self.engine,[self.row]),1)
        with session_scope(self.engine) as s:
            value=CRUD(UnData).get(s,tuple(self.row[k] for k in ('freq_code','period_start','country_code','hs6_code','hs_ver')))
            self.assertEqual(value.korea_share_pct,Decimal('25.000000'))
    def test_country_fk_enforced(self):
        with self.assertRaises(IntegrityError), session_scope(self.engine) as s:
            CRUD(UnData).upsert_many(s,[{**self.row,'country_code':'ZZ'}])
        with session_scope(self.engine) as s:
            self.assertEqual(CRUD(UnData).count(s),0)
    def test_hs_version_fk_enforced(self):
        with self.assertRaises(IntegrityError), session_scope(self.engine) as s:
            CRUD(UnData).upsert_many(s,[{**self.row,'hs_ver':'HS2017'}])
        with session_scope(self.engine) as s:
            self.assertEqual(CRUD(UnData).count(s),0)

if __name__ == '__main__':
    unittest.main()
