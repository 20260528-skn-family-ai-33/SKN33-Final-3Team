"""ERD 재개·정의 검증·잠금 경계 회귀. 임시 SQLite/스키마 정의만 사용하며 외부 연결 없음."""
import copy
import json
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, inspect as sa_inspect
from sqlalchemy.engine import make_url

from db.mysql.migrations import apply_admin_news_erd as migration
from db.mysql.migrations.admin_news_schema import Contract, SchemaMismatch, normalize
from backend.app.db import batch_log
from backend.app.scheduler.jobs import shortnews_load
from backend.tests import test_shortnews_load as short_fixture

raw = short_fixture.raw


class DdlConnection:
    def __init__(self, ddl):
        self.ddl = ddl

    def exec_driver_sql(self, sql):
        table = sql.split('`')[1]
        return SimpleNamespace(first=lambda: (table, self.ddl[table]))


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.contract = Contract(migration.SQL)
        self.conn = DdlConnection(copy.deepcopy(self.contract.ddl))

    def test_complete_applied_schema_is_accepted(self):
        self.contract.validate_existing(self.conn, migration.steps(), set(self.conn.ddl))

    def test_existing_classification_table_missing_unique_is_rejected(self):
        self.conn.ddl['article_classification'] = '\n'.join(
            line for line in self.conn.ddl['article_classification'].splitlines()
            if 'UNIQUE KEY `uq_classification_basis`' not in line)
        with self.assertRaisesRegex(SchemaMismatch, 'article_classification'):
            self.contract.validate_existing(self.conn, migration.steps(), set(self.conn.ddl))

    def test_same_named_index_wrong_columns_is_rejected(self):
        self.conn.ddl['article'] = self.conn.ddl['article'].replace(
            '(`visibility_status`,`published_date`,`article_id`)', '(`published_date`,`article_id`)')
        with self.assertRaises(SchemaMismatch):
            self.contract.validate(self.conn, 'index', 'article', 'ix_article_visible_date')

    def test_missing_base_article_unique_is_rejected(self):
        self.conn.ddl['article'] = '\n'.join(line for line in self.conn.ddl['article'].splitlines()
                                             if 'uq_article_source_id' not in line)
        with self.assertRaises(SchemaMismatch):
            self.contract.validate_existing(self.conn, migration.steps(), set(self.conn.ddl))

    def test_existing_column_wrong_default_or_type_is_rejected(self):
        for old, new in (("DEFAULT 'VISIBLE'", "DEFAULT 'HIDDEN'"),
                         ("enum('VISIBLE','HIDDEN')", 'varchar(10)')):
            with self.subTest(change=new):
                self.conn.ddl['article'] = self.contract.ddl['article'].replace(old, new)
                with self.assertRaises(SchemaMismatch):
                    self.contract.validate(self.conn, 'column', 'article', 'visibility_status')

    def test_nullable_json_resume_is_accepted_but_default_is_not(self):
        original = self.contract.ddl['article']
        self.conn.ddl['article'] = original.replace('`admin_meta_json` json NOT NULL', '`admin_meta_json` json DEFAULT NULL')
        self.contract.validate(self.conn, 'column', 'article', 'admin_meta_json')
        self.conn.ddl['article'] = original.replace('`admin_meta_json` json NOT NULL', "`admin_meta_json` json DEFAULT ('{}')")
        with self.assertRaises(SchemaMismatch):
            self.contract.validate(self.conn, 'column', 'article', 'admin_meta_json')

    def test_foreign_key_action_and_check_contents_are_checked(self):
        for table, name, old, new in (
            ('batch_run', 'fk_batch_retry', 'ON DELETE SET NULL', 'ON DELETE CASCADE'),
            ('article_classification', '-', '(`basis_revision` >= 1)', '(`basis_revision` >= 0)'),
        ):
            with self.subTest(name=name):
                self.conn.ddl[table] = self.contract.ddl[table].replace(old, new)
                self.assertNotEqual(self.conn.ddl[table], self.contract.ddl[table])
                with self.assertRaises(SchemaMismatch):
                    self.contract.validate(self.conn, 'table' if name == '-' else 'constraint', table, name)

    def test_enum_only_expected_append_is_accepted(self):
        original = self.contract.ddl['batch_run']
        self.conn.ddl['batch_run'] = original.replace(",'hs_code_correlation'", '')
        self.contract.validate(self.conn, 'enum', 'batch_run', 'hs_code_correlation')
        self.conn.ddl['batch_run'] = original.replace("'hs_code_correlation'", "'un_data'")
        with self.assertRaises(SchemaMismatch):
            self.contract.validate(self.conn, 'enum', 'batch_run', 'hs_code_correlation')

    def test_check_literal_and_parenthesis_changes_are_not_erased(self):
        self.assertNotEqual(normalize("CHECK (x='a b')"), normalize("CHECK (x='ab')"))
        self.assertNotEqual(normalize("CHECK (x='BASE')"), normalize("CHECK (x='base')"))
        self.assertNotEqual(normalize('CHECK ((a OR b) AND c)'), normalize('CHECK (a OR (b AND c))'))
        self.assertNotEqual(normalize('CHECK (x>0)'), normalize('CHECK (x>0) /*!80016 NOT ENFORCED */'))

    def test_sequence_and_comments_do_not_change_schema(self):
        self.assertEqual(normalize("CREATE TABLE t(x int COMMENT 'one') AUTO_INCREMENT=1"),
                         normalize("create table `t` (`x` INT COMMENT 'two') AUTO_INCREMENT=42"))


