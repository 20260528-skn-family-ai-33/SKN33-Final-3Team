"""Crawler budget/queue SQLite working copy; each commit is durable before HTTP.

The database is a chunked S3 snapshot with CAS. Competing/stale crawlers fail
before reserve() can authorize a request. A failed persistence poisons the
connection; restarting restores the last confirmed snapshot.
"""
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from .objects import S3Objects, StorageError, digest, enabled, file_digest, json_bytes

PREFIX = 'raw/trade-news/database'
POINTER = PREFIX + '/_current.json'
CHUNK_BYTES = 256 * 1024
STATE_TABLES = frozenset(('budget', 'queue', 'queue_period', 'discovery_queue',
                          'list_blocks', 'list_retries', 'body_access', 'state'))


def copy_tables(source, target, selected):
    """Copy one logical DB partition without scanning article bodies for budget writes."""
    schema = source.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type='table' DESC,name").fetchall()
    for kind, name, table, sql in schema:
        if table not in selected or name.startswith('sqlite_'):
            continue
        if kind == 'table':
            target.execute(sql)
            quoted = '"' + name.replace('"', '""') + '"'
            rows = source.execute('SELECT * FROM ' + quoted)
            columns = len(rows.description)
            target.executemany('INSERT INTO ' + quoted + ' VALUES (' + ','.join('?' * columns) + ')', rows)
    # Triggers/indexes are created after row copies, preserving their original SQL.
    for kind, name, table, sql in schema:
        if kind != 'table' and table in selected and not name.startswith('sqlite_'):
            target.execute(sql)
    target.commit()


def partition(db, name):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    selected = tables & STATE_TABLES if name == 'state' else tables - STATE_TABLES
    with closing(sqlite3.connect(':memory:')) as output:
        # An empty partition still needs an initialized SQLite header.
        output.execute('PRAGMA user_version=1')
        copy_tables(db, output, selected)
        return output.serialize()


