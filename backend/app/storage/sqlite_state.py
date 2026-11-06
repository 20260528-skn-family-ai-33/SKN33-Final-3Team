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
from uuid import uuid4

from .objects import S3Objects, StorageError, digest, enabled, file_digest, json_bytes

PREFIX = 'raw/trade-news/database'
POINTER = PREFIX + '/_current.json'
CHUNK_BYTES = 256 * 1024


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


def restore(path, store=None):
    store = store or S3Objects()
    try:
        body, etag = store.get(POINTER)
    except FileNotFoundError as exc:
        raise StorageError('Migrate the crawler budget database before enabling S3') from exc
    meta = json.loads(body)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            verify_snapshot(meta, store, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.stat().st_size != meta['bytes'] or file_digest(temporary) != meta['sha256']:
            raise StorageError('Crawler database snapshot checksum mismatch')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return etag


def migrate_database(path, store=None):
    store = store or S3Objects()
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        content = db.serialize()
    try:
        old, _ = store.get(POINTER)
    except FileNotFoundError:
        meta = snapshot(content, store)
        store.put(POINTER, json_bytes(meta), create=True, content_type='application/json')
    else:
        meta = json.loads(old)
        if meta['sha256'] != digest(content):
            raise StorageError('Crawler database already published with different contents')
    verify_snapshot(meta, store)


class DurableConnection(sqlite3.Connection):
    store = None
    storage_error = None

    def persist(self):
        if self.store is None:
            return
        if self.storage_error:
            raise self.storage_error
        try:
            content = self.serialize()
            checksum = digest(content)
            if checksum == self.last_sha:
                return
            meta = snapshot(content, self.store, self.known_chunks)
            self.etag = self.store.put(POINTER, json_bytes(meta), etag=self.etag,
                                       content_type='application/json')
            self.last_sha = checksum
            self.known_chunks = {part['sha256'] for part in meta['chunks']}
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
    etag = restore(path, store)
    connection = sqlite3.connect(path, factory=DurableConnection)
    connection.store, connection.etag = store, etag
    content = connection.serialize()
    connection.last_sha = digest(content)
    connection.known_chunks = {digest(part) for part in chunks(content)}
    return connection
