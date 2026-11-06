"""Bounded, content-addressed model cache; readers pin files through model loading."""
from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time

from filelock import FileLock
from .objects import ROOT, StorageError, digest, file_digest, json_bytes, key_path

MANIFEST = 'models/monthly/_manifest.json'


def stamp(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


class ModelCache:
    def __init__(self, root, store, interval):
        self.root, self.store, self.interval = Path(root), store, interval
        self.etag, self.state, self.checked = None, None, float('-inf')
        self.verified = {}
        self.mutex = threading.RLock()

    def refresh(self):
        now = time.monotonic()
        if self.state is not None and now - self.checked < self.interval:
            return
        result = self.store.get_if_changed(MANIFEST, self.etag)
        if result is not None:
            body, etag = result
            state = json.loads(body)
            if state.get('schema') != 1 or not isinstance(state.get('files'), dict):
                raise StorageError('Invalid model manifest')
            for name, meta in state['files'].items():
                key_path(name)
                if (not re.fullmatch('[a-f0-9]{64}', meta['sha256']) or
                        meta['key'] != 'models/monthly/_blobs/' + meta['sha256']):
                    raise StorageError('Model artifact is outside its namespace')
            self.state, self.etag = state, etag
        self.checked = now

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

    def materialize(self):
        from .workspace import atomic_write
        files = self.state['files']
        revision = digest(json_bytes(files))[:32]
        directory = self.root / 'versions' / revision
        directory.mkdir(parents=True, exist_ok=True)
        for name, meta in files.items():
            blob = self.root / 'blobs' / meta['sha256']
            if not self.valid(blob, meta['sha256']):
                blob.parent.mkdir(parents=True, exist_ok=True)
                temporary = blob.with_suffix('.part')
                try:
                    with temporary.open('wb') as stream:
                        self.store.verify(meta['key'], meta['sha256'], stream)
                    temporary.replace(blob)
                finally:
                    temporary.unlink(missing_ok=True)
                self.verified[blob] = meta['sha256'], stamp(blob)
            target = directory.joinpath(*key_path(name).parts)
            if not target.resolve().is_relative_to(directory.resolve()):
                raise StorageError('Model path escapes cache')
            # Shared hardlinks avoid copying unchanged model bytes between revisions.
            shared = target.is_file() and not target.is_symlink() and target.samefile(blob)
            if shared:
                self.verified[target] = meta['sha256'], stamp(target)
            elif not self.valid(target, meta['sha256']):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                try:
                    os.link(blob, target)
                except OSError:
                    shutil.copyfile(blob, target)
                self.verified[target] = meta['sha256'], stamp(target)
                self.verified[blob] = meta['sha256'], stamp(blob)
        if not (directory / '.snapshot.json').exists():
            atomic_write(directory / '.snapshot.json', json_bytes(files))
        history_path = self.root / 'history.json'
        history = json.loads(history_path.read_text()) if history_path.exists() else []
        retained = [revision] + [item for item in history if item != revision][:1]
        if retained != history:
            atomic_write(history_path, json_bytes(retained))
            self.prune(retained)
        return directory

    def prune(self, history):
        # All processes hold models.lock from selection through deserialization.
        versions = (self.root / 'versions').resolve()
        for directory in versions.iterdir():
            if (re.fullmatch('[a-f0-9]{32}', directory.name) and directory.name not in history
                    and not directory.is_symlink() and directory.resolve().parent == versions):
                shutil.rmtree(directory)
        retained = set()
        for revision in history:
            if not re.fullmatch('[a-f0-9]{32}', revision):
                raise StorageError('Invalid model cache history')
            source = versions / revision / '.snapshot.json'
            if source.exists():
                retained.update(meta['sha256'] for meta in json.loads(source.read_text()).values())
        for blob in (self.root / 'blobs').glob('*'):
            if re.fullmatch('[a-f0-9]{64}', blob.name) and blob.name not in retained:
                blob.unlink()
        self.verified = {p: value for p, value in self.verified.items() if p.exists()}

    @contextmanager
    def use(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.mutex, FileLock(str(self.root / 'models.lock'), timeout=30):
            self.refresh()
            yield self.materialize()


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
