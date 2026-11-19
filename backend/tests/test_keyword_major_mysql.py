"""실제 MySQL 두 연결 검증. 명시적 로컬 URL만 허용, 임시 DB 생성 후 삭제.

KEYWORD_MAJOR_MYSQL_URL=mysql+pymysql://root:...@127.0.0.1:3307/trade_integration
python -m unittest backend.tests.test_keyword_major_mysql -v
"""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from sqlalchemy import create_engine, MetaData, select, text
from sqlalchemy.engine import make_url
from backend.app.api.database import Database, Store
from backend.app.api.runtime_schema import audit
from backend.app.api.category_transaction import category_transaction
from backend.app.api.keyword_admin import category_change, major_change, categories
from backend.app.api.common import ApiError
from backend.app.models.keyword import KeywordCategory


@unittest.skipUnless(os.environ.get('KEYWORD_MAJOR_MYSQL_URL'), '명시적 로컬 MySQL 동시성 검증 전용')
class KeywordMajorMySQLTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=make_url(os.environ['KEYWORD_MAJOR_MYSQL_URL'])
        if url.host not in ('127.0.0.1','localhost','::1'):
            raise RuntimeError('운영 DB에서 실행하지 마세요. 로컬 MySQL만 허용합니다.')
        cls.name='keyword_test_'+uuid4().hex
        cls.admin=create_engine(url)
        with cls.admin.begin() as c:
            c.exec_driver_sql(f'CREATE DATABASE `{cls.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        cls.engine=create_engine(url.set(database=cls.name),pool_size=4)
        cls.addClassCleanup(cls.cleanup)
        meta=MetaData()
        audit.to_metadata(meta)
        KeywordCategory.__table__.to_metadata(meta)
        meta.create_all(cls.engine)
        cls.db=Database(cls.engine)
        # Reflection 초기화를 경쟁 자체와 분리한다.
        cls.db.table('api_audit_event');cls.db.table('keyword_category')

    @classmethod
    def cleanup(cls):
        cls.engine.dispose()
        with cls.admin.begin() as c: c.exec_driver_sql(f'DROP DATABASE `{cls.name}`')
        cls.admin.dispose()

    def setUp(self):
        self.name='대분류_'+uuid4().hex[:12]
        self.actor={'user_id':1}
        self.create(self.name)

    def create(self,name,minor=None):
        data={'major_name':name,'reason':'통합 검증','confirmed':True}
        if minor: data['minor_name']=minor
        with category_transaction(self.db) as c:
            return category_change(Store(self.db,c),self.actor,data)

    def version(self):
        with self.engine.connect() as c: return categories(Store(self.db,c))['major_version']

    def edit(self,store,version,remove):
        return major_change(store,self.actor,{'major_name':self.name,'new_major_name':self.name+'수정',
            'expected_version':version,'reason':'동시 검증','confirmed':True},removing=remove)

    def race(self,first,second):
        barrier=Barrier(2)
        def run(action):
            barrier.wait(timeout=10)
            try:
                with category_transaction(self.db,timeout=5) as c:
                    connection_id=c.scalar(text('SELECT CONNECTION_ID()'))
                    action(Store(self.db,c))
                return 200,connection_id
            except ApiError as error: return error.status,connection_id
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(run,(first,second)))
        self.assertEqual(sorted(status for status,_ in results),[200,409])
        self.assertEqual(len({identity for _,identity in results}),2)

    def test_same_version_rename_and_delete_only_one_succeeds(self):
        version=self.version()
        self.race(lambda s:self.edit(s,version,False),lambda s:self.edit(s,version,True))
        with self.engine.connect() as c:
            names=categories(Store(self.db,c))['major_names']
        self.assertNotIn(self.name,names)

    def test_delete_and_minor_insert_cannot_both_commit(self):
        version=self.version()
        self.race(lambda s:self.edit(s,version,True),lambda s:category_change(s,self.actor,
            {'major_name':self.name,'minor_name':'경쟁 중분류','confirmed':True,'reason':'동시 검증'}))
        with self.engine.connect() as c:
            data=categories(Store(self.db,c))
        used=any(row['major_name']==self.name for row in data['items'])
        self.assertEqual(self.name in data['major_names'],used)

    def test_exception_releases_lock_and_rolls_back_for_another_connection(self):
        version=self.version()
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with category_transaction(self.db) as c:
                first=c.scalar(text('SELECT CONNECTION_ID()'))
                self.edit(Store(self.db,c),version,True)
                raise RuntimeError('rollback')
        with category_transaction(self.db,timeout=0) as c:
            self.assertNotEqual(first,c.scalar(text('SELECT CONNECTION_ID()')))
            self.assertIn(self.name,categories(Store(self.db,c))['major_names'])
            self.edit(Store(self.db,c),version,True)

    def test_same_lock_also_works_in_read_committed(self):
        original=self.db
        self.db=Database(self.engine.execution_options(isolation_level='READ COMMITTED'))
        try: self.test_delete_and_minor_insert_cannot_both_commit()
        finally: self.db=original
