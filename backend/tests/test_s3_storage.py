"""Offline fault injection: S3 atomic publication, recovery, authorization failures."""
import io
import json
from pathlib import Path
import tempfile
import sqlite3
import time
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from backend.app.api.common import ApiError
from backend.app.api.config import Settings
from backend.app.api.report_storage import S3ReportStorage
from backend.app.storage.objects import Conflict, S3Objects, StorageError, digest, json_bytes
from backend.app.storage.workspace import Workspace, workspace, checkpoint, model_cache
from backend.app.storage.migrate import migrate


class MemoryS3:
    def __init__(self):
        self.data = {}
        self.fail = None
        self.serial = 0

    def put_object(self, Bucket, Key, Body, **options):
        if hasattr(Body, 'read'):
            Body = Body.read()
        if self.fail and self.fail(Key):
            raise ClientError({'Error': {'Code': 'SlowDown'}}, 'PutObject')
        previous = self.data.get(Key)
        if ((options.get('IfNoneMatch') == '*' and previous is not None)
                or ('IfMatch' in options and (not previous or options['IfMatch'] != previous['ETag']))):
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        self.serial += 1
        etag = '"' + str(self.serial) + '"'
        self.data[Key] = {'content': Body, 'Metadata': options.get('Metadata', {}), 'ETag': etag}
        return {'ETag': etag}

    def get_object(self, Bucket, Key, **options):
        if Key not in self.data:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        item = self.data[Key]
        if options.get('IfNoneMatch') == item['ETag']:
            raise ClientError({'Error': {'Code': '304'}}, 'GetObject')
        return {**item, 'Body': io.BytesIO(item['content'])}


