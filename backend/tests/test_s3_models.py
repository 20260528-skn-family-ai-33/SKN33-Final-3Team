"""S3/DB partial success must recover without repeating model training."""
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from backend.tests import test_ml_candidate_models as fixture
from backend.tests.test_s3_storage import MemoryS3
from backend.app.ml import candidate_models as models
from backend.app.storage.objects import S3Objects, StorageError


class S3ModelTests(unittest.TestCase):
    setUp = fixture.MonthlyRunTest.setUp
    tearDown = fixture.MonthlyRunTest.tearDown

    def environment(self, client):
        stack = ExitStack()
        self.addCleanup(stack.close)
        store = S3Objects({'ARTIFACT_S3_BUCKET': 'test-bucket'}, client=client)
        stack.enter_context(patch('backend.app.storage.workspace.enabled', return_value=True))
        stack.enter_context(patch('backend.app.storage.workspace.S3Objects', return_value=store))
        stack.enter_context(patch.object(models.artifacts, 'input_manifest', return_value=fixture.FILES))
        stack.enter_context(patch.object(models.artifacts, 'db_inputs', return_value=fixture.DB))
        return store

    def run_model(self):
        return models.run('2026-02-01', self.engine, kind='lightgbm', out_dir=self.out,
                          cache_dir=None, n_jobs=1, growth=False)

    def test_db_failure_reuses_confirmed_s3_artifact(self):
        self.environment(MemoryS3())
        with patch.object(models.artifacts, 'load_training_data', return_value=fixture.loaded()), \
             patch.object(models, 'register_records', side_effect=RuntimeError('DB unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'DB unavailable'):
                self.run_model()
        # Cache loss between S3 success and DB recovery must not force retraining.
        for path in self.out.glob('*'):
            if path.is_file():
                path.unlink()
        with patch.object(models.artifacts, 'load_training_data', side_effect=AssertionError('unexpected retraining')):
            result = self.run_model()
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertTrue(list(self.out.glob('*.joblib')))

    def test_upload_failure_prevents_db_ready_registration(self):
        client = MemoryS3()
        self.environment(client)
        client.fail = lambda key: '/_blobs/' in key
        with patch.object(models.artifacts, 'load_training_data', return_value=fixture.loaded()), \
             patch.object(models, 'register_records') as register:
            with self.assertRaises(StorageError):
                self.run_model()
        register.assert_not_called()


if __name__ == '__main__':
    unittest.main()
