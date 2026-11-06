"""Versioned S3 snapshots around libraries requiring filesystem paths.

Blobs are immutable. A conditional manifest PUT publishes all files together.
The same manifest carries a renewable lease: an expired writer cannot publish
over its successor. No S3 DeleteObject permission or local durable disk is used.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
from functools import wraps
import inspect
import json
import logging
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from filelock import FileLock

from .objects import ROOT, Conflict, S3Objects, StorageError, digest, enabled, file_digest, json_bytes, key_path

log = logging.getLogger(__name__)
_active = ContextVar('artifact_workspaces', default=())
_verified = OrderedDict()
_verified_lock = threading.Lock()


def cached_digest_matches(path, checksum):
    """Reuse only a hash verified for this exact local file and expected content."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev)
    key = str(path)
    with _verified_lock:
        if _verified.get(key) == (checksum, stamp):
            _verified.move_to_end(key)
            return True
    if file_digest(path) != checksum:
        return False
    # A concurrent modification must not earn a cache hit for unverified bytes.
    after = path.stat()
    if stamp != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev):
        return False
    with _verified_lock:
        _verified[key] = checksum, stamp
        _verified.move_to_end(key)
        while len(_verified) > 8192:
            _verified.popitem(last=False)
    return True


def files(root, prefix=None):
    """Only completed files; never locks, temporary files or symbolic links."""
    for path in sorted(Path(root).rglob('*')):
        relative = path.relative_to(root)
        name = relative.as_posix()
        # Legacy diagnostic logs are not disposable input data.
        if prefix == 'raw/news-history' and relative.parts[0] == 'logs':
            continue
        if prefix == 'models/versions' and not (name.startswith('versions/')
                or name in ('models.joblib', 'predictions.csv', 'metadata.json')):
            continue
        if prefix == 'raw/trade-news' and name in (
                'articles.sqlite3', 'articles.sqlite3-wal', 'articles.sqlite3-shm', 'articles.sqlite3-journal',
                'prepared.sqlite3', 'prepared.sqlite3-wal', 'prepared.sqlite3-shm', 'prepared.sqlite3-journal'):
            continue
        if prefix == 'reports' and path.suffix.lower() not in ('.pdf', '.docx', '.xlsx', '.html'):
            continue
        if path.is_symlink():
            raise StorageError('Symlinks are not allowed in artifact workspaces')
        if any(part.startswith('.') or part.endswith(('.lock', '.tmp', '.part'))
               or '.prepare.' in part for part in relative.parts):
            continue
        if path.is_file():
            yield relative.as_posix(), path


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temp.open('wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class Workspace:
    def __init__(self, root, prefix, *, store=None, lease_seconds=300):
        self.root = Path(root).resolve()
        self.prefix = str(key_path(prefix))
        self.store = store or S3Objects()
        self.key = self.prefix + '/_manifest.json'
        self.lease_seconds = lease_seconds
        self.token = uuid4().hex
        self.mutex = threading.RLock()
        self.stop = threading.Event()
        self.error = None
        self.etag = None
        self.state = {'schema': 1, 'files': {}, 'lease': None}
        self.writer = False

    def local_files(self):
        yield from files(self.root, self.prefix)

    def read_manifest(self, *, missing=False):
        try:
            content, self.etag = self.store.get(self.key)
        except FileNotFoundError:
            if not missing:
                raise StorageError(f'S3 manifest missing: {self.prefix}; migrate before cutover')
            return
        self.state = json.loads(content)
        if self.state.get('schema') != 1 or not isinstance(self.state.get('files'), dict):
            raise StorageError('Invalid S3 artifact manifest')
        for name, meta in self.state['files'].items():
            key_path(name)
            if meta['key'] != self.prefix + '/_blobs/' + meta['sha256']:
                raise StorageError('Artifact manifest references another namespace')

    def _put(self, state):
        self.etag = self.store.put(self.key, json_bytes(state), etag=self.etag,
                                   create=self.etag is None, content_type='application/json')
        self.state = state

    def claim(self):
        self.read_manifest(missing=True)
        lease = self.state.get('lease')
        if lease and lease['expires'] > time.time():
            raise Conflict(f'Artifact workspace is already running: {self.prefix}', retryable=True)
        self._put({**self.state, 'lease': {'token': self.token, 'expires': time.time() + self.lease_seconds}})
        self.writer = True

    def check(self):
        if self.error:
            raise self.error
        lease = self.state.get('lease') or {}
        if not self.writer or lease.get('token') != self.token or lease.get('expires', 0) <= time.time():
            raise Conflict('Artifact lease expired; restart from S3', retryable=True)

    def heartbeat(self):
        while not self.stop.wait(min(30, self.lease_seconds / 3)):
            try:
                with self.mutex:
                    self.check()
                    self._put({**self.state, 'lease': {'token': self.token,
                        'expires': time.time() + self.lease_seconds}})
            except Exception as exc:
                self.error = exc
                return

    def restore(self):
        """Under the local workspace lock, replace only this declared cache tree."""
        self.root.mkdir(parents=True, exist_ok=True)
        existing = dict(self.local_files())
        for name, meta in self.state['files'].items():
            if self.prefix == 'raw/news-history' and name.startswith('logs/'):
                continue
            if self.prefix == 'raw/trade-news' and name.split('-')[0] in ('articles.sqlite3', 'prepared.sqlite3'):
                continue  # Both DBs are restored together from database/_current.json.
            target = self.root.joinpath(*key_path(name).parts)
            if not target.resolve().is_relative_to(self.root):
                raise StorageError('Artifact cache path escapes workspace')
            if target.is_file() and cached_digest_matches(target, meta['sha256']):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('.' + target.name + '.' + uuid4().hex + '.tmp')
            try:
                with temporary.open('wb') as stream:
                    self.store.verify(meta['key'], meta['sha256'], stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        # A failed restore raises before callers can read a partly restored tree.
        for name, path in existing.items():
            if name not in self.state['files']:
                path.unlink()

    def publish(self, *, expected=None):
        try:
            # Upload outside the mutex so large data uploads do not starve lease renewal.
            self.check()
            snapshot = {}
            for name, path in self.local_files():
                checksum = file_digest(path)
                previous = self.state['files'].get(name)
                if previous and previous['sha256'] == checksum:
                    snapshot[name] = previous
                else:
                    snapshot[name] = self.store.immutable_file(self.prefix + '/_blobs/' + checksum, path, checksum)
            if expected is not None and {name: meta['sha256'] for name, meta in snapshot.items()} != expected:
                raise StorageError('Migration source changed during upload; snapshot was not published')
            with self.mutex:
                self.check()
                snapshot_bytes = json_bytes({'schema': 1, 'files': snapshot, 'lease': None})
                self.store.immutable(self.prefix + '/_snapshots/' + digest(snapshot_bytes) + '.json', snapshot_bytes)
                self._put({**self.state, 'files': snapshot})
        except Exception as exc:
            # Pipeline code must not swallow a storage failure and report success.
            self.error = exc
            raise

    def release(self):
        self.stop.set()
        with self.mutex:
            if self.error:
                return  # Fail closed; another writer must wait for lease expiry.
            self.check()
            self._put({**self.state, 'lease': None})


@contextmanager
def workspace(root, prefix, *, write=True, store=None, migrate=False, reject_busy=False):
    root = Path(root).resolve()
    for active in _active.get():
        if active.prefix == prefix:
            if active.root != root or (write and not active.writer):
                raise StorageError('Conflicting nested artifact workspace')
            yield active
            return
    # Keep the lock outside the disposable directory; deleting a cache cannot unlock a worker.
    lock_dir = ROOT / 'runtime/s3-locks'
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_key = digest(str(root).encode())
    with FileLock(str(lock_dir / (lock_key + '.lock')), timeout=0):
        ws = Workspace(root, prefix, store=store)
        thread = None
        context = None
        failed = False
        try:
            if write:
                # Adoption of existing local files is always an explicit migration.
                ws.read_manifest(missing=True)
                if ws.etag is None and not migrate and any(ws.local_files()):
                    raise StorageError(f'Unmigrated local artifacts in {root}')
                ws.claim()
                thread = threading.Thread(target=ws.heartbeat, daemon=True)
                thread.start()
            else:
                ws.read_manifest()
                if reject_busy and (ws.state.get('lease') or {}).get('expires', 0) > time.time():
                    raise Conflict('Artifact workspace is already running', retryable=True)
            if not migrate:
                ws.restore()
            context = _active.set((*_active.get(), ws))
            yield ws
            if write and not migrate:
                ws.publish()
        except BaseException:
            failed = True
            raise
        finally:
            if context is not None:
                _active.reset(context)
            if thread:
                ws.stop.set()
                thread.join(timeout=1)
            if ws.writer:
                try:
                    ws.release()
                except Exception:
                    if not failed:
                        raise
                    log.exception('Artifact lease release failed; original error retained')


def checkpoint(path):
    """Called after atomic state writes, before the next irreversible pipeline step."""
    path = Path(path).resolve()
    for ws in reversed(_active.get()):
        if path.is_relative_to(ws.root):
            ws.publish()
            return


def stored(prefix, root, *, write=True):
    """root is a Path or a callable receiving bound/defaulted function arguments."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            arguments = signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            folder = root(arguments.arguments) if callable(root) else root
            with workspace(folder, prefix, write=write):
                return function(*args, **kwargs)
        wrapped.__kwdefaults__ = function.__kwdefaults__
        return wrapped
    return decorate


def model_cache(settings):
    """Compatibility accessor. Model loaders use model_cache_use to pin readers."""
    from .model_cache import model_cache_use
    with model_cache_use(settings) as directory:
        return directory