class ResumeConnection:
    """MySQL 제어 명령만 대역. 데이터 UPDATE/스냅샷/커밋/롤백은 실제 임시 DB 사용."""
    def __init__(self, inner, corrupt=False):
        self.inner, self.corrupt, self.trigger_calls = inner, corrupt, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.inner.rollback()

    def exec_driver_sql(self, sql):
        if sql == 'SELECT VERSION()':
            return SimpleNamespace(scalar_one=lambda: '8.4.11')
        if sql.startswith('SET SESSION'):
            return None
        if sql.startswith('SHOW CREATE TABLE'):
            return SimpleNamespace(first=lambda: ('article', 'CREATE TABLE article (article_id INTEGER)'))
        if sql == 'SHOW TRIGGERS':
            return SimpleNamespace(mappings=lambda: [])
        if 'FROM batch_run' in sql:
            return SimpleNamespace(scalar_one=lambda: 0)
        if sql.startswith('CREATE TRIGGER'):
            self.trigger_calls += 1
            raise AssertionError('보류 트리거 실행 금지')
        result = self.inner.exec_driver_sql(sql)
        if self.corrupt and sql.startswith('UPDATE article'):
            self.inner.exec_driver_sql("UPDATE article SET title='corrupt' WHERE article_id=1")
        return result

    def execute(self, sql, args=None):
        if 'information_schema.TRIGGERS' in str(sql):
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        return SimpleNamespace(scalar_one=lambda: 1)  # GET_LOCK/RELEASE_LOCK

    def commit(self):
        self.inner.commit()

    def rollback(self):
        self.inner.rollback()


