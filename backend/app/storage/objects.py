"""Small, fail-closed S3 boundary. Credentials come from the AWS provider chain."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]


class StorageError(RuntimeError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class Conflict(StorageError):
    pass


def config(values=None):
    return values if values is not None else {**dotenv_values(ROOT / '.env'), **os.environ}


def enabled(values=None):
    mode = config(values).get('ARTIFACT_STORAGE', 'local') or 'local'
    if mode not in ('local', 's3'):
        raise StorageError('ARTIFACT_STORAGE must be local or s3')
    return mode == 's3'


def key_path(key):
    if (not isinstance(key, str) or not key or '\\' in key or ':' in key
            or key.startswith('/') or any(p in ('', '.', '..') for p in key.split('/'))):
        raise StorageError('Invalid artifact key')
    return PurePosixPath(key)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(part)
    return result.hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class S3Objects:
    def __init__(self, values=None, *, client=None):
        values = config(values)
        self.bucket = values.get('ARTIFACT_S3_BUCKET')
        if not self.bucket:
            raise StorageError('ARTIFACT_S3_BUCKET is required')
        self.prefix = (values.get('ARTIFACT_S3_PREFIX') or '').strip('/')
        if self.prefix:
            key_path(self.prefix)
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', region_name=values.get('AWS_REGION') or 'ap-northeast-2',
                config=Config(connect_timeout=5, read_timeout=30,
                              retries={'mode': 'standard', 'total_max_attempts': 4}))
        self.client = client

    def key(self, key):
        key_path(key)
        return f'{self.prefix}/{key}' if self.prefix else key

    def _call(self, method, **kwargs):
        try:
            return getattr(self.client, method)(Bucket=self.bucket, **kwargs)
        except Exception as exc:
            code = str(getattr(exc, 'response', {}).get('Error', {}).get('Code', ''))
            if code in ('NoSuchKey', '404'):
                raise FileNotFoundError(kwargs.get('Key')) from exc
            if code in ('PreconditionFailed', 'ConditionalRequestConflict', '412', '409'):
                raise Conflict('Artifact changed concurrently; retry from S3', retryable=True) from exc
            retryable = code in ('SlowDown', 'RequestTimeout', 'InternalError', 'ServiceUnavailable', '500', '503')
            retryable |= type(exc).__name__ in ('EndpointConnectionError', 'ConnectionClosedError',
                                               'ConnectTimeoutError', 'ReadTimeoutError')
            raise StorageError('S3 operation failed (' + (code or type(exc).__name__) + ')',
                               retryable=retryable) from exc

    def get(self, key, *, sha256=None):
        result = self._call('get_object', Key=self.key(key))
        try:
            content = result['Body'].read()
        except Exception as exc:
            raise StorageError('S3 response interrupted', retryable=True) from exc
        finally:
            result['Body'].close()
        expected = sha256 or result.get('Metadata', {}).get('sha256')
        if expected and digest(content) != expected:
            raise StorageError('Artifact checksum mismatch')
        return content, result.get('ETag')

    def put(self, key, content, *, etag=None, create=False, content_type='application/octet-stream'):
        checksum = digest(content)
        options = {'IfNoneMatch': '*'} if create else ({'IfMatch': etag} if etag else {})
        result = self._call('put_object', Key=self.key(key), Body=content, ContentType=content_type,
            ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode(),
            Metadata={'sha256': checksum}, **options)
        return result['ETag']

    def immutable(self, key, content):
        """A lost PUT response is safe to retry; never replace different bytes."""
        try:
            self.put(key, content, create=True)
        except Conflict:
            self.get(key, sha256=digest(content))
        return {'key': key, 'sha256': digest(content), 'bytes': len(content)}

    def immutable_file(self, key, path, checksum):
        """Stream large CSV/ZIP/joblib files; only the manifest requires a single PUT."""
        path = Path(path)
        size = path.stat().st_size
        if size < 5 * 1024 ** 3:
            try:
                with path.open('rb') as body:
                    self._call('put_object', Key=self.key(key), Body=body, IfNoneMatch='*',
                        ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode(),
                        Metadata={'sha256': checksum})
            except Conflict:
                self.verify(key, checksum)
        else:
            self._immutable_multipart(key, path, checksum, size)
        if file_digest(path) != checksum:
            raise StorageError('Artifact changed while uploading; snapshot was not published')
        return {'key': key, 'sha256': checksum, 'bytes': size}

    def _immutable_multipart(self, key, path, checksum, size):
        # Do not let an upload whose source changed overwrite an existing blob.
        options = {'Key': self.key(key)}
        upload = self._call('create_multipart_upload', **options,
                            Metadata={'sha256': checksum}, ChecksumAlgorithm='SHA256')
        options['UploadId'] = upload['UploadId']
        completed = False
        try:
            parts, actual = [], hashlib.sha256()
            chunk_size = max(64 * 1024 ** 2, ((size + 9999) // 10000 + 1024 ** 2 - 1) // (1024 ** 2) * 1024 ** 2)
            with Path(path).open('rb') as stream:
                for number, body in enumerate(iter(lambda: stream.read(chunk_size), b''), 1):
                    actual.update(body)
                    part_checksum = base64.b64encode(hashlib.sha256(body).digest()).decode()
                    result = self._call('upload_part', **options, PartNumber=number,
                                        Body=body, ChecksumSHA256=part_checksum)
                    parts.append({'PartNumber': number, 'ETag': result['ETag'],
                                  'ChecksumSHA256': part_checksum})
            if actual.hexdigest() != checksum:
                raise StorageError('Artifact changed while uploading; snapshot was not published')
            try:
                self._call('complete_multipart_upload', **options,
                           MultipartUpload={'Parts': parts}, IfNoneMatch='*')
                completed = True
            except Conflict:
                self.verify(key, checksum)
        finally:
            if not completed:
                try:
                    self._call('abort_multipart_upload', **options)
                except (StorageError, FileNotFoundError):
                    # Preserve the original failure; lifecycle removes any orphan.
                    pass

    def verify(self, key, checksum, destination=None):
        """Stream and hash a download; caller publishes its temporary file only on success."""
        result = self._call('get_object', Key=self.key(key))
        actual = hashlib.sha256()
        try:
            while True:
                part = result['Body'].read(1024 * 1024)
                if not part:
                    break
                actual.update(part)
                if destination is not None:
                    destination.write(part)
        except Exception as exc:
            raise StorageError('S3 download interrupted', retryable=True) from exc
        finally:
            result['Body'].close()
        if actual.hexdigest() != checksum:
            raise StorageError('Artifact checksum mismatch')
