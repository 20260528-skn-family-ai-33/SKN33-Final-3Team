"""Exercise release decisions with temporary files and no AWS or systemd calls."""
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import receive


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for key in ("APP", "STATIC", "STATE", "BACKUPS"):
            path = root / key
            path.mkdir()
            self.enterContext(patch.object(receive, key, path))
        for key in ("MARKER", "BASE", "JOURNAL"):
            self.enterContext(patch.object(receive, key, receive.STATE / key))
        self.service = self.enterContext(patch.object(receive, "service"))
        self.trade = self.enterContext(patch.object(receive, "trade", return_value=0))
        self.stage = root / "stage"
        self.name = "backend/app/example.py"
        for folder, data in ((receive.APP, b"old"), (self.stage / "source", b"new")):
            path = folder / self.name
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
        self.old = {"commit": "a" * 40, "files": {self.name: hashlib.sha256(b"old").hexdigest()}}
        self.new = {"commit": "b" * 40, "files": {self.name: hashlib.sha256(b"new").hexdigest()}}
        receive.save(receive.MARKER, {"commit": self.old["commit"]})
        receive.save(receive.BASE, self.old)
        (self.stage / "web").mkdir()
        (self.stage / "web/index.html").write_bytes(b"new web")
        (receive.STATIC / "index.html").write_bytes(b"old web")

    def deploy(self, web=True):
        return receive.deploy(self.stage, self.new, web, ["start"], float("inf"))

    def assert_asset_failure_blocks_activation(self, code):
        self.trade.return_value = code
        with self.assertRaisesRegex(RuntimeError, "asset or schema"):
            self.deploy()
        self.service.assert_not_called()
        self.assertEqual(receive.read(receive.BASE), self.old)

    def test_missing_model_a_blocks_activation(self):
        self.assert_asset_failure_blocks_activation(12)

    def test_unavailable_article_index_blocks_activation(self):
        self.assert_asset_failure_blocks_activation(13)

    def test_active_job_defers_release(self):
        self.trade.side_effect = [0, 0, 75]
        self.assertFalse(self.deploy())
        self.service.assert_not_called()
        self.assertFalse(receive.JOURNAL.exists())

    def test_failed_readiness_restores_previous_release(self):
        with patch.object(receive, "ready", side_effect=[RuntimeError("readiness"), None]):
            with self.assertRaisesRegex(RuntimeError, "readiness"):
                self.deploy()
        self.assertEqual((receive.APP / self.name).read_bytes(), b"old")
        self.assertEqual((receive.STATIC / "index.html").read_bytes(), b"old web")
        self.assertEqual(receive.read(receive.BASE), self.old)
        self.assertEqual(receive.read(receive.MARKER)["commit"], self.old["commit"])
        self.assertFalse(receive.JOURNAL.exists())

    def test_ml_activation_updates_source_only(self):
        self.assertTrue(self.deploy(web=False))
        self.assertEqual((receive.APP / self.name).read_bytes(), b"new")
        self.assertEqual((receive.STATIC / "index.html").read_bytes(), b"old web")
        self.service.assert_not_called()
        self.assertEqual(receive.read(receive.BASE), self.new)

    def test_ml_boot_consumes_only_the_web_approved_request(self):
        archive = self.stage / "release.tgz"
        with tarfile.open(archive, "w:gz") as bundle:
            for folder, name in (("source", self.name), ("web", "index.html")):
                bundle.add(self.stage / folder / name, arcname=f"{folder}/{name}")
            manifest = self.stage / "manifest.json"
            receive.save(manifest, self.new)
            bundle.add(manifest, arcname="manifest.json")
        request = {"request": "100-1", "commit": self.new["commit"],
                   "sha256": receive.checksum(archive), "expires": 2000}
        objects = {"delivery/request.json": json.dumps(request).encode()}
        s3 = Mock()
        s3.list_objects_v2.side_effect = lambda Prefix, **_: {
            "Contents": [{"Key": Prefix}] if Prefix in objects else []}
        s3.get_object.side_effect = lambda Key, **_: {"Body": io.BytesIO(objects[Key])}
        s3.put_object.side_effect = lambda Key, Body, **_: objects.update({Key: Body})
        def download(bucket, key, path):
            self.assertEqual(key, "delivery/incoming/100-1/release.tgz")
            Path(path).write_bytes(archive.read_bytes())
        s3.download_file.side_effect = download

        ml_state = self.stage / "ml-state"
        ml_state.mkdir()
        with patch.object(receive.boto3, "client", return_value=s3), \
                patch.object(receive, "deploy", return_value=True) as apply_release, \
                patch.object(receive.time, "time", return_value=1000):
            receive.receive(web=False)
            apply_release.assert_not_called()
            receive.receive(web=True)
            self.assertEqual(json.loads(objects["delivery/ml-approved.json"]), request)
            self.assertEqual(json.loads(objects["delivery/results/100-1.json"])["status"], "deployed")
            objects["delivery/request.json"] = b'{"request":"newer-unapproved"}'
            with patch.object(receive, "STATE", ml_state), patch.object(receive.time, "time", return_value=3000):
                receive.receive(web=False)
            _, manifest, web, _, expires = apply_release.call_args.args
            self.assertEqual(manifest, self.new)
            self.assertFalse(web)
            self.assertIsNone(expires)

    def test_ready_api_with_inactive_worker_fails(self):
        def check_unit(action, quiet, unit):
            if unit == "trade-worker":
                raise subprocess.CalledProcessError(3, unit)

        self.service.side_effect = check_unit
        with patch.object(receive.urllib.request, "urlopen",
                          side_effect=lambda *a, **kw: io.BytesIO(b'{"status":"ready"}')), \
                patch.object(receive.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "readiness"):
                receive.ready()