class ResumeTest(unittest.TestCase):
    def exercise(self, corrupt=False):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine('sqlite:///' + str(Path(directory) / 'test.db'))
            with engine.connect() as inner:
                inner.exec_driver_sql('CREATE TABLE article(article_id INTEGER PRIMARY KEY, title TEXT, admin_meta_json JSON NULL)')
                inner.exec_driver_sql('CREATE TABLE users(user_id INTEGER PRIMARY KEY)')
                inner.exec_driver_sql("INSERT INTO article VALUES(1,'keep',NULL),(2,'keep2','{\"manual\":true}')")
                inner.commit()
                conn = ResumeConnection(inner, corrupt)
                fake_engine = SimpleNamespace(dialect=SimpleNamespace(name='mysql'), url=make_url('mysql://test/test'),
                                              connect=lambda: conn, dispose=lambda: None)
                tasks = [s for s in migration.steps() if s[:3] == ('data', 'article', 'admin_meta_json')
                         or s[:3] == ('trigger', 'article', 'trg_article_insert_v3')]
                with patch.object(migration, 'make_engine', return_value=fake_engine), \
                        patch.object(migration, 'ROOT', Path(directory)), \
                        patch.object(migration, 'BACKUP_TABLES', ('article',)), \
                        patch.object(migration, 'steps', return_value=tasks), \
                        patch.object(migration, 'Contract', return_value=Mock()), \
                        patch.object(migration, 'inspect', side_effect=lambda c: sa_inspect(c.inner)), \
                        patch.object(migration, 'article_load_lock', side_effect=lambda _: nullcontext()):
                    if corrupt:
                        with self.assertRaisesRegex(RuntimeError, '기존 행'):
                            migration.run(apply=True)
                        journal = next((Path(directory) / 'data/db_backups').glob('*/journal.json'))
                        self.assertEqual(json.loads(journal.read_text())['status'], 'FAILED_PRESERVATION')
                    else:
                        first = migration.run(apply=True)
                        self.assertEqual(first['status'], 'PARTIAL')  # 트리거 보류만 남음
                        self.assertEqual(first['backfilled_rows'], {'article.admin_meta_json': 1})
                        self.assertTrue(first['preserved']['article']['unchanged'])
                        self.assertEqual(first['steps'][-1]['status'], 'DEFERRED')
                        second = migration.run(apply=True)
                        self.assertEqual(second['backfilled_rows'], {})
                        self.assertEqual(second['steps'][0]['status'], 'EXISTS')
                        self.assertEqual(conn.trigger_calls, 0)
                        values = inner.exec_driver_sql('SELECT title,admin_meta_json FROM article ORDER BY article_id').all()
                        self.assertEqual(values[0][0], 'keep')
                        self.assertEqual(json.loads(values[1][1]), {'manual': True})
                        self.assertEqual(json.loads(values[0][1])['schema_version'], 1)
            engine.dispose()

    def test_resume_expected_backfill_and_repeat_preserve_existing_values(self):
        self.exercise()

    def test_unexpected_change_still_fails_and_records_failure(self):
        self.exercise(corrupt=True)


class MigrationOverlapTest(unittest.TestCase):
    def setUp(self):
        self.fixture = short_fixture.ShortnewsLoadTest()
        self.fixture.extended = False
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_migration_completing_while_shortnews_waits_uses_new_schema(self):
        @contextmanager
        def migrated(_):
            with self.fixture.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE article ADD COLUMN visibility_status VARCHAR(10) NOT NULL DEFAULT 'VISIBLE'")
                conn.exec_driver_sql('ALTER TABLE article ADD COLUMN first_ingested_at DATETIME NULL')
                conn.exec_driver_sql('ALTER TABLE article ADD COLUMN admin_meta_json JSON NOT NULL')
            yield
        with patch.object(shortnews_load, 'article_load_lock', migrated):
            self.assertEqual(shortnews_load.load(self.fixture.engine, [raw()])['articles'], 1)
        first = self.fixture.rows('article')[0]
        self.assertIsNotNone(first['first_ingested_at'])
        self.assertIsNotNone(first['admin_meta_json']['classification_input'])
        shortnews_load.load(self.fixture.engine, [raw()])
        self.assertEqual(len(self.fixture.rows('article')), 1)
        self.assertEqual(self.fixture.rows('article')[0]['article_id'], first['article_id'])

    def check_batch_overlap(self, module):
        applied = False
        @contextmanager
        def migrated(_):
            nonlocal applied
            if not applied:
                with self.fixture.engine.begin() as conn:
                    conn.exec_driver_sql('ALTER TABLE batch_run ADD COLUMN detail_json JSON NOT NULL')
                applied = True
            yield
        from datetime import date
        day = date(2026, 10, 22)
        with patch.object(module, 'article_load_lock', migrated):
            if module is shortnews_load:
                with module.track_load(self.fixture.engine, (day, day), 1):
                    pass
            else:
                with module.track_batch(shortnews_load.SOURCE, 'TEST', 'article', day, day,
                                        engine=self.fixture.engine) as record:
                    self.assertIsNotNone(record.run_id)
        row = self.fixture.rows('batch_run')[0]
        self.assertEqual(row['status'], 'SUCCESS')
        self.assertEqual(row['detail_json']['schema_version'], 1)

    def test_shortnews_batch_reflects_after_migration_lock(self):
        self.check_batch_overlap(shortnews_load)

    def test_common_batch_reflects_after_migration_lock(self):
        self.check_batch_overlap(batch_log)
