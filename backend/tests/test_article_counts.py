"""기사 집계 테이블(FR-906)과 급증 판정(FR-203·204) 계산을 작은 가상 데이터로 확인합니다.

실행: python -m unittest backend/tests/test_article_counts.py
"""
import importlib.util
import unittest
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "app/services/news_keywords/article_counts.py"
spec = importlib.util.spec_from_file_location("article_counts", SRC)
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)


def art(i, date, keys, country="US", hs6=""):
    return {"bbstxSn": str(i), "othbcDt": date, "country_all": country, "kw_topic": "|".join(keys),
            "hs6": hs6, "hs4": hs6[:4], "infoCl": "", "natn": ""}


class CountsTest(unittest.TestCase):
    def test_counts_one_article_once(self):
        """집계: 날짜 × 국가 × 키워드, 한 기사는 같은 키워드를 1건으로"""
        df = pd.DataFrame([art(1, "2026-10-01", ["전기차", "전기차", "배터리"]), art(2, "2026-10-01", ["전기차"]),
                           art(3, "2026-10-01", ["전기차"], country="CN")])
        c = ac.build_counts(df, "kw_topic").set_index(["date", "country", "key"]).articles
        self.assertEqual(c[("2026-10-01", "US", "전기차")], 2)
        self.assertEqual(c[("2026-10-01", "US", "배터리")], 1)
        self.assertEqual(c[("2026-10-01", "CN", "전기차")], 1)

    def test_unknown_country(self):
        """집계: 국가를 알 수 없는 기사는 '-'로"""
        df = pd.DataFrame([art(1, "2026-10-01", ["AI"], country="")])
        self.assertEqual(ac.build_counts(df, "kw_topic").country.tolist(), ["-"])


class SpikeTest(unittest.TestCase):
    def test_major_issue_only_zero_average_uses_one(self):
        for previous, recent, expected_ratio in ((0, 3, 3), (4, 2, 4), (8, 3, 3), (16, 6, 3)):
            with self.subTest(previous=previous):
                rows = [art(i, "2026-10-01", ["AI"]) for i in range(previous)]
                rows += [art(100 + i, "2026-10-26", ["AI"]) for i in range(recent)]
                result = ac.major_issues(pd.DataFrame(rows), "kw_topic", "2026-10-27")
                self.assertEqual(result.key.tolist(), ["AI"])
                self.assertEqual(result.prev_weekly_avg.iloc[0], previous / 8)
                self.assertEqual(result.ratio.iloc[0], expected_ratio)

    def test_interest_spike_only_zero_average_uses_one(self):
        for previous, yesterday, expected_ratio, spike in (
                (0, 2, 2, False), (3, 2, 4.67, True), (7, 2, 2, False), (14, 5, 2.5, True)):
            with self.subTest(previous=previous):
                counts = pd.DataFrame([
                    {"country": "US", "key": "AI", "date": "2026-10-20", "articles": previous},
                    {"country": "US", "key": "AI", "date": "2026-10-26", "articles": yesterday}])
                result = ac.interest_spike(counts, "2026-10-27", "US", "AI")
                self.assertEqual(result["ratio"], expected_ratio)
                self.assertEqual(result["spike"], spike)

    def test_major_issue_fr204(self):
        """FR-204: 최근 7일 3건 ÷ 직전 8주 주평균(0이면 1) = 3배 → 주요 이슈, 2건이면 아님"""
        rows = [art(i, "2026-10-1" + str(i), ["K-뷰티"]) for i in range(3)]  # 10/10~10/12, 판정일 10/14
        rows += [art(10 + i, "2026-10-1" + str(i), ["로봇"]) for i in range(2)]
        r = ac.major_issues(pd.DataFrame(rows), "kw_topic", "2026-10-14")
        self.assertEqual(r.key.tolist(), ["K-뷰티"])
        self.assertEqual(r.ratio.iloc[0], 3.0)

    def test_major_issue_uses_previous_8_weeks(self):
        """FR-204: 직전 8주 동안 주 2건씩 있던 말은 최근 7일 3건이어도 1.5배 → 아님"""
        old = [art(100 + i, (pd.Timestamp("2026-10-06") - pd.Timedelta(days=3 * i + 1)).strftime("%Y-%m-%d"), ["AI"]) for i in range(16)]
        new = [art(i, "2026-10-1" + str(i), ["AI"]) for i in range(3)]
        self.assertEqual(len(ac.major_issues(pd.DataFrame(old + new), "kw_topic", "2026-10-14")), 0)

    def test_interest_spike_fr203(self):
        """FR-203: 어제 3건 ÷ 이틀 전까지 7일 일평균(0이면 1) = 3배 → 급증, 어제와 7일 구간은 겹치지 않음"""
        df = pd.DataFrame([art(i, "2026-10-13", ["전기차"], country="CN") for i in range(3)])
        counts = ac.build_counts(df, "kw_topic")
        r = ac.interest_spike(counts, "2026-10-14", "CN", "전기차")
        self.assertEqual((r["yesterday"], r["ratio"], r["spike"]), (3, 3.0, True))
        self.assertFalse(ac.interest_spike(counts, "2026-10-14", "US", "전기차")["spike"])


class InterestTest(unittest.TestCase):
    def test_product_counts_units(self):
        """품목 집계: HS 6단위(기본)와 4단위를 unit 칸으로 함께 저장"""
        df = pd.DataFrame([art(1, "2026-10-13", [], hs6="850760"), art(2, "2026-10-13", [], hs6="850780")])
        pc = ac.product_counts(df).set_index(["unit", "key"]).articles
        self.assertEqual((pc[("hs6", "850760")], pc[("hs6", "850780")], pc[("hs4", "8507")]), (1, 1, 2))

    def test_interest_alerts_fr203(self):
        """FR-203: 관심 국가 × 관심 품목(6단위·4단위)·키워드 중 급증한 것만 알림"""
        rows = [art(i, "2026-10-13", ["배터리"], country="US", hs6="850760") for i in range(3)]
        rows.append(art(9, "2026-10-13", ["화장품"], country="US", hs6="330499"))
        df = pd.DataFrame(rows)
        r = ac.interest_alerts(ac.build_counts(df, "kw_topic"), ac.product_counts(df), "2026-10-14", ["US"],
                               products=["850760", "8507", "330499"], keywords=["배터리", "화장품"])
        self.assertEqual(sorted((x["type"], x["key"]) for x in r),
                         [("키워드", "배터리"), ("품목(HS4)", "8507"), ("품목(HS6)", "850760")])
        self.assertEqual(ac.interest_alerts(ac.build_counts(df, "kw_topic"), ac.product_counts(df), "2026-10-14",
                                            ["CN"], products=["850760"]), [])


if __name__ == "__main__":
    unittest.main()
