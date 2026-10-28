"""일일 집계용 게시 날짜 색인. 원문 오류가 다른 날짜의 수집을 막지 않게 합니다."""
import json
from news.dates import publication_date


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS article_dates(url TEXT PRIMARY KEY, day TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS article_dates_day ON article_dates(day);
      CREATE TABLE IF NOT EXISTS export_pending(url TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS date_errors(url TEXT PRIMARY KEY, reason TEXT NOT NULL);
    ''')
    for table in ('article_history','reference_articles'):
        for action in ('INSERT','UPDATE','DELETE'):
            urls=['NEW.url'] if action=='INSERT' else ['OLD.url','NEW.url'] if action=='UPDATE' else ['OLD.url']
            invalidation='\n'.join(f'DELETE FROM article_dates WHERE url={url};' for url in urls)
            db.executescript(f'CREATE TRIGGER IF NOT EXISTS {table}_date_{action.lower()} AFTER {action} ON {table} BEGIN {invalidation} END;')


def sync_dates(db):
    db.execute('DELETE FROM date_errors WHERE url NOT IN (SELECT url FROM article_history UNION SELECT url FROM reference_articles)')
    rows=db.execute('''SELECT h.url,h.payload FROM article_history h LEFT JOIN article_dates d USING(url) WHERE d.url IS NULL
      UNION ALL SELECT r.url,r.payload FROM reference_articles r LEFT JOIN article_dates d USING(url)
      WHERE d.url IS NULL AND NOT EXISTS(SELECT 1 FROM article_history h WHERE h.url=r.url)''').fetchall()
    for row in rows:
        try:day=publication_date(json.loads(row['payload'])['published_at']).isoformat()
        except (ValueError,TypeError,KeyError) as exc:
            db.execute('INSERT OR REPLACE INTO date_errors VALUES (?,?)',(row['url'],f'{type(exc).__name__}: {exc}'))
            continue
        db.execute('DELETE FROM date_errors WHERE url=?',(row['url'],))
        db.execute('INSERT OR REPLACE INTO article_dates VALUES (?,?)',(row['url'],day))
    return len(rows)


def data_issues(db):
    errors=[dict(r) for r in db.execute('SELECT url,reason FROM date_errors ORDER BY url')]
    return {'date_errors':errors,'date_index_complete':not bool(errors),
            'note':'게시 날짜 오류 원문은 보존하고 해당 기사의 날짜별 집계를 보류했습니다.' if errors else ''}