def database_stamp(path):
    result = []
    for file in (path, path.with_name(path.name + '-wal')):
        if file.exists():
            info = file.stat()
            result.append((info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        else:
            result.append(None)
    return tuple(result)


def clear_journals(path):
    # Restore callers hold crawler.lock. Old unconfirmed WAL/rollback pages must
    # never be replayed into a database restored from a different S3 snapshot.
    for suffix in ('-wal', '-shm', '-journal'):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def prepared_snapshot(path, store, previous=None):
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        content = db.serialize()
    return snapshot(content, store, {p['sha256'] for p in (previous or {}).get('chunks', [])})


def chunks(content):
    for start in range(0, len(content), CHUNK_BYTES):
        yield content[start:start + CHUNK_BYTES]


def snapshot(content, store, known=()):
    parts = []
    for part in chunks(content):
        checksum = digest(part)
        key = PREFIX + '/_chunks/' + checksum
        if checksum not in known:
            store.immutable(key, part)
        parts.append({'key': key, 'sha256': checksum, 'bytes': len(part)})
    return {'schema': 1, 'sha256': digest(content), 'bytes': len(content), 'chunks': parts}


def verify_snapshot(meta, store, stream=None):
    if meta.get('schema') != 1 or not isinstance(meta.get('chunks'), list):
        raise StorageError('Invalid crawler database pointer')
    for part in meta['chunks']:
        if part['key'] != PREFIX + '/_chunks/' + part['sha256']:
            raise StorageError('Invalid crawler database chunk')
        store.verify(part['key'], part['sha256'], stream)


def restore(path, store=None, *, details=False):
    store = store or S3Objects()
    try:
        body, etag = store.get(POINTER)
    except FileNotFoundError as exc:
        raise StorageError('Migrate the crawler budget database before enabling S3') from exc
    meta = json.loads(body)
    if meta.get('maintenance'):
        raise StorageError('Crawler storage maintenance in progress', retryable=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        if meta.get('schema') == 2:
            with tempfile.TemporaryDirectory(dir=path.parent, prefix='.restore-') as folder:
                with closing(sqlite3.connect(temporary)) as merged:
                    for name in ('state', 'articles'):
                        part = Path(folder) / (name + '.sqlite3')
                        download_snapshot(meta['parts'][name], store, part)
                        with closing(sqlite3.connect(part)) as source:
                            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                            copy_tables(source, merged, tables)
                preparation = meta['parts'].get('prepared')
                if preparation is not None:
                    prepared = Path(folder) / 'prepared.sqlite3'
                    download_snapshot(preparation, store, prepared)
                    clear_journals(path.parent / 'prepared.sqlite3')
                    prepared.replace(path.parent / 'prepared.sqlite3')
                else:
                    clear_journals(path.parent / 'prepared.sqlite3')
                    (path.parent / 'prepared.sqlite3').unlink(missing_ok=True)
        else:
            download_snapshot(meta, store, temporary)
        clear_journals(path)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return (etag, meta) if details else etag


def download_snapshot(meta, store, path):
    with path.open('wb') as stream:
        verify_snapshot(meta, store, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if path.stat().st_size != meta['bytes'] or file_digest(path) != meta['sha256']:
        raise StorageError('Crawler database snapshot checksum mismatch')


def database_bytes(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return db.serialize()


def database_inventory(path):
    content = database_bytes(path)
    return {'file': Path(path).name, 'bytes': len(content), 'sha256': digest(content)}


def migrate_database(path, store=None):
    store = store or S3Objects()
    path = Path(path)
    content = database_bytes(path)
    prepared_path = path.parent / 'prepared.sqlite3'
    prepared = database_bytes(prepared_path) if prepared_path.exists() else None
    prepared_sha = digest(prepared) if prepared is not None else None
    try:
        old, _ = store.get(POINTER)
    except FileNotFoundError:
        # Query the source in one read transaction. A serialized WAL database
        # cannot be queried via deserialize() in an in-memory connection.
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('BEGIN')
            parts = {name: snapshot(partition(db, name), store) for name in ('state', 'articles')}
        parts['prepared'] = snapshot(prepared, store) if prepared is not None else None
        meta = {'schema': 2, 'parts': parts, 'migration_sha256': digest(content)}
        if (digest(database_bytes(path)) != digest(content) or
                (digest(database_bytes(prepared_path)) if prepared_path.exists() else None) != prepared_sha):
            raise StorageError('Crawler migration source changed before publication')
        store.put(POINTER, json_bytes(meta), create=True, content_type='application/json')
    else:
        meta = json.loads(old)
        if meta.get('maintenance') or meta.get('migration_sha256', meta.get('sha256')) != digest(content):
            raise StorageError('Crawler database already published with different contents')
        if meta.get('schema') == 2 and (meta['parts'].get('prepared') or {}).get('sha256') != prepared_sha:
            raise StorageError('Inspection database already published with different contents')
    for item in meta['parts'].values() if meta.get('schema') == 2 else (meta,):
        if item is not None:
            verify_snapshot(item, store)


class DurableConnection(sqlite3.Connection):
    store = None
    storage_error = None

    def track_write(self, action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            self.dirty.add('state' if table in STATE_TABLES else 'articles')
        if action in (sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                      sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX,
                      sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_ALTER_TABLE):
            self.dirty.update(('state', 'articles'))
        return sqlite3.SQLITE_OK

    def persist(self):
        if self.store is None:
            return
        if self.storage_error:
            raise self.storage_error
        try:
            prep_stamp = database_stamp(self.prepared_path)
            if not self.dirty and prep_stamp == self.prepared_stamp:
                return
            parts = dict(self.parts)
            for name in self.dirty:
                previous = parts.get(name) or {}
                content = partition(self, name)
                if digest(content) != previous.get('sha256'):
                    parts[name] = snapshot(content, self.store, {p['sha256'] for p in previous.get('chunks', [])})
            if prep_stamp != self.prepared_stamp or 'prepared' not in parts:
                parts['prepared'] = prepared_snapshot(self.prepared_path, self.store, parts.get('prepared'))
            if parts == self.parts:
                self.dirty.clear()
                self.prepared_stamp = prep_stamp
                return
            # Article completion, inspection and request budget share ONE CAS pointer.
            meta = {'schema': 2, 'parts': parts}
            self.etag = self.store.put(POINTER, json_bytes(meta), etag=self.etag,
                                       content_type='application/json')
            self.parts, self.prepared_stamp = parts, prep_stamp
            self.dirty.clear()
        except Exception as exc:
            self.storage_error = exc
            raise

    def execute(self, *args, **kwargs):
        if self.storage_error:
            raise self.storage_error
        return super().execute(*args, **kwargs)

    def commit(self):
        result = super().commit()
        self.persist()
        return result

    def __exit__(self, kind, value, traceback):
        result = super().__exit__(kind, value, traceback)
        if kind is None:
            self.persist()
        return result


def connect(path):
    if not enabled():
        return sqlite3.connect(path)
    store = S3Objects()
    etag, meta = restore(path, store, details=True)
    connection = sqlite3.connect(path, factory=DurableConnection, cached_statements=0)
    connection.store, connection.etag = store, etag
    connection.parts = meta.get('parts', {})
    connection.dirty = set() if meta.get('schema') == 2 else {'state', 'articles'}
    connection.prepared_path = Path(path).parent / 'prepared.sqlite3'
    connection.prepared_stamp = database_stamp(connection.prepared_path)
    connection.set_authorizer(connection.track_write)
    return connection
