"""키워드 분류 적재(keyword_topic_load)를 SQLite로 확인합니다 (MySQL 없이 실행).

실행: python -m unittest backend/tests/test_keyword_topic_load.py
"""
import tempfile
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update

from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.keyword import KeywordCategory, KeywordTopic
from backend.app.scheduler.jobs import keyword_topic_load as job


class FakeCategories:
    """judge(word) → (분야, 이유, 후보 분야)."""

    def __init__(self, fields=None):
        self.fields = fields or {}

    def judge(self, w):
        return self.fields.get(w), "", ""


AI = "기술·디지털>AI·소프트웨어"
EV = "산업·품목>품목 (→ HS 코드로 연결)"


class BuildRowsTest(unittest.TestCase):
    def rows(self):
        counts = Counter({"AI": 50, "전기차": 40, "은행나무": 1, "산업": 20, "로보택시": 9, "호두": 1})
        cat = FakeCategories({"AI": AI, "전기차": EV, "은행나무": "경제·시장>거시경제"})
        syn = {"인공지능": "AI", "K뷰티": "K-뷰티"}
        hyp = {"K-뷰티": "뷰티", "전기차보조금": "전기차"}
        return {r["keyword_text"]: r for r in job.build_rows(counts, cat, syn, hyp)}

    def test_statuses(self):
        r = self.rows()
        self.assertEqual({k: v["status"] for k, v in r.items() if v["status"] != "SYNONYM"},
                         {"AI": "CLASSIFIED", "전기차": "CLASSIFIED", "은행나무": "CLASSIFIED",
                          "산업": "PENDING", "로보택시": "PENDING", "호두": "UNCLASSIFIED"})
        self.assertEqual(r["은행나무"]["field"], "경제·시장>거시경제")   # 규칙(포함) 추정도 확정 (팀 결정)
        self.assertIsNone(r["산업"]["field"])

    def test_synonym_rows_point_to_final_keyword(self):
        r = self.rows()
        self.assertEqual({k: v["canonical_text"] for k, v in r.items() if v["status"] == "SYNONYM"},
                         {"인공지능": "AI", "K뷰티": "뷰티", "K-뷰티": "뷰티", "전기차보조금": "전기차"})


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = make_engine(f"sqlite:///{(Path(self.tmp.name) / 'kw.db').as_posix()}")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def table(self):
        with session_scope(self.engine) as s:
            return {k: (st, c, cid) for k, st, c, cid in s.execute(select(
                KeywordTopic.keyword_text, KeywordTopic.status, KeywordTopic.canonical_text, KeywordTopic.category_id))}

    ROWS = [{"keyword_text": "AI", "canonical_text": None, "field": AI, "status": "CLASSIFIED"},
            {"keyword_text": "인공지능", "canonical_text": "AI", "field": None, "status": "SYNONYM"},
            {"keyword_text": "로보택시", "canonical_text": None, "field": None, "status": "PENDING"}]

    def test_load_categories_and_topics(self):
        out = job.load(self.engine, self.ROWS)
        self.assertEqual(out["categories"], 23)
        t = self.table()
        self.assertEqual({k: v[0] for k, v in t.items()}, {"AI": "CLASSIFIED", "인공지능": "SYNONYM", "로보택시": "PENDING"})
        with session_scope(self.engine) as s:
            cid = s.scalar(select(KeywordCategory.category_id).filter_by(major_name="기술·디지털", minor_name="AI·소프트웨어"))
        self.assertEqual(t["AI"][2], cid)

    def test_rerun_keeps_category_ids_and_updates(self):
        job.load(self.engine, self.ROWS)
        first = self.table()["AI"][2]
        rows = [dict(r) for r in self.ROWS]
        rows[2] = {**rows[2], "status": "UNCLASSIFIED"}
        job.load(self.engine, rows)
        self.assertEqual(self.table()["AI"][2], first)
        self.assertEqual(self.table()["로보택시"][0], "UNCLASSIFIED")

    def test_admin_edit_in_db_is_not_overwritten(self):
        job.load(self.engine, self.ROWS)
        with session_scope(self.engine) as s:   # 관리자가 DB에서 로보택시를 분류 (synced_at보다 나중에 바뀜)
            cid = s.scalar(select(KeywordCategory.category_id).filter_by(minor_name="로봇·자동화·스마트"))
            s.execute(update(KeywordTopic).where(KeywordTopic.keyword_text == "로보택시")
                      .values(status="CLASSIFIED", category_id=cid, updated_at=datetime(2999, 1, 1)))
        out = job.load(self.engine, self.ROWS)
        self.assertEqual(out["skipped_db_edits"], ["로보택시"])
        self.assertEqual(self.table()["로보택시"][0], "CLASSIFIED")

    def test_rows_missing_from_new_result_are_deleted_but_admin_edits_kept(self):
        job.load(self.engine, self.ROWS)
        with session_scope(self.engine) as s:   # 관리자가 로보택시를 DB에서 바꿈
            s.execute(update(KeywordTopic).where(KeywordTopic.keyword_text == "로보택시")
                      .values(updated_at=datetime(2999, 1, 1)))
        out = job.load(self.engine, self.ROWS[:1])   # 이번 결과에는 AI만 있음
        self.assertEqual(set(self.table()), {"AI", "로보택시"})   # 인공지능은 지움, 관리자가 바꾼 로보택시는 남김
        self.assertEqual((out["deleted"], out["kept_db_edits"]), (1, ["로보택시"]))

    def test_db_is_loaded_even_if_result_file_is_locked(self):
        orig = (job.counts_from, job.build_rows, job.export, job.make_engine, job.dictionaries)
        url = str(self.engine.url)
        job.dictionaries = lambda: (None, {}, {})
        job.counts_from = lambda *a: Counter()
        job.build_rows = lambda *a, **k: self.ROWS
        job.make_engine = lambda db_url=None: orig[3](url)

        def locked(rows):
            raise PermissionError("엑셀에 열려 있음")
        job.export = locked
        try:
            self.assertEqual(job.run(db_url=url, log_dir=Path(self.tmp.name) / "logs"), 0)
        finally:
            job.counts_from, job.build_rows, job.export, job.make_engine, job.dictionaries = orig
            for h in job.log.handlers:
                h.close()
            job.log.handlers.clear()
        self.assertEqual(set(self.table()), {"AI", "인공지능", "로보택시"})

    def test_missing_dictionary_stops_without_touching_db(self):
        """사전이 없으면 적재하지 않음 — 빈 사전으로 넣으면 DB의 동의어(SYNONYM) 행이 '사라진 키워드'로 지워지기 때문"""
        job.load(self.engine, self.ROWS)
        orig = job.kc.SYNONYMS_PATH
        job.kc.SYNONYMS_PATH = Path(self.tmp.name) / "없음.csv"
        try:
            self.assertEqual(job.run(db_url=str(self.engine.url), dry_run=False, log_dir=Path(self.tmp.name) / "logs"), 1)
        finally:
            job.kc.SYNONYMS_PATH = orig
            for h in job.log.handlers:
                h.close()
            job.log.handlers.clear()
        self.assertEqual(set(self.table()), {"AI", "인공지능", "로보택시"})   # 그대로

    def test_counts_include_keywords_only_in_csv(self):
        """기사 적재 전에 돌려도 새 CSV의 키워드가 빠지지 않음: DB 기사 수 + CSV에만 있는 키워드 (둘 다 있으면 큰 값)"""
        clean = Path(self.tmp.name) / "clean.csv"
        clean.write_text("bbstxSn,kw_topic\n1,AI|새키워드\n2,새키워드\n3,AI\n", encoding="utf-8-sig")
        orig = job.keyword_article_counts
        job.keyword_article_counts = lambda engine: Counter({"AI": 5, "옛키워드": 2})
        try:
            counts = job.counts_from("db", None, clean)
        finally:
            job.keyword_article_counts = orig
        self.assertEqual(dict(counts), {"AI": 5, "옛키워드": 2, "새키워드": 2})

    def test_unknown_category_fails_without_writing(self):
        bad = [{"keyword_text": "X", "canonical_text": None, "field": "없는>분야", "status": "CLASSIFIED"}]
        with self.assertRaises(ValueError):
            job.load(self.engine, bad)
        self.assertEqual(self.table(), {})


if __name__ == "__main__":
    unittest.main()
