"""기사 임베딩 동기화를 가짜 임베딩·메모리 Chroma·SQLite로 확인합니다 (OpenAI·MySQL 없이 실행).

실행: python -m unittest backend/tests/test_news_embed.py
"""
import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

import chromadb
from sqlalchemy import update

from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.batch import ServiceOption
from backend.app.models.news import Article, ArticleTag
from backend.app.scheduler.jobs import news_embed as ne


class FakeEmbed:
    """문서마다 다른 8차원 벡터. 호출한 문서 수를 셉니다."""

    def __init__(self):
        self.texts = []

    def __call__(self, texts):
        self.texts += texts
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class ChunkTest(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(ne.chunk_text("첫 문단\n둘째 문단"), ["첫 문단\n둘째 문단"])

    def test_long_text_splits_by_paragraph_with_overlap(self):
        paras = [f"{i}" * 400 for i in range(5)]  # 400자 문단 5개
        chunks = ne.chunk_text("\n".join(paras), size=1000, overlap=150)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(chunks[0].startswith("0" * 400) and chunks[0].endswith("1" * 400))
        self.assertTrue(chunks[1].startswith("1" * 150))  # 앞 조각 끝 150자로 시작
        self.assertEqual("".join(chunks).replace("\n", "").count("4"), 400)

    def test_paragraph_longer_than_size_is_cut(self):
        chunks = ne.chunk_text("가" * 2500, size=1000, overlap=150)
        self.assertTrue(all(len(c) <= 1000 + 151 for c in chunks))
        self.assertEqual(chunks[0], "가" * 1000)

    def test_empty_body_uses_title(self):
        docs = ne.build_docs({"article_id": 1, "external_id": "9", "source_code": "KOTRA_MARKET", "title": "제목",
                              "published_date": date(2026, 1, 2), "category": None, "body_masked": "",
                              "countries": [], "keywords": []})
        self.assertEqual(list(docs), ["1:0"])
        doc, meta = docs["1:0"]
        self.assertEqual(doc, "[2026-01-02] 제목\n제목")
        self.assertNotIn("countries", meta)  # 빈 목록은 메타데이터에 넣지 않음


class NewsEmbedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = f"sqlite:///{(Path(self.tmp.name) / 'news.db').as_posix()}"
        self.engine = make_engine(self.db)
        Base.metadata.create_all(self.engine)
        with session_scope(self.engine) as s:
            s.add(ServiceOption(option_code="KOTRA_MARKET", option_type="SOURCE", label_ko="KOTRA", value_json={}))
            s.flush()
            s.add_all([Article(article_id=1, source_code="KOTRA_MARKET", external_id="101", title="미국 배터리 공급망",
                               published_date=date(2026, 10, 1), source_url="u", body_masked="배터리 " * 300,
                               category="트렌드"),
                       Article(article_id=2, source_code="KOTRA_MARKET", external_id="102", title="일본 화장품",
                               published_date=date(2026, 10, 5), source_url="u", body_masked="화장품 시장", category=None)])
            s.flush()
            s.add_all([ArticleTag(article_id=1, country_code="US"), ArticleTag(article_id=1, keyword_text="배터리"),
                       ArticleTag(article_id=1, keyword_text="공급망"), ArticleTag(article_id=2, country_code="JP"),
                       ArticleTag(article_id=2, keyword_text="화장품"), ArticleTag(article_id=2, keyword_text="배터리")])
        self.client = chromadb.EphemeralClient()
        for name in (ne.ARTICLE_COLLECTION, ne.KEYWORD_COLLECTION):  # 메모리 클라이언트는 프로세스에서 공유됨
            if name in [c.name for c in self.client.list_collections()]:
                self.client.delete_collection(name)

    def tearDown(self):
        self.engine.dispose()
        for h in ne.log.handlers:
            h.close()
        ne.log.handlers.clear()
        self.tmp.cleanup()

    def run_sync(self, embed, **kw):
        return ne.run(self.db, client=self.client, embed=embed, log_dir=Path(self.tmp.name), **kw)

    def articles(self):
        return self.client.get_collection(ne.ARTICLE_COLLECTION)

    def test_chunks_and_keywords_are_added_with_metadata(self):
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        got = self.articles().get(include=["documents", "metadatas"])
        by_id = dict(zip(got["ids"], zip(got["documents"], got["metadatas"])))
        self.assertEqual(sorted(by_id), ["1:0", "1:1", "2:0"])  # 1,200자 기사는 조각 2개
        doc, meta = by_id["1:0"]
        self.assertTrue(doc.startswith("[2026-10-01 | US | 트렌드] 미국 배터리 공급망\n배터리"))
        self.assertEqual((meta["article_id"], meta["external_id"], meta["published_ymd"], meta["countries"],
                          meta["keywords"], meta["chunk"]), (1, "101", 20261001, ["US"], ["배터리", "공급망"], 0))
        kw = self.client.get_collection(ne.KEYWORD_COLLECTION).get(include=["metadatas"])
        self.assertEqual(dict(zip(kw["ids"], (m["article_count"] for m in kw["metadatas"]))),
                         {"공급망": 1, "배터리": 2, "화장품": 1})

    def test_rerun_adds_nothing_and_changed_article_is_replaced(self):
        self.run_sync(FakeEmbed())
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(embed.texts, [])  # 이미 있으면 OpenAI를 부르지 않음
        with session_scope(self.engine) as s:
            s.execute(update(Article).where(Article.article_id == 1).values(body_masked="짧아진 본문"))
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(len(embed.texts), 1)  # 바뀐 기사만, 조각 1개로 다시
        self.assertEqual(sorted(self.articles().get()["ids"]), ["1:0", "2:0"])  # 예전 조각 1:1은 지워짐

    def test_keyword_count_is_updated_without_embedding(self):
        self.run_sync(FakeEmbed())
        with session_scope(self.engine) as s:
            s.add(ArticleTag(article_id=2, keyword_text="공급망"))
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(embed.texts, [])  # 기사 수만 바뀌면 다시 임베딩하지 않음
        got = self.client.get_collection(ne.KEYWORD_COLLECTION).get(ids=["공급망"], include=["metadatas"])
        self.assertEqual(got["metadatas"][0]["article_count"], 2)

    def test_keywords_only_leaves_articles_untouched(self):
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed, keywords_only=True), 0)
        self.assertEqual(sorted(embed.texts), ["공급망", "배터리", "화장품"])   # 키워드만 임베딩
        self.assertNotIn(ne.ARTICLE_COLLECTION, [c.name for c in self.client.list_collections()])

    def test_dry_run_does_not_embed(self):
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed, dry_run=True), 0)
        self.assertEqual(embed.texts, [])
        self.assertEqual(self.articles().count(), 0)

    def test_search_filters_by_country_keyword_and_date(self):
        embed = FakeEmbed()
        self.run_sync(embed)
        hits = ne.search("배터리", n=5, country="JP", client=self.client, embed=embed)
        self.assertEqual([h[0] for h in hits], ["2:0"])
        hits = ne.search("배터리", n=5, keyword="공급망", date_from="2026-10-01", date_to="2026-10-01",
                         client=self.client, embed=embed)
        self.assertEqual(sorted(h[0] for h in hits), ["1:0", "1:1"])
        self.assertEqual(ne.search_keywords("배터리", n=1, client=self.client, embed=embed)[0][::2], ("배터리", 2))

    def test_model_mismatch_requires_rebuild(self):
        self.client.create_collection(ne.ARTICLE_COLLECTION, embedding_function=None,
                                      metadata={"embedding_model": "text-embedding-3-small"})
        self.assertEqual(self.run_sync(FakeEmbed()), 1)
        self.assertEqual(self.run_sync(FakeEmbed(), rebuild=True), 0)
        self.assertEqual(self.articles().count(), 3)

    def test_failed_batch_is_retried_next_run(self):
        calls = {"n": 0}

        def flaky(texts):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("일시 오류")
            return FakeEmbed()(texts)

        self.assertEqual(self.run_sync(flaky), 1)
        self.assertEqual(self.articles().count(), 0)
        self.assertEqual(self.run_sync(flaky), 0)
        self.assertEqual(self.articles().count(), 3)


if __name__ == "__main__":
    unittest.main()
