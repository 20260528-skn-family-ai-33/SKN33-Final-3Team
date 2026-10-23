"""단신속보 적재 회귀 검증. 실제 API/운영 DB 없이 SQLite FK와 ERD 추가 컬럼까지 확인합니다."""
import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import Column, DateTime, JSON, MetaData, String, event, select
from sqlalchemy.dialects import mysql

from backend.app.db.session import make_engine, session_scope
from backend.app.db import news_lock
from backend.app.models.base import Base
from backend.app.models.hs_code import HsCodeMst
from backend.app.models.news import Article, ArticleTag, NewsKeywordDaily, NewsProductDaily
from backend.app.scheduler.jobs import news_load, shortnews_load as job
from backend.app.services import shortnews_load as sn
from backend.app.services import news_schema
from backend.tests.news_erd_fixture import extend_metadata, install_trigger


def raw(key="001", day="2026-10-01", **changes):
    return {"nttSn": key, "nttSj": "단신 제목", "nttCn": "<p>문의 a@example.com 02-123-4567</p>",
            "othbcDt": day, "kotraNewsUrl": "https://example.com/news/" + key, "nat": "미국", **changes}


def clean(key="001", keywords="AI", countries="US", hs="854143"):
    return {"nttSn": key, "kw_topic": keywords, "country_all": countries, "hs6": hs}


