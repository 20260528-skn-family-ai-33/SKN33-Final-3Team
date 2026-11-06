"""Report byte storage. S3 mode never falls back to a local persistent file."""
import hashlib
from functools import lru_cache
import os
from pathlib import Path
from backend.app.api.common import require, now
from backend.app.storage.objects import S3Objects, StorageError, digest, enabled, key_path


class LocalReportStorage:
    def __init__(self, root): self.root=Path(root).resolve()

    def path(self, key):
        target=(self.root/key).resolve()
        require(target.is_relative_to(self.root),'파일 경로를 확인하세요.','NOT_FOUND',404)
        return target

    def write(self,key,content):
        target=self.path(key); target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix(target.suffix+'.tmp')
        temporary.write_bytes(content); temporary.replace(target)
        return {'storage':'local','key':key,'sha256':hashlib.sha256(content).hexdigest(),
            'created_at':now().isoformat()+'Z'}

    def read(self,metadata):
        require(metadata.get('storage')=='local','파일 저장소 연결이 준비되지 않았습니다.','FEATURE_NOT_READY',503)
        target=self.path(metadata['key'])
        # DB에 경로는 있는데 파일이 없는 경우(삭제·다른 서버 저장 등). 사용자가 할 수 있는 조치를 안내한다.
        require(target.is_file(),'현재 리포트를 이용할 수 없습니다. 리포트 재생성 또는 잠시 후 다시 시도해 주세요.','NOT_FOUND',404)
        content=target.read_bytes()
        require(hashlib.sha256(content).hexdigest()==metadata['sha256'],'파일 무결성을 확인하세요.','FILE_UNAVAILABLE',503)
        return content


class S3ReportStorage:
    def __init__(self, settings, *, store=None):
        self.store = store or S3Objects(settings.values)

    def write(self, key, content):
        key_path(key)
        # Digest suffix makes retries safe even for formats containing render timestamps.
        key = 'reports/' + key + '.' + digest(content)
        try:
            metadata = self.store.immutable(key, content)
        except StorageError as exc:
            self.failure(exc)
        return {**metadata, 'storage': 's3', 'created_at': now().isoformat() + 'Z'}

    def read(self, metadata):
        key = metadata['key']
        if metadata.get('storage') == 'local':
            # Legacy migration copies the bytes to reports/<old key>. Never read local disk.
            key = 'reports/' + key
        else:
            require(metadata.get('storage') == 's3' and key.startswith('reports/'),
                    '리포트 저장 경로를 확인하세요.', 'FILE_UNAVAILABLE', 503)
        try:
            return self.store.get(key, sha256=metadata['sha256'])[0]
        except FileNotFoundError:
            from backend.app.api.common import ApiError
            raise ApiError(503, 'FILE_UNAVAILABLE', '저장된 리포트를 찾을 수 없습니다. 운영자 확인이 필요합니다.')
        except StorageError as exc:
            self.failure(exc)

    @staticmethod
    def failure(exc):
        from backend.app.api.common import ApiError
        raise ApiError(503, 'FILE_UNAVAILABLE', '리포트 저장소를 이용할 수 없습니다.',
                       retryable=exc.retryable) from exc


@lru_cache(maxsize=8)
def _s3_report_storage(pid, bucket, prefix, region):
    from types import SimpleNamespace
    return S3ReportStorage(SimpleNamespace(values={'ARTIFACT_S3_BUCKET': bucket,
        'ARTIFACT_S3_PREFIX': prefix, 'AWS_REGION': region}))


def report_storage(settings):
    try:
        if enabled(settings.values):
            return _s3_report_storage(os.getpid(), settings.get('ARTIFACT_S3_BUCKET'),
                settings.get('ARTIFACT_S3_PREFIX', ''), settings.get('AWS_REGION', 'ap-northeast-2'))
        return LocalReportStorage(settings.files)
    except StorageError as exc:
        S3ReportStorage.failure(exc)
