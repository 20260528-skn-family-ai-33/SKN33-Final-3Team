"""HS 코드 임베딩 동기화를 가짜 임베딩·메모리 Chroma·SQLite로 확인합니다 (OpenAI·MySQL 없이 실행).

실행: python -m unittest backend/tests/test_hs_code_embed.py
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

import chromadb

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.hs_code import HsCodeMst
from backend.app.scheduler.jobs import hs_code_embed as he

ROWS = [("15", "HS2022", "제15류 동물성ㆍ식물성 지방과 기름", None),
        ("1509", "HS2022", "올리브유와 그 분획물", None),
        ("150920", "HS2022", "엑스트라 버진 올리브유", "엑스트라 버진"),
        ("1509201000", "HS2022", "기타", None),
        ("150910", "HS2017", "Olive oil virgin", "Vegetable oils; olive oil, virgin")]


class FakeEmbed:
    """문서마다 다른 8차원 벡터. 호출한 문서 수를 셉니다."""

    def __init__(self):
        self.texts = []

    def __call__(self, texts):
        self.texts += texts
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class HsCodeEmbedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = f"sqlite:///{(Path(self.tmp.name) / 'hs.db').as_posix()}"
        self.engine = make_engine(self.db)
        Base.metadata.create_all(self.engine)
        self.put(*ROWS)
        self.client = chromadb.EphemeralClient()
        if he.COLLECTION in [c.name for c in self.client.list_collections()]:  # 메모리 클라이언트는 프로세스에서 공유됨
            self.client.delete_collection(he.COLLECTION)

    def tearDown(self):
        self.engine.dispose()
        for h in he.log.handlers:
            h.close()
        he.log.handlers.clear()
        self.tmp.cleanup()

    def put(self, *rows):
        with session_scope(self.engine) as s:
            CRUD(HsCodeMst).upsert_many(s, [{"hs_code": c, "hs_ver": v, "name_ko": n, "original_name_ko": o,
                                             "source_edition": "t"} for c, v, n, o in rows])

    def run_sync(self, embed):
        return he.run(self.db, client=self.client, embed=embed, log_dir=Path(self.tmp.name))

    def test_document_has_parent_path(self):
        docs = he.build_docs(he.read_rows(self.engine))
        doc, meta = docs["HS2022:1509201000"]
        self.assertEqual(doc.splitlines(), ["[HS2022] 1509201000 (10단위)", "류 15: 제15류 동물성ㆍ식물성 지방과 기름",
                                            "호 1509: 올리브유와 그 분획물",
                                            "소호 150920: 엑스트라 버진 올리브유 (원문: 엑스트라 버진)",
                                            "품목 1509201000: 기타"])
        self.assertEqual((meta["hs2"], meta["hs4"], meta["hs6"], meta["depth"]), ("15", "1509", "150920", 10))
        self.assertNotIn("hs6", docs["HS2022:150920"][1])
        # 같은 판에 상위 코드가 없으면 그 줄은 뺌 (HS2017 소호만 있는 경우)
        self.assertEqual(docs["HS2017:150910"][0].splitlines()[1:],
                         ["소호 150910: Olive oil virgin (원문: Vegetable oils; olive oil, virgin)"])

    def test_only_unregistered_ids_are_added(self):
        embed = FakeEmbed()
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(len(embed.texts), 5)
        col = self.client.get_collection(he.COLLECTION)
        self.assertEqual(col.count(), 5)
        self.assertEqual(set(col.get(ids=["HS2022:150920"])["metadatas"][0]),
                         {"hs_code", "hs_ver", "depth", "name_ko", "hs2", "hs4"})  # 출처·해시는 저장하지 않음

        embed = FakeEmbed()  # 새 코드가 없으면 임베딩 호출 없음
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(embed.texts, [])

        # 새 코드 추가 + 기존 6단위 이름·출처 변경 → 새 코드만 임베딩, 기존 id는 그대로
        self.put(("1509209000", "HS2022", "기타 2", None), ("150920", "HS2022", "엑스트라 버진 올리브유(정제 안 함)", "엑스트라 버진"))
        embed = FakeEmbed()
        self.run_sync(embed)
        self.assertEqual([t.splitlines()[0] for t in embed.texts], ["[HS2022] 1509209000 (10단위)"])
        self.assertEqual(col.get(ids=["HS2022:150920"])["metadatas"][0]["name_ko"], "엑스트라 버진 올리브유")

    def test_failed_codes_are_retried(self):
        def broken(texts):
            raise RuntimeError("OpenAI 오류")

        self.assertEqual(self.run_sync(broken), 1)
        embed = FakeEmbed()  # 실패한 코드는 컬렉션에 없으므로 다음 실행에서 추가
        self.assertEqual(self.run_sync(embed), 0)
        self.assertEqual(len(embed.texts), 5)

    def test_search_filters(self):
        embed = FakeEmbed()
        self.run_sync(embed)
        doc = he.build_docs(he.read_rows(self.engine))["HS2022:150920"][0]
        hits = he.search(doc, n=2, hs_ver="HS2022", depth=6, client=self.client, embed=embed)
        self.assertEqual(hits[0][0], "HS2022:150920")  # 같은 문서는 거리 0으로 가장 가까움
        self.assertTrue(all(m["hs_ver"] == "HS2022" and m["depth"] == 6 for _, _, m in hits))

    def test_model_change_needs_rebuild(self):
        self.client.create_collection(he.COLLECTION, embedding_function=None, metadata={"embedding_model": "old-model"})
        self.assertEqual(self.run_sync(FakeEmbed()), 1)
        self.assertEqual(he.run(self.db, rebuild=True, client=self.client, embed=FakeEmbed(), log_dir=Path(self.tmp.name)), 0)
        self.assertEqual(self.client.get_collection(he.COLLECTION).metadata["embedding_model"], he.MODEL)


if __name__ == "__main__":
    unittest.main()
