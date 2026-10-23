"""단신속보 적재를 가짜 LLM·가짜 임베딩·메모리 Chroma·SQLite로 확인합니다 (OpenAI·MySQL 없이 실행).

실행: python -m unittest backend/tests/test_short_news_load.py
"""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chromadb
from sqlalchemy import select

from backend.app.db.crud import CRUD
from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.news import Article, ArticleTag
from backend.app.models.hs_code import HsCodeMst
from backend.app.scheduler.jobs import hs_code_embed as he
from backend.app.scheduler.jobs import short_news_load as sn
from backend.app.services.news_keywords import keyword_clean as kc
from backend.tests.test_hs_code_embed import FakeEmbed

HS = [("27", "제27류 광물성 연료", None), ("2711", "석유가스와 그 밖의 가스상 탄화수소", None),
      ("271111", "액화 석유가스 천연가스", "천연가스"), ("8112", "베릴륨ㆍ갈륨ㆍ게르마늄과 이것으로 만든 제품", None),
      ("811292", "기타괴웨이스트가루제품", "괴, 웨이스트와 스크랩, 가루")]
CSV_ROWS = [
    {"nat": "러시아연방", "nttSj": "[러시아] 한국, 러시아산 LNG 수입 증가", "kotraNewsUrl": "https://k/1", "othbcDt": "2025-02-05",
     "nttSn": "1", "kbc": "모스크바무역관", "regn": "유럽",
     "body": "한국, 러시아산 LNG 수입 증가\nㅇ 1월 LNG 수입 20%\xa0증가\n문의 : 홍길동 과장(hong@kotra.or.kr)\n[원문링크:\xa0https://tass.ru/1]"},
    {"nat": "중국", "nttSj": "[중국] 中 외환보유액 3조 달러", "kotraNewsUrl": "https://k/2", "othbcDt": "2025-05-01",
     "nttSn": "2", "kbc": "베이징무역관", "regn": "아시아", "body": "외환보유액 증가. 대표번호 +86 10 1234 5678"},
    {"nat": "중국", "nttSj": "[중국] 날짜 오류", "kotraNewsUrl": "https://k/3", "othbcDt": "2025-13-01",
     "nttSn": "3", "kbc": "", "regn": "아시아", "body": "본문"},
]


class FakeChat:
    """1차는 LNG 기사에만 품목을 주고, 2차는 후보 코드 하나와 후보에 없는 코드 하나를 고릅니다."""

    def __init__(self):
        self.calls = 0

    def __call__(self, system, user):
        self.calls += 1
        if system == sn.PICK_PROMPT:
            return {"hs_codes": ["271111", "999999"]}
        lng = "LNG" in user
        return {"summary": "요약 문장.", "keywords": ["LNG 수입", "LNG 수입", " "] if lng else ["외환보유액"],
                "products": ["액화천연가스"] if lng else []}


class ShortNewsLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = f"sqlite:///{(self.dir / 'news.db').as_posix()}"
        self.engine = make_engine(self.db)
        Base.metadata.create_all(self.engine)
        for name in ("load_synonyms", "load_hypernyms", "load_categories"):  # 저장소 사전 파일과 무관하게 빈 사전으로 시험
            patcher = mock.patch.object(kc, name, return_value={})
            patcher.start()
            self.addCleanup(patcher.stop)
        with session_scope(self.engine) as s:
            CRUD(HsCodeMst).upsert_many(s, [{"hs_code": c, "hs_ver": "HS2022", "name_ko": n, "original_name_ko": o,
                                             "source_edition": "t"} for c, n, o in HS])
        self.client = chromadb.EphemeralClient()
        for name in (he.COLLECTION, sn.COLLECTION):  # 메모리 클라이언트는 프로세스에서 공유됨
            if name in [c.name for c in self.client.list_collections()]:
                self.client.delete_collection(name)
        self.embed = FakeEmbed()
        he.run(self.db, client=self.client, embed=self.embed, log_dir=self.dir)
        self.csv = self.dir / "short_news.csv"
        with open(self.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(CSV_ROWS[0]))
            w.writeheader()
            w.writerows(CSV_ROWS)

    def tearDown(self):
        self.engine.dispose()
        for lg in (he.log, sn.log):
            for h in lg.handlers:
                h.close()
            lg.handlers.clear()
        self.tmp.cleanup()

    def run_load(self, chat):
        return sn.run(self.csv, self.db, client=self.client, ask=chat, embed=self.embed, log_dir=self.dir,
                      cache_path=self.dir / "cache.jsonl", workers=2)

    def test_clean_and_mask(self):
        body = sn.mask(sn.clean(CSV_ROWS[0]["body"], CSV_ROWS[0]["nttSj"]))
        self.assertEqual(body, "ㅇ 1월 LNG 수입 20% 증가\n문의 : [연락처]")  # 제목 반복·링크 줄 삭제, 연락처 가림
        self.assertEqual(sn.mask("메일 a.b@x.co.kr 전화 02-123-4567"), "메일 [이메일] 전화 [전화번호]")
        self.assertEqual(sn.mask("2026 2027 2028 / 4.36% 3.85%"), "2026 2027 2028 / 4.36% 3.85%")  # 표 숫자는 그대로
        self.assertEqual(sn.clean("본문\n자료: https//", "[x] y"), "본문")

    def test_reads_collector_csv(self):
        """shortnews_collect.py 결과(nttCn HTML, 제목 문자 참조)를 그대로 읽고, --csv 없으면 최신 결과를 고릅니다."""
        out = self.dir / "outputs"
        for run_dir, title in (("20260101_000000_0", "옛 결과"), ("20261015_161111_0", "[러시아] 러&middot;유럽 교역")):
            (out / run_dir).mkdir(parents=True)
            with open(out / run_dir / "shortnews_raw.csv", "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["kbc", "nttSn", "nttSj", "nttCn", "regn", "nat", "othbcDt", "kotraNewsUrl"])
                w.writeheader()
                w.writerow({"kbc": "모스크바무역관", "nttSn": "9", "nttSj": title, "regn": "유럽", "nat": "러시아연방",
                            "othbcDt": "2026-01-30", "kotraNewsUrl": "https://k/9",
                            "nttCn": '<p><span>□ </span><span>러</span>&middot;유럽 교역</p><p>ㅇ 루블화&nbsp;비중 '
                                     '<공평경쟁 조례> 75%<br>- 수입 69%</p>'})
        (out / "20261231_000000_0").mkdir()  # 수집이 실패해 빈 폴더는 건너뜀
        latest = sn.latest_collect_csv(out)
        self.assertEqual(latest.parent.name, "20261015_161111_0")
        [a] = sn.read_articles(latest)
        self.assertEqual(a["title"], "[러시아] 러·유럽 교역")
        self.assertEqual(a["body_masked"], "ㅇ 루블화 비중 <공평경쟁 조례> 75%\n- 수입 69%")  # 첫 줄 제목 반복 삭제
        with self.assertRaises(FileNotFoundError):
            sn.latest_collect_csv(self.dir / "없음")

    def test_load_and_rerun(self):
        chat = FakeChat()
        self.assertEqual(self.run_load(chat), 0)
        self.assertEqual(chat.calls, 3)  # LNG 기사 2회(추출·선택), 외환 기사 1회(품목 없음)
        with session_scope(self.engine) as s:
            arts = {a.external_id: (a.category, a.body_masked) for a in s.scalars(select(Article))}
            tags = {(t.article_id, t.keyword_text, t.hs_code) for t in s.scalars(select(ArticleTag))}
        self.assertEqual(set(arts), {"1", "2"})  # 날짜 오류 행 제외
        self.assertEqual(arts["2"], ("short_news", "외환보유액 증가. 대표번호 [전화번호]"))
        self.assertIn((1, None, "271111"), tags)  # 후보 밖 999999는 버림
        self.assertEqual(len(tags), 3)  # LNG: 키워드 1(중복·공백 제거) + HS 1, 외환: 키워드 1
        self.assertIn((1, "LNG수입", None), tags)  # 해외시장뉴스와 같은 정제 (한글이 있으면 공백 제거)
        col = sn.get_news_collection(self.client)
        self.assertEqual(col.count(), 2)
        hit = sn.search("LNG", n=1, where={"nat": "러시아연방"}, client=self.client, embed=self.embed)[0]
        self.assertEqual((hit[2]["external_id"], hit[2]["hs_codes"]), ("1", "271111"))
        self.assertIn("[연락처]", col.get(ids=[hit[0]])["documents"][0])  # 문서는 마스킹한 본문

        again = FakeChat()  # 재실행: 캐시·DB·컬렉션 모두 이미 있어 아무것도 다시 하지 않음
        self.assertEqual(self.run_load(again), 0)
        self.assertEqual(again.calls, 0)
        with session_scope(self.engine) as s:
            self.assertEqual(CRUD(ArticleTag).count(s), 3)
        self.assertEqual(col.count(), 2)

    def test_retag_after_dictionary_change(self):
        """동의어 사전이 바뀐 뒤 --retag로 다시 실행하면 이미 넣은 기사 태그도 대표어로"""
        self.assertEqual(self.run_load(FakeChat()), 0)
        kc.load_synonyms.return_value = {"LNG수입": "LNG"}
        self.assertEqual(sn.run(self.csv, self.db, client=self.client, ask=FakeChat(), embed=self.embed, log_dir=self.dir,
                                cache_path=self.dir / "cache.jsonl", workers=2, retag=True), 0)
        with session_scope(self.engine) as s:
            words = sorted(t.keyword_text for t in s.scalars(select(ArticleTag)) if t.keyword_text)
        self.assertEqual(words, ["LNG", "외환보유액"])

    def test_failed_analysis_is_retried_next_run(self):
        def broken(system, user):
            raise RuntimeError("API 오류")
        sn.RETRIES, retries = 1, sn.RETRIES
        try:
            self.assertEqual(self.run_load(broken), 1)
        finally:
            sn.RETRIES = retries
        with session_scope(self.engine) as s:
            self.assertEqual(CRUD(Article).count(s), 0)
        self.assertEqual(self.run_load(FakeChat()), 0)


if __name__ == "__main__":
    unittest.main()
