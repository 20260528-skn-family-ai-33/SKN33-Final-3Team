"""출처·본문 계약 전환의 누락/중복/롤백 검증. 운영 API·DB를 사용하지 않습니다."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, insert, select

from backend.app.db.batch_log import Source, ensure_source
from backend.app.db.session import session_scope
from backend.app.scheduler.jobs import shortnews_daily as daily, shortnews_load as loader
from backend.app.services import shortnews_load as sn
from backend.tests import test_shortnews_daily as daily_fixture, test_shortnews_load as fixture


class SourceTransitionTest(unittest.TestCase):
    def setUp(self):
        self.c = daily_fixture.ShortnewsDailyTest()
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.db = self.c.db

    def make_legacy(self):
        table = loader.reflect(self.db.engine, "article")
        with session_scope(self.db.engine) as session:
            ensure_source(session, Source(loader.LEGACY_SOURCE_CODE, "legacy", {}))
            session.execute(table.update().where(table.c.source_code == sn.SOURCE_CODE)
                            .values(source_code=loader.LEGACY_SOURCE_CODE))

    def test_legacy_article_id_tags_metadata_and_references_survive_repeated_load(self):
        self.db.run_job([fixture.raw()], [fixture.clean()])
        before = self.db.rows("article")[0]
        tags = self.db.rows("article_tag")
        metadata = MetaData()
        loader.reflect(self.db.engine, "article").to_metadata(metadata)
        reference = Table("transition_reference", metadata, Column("id", Integer, primary_key=True),
                          Column("article_id", ForeignKey("article.article_id")))
        reference.create(self.db.engine)
        with self.db.engine.begin() as connection:
            connection.execute(reference.insert().values(id=1, article_id=before["article_id"]))
        self.make_legacy()
        for _ in range(2):
            self.assertEqual(self.db.run_job([fixture.raw()]), 0)
        rows = self.db.rows("article")
        self.assertEqual(rows, [before])
        for table in ("news_keyword_daily", "news_product_daily"):
            self.assertTrue(all(row["day_count"] == 1 for row in self.db.rows(table)))
        self.assertEqual(len(self.db.rows("article_tag")), len(tags))
        with self.db.engine.connect() as connection:
            self.assertEqual(connection.scalar(select(reference.c.article_id)), before["article_id"])

    def test_all_legacy_rows_migrate_even_with_empty_api_result(self):
        self.db.run_job([fixture.raw("001", "2026-10-20"), fixture.raw("002", "2026-10-21")], [fixture.clean()])
        before = self.db.rows("article")
        self.make_legacy()
        self.assertEqual(self.db.run_job([]), 0)
        self.assertEqual(self.db.rows("article"), before)

    def test_source_migration_rolls_back_if_aggregation_fails(self):
        self.db.run_job([fixture.raw()], [fixture.clean()])
        self.make_legacy()
        before = {name: self.db.rows(name) for name in ("article", "article_tag", "news_keyword_daily", "news_product_daily")}
        with patch.object(loader.news_job, "rebuild_daily", side_effect=RuntimeError("synthetic failure")):
            self.assertEqual(self.db.run_job([fixture.raw("002")]), 1)
        self.assertEqual({name: self.db.rows(name) for name in before}, before)
        self.assertEqual(self.db.run_job([fixture.raw("002")]), 0)
        self.assertEqual(len(self.db.rows("article")), 2)
        self.assertTrue(all(r["source_code"] == sn.SOURCE_CODE for r in self.db.rows("article")))

    def test_preexisting_collision_fails_before_any_article_changes(self):
        self.db.run_job([fixture.raw()], [fixture.clean()])
        before = self.db.rows("article")[0]
        self.make_legacy()
        table = loader.reflect(self.db.engine, "article")
        with self.db.engine.begin() as connection:
            connection.execute(insert(table).values(**{k: v for k, v in before.items() if k != "article_id"}))
        saved = {name: self.db.rows(name) for name in ("article", "article_tag", "news_keyword_daily", "news_product_daily")}
        self.assertEqual(self.db.run_job([fixture.raw("002")]), 1)
        self.assertEqual({name: self.db.rows(name) for name in saved}, saved)
        self.assertEqual(self.db.rows("batch_run")[-1]["status"], "FAILED")

    def test_missing_body_never_overwrites_article_or_migrates_source(self):
        self.db.run_job([fixture.raw(nttCn="<p>보존할 실제 본문</p>")])
        self.make_legacy()
        before = self.db.rows("article")
        for changes in ({"nttCn": None, "body": None}, {"nttCn": "<p> </p>", "body": " "}):
            with self.subTest(changes=changes):
                self.assertEqual(self.db.run_job([fixture.raw(**changes)]), 1)
                self.assertEqual(self.db.rows("article"), before)

    def test_missing_body_in_one_row_fails_whole_day_and_recovers(self):
        missing = fixture.raw("002", "2026-10-22", smmarCn="구형 입력의 개요")
        missing.pop("nttCn")
        self.c.pages = [([fixture.raw("001", "2026-10-22"), missing], 2)]
        failed = self.c.run_job()
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(self.db.rows("article"), [])
        self.assertEqual(self.c.state()["days"]["2026-10-22"], "FAILED")
        missing["nttCn"] = "<p>복구된 본문</p>"
        self.assertEqual(self.c.run_job()["status"], "SUCCESS")
        self.assertEqual(len(self.db.rows("article")), 2)

    def test_v2_successes_are_recollected_after_contract_change_without_duplicates(self):
        self.c.fetch.side_effect = self.c.fetch_each_day
        self.assertEqual(self.c.run_job()["status"], "SUCCESS")
        self.make_legacy()
        path = self.c.output / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["schema_version"] = 2
        state.pop("pipeline_revision")
        path.write_text(json.dumps(state), encoding="utf-8")
        self.c.requests.clear()
        now = datetime.fromisoformat("2026-10-24T03:00:00+09:00")
        result = daily.run(env_path=self.c.env, output_dir=self.c.output, db_url=self.db.url, now=now)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261022", "20261023"])
        self.assertEqual(len(self.db.rows("article")), 2)
        self.assertTrue(all(r["source_code"] == sn.SOURCE_CODE for r in self.db.rows("article")))
        self.c.requests.clear()
        self.assertEqual(daily.run(env_path=self.c.env, output_dir=self.c.output, db_url=self.db.url, now=now)["status"], "SUCCESS")
        self.assertEqual([p["search2"] for p in self.c.requests], ["20261023"])
        self.assertEqual(len(self.db.rows("article")), 2)

    def test_revision_mismatch_invalidates_every_database_namespace(self):
        self.c.run_job()
        path = self.c.output / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        progress = next(iter(state["databases"].values()))
        state["databases"]["0" * 64] = json.loads(json.dumps(progress))
        state["pipeline_revision"] = "old-contract"
        path.write_text(json.dumps(state), encoding="utf-8")
        state = daily.read_state(self.c.output, datetime.fromisoformat("2026-10-24").date(),
                                 daily.database_identity(self.db.url))
        self.assertEqual(state["pipeline_revision"], daily.PIPELINE_REVISION)
        self.assertTrue(all(p["days"]["2026-10-22"] == "FAILED" for p in state["databases"].values()))


if __name__ == "__main__":
    unittest.main()
