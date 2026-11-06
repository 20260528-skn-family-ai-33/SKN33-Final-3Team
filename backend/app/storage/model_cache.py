"""Bounded, content-addressed model cache; readers pin files through model loading."""
from contextlib import contextmanager
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from filelock import FileLock, Timeout
from .objects import ROOT, StorageError, digest, file_digest, json_bytes, key_path

MANIFEST = 'models/monthly/_manifest.json'
log = logging.getLogger(__name__)


def stamp(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


class ModelCache:
    def __init__(self, root, store, interval):
        self.root, self.store, self.interval = Path(root), store, interval
        self.etag, self.state, self.checked = None, None, float('-inf')
        self.verified = {}
        self.mutex = threading.RLock()
        self.ready = None
        self.offline = False

    def validate_manifest(self, state):
        if state.get('schema') != 1 or not isinstance(state.get('files'), dict):
            raise StorageError('Invalid model manifest')
        for name, meta in state['files'].items():
            key_path(name)
            if (not re.fullmatch('[a-f0-9]{64}', meta['sha256']) or
                    meta['key'] != 'models/monthly/_blobs/' + meta['sha256']):
                raise StorageError('Model artifact is outside its namespace')
        return state

    def fallback(self):
        path = self.root / 'current.json'
        if path.exists():
            return self.validate_manifest(json.loads(path.read_text()))
        history_path = self.root / 'history.json'
        if history_path.exists():
            history = json.loads(history_path.read_text())
            if history:
                revision = history[0]
                if not isinstance(revision, str) or not re.fullmatch('[a-f0-9]{32}', revision):
                    raise StorageError('Invalid model cache history')
                files = json.loads((self.root / 'versions' / revision / '.snapshot.json').read_text())
                return self.validate_manifest({'schema': 1, 'files': files})
        return self.ready

    def refresh(self):
        now = time.monotonic()
        if self.state is not None and now - self.checked < self.interval:
            return self.offline
        try:
            result = self.store.get_if_changed(MANIFEST, self.etag)
        except StorageError as exc:
            cached = self.fallback() if exc.retryable else None
            if cached is None:
                raise
            # Do not pair the failed candidate's ETag with a different manifest.
            self.state, self.etag = cached, None
            self.checked = now
            if not self.offline:
                log.warning('Model manifest temporarily unavailable; using verified local snapshot')
            self.offline = True
            return True
        if result is not None:
            body, etag = result
            state = self.validate_manifest(json.loads(body))
            self.state, self.etag = state, etag
        self.checked = now
        self.offline = False
        return False

    def valid(self, path, checksum):
        if path.is_symlink():
            raise StorageError('Symlink in model cache')
        if not path.is_file():
            return False
        current = stamp(path)
        if self.verified.get(path) == (checksum, current):
            return True
        if file_digest(path) != checksum:
            return False
        self.verified[path] = checksum, current
        return True

    def lock(self):
        return FileLock(str(self.root / 'models.lock'), timeout=30)

    def pin(self, revision):
        pins = self.root / 'pins'
        pins.mkdir(exist_ok=True)
        path = pins / (revision + '-' + uuid4().hex + '.lock')
        lock = FileLock(str(path), timeout=0)
        lock.acquire()
        return path, lock

    def unpin(self, pin):
        path, lock = pin
        lock.release()
        path.unlink(missing_ok=True)

    def materialize(self, state, *, local_only=False):
        from .workspace import atomic_write
        files = state['files']
        revision = digest(json_bytes(files))[:32]
        directory = self.root / 'versions' / revision
        with self.lock():
            pin = self.pin(revision)
            current_path = self.root / 'current.json'
            before = current_path.read_bytes() if current_path.exists() else None
        staging = self.root / ('.prepare-' + pin[0].stem.rsplit('-', 1)[1])
        try:
            # Hashes, downloads and copies never hold the publication/prune lock.
            if directory.is_symlink() or any(not (directory / name).resolve().is_relative_to(directory.resolve()) for name in files):
                raise StorageError('Model path escapes cache')
            if directory.is_dir() and all(self.valid(directory / name, meta['sha256'])
                                          for name, meta in files.items()):
                return directory, pin
            if local_only:
                raise StorageError('Verified cached model is unavailable during S3 outage')
            staging.mkdir()
            for name, meta in files.items():
                target = staging.joinpath(*key_path(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                blob = self.root / 'blobs' / meta['sha256']
                reused = False
                try:
                    if self.valid(blob, meta['sha256']):
                        try:
                            os.link(blob, target)
                        except OSError:
                            shutil.copyfile(blob, target)
                        reused = True
                except FileNotFoundError:
                    pass  # Concurrent prune: download into this private staging tree.
                if not reused:
                    with target.open('wb') as stream:
                        self.store.verify(meta['key'], meta['sha256'], stream)
            atomic_write(staging / '.snapshot.json', json_bytes(files))
            with self.lock():
                (self.root / 'blobs').mkdir(exist_ok=True)
                directory.mkdir(parents=True, exist_ok=True)
                for name, meta in files.items():
                    source = staging / name
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    blob = self.root / 'blobs' / meta['sha256']
                    # Link/rename only: all byte copying and verification finished
                    # in the private staging directory before taking this lock.
                    if not blob.exists() or not blob.samefile(source):
                        temporary = blob.with_name('.' + blob.name + '.' + uuid4().hex)
                        try:
                            os.link(source, temporary)
                            temporary.replace(blob)
                        except OSError:
                            pass  # Filesystems without hardlinks keep private copies.
                        finally:
                            temporary.unlink(missing_ok=True)
                    if not target.exists() or not target.samefile(source):
                        source.replace(target)
                    self.verified[target] = meta['sha256'], stamp(target)
                atomic_write(directory / '.snapshot.json', json_bytes(files))
                history_path = self.root / 'history.json'
                history = json.loads(history_path.read_text()) if history_path.exists() else []
                current = current_path.read_bytes() if current_path.exists() else None
                # A slow older download must not replace a newer publication.
                retained = ([revision] + [item for item in history if item != revision][:1]
                            if current == before else history)
                atomic_write(history_path, json_bytes(retained))
                if current == before:
                    atomic_write(current_path, json_bytes(state))
                self.prune(retained)
            return directory, pin
        except BaseException:
            with self.lock():
                self.unpin(pin)
            raise
        finally:
            if staging.exists():
                # A concurrent prune can remove an abandoned staging tree after
                # unpinning on failure. Cleanup must not mask the original error.
                shutil.rmtree(staging, ignore_errors=True)

    def prune(self, history):
        # Live readers hold independent OS locks; dead processes release them.
        live = set(history)
        preparing = set()
        for path in (self.root / 'pins').glob('*.lock'):
            lock = FileLock(str(path), timeout=0)
            try:
                lock.acquire()
            except Timeout:
                live.add(path.name[:32])
                preparing.add(path.stem.rsplit('-', 1)[1])
            else:
                lock.release()
                path.unlink(missing_ok=True)
        for directory in self.root.glob('.prepare-*'):
            token = directory.name.removeprefix('.prepare-')
            if (re.fullmatch('[a-f0-9]{32}', token) and token not in preparing
                    and not directory.is_symlink() and directory.resolve().parent == self.root.resolve()):
                shutil.rmtree(directory)
        versions = (self.root / 'versions').resolve()
        for directory in versions.iterdir():
            if (re.fullmatch('[a-f0-9]{32}', directory.name) and directory.name not in live
                    and not directory.is_symlink() and directory.resolve().parent == versions):
                shutil.rmtree(directory)
        retained = set()
        for revision in live:
            if not re.fullmatch('[a-f0-9]{32}', revision):
                raise StorageError('Invalid model cache history')
            source = versions / revision / '.snapshot.json'
            if source.exists():
                retained.update(meta['sha256'] for meta in json.loads(source.read_text()).values())
        for blob in (self.root / 'blobs').glob('*'):
            if re.fullmatch('[a-f0-9]{64}', blob.name) and blob.name not in retained:
                blob.unlink()
        self.verified = {p: value for p, value in list(self.verified.items()) if p.exists()}

    @contextmanager
    def use(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.mutex:
            local_only = self.refresh()
            state = self.state
        directory, pin = self.materialize(state, local_only=local_only)
        self.ready = state
        try:
            yield directory
        finally:
            with self.lock():
                self.unpin(pin)
                history = json.loads((self.root / 'history.json').read_text())
                self.prune(history)


@lru_cache(maxsize=8)
def manager(pid, root, bucket, prefix, region, interval):
    from .objects import S3Objects
    store = S3Objects({'ARTIFACT_S3_BUCKET': bucket, 'ARTIFACT_S3_PREFIX': prefix, 'AWS_REGION': region})
    namespace = digest(json_bytes([bucket, prefix, region]))[:16]
    return ModelCache(Path(root) / 'model-cache' / namespace, store, interval)


def configured(settings):
    interval = float(settings.get('ARTIFACT_MODEL_CHECK_SECONDS', '30'))
    if not 0 <= interval <= 3600:
        raise StorageError('ARTIFACT_MODEL_CHECK_SECONDS must be between 0 and 3600')
    root = (ROOT / settings.get('ARTIFACT_CACHE_DIRECTORY', 'runtime/s3-cache')).resolve()
    return manager(os.getpid(), str(root), settings.get('ARTIFACT_S3_BUCKET'),
        settings.get('ARTIFACT_S3_PREFIX', ''), settings.get('AWS_REGION', 'ap-northeast-2'), interval)


@contextmanager
def model_cache_use(settings):
    with configured(settings).use() as directory:
        yield directory
