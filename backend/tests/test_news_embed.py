"""기사 임베딩 동기화를 가짜 임베딩·메모리 Chroma·SQLite로 확인합니다 (OpenAI·MySQL 없이 실행).

실행: python -m unittest backend/tests/test_news_embed.py
"""
import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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


class UsageTest(unittest.TestCase):
    def test_openai_tokens_are_summed_with_cost(self):
        res = lambda n, k: SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])] * k,
                                           usage=SimpleNamespace(total_tokens=n))
        client = mock.Mock()
        client.embeddings.create.side_effect = [res(600_000, 2), res(400_000, 1)]
        with mock.patch.object(ne, "OpenAI", return_value=client), \
                mock.patch.object(ne, "dotenv_values", return_value={"OPENAI_API_KEY": "test"}):
            embed = ne.openai_embed()
            embed(["a", "b"])
            embed(["c"])
        self.assertEqual(embed.usage["tokens"], 1_000_000)
        self.assertEqual(ne.usage_text(embed), " | 토큰 1,000,000 (약 $0.13)")

    def test_get_all_reads_every_page(self):
        client = chromadb.EphemeralClient()
        if "paging" in [c.name for c in client.list_collections()]:
            client.delete_collection("paging")
        col = client.create_collection("paging", embedding_function=None)
        col.add(ids=[f"{i}" for i in range(7)], documents=[f"d{i}" for i in range(7)],
                metadatas=[{"n": i} for i in range(7)], embeddings=[[float(i), 1.0] for i in range(7)])
        got = ne.get_all(col, ["metadatas", "documents"], page=3)   # 3 + 3 + 1
        self.assertEqual(sorted(got["ids"], key=int), [f"{i}" for i in range(7)])
        self.assertEqual(sorted(m["n"] for m in got["metadatas"]), list(range(7)))
        self.assertEqual(len(got["documents"]), 7)
        self.assertEqual(len(ne.get_all(col, [], page=7)["ids"]), 7)   # 딱 맞아떨어지는 경우 (7 + 0)

    def test_fake_embed_has_no_usage_text(self):
        self.assertEqual(ne.usage_text(FakeEmbed()), "")
        self.assertEqual(ne.usage_text(None), "")


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
        orig = ne.read_rows
        ne.read_rows = lambda engine: (_ for _ in ()).throw(AssertionError("본문을 읽으면 안 됨"))
        try:
            self.assertEqual(self.run_sync(embed, keywords_only=True), 0)
        finally:
            ne.read_rows = orig
        self.assertEqual(sorted(embed.texts), ["공급망", "배터리", "화장품"])   # 키워드만 임베딩
        self.assertNotIn(ne.ARTICLE_COLLECTION, [c.name for c in self.client.list_collections()])

    def test_dry_run_does_not_embed(self):
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed, dry_run=True), 0)
        self.assertEqual(embed.texts, [])
        self.assertEqual(self.articles().count(), 0)

    def test_rebuild_dry_run_deletes_nothing(self):
        """--rebuild --dry-run은 미리 보기라 컬렉션을 지우지 않음 (키워드만이어도)"""
        self.run_sync(FakeEmbed())
        for kw in ({}, {"keywords_only": True}):
            embed = FakeEmbed()
            self.assertEqual(self.run_sync(embed, rebuild=True, dry_run=True, **kw), 0)
            self.assertEqual(embed.texts, [])
            self.assertEqual(self.articles().count(), 3)
            self.assertEqual(self.client.get_collection(ne.KEYWORD_COLLECTION).count(), 3)

    def test_keyword_gone_from_db_is_deleted(self):
        """DB에서 사라진 키워드(사전이 바뀌어 동의어·하위어가 됨)는 추천에 남지 않게 지움, 임베딩은 다시 안 함"""
        self.run_sync(FakeEmbed())
        with session_scope(self.engine) as s:
            s.execute(ArticleTag.__table__.delete().where(ArticleTag.keyword_text == "공급망"))
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed, keywords_only=True), 0)
        self.assertEqual(embed.texts, [])
        self.assertEqual(sorted(self.client.get_collection(ne.KEYWORD_COLLECTION).get()["ids"]), ["배터리", "화장품"])

    def test_keyword_change_updates_metadata_without_embedding(self):
        """본문·머리줄은 같고 키워드만 바뀌면 다시 임베딩하지 않고 메타데이터만 갱신 → 새 키워드로 검색됨"""
        self.run_sync(FakeEmbed())
        with session_scope(self.engine) as s:
            s.add(ArticleTag(article_id=2, keyword_text="공급망"))
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(embed.texts, [])   # 기사 조각·키워드 모두 다시 임베딩 안 함
        meta = self.articles().get(ids=["2:0"], include=["metadatas"])["metadatas"][0]
        self.assertEqual(meta["keywords"], ["화장품", "배터리", "공급망"])
        hits = ne.search("화장품", n=5, keyword="공급망", client=self.client, embed=embed)
        self.assertIn("2:0", [h[0] for h in hits])

    def test_last_tag_removed_is_cleared(self):
        """기사의 키워드를 모두 지우면 메타데이터에서도 키워드가 사라져 예전 키워드로 검색되지 않음"""
        self.run_sync(FakeEmbed())
        with session_scope(self.engine) as s:
            s.execute(ArticleTag.__table__.delete().where(ArticleTag.article_id == 2, ArticleTag.keyword_text.isnot(None)))
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(embed.texts, [])   # 머리줄에는 키워드가 없어 다시 임베딩 안 함
        meta = self.articles().get(ids=["2:0"], include=["metadatas"])["metadatas"][0]
        self.assertNotIn("keywords", meta)
        self.assertEqual(meta["countries"], ["JP"])
        hits = ne.search("화장품", n=5, keyword="화장품", client=self.client, embed=embed)
        self.assertNotIn("2:0", [h[0] for h in hits])

    def test_header_change_re_embeds_article(self):
        """국가·발행일이 바뀌면 머리줄(문서)이 바뀌므로 그 기사만 다시 임베딩 → 검색 결과 문서와 필터가 같은 정보"""
        self.run_sync(FakeEmbed())
        with session_scope(self.engine) as s:
            s.execute(ArticleTag.__table__.delete().where(ArticleTag.article_id == 2, ArticleTag.country_code.isnot(None)))
            s.add(ArticleTag(article_id=2, country_code="US"))
            s.execute(update(Article).where(Article.article_id == 2).values(published_date=date(2026, 10, 6)))
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(len(embed.texts), 1)   # 2번 기사 조각 1개만
        got = self.articles().get(ids=["2:0"], include=["documents", "metadatas"])
        self.assertTrue(got["documents"][0].startswith("[2026-10-06 | US] 일본 화장품"))
        self.assertEqual((got["metadatas"][0]["countries"], got["metadatas"][0]["published_date"]), (["US"], "2026-10-06"))
        hits = ne.search("화장품", n=5, country="US", date_from="2026-10-06", client=self.client, embed=embed)
        self.assertEqual([h[0] for h in hits], ["2:0"])
        self.assertTrue(hits[0][2].startswith("[2026-10-06 | US]"))

    def test_partly_embedded_article_is_redone(self):
        """중간에 멈춰 조각이 일부만 들어간 기사는 다음 실행에서 그 기사만 다시 넣음"""
        self.run_sync(FakeEmbed())
        self.articles().delete(ids=["1:1"])
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(len(embed.texts), 2)   # 1번 기사 조각 2개만
        self.assertEqual(sorted(self.articles().get()["ids"]), ["1:0", "1:1", "2:0"])

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
