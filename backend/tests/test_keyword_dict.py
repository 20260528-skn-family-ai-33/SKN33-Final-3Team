"""키워드 사전 DB ↔ csv 맞추기와 관리자 기능(분류 지정·새 분류·동의어)을 SQLite와 임시 csv로 확인합니다.

실행: python -m unittest backend/tests/test_keyword_dict.py
"""
import csv
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import select

import backend.app.models.hs_code  # noqa: F401  article_tag의 hs_code FK 대상
from backend.app.db.session import make_engine, session_scope
from backend.app.models.article import Article, ArticleTag
from backend.app.models.base import Base
from backend.app.models.batch import ServiceOption
from backend.app.models.keyword import KeywordCategory, KeywordTopic
from backend.app.services.news_keywords import keyword_dict as kd


class KeywordDictTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name, path in (("CATEGORY_PATH", kd.CATEGORY_PATH), ("ADDITIONS_PATH", kd.ADDITIONS_PATH)):
            shutil.copy(path, tmp / path.name)
            patcher = mock.patch.object(kd, name, tmp / path.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        kd.sync(self.engine)

    def topic(self, word):
        with session_scope(self.engine) as s:
            t = s.get(KeywordTopic, word)
            c = s.get(KeywordCategory, t.category_id) if t and t.category_id else None
            return t and (t.status, t.canonical_text, c and f"{c.major_name}>{c.minor_name}")

    def additions(self):
        return {r["주제어"]: r for r in kd.read_csv(kd.ADDITIONS_PATH)}

    def classify(self, word):
        """기사 처리(keyword_clean)가 지금 사전으로 내는 (주제어, 분야)."""
        _, _, syn, cat = rules = kd.load_rules()
        [t] = kd.topics([word], rules)
        return t, cat.lookup(t)

    def test_first_sync_fills_db_and_is_stable(self):
        self.assertEqual(self.topic("반덤핑"), ("CLASSIFIED", None, "무역·통상>관세·무역규제"))
        self.assertEqual(self.topic("인공지능"), ("SYNONYM", "AI", None))
        self.assertEqual(self.topic("외국인"), ("UNCLASSIFIED", None, None))  # 팀 검수에서 미분류로 정함
        with session_scope(self.engine) as s:
            self.assertEqual(len(s.scalars(select(KeywordCategory)).all()), 23)
        self.assertEqual(kd.sync(self.engine), {"inserted": 0, "updated": 0, "deleted": 0,
                                                "pulled_categories": 0, "pulled_topics": 0})
        self.assertEqual(self.additions(), {})  # 팀 사전은 그대로, 더한 말 없음

    def test_db_edit_goes_to_csv(self):
        """SQL로 DB를 직접 고쳐도(updated_at이 synced_at보다 나중) 다음 sync에 csv로 가고 기사 처리에 반영"""
        with session_scope(self.engine) as s:
            cid = s.scalar(select(KeywordCategory.category_id).where(KeywordCategory.minor_name == "거시경제"))
            t = s.get(KeywordTopic, "외국인")
            t.status, t.category_id, t.updated_at = "CLASSIFIED", cid, datetime.now() + timedelta(seconds=1)
        self.assertEqual(kd.sync(self.engine)["pulled_topics"], 1)
        self.assertEqual(self.additions()["외국인"]["상태"], "분류")
        self.assertEqual(self.classify("외국인"), ("외국인", "경제·시장>거시경제"))

    def test_csv_edit_goes_to_db(self):
        """csv(topic_additions.csv)를 사람이 고쳐도 다음 sync에 DB로"""
        with open(kd.ADDITIONS_PATH, "a", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(["외국인", "분류", "", "경제·시장", "인구·노동", ""])
        self.assertEqual(kd.sync(self.engine)["updated"], 1)
        self.assertEqual(self.topic("외국인"), ("CLASSIFIED", None, "경제·시장>인구·노동"))

    def test_same_word_both_sides_db_wins(self):
        with open(kd.ADDITIONS_PATH, "a", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(["외국인", "분류", "", "경제·시장", "인구·노동", ""])
        kd.classify_topic(self.engine, "외국인", "경제·시장>거시경제")
        self.assertEqual(self.topic("외국인")[2], "경제·시장>거시경제")
        self.assertEqual(self.additions()["외국인"]["중분류"], "거시경제")

    def test_new_word_pending_then_admin_classifies_into_new_category(self):
        self.assertEqual(kd.register_topics(self.engine, ["양자컴퓨터", "쿼터", "반덤핑"]), 2)
        self.assertEqual(self.topic("양자컴퓨터"), ("PENDING", None, None))  # 규칙으로 못 정함 → 검수 대기
        self.assertEqual(self.topic("쿼터")[0], "CLASSIFIED")  # 새 말이어도 규칙(일치)이면 분야
        self.assertEqual(self.additions()["양자컴퓨터"]["상태"], "신규")
        self.assertIn(("양자컴퓨터", 0), kd.pending_topics(self.engine, 100))

        kd.create_category(self.engine, "기술·디지털", "양자기술")
        self.assertIn({"대분류": "기술·디지털", "중분류": "양자기술"}, kd.read_csv(kd.CATEGORY_PATH))
        kd.classify_topic(self.engine, "양자컴퓨터", "기술·디지털>양자기술")
        self.assertEqual(self.topic("양자컴퓨터"), ("CLASSIFIED", None, "기술·디지털>양자기술"))
        self.assertEqual(self.additions()["양자컴퓨터"]["상태"], "분류")
        self.assertEqual(self.classify("양자컴퓨터"), ("양자컴퓨터", "기술·디지털>양자기술"))
        self.assertNotIn("양자컴퓨터", dict(kd.pending_topics(self.engine, 100)))

        kd.classify_topic(self.engine, "반덤핑", kd.NONE)  # 분야 없음으로 확정
        self.assertEqual(self.topic("반덤핑"), ("UNCLASSIFIED", None, None))
        self.assertEqual(self.classify("반덤핑"), ("반덤핑", None))
        with self.assertRaises(ValueError):
            kd.classify_topic(self.engine, "반덤핑", "없는>분류")

    def test_merge_synonym_retags_articles(self):
        kd.register_topics(self.engine, ["양자컴퓨팅", "양자컴퓨터", "퀀텀컴퓨터"])
        kd.merge_synonym(self.engine, "퀀텀컴퓨터", "양자컴퓨팅")
        with session_scope(self.engine) as s:
            s.add(ServiceOption(option_code="T", option_type="SOURCE", label_ko="t", value_json={}))
            s.flush()
            for i in (1, 2):
                s.add(Article(article_id=i, source_code="T", external_id=str(i), title="t", published_date=date(2026, 1, i),
                              source_url="u", body_masked="b"))
            s.flush()
            s.add_all([ArticleTag(article_id=1, keyword_text="양자컴퓨터"), ArticleTag(article_id=1, keyword_text="양자컴퓨팅"),
                       ArticleTag(article_id=2, keyword_text="양자컴퓨터")])
        # 양자컴퓨팅이 양자컴퓨터의 동의어가 되면, 양자컴퓨팅을 대표어로 쓰던 퀀텀컴퓨터도 양자컴퓨터로
        kd.merge_synonym(self.engine, "양자컴퓨팅", "양자컴퓨터")
        with session_scope(self.engine) as s:
            tags = sorted((t.article_id, t.keyword_text) for t in s.scalars(select(ArticleTag)))
        self.assertEqual(tags, [(1, "양자컴퓨터"), (2, "양자컴퓨터")])
        self.assertEqual(self.topic("퀀텀컴퓨터"), ("SYNONYM", "양자컴퓨터", None))
        self.assertEqual(self.additions()["양자컴퓨팅"]["대표어"], "양자컴퓨터")
        self.assertEqual(self.classify("퀀텀컴퓨터")[0], "양자컴퓨터")
        with self.assertRaises(ValueError):  # 순환
            kd.merge_synonym(self.engine, "양자컴퓨터", "퀀텀컴퓨터")
        with self.assertRaises(ValueError):
            kd.merge_synonym(self.engine, "양자컴퓨터", "사전에없는말")


if __name__ == "__main__":
    unittest.main()
