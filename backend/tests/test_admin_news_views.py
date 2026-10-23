"""초기/관리자 SQL의 실제 뷰를 실행해 출처·공개 상태·중복 집계를 검증합니다."""
import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ArticleViewsTest(unittest.TestCase):
    def check_views(self, admin):
        path = ('db/mysql/migrations/20261023_admin_news_erd.sql' if admin
                else 'db/mysql/init/01_schema.sql')
        views = re.findall(r'CREATE(?: OR REPLACE)? VIEW v_article_.*?;', (ROOT / path).read_text())
        self.assertEqual(len(views), 2)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''
            CREATE TABLE article(article_id INTEGER PRIMARY KEY, source_code TEXT,
                                 published_date TEXT, visibility_status TEXT DEFAULT 'VISIBLE');
            CREATE TABLE article_tag(article_id INTEGER, country_code TEXT);
        ''')
        for sql in views:
            db.execute(sql.replace('CREATE OR REPLACE VIEW', 'CREATE VIEW'))
        sources = ['KOTRA_MARKET', 'HANKYUNG', 'FNNEWS', 'KOTRA_SHORT_NEWS', 'OTHER']
        db.executemany("INSERT INTO article(article_id,source_code,published_date) VALUES (?,?,'2026-10-21')",
                       enumerate(sources, 1))
        db.executemany("INSERT INTO article_tag VALUES (?,'KR')", [(1,), (2,), (2,), (4,), (5,)])
        if admin:
            db.execute("INSERT INTO article VALUES (6,'KOTRA_SHORT_NEWS','2026-10-21','HIDDEN')")
            db.execute("INSERT INTO article_tag VALUES (6,'KR')")
        self.assertEqual(db.execute('SELECT * FROM v_article_day').fetchall(), [('2026-10-21', 4)])
        self.assertEqual(db.execute('SELECT * FROM v_article_country_day').fetchall(), [('2026-10-21', 'KR', 3)])

    def test_initial_views(self):
        self.check_views(False)

    def test_admin_views_exclude_hidden(self):
        self.check_views(True)