class ConversionTest(unittest.TestCase):
    def test_missing_optional_fields_are_equal_in_csv_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            sparse = {"nttSn": "001", "othbcDt": "2026-10-01"}
            json_path, csv_path = directory / "raw.json", directory / "raw.csv"
            json_path.write_text(json.dumps([sparse]), encoding="utf-8")
            with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=sn.RAW_COLUMNS)
                writer.writeheader()
                writer.writerow(sparse)
            self.assertEqual(sn.read_records(json_path), sn.read_records(csv_path))
            for path in (json_path, csv_path):
                self.assertEqual(sn.read_records(path)[0]["nat"], "")

    def test_mapping_and_masking_and_date(self):
        r = sn.article_row(raw(day="20261001", regn="북미"))
        self.assertEqual(r["external_id"], "001")
        self.assertEqual(r["published_date"], date(2026, 10, 1))
        self.assertEqual(r["body_masked"], "문의 [이메일] [전화번호]")
        self.assertIsNone(r["category"])
        with self.assertRaisesRegex(ValueError, "본문"):
            sn.article_row(raw(nttCn="<p></p>", nttSj="문의 a@example.com"))

    def test_bad_identity_date_and_erd_length_fail(self):
        for changes in ({"nttSn": ""}, {"nttSn": "한글"}, {"nttSn": "x" * 101},
                        {"othbcDt": "2026-02-30"}, {"nttSj": "가" * 601}, {"kotraNewsUrl": "x" * 2049}):
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                sn.article_row(raw(**changes))

    def test_csv_json_roundtrip_preserves_ids(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "raw.json").write_text(json.dumps([raw()], ensure_ascii=False), encoding="utf-8-sig")
            with (p / "raw.csv").open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(raw()))
                writer.writeheader()
                writer.writerow(raw())
            self.assertEqual(sn.read_records(p / "raw.csv"), sn.read_records(p / "raw.json"))
            (p / "bad.json").write_text('{"nttSn": "001"}')
            with self.assertRaises(ValueError):
                sn.read_records(p / "bad.json")
            (p / "bad.csv").write_text("nttSn,nttSj\n001,제목", encoding="utf-8")
            with self.assertRaises(ValueError):
                sn.read_records(p / "bad.csv")

    def test_team_body_fields_roundtrip_without_losing_plain_text(self):
        records = [
            raw("001", body="<수출전망> 문의 a@example.com", nttCn="<p>선택하지 않을 HTML</p>"),
            raw("002", body="  ", nttCn="<p>첫 문단</p><p>문의 02-123-4567</p>"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw.json").write_text(json.dumps(records), encoding="utf-8")
            with (root / "raw.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
            for name in ("raw.csv", "raw.json"):
                with self.subTest(format=name):
                    articles = [sn.article_row(r) for r in sn.read_records(root / name)]
                    self.assertEqual([a["body_masked"] for a in articles],
                                     ["<수출전망> 문의 [이메일]", "첫 문단\n문의 [전화번호]"])
                    self.assertTrue(all(a["source_code"] == "KOTRA_SHORT_NEWS" for a in articles))


class ShortnewsLoadTest(unittest.TestCase):
    extended = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        load_lock_patch = patch.object(news_lock, "LOAD_LOCK_DIR", self.root / "load_locks")
        load_lock_patch.start()
        self.addCleanup(load_lock_patch.stop)
        snapshot_patch = patch.object(news_schema, "SNAPSHOT_DIR", self.root / "snapshots")
        snapshot_patch.start()
        self.addCleanup(snapshot_patch.stop)
        self.url = "sqlite:///" + str(self.root / "test.db")
        self.engine = make_engine(self.url)

        @event.listens_for(self.engine, "connect")
        def enable_fk(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        metadata = MetaData()
        for table in list(Base.metadata.tables.values()):
            table.to_metadata(metadata)
        metadata.tables["country"].append_column(Column("name_ko", String(120)))
        if self.extended:
            extend_metadata(metadata)
        metadata.create_all(self.engine)
        if self.extended:
            install_trigger(self.engine)
        with session_scope(self.engine) as s:
            s.execute(metadata.tables["country"].insert(), [
                {"country_code": "US", "name_ko": "미국"}, {"country_code": "CN", "name_ko": "중국"},
                {"country_code": "JP", "name_ko": "일본"}, {"country_code": "CZ", "name_ko": "체코공화국"}])
            s.add_all([HsCodeMst(hs_code=c, hs_ver="HS2022", name_ko=c, source_edition="test",
                                original_name_ko=c if len(c) == 6 else None)
                       for c in ("8541", "854143")])

    def tearDown(self):
        for handler in job.log.handlers:
            handler.close()
        job.log.handlers.clear()
        self.engine.dispose()
        self.tmp.cleanup()

    def rows(self, name):
        table = job.reflect(self.engine, name)
        with self.engine.connect() as connection:
            return [dict(r) for r in connection.execute(select(table)).mappings()]

    def run_job(self, raws, cleans=None):
        p = self.root / "raw.json"
        p.write_text(json.dumps(raws, ensure_ascii=False), encoding="utf-8")
        c = None
        if cleans is not None:
            c = self.root / "clean.json"
            c.write_text(json.dumps(cleans), encoding="utf-8")
        return job.run(p, c, self.url, self.root / "logs")

    def test_raw_only_and_batch_and_unknown_country_preserves_article(self):
        self.assertEqual(self.run_job([raw(), raw("002", nat="미등록국")]), 0)
        self.assertEqual(len(self.rows("article")), 2)
        self.assertEqual([t["country_code"] for t in self.rows("article_tag")], ["US"])
        batch = self.rows("batch_run")[0]
        self.assertEqual((batch["status"], batch["received_count"], batch["request_count"]), ("SUCCESS", 2, 0))
        self.assertEqual(self.rows("service_option")[0]["option_code"], "KOTRA_SHORT_NEWS")
        if self.extended:
            meta = self.rows("article")[0]["admin_meta_json"]
            snapshot = meta["classification_input"]
            payload = Path(snapshot["snapshot_ref"]).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), snapshot["sha256"])
            self.assertIn("files", json.loads(payload))
            self.assertIn("tables", json.loads(payload))
            self.assertEqual(meta, news_schema.admin_defaults(snapshot))
            self.assertIsNotNone(self.rows("article")[0]["first_ingested_at"])
            self.assertEqual(batch["detail_json"]["result"]["unknown_countries"], ["미등록국"])

    def test_repeated_and_duplicate_rows_update_same_id(self):
        job.load(self.engine, [raw()], [clean()])
        before = self.rows("article")[0]
        result = job.load(self.engine, [raw(nttSj="앞"), raw(nttSj="뒤")])
        after = self.rows("article")[0]
        self.assertEqual(after["article_id"], before["article_id"])
        self.assertEqual(after["title"], "뒤")
        self.assertEqual(result["duplicates"], 1)
        self.assertTrue(any(t["keyword_text"] == "AI" for t in self.rows("article_tag")))
        self.assertTrue(any(t["hs_code"] == "854143" for t in self.rows("article_tag")))
        if self.extended:
            self.assertEqual(after["first_ingested_at"], before["first_ingested_at"])

    def test_country_change_and_date_change_rebuild_old_and_new_days(self):
        job.load(self.engine, [raw()], [clean()])
        job.load(self.engine, [raw(day="2026-10-03", nat="중국")])
        counts = self.rows("news_keyword_daily")
        self.assertEqual({r["metric_date"] for r in counts}, {date(2026, 10, 3)})
        self.assertEqual({r["country_key"] for r in counts}, {"*", "CN"})
        self.assertEqual({r["hs_code"] for r in self.rows("news_product_daily")}, {"8541", "854143"})

    def test_partial_clean_does_not_clear_other_keywords(self):
        raws = [raw(), raw("002")]
        job.load(self.engine, raws, [clean(), clean("002", keywords="반도체")])
        job.load(self.engine, raws, [clean("002", keywords="배터리", hs="")])
        self.assertEqual({t["keyword_text"] for t in self.rows("article_tag") if t["keyword_text"]}, {"AI", "배터리"})

    def test_source_collision_and_cross_source_counts(self):
        # 기존 시장뉴스를 동일 원천 ID로 만들어도 단신속보와 구분되어야 합니다.
        job.load(self.engine, [raw()], [clean()])
        table = job.reflect(self.engine, "article")
        with session_scope(self.engine) as s:
            from backend.app.db.batch_log import ensure_source
            ensure_source(s, news_load.SOURCE)
            r = sn.article_row(raw())
            r["source_code"] = "KOTRA_MARKET"
            news_load.upsert_articles(s, [r], table)
            market_id = news_load.article_ids(s, ["001"])["001"]
            s.add(ArticleTag(article_id=market_id, keyword_text="AI"))
        job.load(self.engine, [raw(nttSj="단신 변경")], [clean()])
        articles = {r["source_code"]: r for r in self.rows("article")}
        self.assertEqual(articles["KOTRA_MARKET"]["title"], "단신 제목")
        self.assertEqual(articles["KOTRA_SHORT_NEWS"]["title"], "단신 변경")
        total = [r for r in self.rows("news_keyword_daily") if r["country_key"] == "*"]
        self.assertEqual(total[0]["day_count"], 2)

    def test_manual_metadata_and_tags_remain(self):
        if not self.extended:
            self.skipTest("관리자 컬럼은 신규 ERD에만 존재")
        job.load(self.engine, [raw()], [clean()])
        table = job.reflect(self.engine, "article")
        meta = self.rows("article")[0]["admin_meta_json"]
        meta.update(country_tags_manual=True, hs_tags_manual=True)
        meta["visibility"]["reason"] = "관리자 숨김"
        first_ingested = datetime(2025, 1, 1)
        with self.engine.begin() as conn:
            conn.execute(table.update().values(visibility_status="HIDDEN", admin_meta_json=meta,
                                               first_ingested_at=first_ingested))
        job.load(self.engine, [raw(nat="중국")], [clean(countries="CN", hs="", keywords="새키워드")])
        saved = self.rows("article")[0]
        self.assertEqual(saved["visibility_status"], "HIDDEN")
        self.assertEqual(saved["admin_meta_json"], meta)
        self.assertEqual(saved["first_ingested_at"], first_ingested)
        tags = self.rows("article_tag")
        self.assertEqual({t["country_code"] for t in tags if t["country_code"]}, {"US"})
        self.assertEqual({t["hs_code"] for t in tags if t["hs_code"]}, {"854143"})

    def test_mid_transaction_failure_rolls_back_and_logs_failed(self):
        self.assertEqual(self.run_job([raw()], [clean()]), 0)
        before = {name: self.rows(name) for name in ("article", "article_tag", "news_keyword_daily", "news_product_daily")}
        real_rebuild = news_load.rebuild_daily

        def fail_after_rebuild(*args):
            real_rebuild(*args)
            raise RuntimeError("test failure")

        with patch.object(news_load, "rebuild_daily", side_effect=fail_after_rebuild):
            self.assertEqual(self.run_job([raw(nttSj="수정"), raw("002")], [clean(keywords="변경")]), 1)
        for name, rows in before.items():
            self.assertEqual(self.rows(name), rows)
        self.assertEqual([r["status"] for r in self.rows("batch_run")], ["SUCCESS", "FAILED"])

    def test_invalid_input_writes_nothing_and_empty_input_is_safe(self):
        self.assertEqual(self.run_job([raw(), raw("002", othbcDt="bad")]), 1)
        self.assertEqual(self.rows("article"), [])
        self.assertEqual(self.run_job([]), 0)
        self.assertEqual(self.rows("article"), [])

    def test_country_alias_and_unknown_clean_tags(self):
        job.load(self.engine, [raw(nat="체코")])
        self.assertEqual(self.rows("article_tag")[0]["country_code"], "CZ")
        result = job.load(self.engine, [raw()], [clean(countries="US|ZZ", hs="854143|000000")])
        self.assertEqual(result["unknown_countries"], ["ZZ"])
        self.assertEqual(result["unknown_hs"], ["000000"])
        self.assertEqual(len(self.rows("article")), 1)
        self.assertEqual(len(self.rows("article_tag")), 3)

    def test_non_hs6_is_reported_not_stored_or_counted(self):
        result = job.load(self.engine, [raw()], [clean(hs="8541|854143")])
        self.assertEqual(result["unknown_hs"], ["8541"])
        self.assertEqual([r["hs_code"] for r in self.rows("article_tag") if r["hs_code"]], ["854143"])
        self.assertEqual({r["hs_code"] for r in self.rows("news_product_daily")}, {"8541", "854143"})

    def test_missing_json_country_can_be_loaded(self):
        row = raw()
        row.pop("nat")
        self.assertEqual(self.run_job([row]), 0)
        self.assertEqual(len(self.rows("article")), 1)
        self.assertEqual(self.rows("article_tag"), [])

    def test_missing_table_fails_with_batch_record(self):
        NewsProductDaily.__table__.drop(self.engine)
        self.assertEqual(self.run_job([raw()]), 1)
        self.assertEqual(self.rows("article"), [])
        self.assertEqual(self.rows("batch_run")[0]["status"], "FAILED")

    def test_mysql_statement_uses_upsert_without_overwriting_admin_fields(self):
        table = job.reflect(self.engine, "article")
        statements = []

        class Session:
            def get_bind(self):
                return type("Bind", (), {"dialect": mysql.dialect()})()

            def execute(self, stmt):
                statements.append(str(stmt.compile(dialect=mysql.dialect())))

        with session_scope(self.engine) as s:
            rows = news_schema.article_insert_rows(s, table, [sn.article_row(raw())])
        news_load.upsert_articles(Session(), rows, table)
        update = statements[0].split("ON DUPLICATE KEY UPDATE")[1]
        self.assertIn("body_masked", update)
        self.assertNotIn("admin_meta_json", update)
        self.assertNotIn("first_ingested_at", update)
        self.assertNotIn("visibility_status", update)


class LegacySchemaTest(ShortnewsLoadTest):
    extended = False


if __name__ == "__main__":
    unittest.main()
