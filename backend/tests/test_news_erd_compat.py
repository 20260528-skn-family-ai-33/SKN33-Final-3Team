"""기존 뉴스의 모든 적재 시나리오를 관리자 ERD 컬럼·INSERT 트리거와 함께 검증합니다."""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from backend.app.db.session import session_scope
from backend.app.db.batch_log import ensure_source
from backend.app.services import news_schema
from backend.app.scheduler.jobs import news_load as job
from backend.tests import test_news_load as original
from backend.tests.news_erd_fixture import install_trigger


class AdminErdNewsLoadTest(original.NewsLoadTest):
    def setUp(self):
        super().setUp()
        self.snapshot_dir = Path(self.tmp.name) / "snapshots"
        snapshot_patch = patch.object(news_schema, "SNAPSHOT_DIR", self.snapshot_dir)
        snapshot_patch.start()
        self.addCleanup(snapshot_patch.stop)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE article ADD COLUMN visibility_status VARCHAR(10) NOT NULL DEFAULT 'VISIBLE'")
            conn.exec_driver_sql("ALTER TABLE article ADD COLUMN first_ingested_at DATETIME NULL")
            conn.exec_driver_sql("ALTER TABLE article ADD COLUMN admin_meta_json JSON NOT NULL")
            conn.exec_driver_sql("ALTER TABLE batch_run ADD COLUMN detail_json JSON NOT NULL")
        install_trigger(self.engine)

    def article(self):
        table = news_schema.reflect(self.engine, "article")
        with self.engine.connect() as conn:
            return dict(conn.execute(table.select()).mappings().one())

    def test_snapshot_and_batch_required_json_are_written(self):
        self.assertEqual(self.run_job([original.raw("1", "2026-10-01")], [original.clean("1", "2026-10-01")]), 0)
        meta = self.article()["admin_meta_json"]
        snapshot = meta["classification_input"]
        payload = Path(snapshot["snapshot_ref"]).read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), snapshot["sha256"])
        saved = json.loads(payload)
        self.assertIn("backend/app/services/news_keywords/category_rules.py", saved["files"])
        self.assertIn("keyword_topic", saved["tables"])
        self.assertEqual(meta["classification_state"], {"status": "PENDING", "revision": 0})
        batch = news_schema.reflect(self.engine, "batch_run")
        with self.engine.connect() as conn:
            row = conn.execute(batch.select()).mappings().one()
            self.assertEqual(row["detail_json"]["schema_version"], 1)
            self.assertEqual(row["status"], "SUCCESS")

    def test_snapshot_and_admin_flags_survive_rerun(self):
        self.run_job([original.raw("1", "2026-10-01")], [original.clean("1", "2026-10-01", "AI", "US", "854143")])
        before = self.article()
        meta = before["admin_meta_json"]
        meta.update(country_tags_manual=True, hs_tags_manual=True)
        first = datetime(2025, 1, 1)
        table = news_schema.reflect(self.engine, "article")
        with self.engine.begin() as conn:
            conn.execute(table.update().values(admin_meta_json=meta, visibility_status="HIDDEN", first_ingested_at=first))
        self.assertEqual(self.run_job([original.raw("1", "2026-10-02", title="변경")],
                                     [original.clean("1", "2026-10-02", "반도체", "CN", "")]), 0)
        after = self.article()
        self.assertEqual(after["admin_meta_json"], meta)
        self.assertEqual(after["visibility_status"], "HIDDEN")
        self.assertEqual(after["first_ingested_at"], first)
        tags = self.rows(original.ArticleTag, "country_code", "hs_code", "keyword_text")
        self.assertIn(("US", None, None), tags)
        self.assertIn((None, "854143", None), tags)
        self.assertIn((None, None, "반도체"), tags)

    def test_policy_change_only_changes_new_article_snapshot(self):
        policy_root = Path(self.tmp.name) / "policy-root"
        policy = policy_root / "backend/app/services/news_keywords/rules.py"
        policy.parent.mkdir(parents=True)
        policy.write_text("POLICY = 1\n", encoding="utf-8")
        with patch.object(news_schema, "ROOT", policy_root):
            job.load(self.engine, [original.raw("1", "2026-10-01")], [])
            first = self.article()["admin_meta_json"]["classification_input"]
            policy.write_text("POLICY = 2\n", encoding="utf-8")
            job.load(self.engine, [original.raw("1", "2026-10-01"), original.raw("2", "2026-10-01")], [])
        table = news_schema.reflect(self.engine, "article")
        with self.engine.connect() as conn:
            snapshots = {r["external_id"]: r["admin_meta_json"]["classification_input"]
                         for r in conn.execute(table.select()).mappings()}
        self.assertEqual(snapshots["1"], first)
        self.assertNotEqual(snapshots["2"]["sha256"], first["sha256"])
        for key, version in (("1", 1), ("2", 2)):
            payload = Path(snapshots[key]["snapshot_ref"]).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), snapshots[key]["sha256"])
            self.assertEqual(json.loads(payload)["files"]["backend/app/services/news_keywords/rules.py"],
                             f"POLICY = {version}\n")

    def test_trigger_rejects_the_previous_null_snapshot(self):
        table = news_schema.reflect(self.engine, "article")
        with session_scope(self.engine) as session:
            ensure_source(session, job.SOURCE)
        with self.assertRaises(IntegrityError):
            with session_scope(self.engine) as session:
                row = original.nl.article_row(original.raw("1", "2026-10-01"))
                row["published_date"] = original.date(2026, 10, 1)
                row.update(first_ingested_at=datetime(2026, 10, 1), visibility_status="VISIBLE",
                           admin_meta_json=news_schema.admin_defaults(None))
                session.execute(table.insert().values(**row))

    def test_snapshot_write_failure_rolls_back_article(self):
        with patch.object(news_schema, "classification_snapshot", side_effect=OSError("test snapshot unavailable")):
            self.assertEqual(self.run_job([original.raw("1", "2026-10-01")], [original.clean("1", "2026-10-01")]), 1)
        self.assertEqual(self.rows(original.Article, "article_id"), [])
        self.assertEqual(self.rows(original.BatchRun, "status"), [("FAILED",)])

    def test_corrupt_snapshot_is_not_overwritten(self):
        self.run_job([original.raw("1", "2026-10-01")], [original.clean("1", "2026-10-01")])
        snapshot = self.article()["admin_meta_json"]["classification_input"]
        path = Path(snapshot["snapshot_ref"])
        path.write_text("corrupt", encoding="utf-8")
        with self.assertRaises(ValueError):
            job.load(self.engine, [original.raw("2", "2026-10-01")], [])
        self.assertEqual(path.read_text(), "corrupt")
        self.assertEqual(len(self.rows(original.Article, "article_id")), 1)
