"""해외시장뉴스 적재(news_load)를 SQLite로 확인합니다 (MySQL 없이 실행).

실행: python -m unittest backend/tests/test_news_load.py
"""
import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import select

from backend.app.db.session import make_engine, session_scope
from backend.app.models.base import Base
from backend.app.models.batch import BatchRun, ServiceOption
from backend.app.models.hs_code import HsCodeMst
from backend.app.models.news import COUNTRY, Article, ArticleTag, NewsKeywordDaily, NewsProductDaily
from backend.app.scheduler.jobs import news_load as job
from backend.app.services import news_load as nl


def raw(sn, day, title="제목", body="<p>본문</p>", info="트렌드"):
    return {"bbstxSn": sn, "newsTitl": title, "newsBdt": body, "othbcDt": f"{day} 09:00:00",
            "kotraNewsUrl": f"https://dream.kotra.or.kr/x?dataIdx={sn}", "infoCl": info, "cntntSumar": "요약"}


def clean(sn, day, kw="", countries="", hs6=""):
    return {"bbstxSn": sn, "othbcDt": day, "kw_topic": kw, "country_all": countries, "hs6": hs6}


class MaskTest(unittest.TestCase):
    def test_html_to_text_drops_tags_script_and_images(self):
        html = ('<p style="x">첫&nbsp;문단</p><script>alert(1)</script><img src="data:image/png;base64,AAAA">'
                '<div>둘째 &amp; 문단</div>')
        self.assertEqual(nl.html_to_text(html), "첫 문단\n둘째 & 문단")

    def test_mask_email_and_phones(self):
        text = "문의 kim.a@kotra.or.kr / 02-3460-7114, +82-2-3460-7114, (647) 299-9203"
        self.assertEqual(nl.mask(text), "문의 [이메일] / [전화번호], [전화번호], [전화번호]")

    def test_mask_partial_and_domainless_emails_but_not_program_names(self):
        self.assertEqual(nl.mask("담당 wooyoung@daehangroup / info@nexus4013 / sales@tco-****.com"),
                         "담당 [이메일] / [이메일] / [이메일]")
        for text in ("Tech@SG Programme", "Autism@Work", "BE@RBRICK", "SPIN@HSITP", "Garden Pod @ Gardens"):
            self.assertEqual(nl.mask(text), text)

    def test_hs_ranges_and_dates_are_not_phones(self):
        for text in ("HS 6210.4010-6210", "1902-1904-1905호", "2022.10.13", "2022-2023년", "3.4%"):
            self.assertEqual(nl.mask(text), text)

    def test_empty_body_falls_back_to_summary_then_title(self):
        self.assertEqual(nl.article_row(raw("1", "2026-01-01", body="<p> </p>"))["body_masked"], "요약")
        r = raw("1", "2026-01-01", body="")
        r["cntntSumar"] = ""
        self.assertEqual(nl.article_row(r)["body_masked"], "제목")


