"""실제 MySQL ERD 검증 (명시적 실행만). 모든 시험 행은 외부 트랜잭션에서 롤백.

ADMIN_NEWS_MYSQL_TEST=1 .venv/bin/python -m unittest backend.tests.test_admin_news_mysql -v
AUTO_INCREMENT 번호는 MySQL 특성상 롤백되지 않아 번호 공백이 생길 수 있습니다.
"""
import hashlib
import json
import os
import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.app.db import batch_log
from backend.app.db.session import make_engine
from backend.app.scheduler.jobs import news_load, shortnews_load
from backend.app.services import news_schema
from backend.tests.test_news_load import raw as news_raw, clean as news_clean
from backend.tests.test_shortnews_load import raw as short_raw, clean as short_clean

DAY = date(2000, 1, 1)


@unittest.skipUnless(os.environ.get('ADMIN_NEWS_MYSQL_TEST') == '1', '명시적 MySQL 검증 전용')
class AdminNewsMySQLTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine()
        cls.tables = ('article', 'article_tag', 'batch_run', 'news_keyword_daily',
                      'news_product_daily', 'service_option', 'article_classification', 'async_job')
        cls.before = cls.fingerprint()
        with cls.engine.connect() as conn:
            cls.policy = news_schema.classification_snapshot(conn)

    @classmethod
    def fingerprint(cls):
        with cls.engine.connect() as conn:
            result = {}
            for table in cls.tables:
                rows = sorted(json.dumps(list(r), default=str, ensure_ascii=False)
                              for r in conn.exec_driver_sql(f'SELECT * FROM `{table}`'))
                result[table] = hashlib.sha256('\n'.join(rows).encode()).hexdigest()
            return result

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.before != cls.fingerprint():
                raise AssertionError('MySQL 검증 후 기존 행이 달라졌습니다')
        finally:
            cls.engine.dispose()

    def setUp(self):
        self.conn = self.engine.connect()
        self.outer = self.conn.begin()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.outer.rollback)
        self.article = news_schema.reflect(self.conn, 'article')
        self.batch = news_schema.reflect(self.conn, 'batch_run')
        self.key = 'erd-check-' + uuid4().hex

        @contextmanager
        def scope(_engine):
            with Session(bind=self.conn, join_transaction_mode='create_savepoint') as session:
                try:
                    yield session
                    session.commit()  # SAVEPOINT만 해제; 외부 트랜잭션은 tearDown에서 롤백.
                except BaseException:
                    session.rollback()
                    raise

        for module in (news_load, shortnews_load, batch_log):
            patcher = patch.object(module, 'session_scope', scope)
            patcher.start()
            self.addCleanup(patcher.stop)

    def scalar(self, sql, **params):
        return self.conn.execute(text(sql), params).scalar_one()

    def insert_article(self, source='KOTRA_SHORT_NEWS', **changes):
        with Session(bind=self.conn, join_transaction_mode='create_savepoint') as session:
            batch_log.ensure_source(session, batch_log.Source(source, source))
            session.commit()
        row = dict(source_code=source, external_id=self.key + source,
                   title='ERD 롤백 검증', published_date=DAY, source_url='https://example.com/erd-test',
                   body_masked='검증용 본문', category=None, visibility_status='VISIBLE',
                   first_ingested_at=news_schema.now(), admin_meta_json=news_schema.admin_defaults(self.policy))
        row.update(changes)
        return self.conn.execute(self.article.insert().values(**row)).inserted_primary_key[0]

    def reject(self, action, code):
        with self.assertRaises(DBAPIError) as caught:
            with self.conn.begin_nested():
                action()
        self.assertEqual(caught.exception.orig.args[0], code)

    def require_trigger(self, name):
        if not self.scalar('SELECT COUNT(*) FROM information_schema.TRIGGERS '
                           'WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=:n', n=name):
            self.skipTest('RDS 1419: 트리거 적용 대기 ' + name)

    def test_required_columns_constraints_and_foreign_keys(self):
        columns = {c['name']: c for c in inspect(self.conn).get_columns('article')}
        self.assertFalse(columns['admin_meta_json']['nullable'])
        self.assertTrue(columns['first_ingested_at']['nullable'])
        self.assertEqual(columns['visibility_status']['type'].enums, ['VISIBLE', 'HIDDEN'])
        fks = inspect(self.conn).get_foreign_keys('article_classification')
        self.assertEqual({f['referred_table'] for f in fks},
                         {'article', 'keyword_category', 'keyword_topic', 'async_job'})
        self.assertEqual(len(inspect(self.conn).get_check_constraints('article_classification')), 3)
        self.assertFalse(next(c for c in inspect(self.conn).get_columns('batch_run')
                              if c['name'] == 'detail_json')['nullable'])

    def test_views_preserve_market_scope_and_expose_source_and_integrated_counts(self):
        sources = ('KOTRA_MARKET', 'HANKYUNG', 'FNNEWS', 'KOTRA_SHORT_NEWS')
        views = ('v_article_day', 'v_article_country_day', 'v_article_source_day',
                 'v_article_source_country_day', 'v_article_integrated_day', 'v_article_integrated_country_day')
        def counts():
            return {view: self.scalar('SELECT COALESCE(SUM(article_count),0) FROM ' + view
                    + ' WHERE published_date=:d' + (" AND country_code='US'" if 'country' in view else ''), d=DAY)
                    for view in views}
        before = counts()
        ids = {}
        for source in sources:
            aid = self.insert_article(source)
            ids[source] = aid
            self.conn.execute(text("INSERT INTO article_tag(article_id,country_code) VALUES (:id,'US')"), {'id': aid})
            self.conn.execute(text("INSERT INTO article_tag(article_id,keyword_text) VALUES (:id,'AI'),(:id,'무역')"), {'id': aid})
        def assert_delta(expected):
            actual = counts()
            self.assertEqual({view: actual[view] - before[view] for view in views}, dict(zip(views, expected)))
        assert_delta((1, 1, 4, 4, 2, 2))
        for source in sources:
            self.assertGreaterEqual(self.scalar('SELECT article_count FROM v_article_source_day '
                                               'WHERE published_date=:d AND source_code=:s', d=DAY, s=source), 1)
        # Existing MARKET views exclude hidden rows. Source/integrated views retain
        # their current total-ingestion contract; migration must not broaden MARKET.
        self.conn.execute(self.article.update().where(self.article.c.article_id.in_(
            [ids['KOTRA_MARKET'], ids['KOTRA_SHORT_NEWS']])).values(visibility_status='HIDDEN'))
        assert_delta((0, 0, 4, 4, 2, 2))

    def test_existing_news_and_shortnews_loaders_repeat_preserve_admin_state(self):
        news_load.load(self.engine, [news_raw(self.key, DAY.isoformat())], [news_clean(self.key, DAY.isoformat(), 'AI', 'US')])
        shortnews_load.load(self.engine, [short_raw(self.key, DAY.isoformat())], [short_clean(self.key, 'AI', 'US', '')])
        query = select(self.article).where(self.article.c.external_id == self.key)
        before = {r['source_code']: dict(r) for r in self.conn.execute(query).mappings()}
        self.assertEqual(len(before), 2)
        for row in before.values():
            self.assertIsNotNone(row['first_ingested_at'])
            self.assertEqual(row['admin_meta_json']['classification_input'], self.policy)
            meta = row['admin_meta_json']
            meta.update(country_tags_manual=True, hs_tags_manual=True)
            self.conn.execute(self.article.update().where(self.article.c.article_id == row['article_id']).values(
                admin_meta_json=meta, visibility_status='HIDDEN'))
        news_load.load(self.engine, [news_raw(self.key, DAY.isoformat())], [news_clean(self.key, DAY.isoformat(), 'AI', 'CN')])
        shortnews_load.load(self.engine, [short_raw(self.key, DAY.isoformat())], [short_clean(self.key, 'AI', 'CN', '')])
        after = {r['source_code']: dict(r) for r in self.conn.execute(query).mappings()}
        for source, old in before.items():
            new = after[source]
            self.assertEqual((old['article_id'], old['first_ingested_at'], old['admin_meta_json']),
                             (new['article_id'], new['first_ingested_at'], new['admin_meta_json']))
            self.assertEqual(new['visibility_status'], 'HIDDEN')
            self.assertEqual(self.scalar('SELECT COUNT(*) FROM article_tag WHERE article_id=:id', id=new['article_id']), 2)
            self.assertEqual(self.scalar("SELECT country_code FROM article_tag WHERE article_id=:id AND country_code IS NOT NULL", id=new['article_id']), 'US')

    def test_json_schema_rejects_invalid_article_and_batch_metadata(self):
        invalid = news_schema.admin_defaults(self.policy)
        del invalid['visibility']  # Keep snapshot valid so CHECK, not BEFORE INSERT, rejects it.
        self.reject(lambda: self.insert_article(admin_meta_json=invalid), 3819)
        row = dict(source_code='KOTRA_SHORT_NEWS', job_name='ERD_ROLLBACK_CHECK', target_table='article',
                   period_start=DAY, period_end=DAY, detail_json={})
        self.reject(lambda: self.conn.execute(self.batch.insert().values(**row)), 3819)

    def test_common_batch_logger_success_failure_and_detail_json(self):
        with batch_log.track_batch(shortnews_load.SOURCE, 'ERD_ROLLBACK_CHECK', 'article', DAY, DAY, engine=self.engine) as record:
            self.assertIsNotNone(record.run_id)
            record.received_count = 3
        saved = self.conn.execute(select(self.batch).where(self.batch.c.run_id == record.run_id)).mappings().one()
        self.assertEqual((saved['status'], saved['received_count'], saved['detail_json']), ('SUCCESS', 3, {'schema_version': 1}))
        with self.assertRaisesRegex(RuntimeError, 'rollback test'):
            with batch_log.track_batch(shortnews_load.SOURCE, 'ERD_ROLLBACK_FAIL', 'article', DAY, DAY, engine=self.engine) as failed:
                raise RuntimeError('rollback test')
        self.assertEqual(self.scalar('SELECT status FROM batch_run WHERE run_id=:id', id=failed.run_id), 'FAILED')

    def test_classification_unique_check_foreign_keys_and_cascade(self):
        aid = self.insert_article()
        category = self.scalar('SELECT MIN(category_id) FROM keyword_category')
        table = news_schema.reflect(self.conn, 'article_classification')
        row = dict(article_id=aid, category_id=category, source_kind='BASE', keyword_text=None,
                   basis_revision=1, representative_term='검증', basis_key='f'*64, applied_at=news_schema.now())
        self.conn.execute(table.insert().values(**row))
        self.reject(lambda: self.conn.execute(table.insert().values(**row)), 1062)
        self.reject(lambda: self.conn.execute(table.insert().values(**{**row, 'basis_key': 'e'*64, 'basis_revision': 0})), 3819)
        self.reject(lambda: self.conn.execute(table.insert().values(**{**row, 'basis_key': 'e'*64, 'keyword_text': 'invalid'})), 3819)
        self.reject(lambda: self.conn.execute(table.insert().values(**{**row, 'basis_key': 'e'*64, 'category_id': 4294967295})), 1452)
        self.conn.execute(self.article.delete().where(self.article.c.article_id == aid))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM article_classification WHERE article_id=:id', id=aid), 0)

    def test_article_trigger_rejects_missing_ingest_time_and_snapshot(self):
        self.require_trigger('trg_article_insert_v3')
        self.reject(lambda: self.insert_article(first_ingested_at=None), 1644)
        self.reject(lambda: self.insert_article(admin_meta_json=news_schema.admin_defaults(None)), 1644)

    def test_batch_triggers_reject_self_retry(self):
        self.require_trigger('trg_batch_run_insert_v3')
        self.require_trigger('trg_batch_run_update_v3')
        # This test must also pass in a fresh DB without a prior short-news load.
        with Session(bind=self.conn, join_transaction_mode='create_savepoint') as session:
            batch_log.ensure_source(session, shortnews_load.SOURCE)
            session.commit()
        row = dict(source_code='KOTRA_SHORT_NEWS', job_name='ERD_ROLLBACK_CHECK', target_table='article',
                   period_start=DAY, period_end=DAY, detail_json={'schema_version': 1})
        rid = self.conn.execute(self.batch.insert().values(**row)).inserted_primary_key[0]
        self.reject(lambda: self.conn.execute(self.batch.update().where(self.batch.c.run_id == rid).values(retry_of_run_id=rid)), 1644)
        self.reject(lambda: self.conn.execute(self.batch.insert().values(**row, run_id=rid, retry_of_run_id=rid)), 1644)