class S3StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'cache'
        self.root.mkdir()
        self.client = MemoryS3()
        self.settings = Settings(values={'ARTIFACT_STORAGE': 's3', 'ARTIFACT_S3_BUCKET': 'unit-bucket',
                                         'ARTIFACT_MODEL_CHECK_SECONDS': '0',
                                         'ARTIFACT_CACHE_DIRECTORY': str(Path(self.temp.name) / 'models-cache')})
        self.store = S3Objects(self.settings.values, client=self.client)
        self.prefix = 'raw/test'

    def test_delete_cache_and_recover_checkpoints_and_raw_together(self):
        with workspace(self.root, self.prefix, store=self.store):
            (self.root / 'raw.csv').write_bytes(b'raw data')
            (self.root / 'checkpoint.json').write_text('{"status":"WAITING"}')
            checkpoint(self.root / 'checkpoint.json')
        for path in self.root.iterdir():
            path.unlink()
        with workspace(self.root, self.prefix, store=self.store, write=False):
            self.assertEqual((self.root / 'raw.csv').read_bytes(), b'raw data')
            self.assertEqual(json.loads((self.root / 'checkpoint.json').read_text())['status'], 'WAITING')

    def test_blob_failure_does_not_publish_complete_checkpoint(self):
        with workspace(self.root, self.prefix, store=self.store):
            (self.root / 'checkpoint.json').write_text('WAITING')
        self.client.fail = lambda key: '/_blobs/' in key
        with self.assertRaises(StorageError):
            with workspace(self.root, self.prefix, store=self.store):
                (self.root / 'checkpoint.json').write_text('COMPLETE')
                (self.root / 'raw.csv').write_text('new data')
        self.client.fail = None
        with workspace(self.root, self.prefix, store=self.store, write=False):
            self.assertEqual((self.root / 'checkpoint.json').read_text(), 'WAITING')
            self.assertFalse((self.root / 'raw.csv').exists())

    def test_manifest_failure_keeps_previous_snapshot(self):
        with workspace(self.root, self.prefix, store=self.store):
            (self.root / 'raw.csv').write_text('old')
        with self.assertRaises(StorageError):
            with workspace(self.root, self.prefix, store=self.store):
                (self.root / 'raw.csv').write_text('new')
                self.client.fail = lambda key: key.endswith('_manifest.json')
        self.client.fail = None
        with workspace(self.root, self.prefix, store=self.store, write=False):
            self.assertEqual((self.root / 'raw.csv').read_text(), 'old')

    def test_expired_writer_cannot_publish_after_takeover(self):
        first = Workspace(self.root, self.prefix, store=self.store, lease_seconds=300)
        first.claim()
        with self.assertRaises(Conflict):
            Workspace(self.root, self.prefix, store=self.store).claim()
        with patch('backend.app.storage.workspace.time.time', return_value=time.time() + 400):
            second = Workspace(self.root, self.prefix, store=self.store)
            second.claim()
        (self.root / 'raw.csv').write_text('stale writer')
        with self.assertRaises(Conflict):
            first.publish()

    def test_failure_swallowed_by_pipeline_cannot_become_success(self):
        with self.assertRaises(StorageError):
            with workspace(self.root, self.prefix, store=self.store):
                (self.root / 'raw.csv').write_text('data')
                self.client.fail = lambda key: '/_blobs/' in key
                try:
                    checkpoint(self.root)
                except StorageError:
                    pass
                self.client.fail = None

    def test_immutable_retry_and_corrupt_bytes(self):
        metadata = self.store.immutable('reports/a.pdf', b'pdf')
        self.assertEqual(self.store.immutable('reports/a.pdf', b'pdf'), metadata)
        with self.assertRaises(StorageError):
            self.store.immutable('reports/a.pdf', b'different')
        self.client.data['reports/a.pdf']['content'] = b'corrupt'
        with self.assertRaises(StorageError):
            self.store.get(metadata['key'], sha256=metadata['sha256'])

    def test_multipart_changed_source_aborts_and_conflict_preserves_existing(self):
        path = self.root / 'large.csv'
        path.write_bytes(b'original')
        original = digest(b'original')
        self.store.immutable('raw/large', b'original')
        self.client.create_multipart_upload = Mock(return_value={'UploadId': 'upload-1'})
        self.client.upload_part = Mock(return_value={'ETag': 'part'})
        self.client.abort_multipart_upload = Mock(return_value={})
        self.client.complete_multipart_upload = Mock(side_effect=ClientError(
            {'Error': {'Code': 'PreconditionFailed'}}, 'CompleteMultipartUpload'))
        self.store._immutable_multipart('raw/large', path, original, path.stat().st_size)
        self.assertEqual(self.client.complete_multipart_upload.call_args.kwargs['IfNoneMatch'], '*')
        self.assertEqual(self.store.get('raw/large')[0], b'original')
        self.client.complete_multipart_upload.reset_mock()
        path.write_bytes(b'changed')
        with self.assertRaises(StorageError):
            self.store._immutable_multipart('raw/large', path, original, path.stat().st_size)
        self.client.complete_multipart_upload.assert_not_called()
        self.assertEqual(self.client.abort_multipart_upload.call_count, 2)
        self.assertEqual(self.store.get('raw/large')[0], b'original')

    def test_report_reads_s3_and_legacy_metadata_never_local(self):
        report = S3ReportStorage(self.settings, store=self.store)
        meta = report.write('1/2/claim/report.pdf', b'pdf')
        self.assertEqual(report.read(meta), b'pdf')
        self.store.immutable('reports/1/old.pdf', b'old')
        self.assertEqual(report.read({'storage': 'local', 'key': '1/old.pdf', 'sha256': digest(b'old')}), b'old')
        with self.assertRaises(ApiError):
            report.read({'storage': 's3', 'key': 'models/private', 'sha256': 'x'})
        self.client.fail = lambda key: True
        with self.assertRaises(ApiError) as caught:
            report.write('1/2/claim/report.xlsx', b'xlsx')
        self.assertTrue(caught.exception.retryable)

    def test_access_denied_is_not_retryable_and_not_missing(self):
        with patch.object(self.client, 'get_object', side_effect=ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')):
            with self.assertRaises(StorageError) as caught:
                self.store.get('reports/private')
        self.assertFalse(caught.exception.retryable)

    def test_path_traversal_rejected(self):
        for key in ('../x', '/absolute', 'C:/x', 'a/../x', 'a\\x'):
            with self.assertRaises(StorageError):
                self.store.get(key)

    def test_migration_idempotency_and_changed_source_rejected(self):
        (self.root / 'raw.csv').write_text('legacy')
        with self.assertRaises(StorageError):
            with workspace(self.root, self.prefix, store=self.store):
                pass
        migrate(self.root, self.prefix, self.store)
        migrate(self.root, self.prefix, self.store)
        (self.root / 'raw.csv').write_text('different')
        with self.assertRaises(StorageError):
            migrate(self.root, self.prefix, self.store)

    def test_model_cache_recovers_and_does_not_mix_revisions(self):
        with workspace(self.root, 'models/monthly', store=self.store):
            (self.root / 'model.joblib').write_bytes(b'model one')
        from backend.app.storage.model_cache import manager
        manager.cache_clear()
        self.addCleanup(manager.cache_clear)
        with patch('backend.app.storage.objects.S3Objects', return_value=self.store):
            first = model_cache(self.settings)
            with workspace(self.root, 'models/monthly', store=self.store):
                (self.root / 'model.joblib').write_bytes(b'model two')
            second = model_cache(self.settings)
        self.assertNotEqual(first, second)
        self.assertEqual((first / 'model.joblib').read_bytes(), b'model one')
        self.assertEqual((second / 'model.joblib').read_bytes(), b'model two')

    def test_migration_detects_source_changed_during_upload(self):
        (self.root / 'a.csv').write_text('first')
        (self.root / 'b.csv').write_text('reviewed')
        def mutate(key):
            if '/_blobs/' in key:
                (self.root / 'b.csv').write_text('changed by a running collector')
            return False
        self.client.fail = mutate
        with self.assertRaisesRegex(StorageError, 'Migration source changed'):
            migrate(self.root, self.prefix, self.store)
        remote = Workspace(self.root, self.prefix, store=self.store)
        remote.read_manifest()
        self.assertEqual(remote.state['files'], {})

    def test_sqlite_budget_is_durable_before_next_request_and_failure_poisons_connection(self):
        from backend.app.storage import sqlite_state
        path = self.root / 'articles.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE budget(count INTEGER)')
            db.execute('INSERT INTO budget VALUES(0)')
        db.close()
        sqlite_state.migrate_database(path, self.store)
        with patch.object(sqlite_state, 'enabled', return_value=True), patch.object(sqlite_state, 'S3Objects', return_value=self.store):
            db = sqlite_state.connect(path)
            with db:
                db.execute('UPDATE budget SET count=1')
            db.close()
            path.unlink()
            db = sqlite_state.connect(path)
            self.assertEqual(db.execute('SELECT count FROM budget').fetchone()[0], 1)
            self.client.fail = lambda key: key == sqlite_state.POINTER
            with self.assertRaises(StorageError):
                with db:
                    db.execute('UPDATE budget SET count=2')
            with self.assertRaises(StorageError):
                db.execute('SELECT count FROM budget')
            db.close()
            self.client.fail = None
            db = sqlite_state.connect(path)
            self.assertEqual(db.execute('SELECT count FROM budget').fetchone()[0], 1)
            db.close()

    def test_un_recovery_fence_is_persisted_before_work_and_release_after_work(self):
        from backend.app.scheduler.jobs import un_data_refresh, un_data_resume
        with workspace(self.root, self.prefix, store=self.store):
            un_data_refresh.save(self.root / un_data_resume.RECOVERY_FENCE, {'attempt': 'one'})
            un_data_refresh.save(self.root / un_data_resume.RECOVERY_GUARD, {'attempt': 'one'})
            remote = Workspace(Path(self.temp.name) / 'remote', self.prefix, store=self.store)
            remote.read_manifest()
            remote.restore()
            self.assertTrue(un_data_resume.recovery_blocked(remote.root))
            un_data_resume.finalize_attempt(self.root, {'token': 'one'})
            remote.read_manifest()
            remote.restore()
            self.assertFalse(un_data_resume.recovery_blocked(remote.root))

    def test_sqlite_budget_update_uploads_changed_chunks_only(self):
        from backend.app.storage import sqlite_state
        path = self.root / 'articles.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE budget(count INTEGER)')
            db.execute('INSERT INTO budget VALUES(0)')
            db.execute('CREATE TABLE articles(body BLOB)')
            db.execute('INSERT INTO articles VALUES(?)', (b'x' * 900_000,))
        db.close()
        sqlite_state.migrate_database(path, self.store)
        before = set(self.client.data)
        with patch.object(sqlite_state, 'enabled', return_value=True), patch.object(sqlite_state, 'S3Objects', return_value=self.store):
            db = sqlite_state.connect(path)
            with db:
                db.execute('UPDATE budget SET count=1')
            db.close()
        meta = json.loads(self.store.get(sqlite_state.POINTER)[0])
        added = {key for key in set(self.client.data) - before if '/_chunks/' in key}
        self.assertGreater(len(added), 0)
        self.assertLess(len(added), sum(len(part['chunks']) for part in meta['parts'].values() if part))


if __name__ == '__main__':
    unittest.main()
