"""Check missing production inputs with local fixtures and the real asset loaders."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_assets
from backend.app.api.config import Settings


class MissingAssetTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        settings = Settings(values={"ARTIFACT_STORAGE": "local",
                                    "API_ML_MODEL_DIRECTORY": str(self.folder),
                                    "NEWS_VECTOR_ROOT": str(self.folder)})
        self.enterContext(patch("backend.app.api.config.Settings", return_value=settings))

    def test_missing_ready_registry(self):
        with self.assertRaises(FileNotFoundError):
            check_assets.model_check()

    def test_ready_registry_points_to_missing_model(self):
        registry = {"status": "READY", "model_key": "model_a", "data_info": {"trade_month": "2026-09"},
                    "created_at": "2026-10-01", "artifact_path": "missing.joblib"}
        (self.folder / "model_a_two_stage_202609.registry.json").write_text(json.dumps(registry))
        with self.assertRaises(FileNotFoundError):
            check_assets.model_check()

    def test_missing_active_article_index(self):
        with self.assertRaises(FileNotFoundError):
            check_assets.article_check()
