"""관리자 컬럼이 있는 합성 SQLite에서 집계 의미만 시험합니다. MySQL DDL 시험이 아닙니다."""
import json
import re
import sqlite3
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = {
    'old': ROOT / 'db/mysql/migrations/20261022_trade_news_sources.sql',
    'latest': ROOT / 'db/mysql/migrations/20261025_news_source_views.sql',
}


def apply_sqlite_views(db, path):
    """검증용으로 INSERT IGNORE와 CREATE OR REPLACE VIEW 두 구문만 SQLite에 맞춥니다."""
    sql = re.sub(r'^--.*$', '', path.read_text(), flags=re.M)
    for statement in sql.split(';'):
        statement = statement.strip()
        if not statement:
            continue
        match = re.match(r'CREATE OR REPLACE VIEW (\w+)\b', statement)
        if match:
            db.execute('DROP VIEW IF EXISTS ' + match[1])
            db.execute(statement.replace('CREATE OR REPLACE VIEW', 'CREATE VIEW', 1))
        else:
            assert statement.startswith('INSERT IGNORE INTO service_option')
            db.execute(statement.replace('INSERT IGNORE', 'INSERT OR IGNORE', 1))


@pytest.mark.parametrize('order', [
    ('old', 'latest'), ('latest', 'old'), ('old', 'old'),
    ('latest', 'latest'), ('old', 'latest', 'old', 'latest'),
])
def test_view_order_reapply_preserves_each_population_and_admin_rows(order):
    db = sqlite3.connect(':memory:')
    try:
        db.executescript('''
            CREATE TABLE article(article_id INTEGER PRIMARY KEY,source_code TEXT,
                published_date TEXT,visibility_status TEXT,admin_meta_json TEXT);
            CREATE TABLE article_tag(article_id INTEGER,country_code TEXT);
            CREATE TABLE service_option(option_code TEXT PRIMARY KEY,option_type TEXT,
                label_ko TEXT,value_json TEXT,sort_order INTEGER,enabled INTEGER);
        ''')
        rows = [
            (1,'KOTRA_MARKET','2026-10-21','VISIBLE'), (2,'KOTRA_MARKET','2026-10-21','HIDDEN'),
            (3,'KOTRA_SHORT_NEWS','2026-10-21','VISIBLE'), (4,'KOTRA_SHORT_NEWS','2026-10-21','HIDDEN'),
            (5,'HANKYUNG','2026-10-21','VISIBLE'), (6,'FNNEWS','2026-10-21','HIDDEN'),
            (7,'KOTRA_MARKET','2026-10-22','VISIBLE'), (8,'HANKYUNG','2026-10-22','VISIBLE'),
            (9,'KOTRA_SHORT_NEWS','2026-10-22','HIDDEN'), (10,'OTHER','2026-10-22','VISIBLE'),
        ]
        db.executemany('INSERT INTO article VALUES (?,?,?,?,?)',
                       [(*r,json.dumps({'country_tags_manual':True,'fixture':r[0]})) for r in rows])
        db.executemany('INSERT INTO article_tag VALUES (?,?)',
                       [(1,'US'),(1,'US'),(1,'KR'),(2,'US'),(3,'US'),(4,'KR'),(5,'US'),
                        (5,None),(6,'US'),(7,'KR'),(8,'KR'),(9,'US'),(10,'US')])
        db.execute("INSERT INTO service_option VALUES ('HANKYUNG','SOURCE','manual','{}',99,0)")
        original_articles=db.execute('SELECT * FROM article ORDER BY article_id').fetchall()
        original_tags=db.execute('SELECT * FROM article_tag').fetchall()
        original_source=db.execute("SELECT * FROM service_option WHERE option_code='HANKYUNG'").fetchone()
        expected={
            'v_article_day':[('2026-10-21',1),('2026-10-22',1)],
            'v_article_country_day':[('2026-10-21','KR',1),('2026-10-21','US',1),('2026-10-22','KR',1)],
            'v_article_source_day':sorted([
                ('2026-10-21','KOTRA_MARKET',2),('2026-10-21','KOTRA_SHORT_NEWS',2),
                ('2026-10-21','HANKYUNG',1),('2026-10-21','FNNEWS',1),
                ('2026-10-22','KOTRA_MARKET',1),('2026-10-22','KOTRA_SHORT_NEWS',1),
                ('2026-10-22','HANKYUNG',1),('2026-10-22','OTHER',1)]),
            'v_article_source_country_day':sorted([
                ('2026-10-21','KOTRA_MARKET','KR',1),('2026-10-21','KOTRA_MARKET','US',2),
                ('2026-10-21','KOTRA_SHORT_NEWS','KR',1),('2026-10-21','KOTRA_SHORT_NEWS','US',1),
                ('2026-10-21','HANKYUNG','US',1),('2026-10-21','FNNEWS','US',1),
                ('2026-10-22','KOTRA_MARKET','KR',1),('2026-10-22','KOTRA_SHORT_NEWS','US',1),
                ('2026-10-22','HANKYUNG','KR',1),('2026-10-22','OTHER','US',1)]),
            'v_article_integrated_day':[('2026-10-21',4),('2026-10-22',2)],
            'v_article_integrated_country_day':[('2026-10-21','KR',2),('2026-10-21','US',3),
                                                ('2026-10-22','KR',1),('2026-10-22','US',1)],
        }
        for script in order:
            apply_sqlite_views(db,SCRIPTS[script])
            for view,values in expected.items():
                assert sorted(db.execute('SELECT * FROM '+view).fetchall())==values,(script,view)
            assert db.execute('SELECT * FROM article ORDER BY article_id').fetchall()==original_articles
            assert db.execute('SELECT * FROM article_tag').fetchall()==original_tags
            assert db.execute("SELECT * FROM service_option WHERE option_code='HANKYUNG'").fetchone()==original_source
        if 'old' in order:
            assert db.execute('SELECT COUNT(*) FROM service_option').fetchone()[0]==2
            assert db.execute("SELECT enabled FROM service_option WHERE option_code='FNNEWS'").fetchone()[0]==1
    finally:
        db.close()
