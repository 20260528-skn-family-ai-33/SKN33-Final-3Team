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
    """judge(word) → (분야, 이유, 후보 분야). listed = 분류표에 있는 말."""

    def __init__(self, fields=None, cands=None, listed=()):
        self.fields, self.cands, self.listed = fields or {}, cands or {}, set(listed)

    def judge(self, w):
        return self.fields.get(w), "", self.cands.get(w, "")


AI = "기술·디지털>AI·소프트웨어"
EV = "산업·품목>품목 (→ HS 코드로 연결)"


class BuildRowsTest(unittest.TestCase):
    def rows(self):
        counts = Counter({"AI": 50, "전기차": 40, "은행나무": 1, "건강": 30, "산업": 20, "로보택시": 9, "호두": 1})
        cat = FakeCategories({"AI": AI, "전기차": EV}, {"은행나무": "경제·시장>거시경제"}, listed=["건강", "산업"])
        syn = {"인공지능": "AI", "K뷰티": "K-뷰티"}
        hyp = {"K-뷰티": "뷰티", "전기차보조금": "전기차"}
        rule = lambda w: (EV, "LLM 판단") if w == "건강" else (None, "")   # 팀 미분류 말의 규칙 결과
        return {r["keyword_text"]: r for r in job.build_rows(counts, cat, syn, hyp, rule=rule)}

    def test_statuses(self):
        r = self.rows()
        self.assertEqual({k: v["status"] for k, v in r.items() if v["status"] != "SYNONYM"},
                         {"AI": "CLASSIFIED", "전기차": "CLASSIFIED", "은행나무": "CLASSIFIED", "건강": "CLASSIFIED",
                          "산업": "PENDING", "로보택시": "PENDING", "호두": "UNCLASSIFIED"})
        self.assertEqual(r["은행나무"]["field"], "경제·시장>거시경제")   # 규칙(포함) 추정도 확정 (팀 결정)
        self.assertEqual(r["건강"]["field"], EV)   # 팀이 미분류로 정한 말도 규칙 결과로 (팀 결정 2026-10-21)

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
        orig = (job.counts_from, job.build_rows, job.export, job.make_engine)
        url = str(self.engine.url)
        job.counts_from = lambda *a: Counter()
        job.build_rows = lambda *a, **k: self.ROWS
        job.make_engine = lambda db_url=None: orig[3](url)

        def locked(rows):
            raise PermissionError("엑셀에 열려 있음")
        job.export = locked
        try:
            self.assertEqual(job.run(db_url=url, log_dir=Path(self.tmp.name) / "logs"), 0)
        finally:
            job.counts_from, job.build_rows, job.export, job.make_engine = orig
            for h in job.log.handlers:
                h.close()
            job.log.handlers.clear()
        self.assertEqual(set(self.table()), {"AI", "인공지능", "로보택시"})

    def test_unknown_category_fails_without_writing(self):
        bad = [{"keyword_text": "X", "canonical_text": None, "field": "없는>분야", "status": "CLASSIFIED"}]
        with self.assertRaises(ValueError):
            job.load(self.engine, bad)
        self.assertEqual(self.table(), {})


if __name__ == "__main__":
    unittest.main()