class LatestRawTest(unittest.TestCase):
    def test_latest_end_date_then_longest_period(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("news_raw_20220101_20261013.csv", "news_raw_20260101_20261013.csv", "news_raw_2026.csv"):
                (Path(d) / name).write_text("x")
            self.assertEqual(job.latest_raw(Path(d)).name, "news_raw_20220101_20261013.csv")  # 끝 날짜가 같으면 긴 기간
            (Path(d) / "news_raw_20260101_20261020.csv").write_text("x")
            self.assertEqual(job.latest_raw(Path(d)).name, "news_raw_20260101_20261020.csv")


class CountTest(unittest.TestCase):
    def test_distinct_articles_per_country_and_total(self):
        dates = {1: "d", 2: "d"}
        counts = nl.daily_counts(dates, [(1, "AI"), (1, "AI"), (2, "AI")], {1: {"US", "CN"}, 2: {"US"}})
        self.assertEqual(counts, {("d", "AI", None): 2, ("d", "AI", "US"): 2, ("d", "AI", "CN"): 1})


class NewsLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f"sqlite:///{(Path(self.tmp.name) / 'news.db').as_posix()}"
        self.engine = make_engine(self.url)
        Base.metadata.create_all(self.engine)
        with session_scope(self.engine) as s:
            s.execute(COUNTRY.insert(), [{"country_code": c} for c in ("US", "CN", "JP")])
            s.add_all([HsCodeMst(hs_code=c, hs_ver="HS2022", name_ko=c, source_edition="t",
                                 original_name_ko=c if len(c) == 6 else None) for c in ("8541", "854143")])

    def tearDown(self):
        for h in job.log.handlers:  # Windows는 열린 로그 파일을 지우지 못함
            h.close()
        job.log.handlers.clear()
        self.engine.dispose()
        self.tmp.cleanup()

    def run_job(self, raws, cleans):
        raw_path, clean_path = Path(self.tmp.name) / "raw.csv", Path(self.tmp.name) / "clean.csv"
        for path, rows in ((raw_path, raws), (clean_path, cleans)):
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
        return job.run(raw_path, clean_path, self.url, log_dir=Path(self.tmp.name) / "logs")

    def rows(self, model, *cols, **where):
        """조회 결과를 정렬해 돌려줍니다. 정렬 키에서 NULL은 빈 문자열로 봅니다 (태그 행은 종류마다 NULL 칸이 다름)."""
        with session_scope(self.engine) as s:
            q = select(*(getattr(model, c) for c in cols)).filter_by(**where)
            return sorted((tuple(r) for r in s.execute(q)), key=lambda r: tuple("" if v is None else str(v) for v in r))

    def test_load_articles_tags_counts_and_batch(self):
        raws = [raw("10", "2026-10-01", body="<p>연락 a@b.com</p>"), raw("11", "2026-10-01"), raw("12", "2026-10-02")]
        cleans = [clean("10", "2026-10-01", "AI|반도체", "US|CN", "854143"),
                  clean("11", "2026-10-01", "AI", "US|ZZ"),
                  clean("12", "2026-10-02", "AI")]
        self.assertEqual(self.run_job(raws, cleans), 0)
        self.assertEqual(self.rows(Article, "external_id", "source_code", "published_date", "category"),
                         [(i, "KOTRA_MARKET", date(2026, 10, d), "트렌드") for i, d in (("10", 1), ("11", 1), ("12", 2))])
        self.assertEqual(self.rows(Article, "body_masked", external_id="10"), [("연락 [이메일]",)])
        tags = self.rows(ArticleTag, "keyword_text", "country_code", "hs_code", "hs_ver")
        # 없는 국가(ZZ)는 태그에서 빠짐
        self.assertEqual([t[1] for t in tags if t[1]], ["CN", "US", "US"])
        self.assertEqual([t[2:] for t in tags if t[2]], [("854143", "HS2022")])
        self.assertEqual([t[0] for t in tags if t[0]], ["AI", "AI", "AI", "반도체"])
        kw = self.rows(NewsKeywordDaily, "metric_date", "keyword_text", "country_key", "day_count")
        d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
        self.assertEqual(kw, [(d1, "AI", "*", 2), (d1, "AI", "CN", 1), (d1, "AI", "US", 2),
                              (d1, "반도체", "*", 1), (d1, "반도체", "CN", 1), (d1, "반도체", "US", 1), (d2, "AI", "*", 1)])
        hs = self.rows(NewsProductDaily, "hs_code", "country_key", "day_count")
        self.assertEqual(hs, [("8541", "*", 1), ("8541", "CN", 1), ("8541", "US", 1),
                              ("854143", "*", 1), ("854143", "CN", 1), ("854143", "US", 1)])
        self.assertEqual(self.rows(ServiceOption, "option_code", "option_type"), [("KOTRA_MARKET", "SOURCE")])
        self.assertEqual(self.rows(BatchRun, "job_name", "target_table", "status", "received_count"),
                         [("NEWS_LOAD", "article", "SUCCESS", 3)])

    def test_rerun_updates_in_place_and_replaces_old_tags(self):
        self.run_job([raw("10", "2026-10-01")], [clean("10", "2026-10-01", "AI|구단어", "US")])
        first_id = self.rows(Article, "article_id")
        self.run_job([raw("10", "2026-10-01", title="새 제목")], [clean("10", "2026-10-01", "AI", "US")])
        self.assertEqual(self.rows(Article, "article_id"), first_id)
        self.assertEqual(self.rows(Article, "title"), [("새 제목",)])
        self.assertEqual(self.rows(ArticleTag, "keyword_text", "country_code"), [(None, "US"), ("AI", None)])
        self.assertEqual(self.rows(NewsKeywordDaily, "keyword_text", "country_key"), [("AI", "*"), ("AI", "US")])

    def test_changed_published_date_removes_old_day_counts(self):
        self.run_job([raw("10", "2026-10-01")], [clean("10", "2026-10-01", "AI")])
        self.run_job([raw("10", "2026-10-03")], [clean("10", "2026-10-03", "AI")])
        self.assertEqual(self.rows(NewsKeywordDaily, "metric_date", "keyword_text", "country_key"),
                         [(date(2026, 10, 3), "AI", "*")])

    def test_duplicate_article_id_in_raw_keeps_last_row(self):
        self.assertEqual(self.run_job([raw("10", "2026-10-01", title="앞"), raw("10", "2026-10-01", title="뒤")],
                                      [clean("10", "2026-10-01", "AI")]), 0)
        self.assertEqual(self.rows(Article, "title"), [("뒤",)])

    def test_article_without_keyword_result_is_loaded_without_tags(self):
        self.assertEqual(self.run_job([raw("10", "2026-10-01"), raw("11", "2026-10-01")],
                                      [clean("10", "2026-10-01", "AI")]), 0)
        self.assertEqual(len(self.rows(Article, "article_id")), 2)
        self.assertEqual(self.rows(ArticleTag, "keyword_text"), [("AI",)])  # 키워드 결과 없는 11번은 태그 0건

    def test_missing_table_fails_without_writing(self):
        with session_scope(self.engine) as s:
            s.execute(NewsProductDaily.__table__.delete())
        NewsProductDaily.__table__.drop(self.engine)
        self.assertEqual(self.run_job([raw("10", "2026-10-01")], [clean("10", "2026-10-01", "AI")]), 1)
        self.assertEqual(self.rows(Article, "article_id"), [])
        self.assertEqual(self.rows(BatchRun, "status"), [("FAILED",)])

    def test_db_error_rolls_back_everything(self):
        self.run_job([raw("10", "2026-10-01")], [clean("10", "2026-10-01", "AI")])
        too_long = "x" * 601
        with self.assertRaises(Exception):  # SQLite는 길이를 검사하지 않으므로 직접 실패를 만듦
            with session_scope(self.engine) as s:
                job.upsert_articles(s, [nl.article_row(raw("11", "2026-10-01", title=too_long))])
                raise RuntimeError("중간 실패")
        self.assertEqual([r[0] for r in self.rows(Article, "external_id")], ["10"])


if __name__ == "__main__":
    unittest.main()
