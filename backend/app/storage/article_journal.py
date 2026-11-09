"""Transactional row deltas between bounded article snapshot compactions."""
import base64
import json
import sqlite3

COMPACT_COMMITS = 100
COMPACT_BYTES = 8 * 1024 * 1024


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def encode(value):
    if isinstance(value, bytes):
        return {'sqlite_blob': base64.b64encode(value).decode('ascii')}
    raise TypeError(type(value).__name__)


def decode(value):
    return base64.b64decode(value['sqlite_blob'], validate=True)


class ArticleJournal:
    def __init__(self, db, excluded):
        self.db, self.excluded = db, excluded
        self.install()

    def install(self):
        db = self.db
        db.execute('PRAGMA recursive_triggers=ON')
        db.execute('CREATE TEMP TABLE IF NOT EXISTS _s3_article_changes (name TEXT, id INTEGER, PRIMARY KEY(name,id))')
        for row in db.execute("SELECT name FROM sqlite_temp_master WHERE type='trigger' AND name LIKE '_s3_article_%'").fetchall():
            db.execute('DROP TRIGGER temp.' + quoted(row[0]))
        self.supported = True
        for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            if name in self.excluded:
                continue
            if 'WITHOUT ROWID' in sql.upper():
                self.supported = False
                continue
            literal = "'" + name.replace("'", "''") + "'"
            for action, refs in (('INSERT', ('NEW',)), ('DELETE', ('OLD',)), ('UPDATE', ('OLD', 'NEW'))):
                body = ' '.join(f'INSERT OR IGNORE INTO _s3_article_changes VALUES ({literal},{ref}.rowid);' for ref in refs)
                db.execute(f'CREATE TEMP TRIGGER {quoted("_s3_article_" + name + action)} AFTER {action} ON main.{quoted(name)} BEGIN {body} END')
        self.schema = db.execute('PRAGMA main.schema_version').fetchone()[0]
        self.clear()

    def unchanged_schema(self):
        return self.supported and self.schema == self.db.execute('PRAGMA main.schema_version').fetchone()[0]

    def capture(self):
        changes = []
        for name, rowid in self.db.execute('SELECT name,id FROM temp._s3_article_changes ORDER BY name,id').fetchall():
            row = self.db.execute('SELECT * FROM ' + quoted(name) + ' WHERE rowid=?', (rowid,)).fetchone()
            changes.append([name, rowid, list(row) if row is not None else None])
        return json.dumps(changes, ensure_ascii=False, separators=(',', ':'), default=encode).encode() if changes else None

    def clear(self):
        self.db.execute('DELETE FROM temp._s3_article_changes')
        sqlite3.Connection.commit(self.db)


def replay(db, contents):
    # Deltas already include effects of source triggers. Re-running them while
    # restoring would invalidate indexes or create changes a second time.
    triggers = db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
    for name, _ in triggers:
        db.execute('DROP TRIGGER ' + quoted(name))
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for content in contents:
        changes = json.loads(content, object_hook=decode)
        for name, rowid, values in changes:
            if name not in tables or type(rowid) is not int:
                raise ValueError('Invalid article delta')
            db.execute('DELETE FROM ' + quoted(name) + ' WHERE rowid=?', (rowid,))
        for name, rowid, values in changes:
            if values is not None:
                columns = [r[1] for r in db.execute('PRAGMA table_info(' + quoted(name) + ')')]
                sql = 'INSERT INTO ' + quoted(name) + '(rowid,' + ','.join(map(quoted, columns)) + ') VALUES (' + ','.join('?' for _ in range(len(values) + 1)) + ')'
                db.execute(sql, [rowid, *values])
    for _, sql in triggers:
        db.execute(sql)
    db.commit()
