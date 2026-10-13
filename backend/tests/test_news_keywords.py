"""해외시장뉴스 키워드 정제(①)·지역 분리(②)를 실제 데이터에서 나온 예시로 확인합니다.

실행: python -m unittest backend/tests/test_news_keywords.py
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1] / "app/services/news_keywords/keyword_clean.py"
spec = importlib.util.spec_from_file_location("keyword_clean", SRC)
kc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kc)
GEO = kc.load_geo()


class CleanTest(unittest.TestCase):
    """① 정제"""
    def test_html_hashtag_quotes(self):
        """① 정제: HTML 문자(&#39;)·해시태그(#)·따옴표 제거"""
        self.assertEqual(kc.clean_keywords("#라이브커머스,#틱톡"), ["라이브커머스", "틱톡"])
        self.assertEqual(kc.clean_keywords("&#39;Buy Canadian&#39; 운동"), ["BUYCANADIAN운동"])
        self.assertEqual(kc.clean_keywords("#알제리 #플라스틱 #현지생산"), ["알제리", "플라스틱", "현지생산"])

    def test_spacing_case_duplicates(self):
        """① 정제: 띄어쓰기·대소문자 통일, 같은 말 중복 제거 (ai → AI, 전기 차 → 전기차)"""
        # 표기만 다른 키워드는 하나로 합쳐집니다.
        self.assertEqual(kc.clean_keywords("AI,ai, 전기 차,전기차"), ["AI", "전기차"])
        self.assertEqual(kc.clean_keywords("electric vehicle"), ["ELECTRIC VEHICLE"])

    def test_missing_comma_split(self):
        """① 정제: 쉼표 없이 붙은 키워드 나누기 (두 칸 공백, 가운뎃점)"""
        self.assertEqual(kc.clean_keywords("자동차 전기차  소비촉진"), ["자동차전기차", "소비촉진"])
        self.assertEqual(kc.clean_keywords("인테리어&middot;조경"), ["인테리어", "조경"])

    def test_parenthesis(self):
        """① 정제: 괄호 나누기 (외국인직접투자(FDI) → 외국인직접투자, FDI)"""
        self.assertEqual(kc.clean_keywords("외국인직접투자(FDI)"), ["외국인직접투자", "FDI"])

    def test_empty(self):
        """① 정제: 빈 키워드·기호만 있는 키워드 처리"""
        self.assertEqual(kc.clean_keywords(""), [])
        self.assertEqual(kc.clean_keywords(None), [])
        self.assertEqual(kc.clean_keywords(" , ,#"), [])


class GeoTest(unittest.TestCase):
    """② 지역 분리"""
    def split(self, word):
        places, topic = kc.split_geo(word, GEO)
        return [p["code"] for p in places], topic

    def test_exact(self):
        """② 지역: 나라·도시·권역 이름만 있는 키워드 (베트남 → VN, 두바이 → AE, 중동 → 권역)"""
        self.assertEqual(self.split("베트남"), (["VN"], None))
        self.assertEqual(self.split("두바이"), (["AE"], None))  # 도시 → 소속 국가
        self.assertEqual(self.split("중동"), (["중동"], None))
        self.assertEqual(self.split("UAE"), (["AE"], None))

    def test_compound(self):
        """② 지역: 나라+주제 복합어 나누기 (중국경제 → CN + 경제)"""
        self.assertEqual(self.split("중국경제"), (["CN"], "경제"))
        self.assertEqual(self.split("인도벵갈루루"), (["IN", "IN"], None))
        self.assertEqual(self.split("러시아-우크라이나"), (["RU", "UA"], None))

    def test_longest_name_first(self):
        """② 지역: 긴 이름부터 확인 (인도네시아경제를 인도로 자르지 않음)"""
        self.assertEqual(self.split("인도네시아경제"), (["ID"], "경제"))
        self.assertEqual(self.split("동남아시아전기차"), (["아세안"], "전기차"))

    def test_no_wrong_split(self):
        """② 지역: 잘못 자르지 않기 (피지컬AI, EUDR, 중국산, 파리바게뜨)"""
        self.assertEqual(self.split("피지컬AI"), ([], "피지컬AI"))  # 피지(Fiji)는 단독일 때만
        self.assertEqual(self.split("EUDR"), ([], "EUDR"))  # 영문 약어는 단독일 때만
        self.assertEqual(self.split("중국산"), ([], "중국산"))  # 남는 말이 한 글자
        self.assertEqual(self.split("파리바게뜨"), ([], "파리바게뜨"))  # 짧은 도시명은 단독일 때만


class ArticleTest(unittest.TestCase):
    """기사 한 건 처리 (국가 필터·연도·결과 파일)"""
    def test_article(self):
        """기사: 키워드 한 줄 → 주제어·국가·도시 칸 채우기"""
        r = kc.process_article("인도네시아,전기차,ev,인니취업,자카르타", "인도네시아", GEO)
        self.assertEqual(r["kw_topic"], ["전기차", "EV", "취업"])
        self.assertEqual(r["natn_code"], "ID")
        self.assertEqual(r["geo_city"], ["자카르타"])
        self.assertEqual(r["country_all"], ["ID"])
        self.assertTrue(r["natn_repeated"])

    def test_city_not_in_country_filter(self):
        """기사: 도시는 국가 필터에 넣지 않음 (미국 기사의 두바이초콜릿 → 국가 US만)"""
        # 미국 기사의 '두바이초콜릿'은 디저트 → 도시 칸에만 남고 국가 필터는 natn(미국)만
        r = kc.process_article("두바이초콜릿,피스타치오", "미국", GEO)
        self.assertEqual(r["geo_city"], ["두바이"])
        self.assertEqual(r["country_all"], ["US"])

    def test_keyword_country_when_natn_not_country(self):
        """기사: 기사 국가가 KOTRA 본사·전세계일 때만 키워드 속 나라로 보충"""
        r = kc.process_article("베트남,전기차", "KOTRA 본사", GEO)
        self.assertEqual(r["natn_code"], "")
        self.assertEqual(r["country_all"], ["VN"])

    def test_year_moved_not_deleted(self):
        """기사: 연도는 지우지 않고 언급 연도 칸으로 (2026년 → 2026, 비전2030은 유지)"""
        r = kc.process_article("2026년,전망,비전2030,2025", "사우디아라비아", GEO)
        self.assertEqual(r["kw_year"], ["2026", "2025"])
        self.assertEqual(r["kw_topic"], ["전망", "비전2030"])

    def test_run_keeps_raw(self):
        """결과 파일: 기준일 필터, 원본 키워드 보존, 나미비아(NA)가 빈 값으로 안 바뀜"""
        df = pd.DataFrame({"bbstxSn": ["1", "2"], "othbcDt": ["2021-12-31", "2024-01-01"],
                           "natn": ["KOTRA 본사", "나미비아"], "kwrd": ["무역", "NA,광업"]})
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "news_raw.csv"
            df.to_csv(src, index=False, encoding="utf-8-sig")
            out, report = kc.run(src, Path(tmp) / "out", since="2022-01-01")
            self.assertEqual(len(out), 1)  # 기준일 이전 기사 제외
            self.assertEqual(out.loc[0, "kwrd"], "NA,광업")  # 원본 보존
            self.assertEqual(out.loc[0, "natn_code"], "NA")  # 나미비아 NA가 빈 값으로 바뀌지 않음
            self.assertEqual(report["articles"], 1)
            self.assertTrue((Path(tmp) / "out" / "news_keywords_report.json").exists())


class ExceptionTest(unittest.TestCase):
    """②+ 예외 목록"""
    KEEP = kc.load_exceptions()

    def topic(self, kwrd, natn=""):
        return kc.process_article(kwrd, natn, GEO, self.KEEP)

    def test_events_kept_whole(self):
        """예외: 사건 이름은 통째로 (우크라이나사태, 중동전쟁, 미국대선) + 국가 정보는 기록"""
        r = self.topic("우크라이나사태,중동전쟁,미국대선")
        self.assertEqual(r["kw_topic"], ["우크라이나사태", "중동전쟁", "미국대선"])
        self.assertEqual(r["geo_country"], ["UA", "US"])  # 국가 정보는 그대로 기록
        self.assertEqual(r["geo_region"], ["중동"])

    def test_institutions_kept_whole(self):
        """예외: 기관 이름은 통째로 (태국투자청, 워싱턴무역관, 유럽중앙은행)"""
        r = self.topic("태국투자청,워싱턴무역관,유럽중앙은행")
        self.assertEqual(r["kw_topic"], ["태국투자청", "워싱턴무역관", "유럽중앙은행"])

    def test_chain_kept_whole(self):
        """예외: 나라가 둘 붙어도 통째로 (이란이스라엘사태)"""
        # 이란 + 이스라엘 + 사태처럼 나라가 둘 붙어도 통째로 유지
        r = self.topic("이란이스라엘사태,러시아-우크라이나사태")
        self.assertEqual(r["kw_topic"], ["이란이스라엘사태", "러시아-우크라이나사태"])
        self.assertEqual(r["geo_country"], ["IR", "IL", "RU", "UA"])

    def test_normal_topics_still_split(self):
        """예외: 일반 주제는 그대로 나눔 (중국경제 → 경제)"""
        r = self.topic("중국경제,태국전기차", "중국")
        self.assertEqual(r["kw_topic"], ["경제", "전기차"])

    def test_whole_word_exception(self):
        """예외: 단어 전체가 목록에 있으면 통째로 (두바이초콜릿)"""
        r = self.topic("두바이초콜릿,두바이전시회", "미국")
        self.assertEqual(r["kw_topic"], ["두바이초콜릿", "전시회"])  # 디저트 이름은 통째로, 일반 복합어는 분리
        self.assertEqual(r["country_all"], ["US"])

    def test_ending_rule(self):
        """예외: 목록의 말로 끝나면 통째로 (대통령선거, 후티반군, 관세청, 오사카엑스포, 연방정부)"""
        r = self.topic("체코대통령선거,예멘후티반군,태국관세청,오사카엑스포,독일연방정부,캐나다전시회")
        self.assertEqual(r["kw_topic"], ["체코대통령선거", "예멘후티반군", "태국관세청", "오사카엑스포", "독일연방정부", "전시회"])

    def test_without_exceptions_splits(self):
        """예외: 예외 목록 없이 부르면 기존처럼 나눔"""
        r = kc.process_article("우크라이나사태", "", GEO)  # 예외 목록 없이 부르면 기존처럼 나눔
        self.assertEqual(r["kw_topic"], ["사태"])


class StopwordTest(unittest.TestCase):
    """④ 불용어"""
    STOP = kc.load_stopwords()

    def test_stopwords_removed_only_from_spike(self):
        """불용어: 넓은 말은 급증 계산용(kw_spike)에서만 빠지고 검색용(kw_topic)에는 남음"""
        r = kc.process_article("수출,경제,동향,전기차,공급망", "미국", GEO, frozenset(), self.STOP)
        self.assertEqual(r["kw_topic"], ["수출", "경제", "동향", "전기차", "공급망"])
        self.assertEqual(r["kw_spike"], ["전기차", "공급망"])

    def test_frequent_but_meaningful_kept(self):
        """불용어: 자주 나와도 이슈가 되는 말은 유지 (AI, 공급망, ESG, 규제, FTA)"""
        r = kc.process_article("AI,공급망,ESG,규제,FTA", "", GEO, frozenset(), self.STOP)
        self.assertEqual(r["kw_spike"], ["AI", "공급망", "ESG", "규제", "FTA"])

    def test_stopword_after_geo_split(self):
        """불용어: 나라를 떼고 남은 말에도 적용 (중국경제 → 경제 → 급증 계산 제외)"""
        r = kc.process_article("중국경제,중국전기차", "중국", GEO, frozenset(), self.STOP)
        self.assertEqual(r["kw_topic"], ["경제", "전기차"])
        self.assertEqual(r["kw_spike"], ["전기차"])

    def test_stopword_file(self):
        """불용어: 목록 파일에 유형·기사수·이유가 모두 있음"""
        import csv
        with open(kc.STOPWORDS_PATH, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertGreater(len(rows), 30)
        self.assertTrue(all(r["유형"] and r["이유"] and r["기사수"].isdigit() for r in rows))


class SynonymTest(unittest.TestCase):
    """④ 동의어"""
    SYN = kc.load_synonyms()
    STOP = kc.load_stopwords()

    def topic(self, kwrd, natn=""):
        return kc.process_article(kwrd, natn, GEO, kc.load_exceptions(), self.STOP, self.SYN)

    def test_same_meaning_merged(self):
        """동의어: 같은 뜻 다른 표기를 대표어 하나로 (EV·전기자동차 → 전기차, 인공지능 → AI)"""
        r = self.topic("전기차,EV,전기자동차,인공지능,K뷰티,K-BEAUTY")
        self.assertEqual(r["kw_topic"], ["전기차", "AI", "K-뷰티"])

    def test_after_geo_split(self):
        """동의어: 나라를 뗀 뒤에도 적용 (베트남EV → VN + 전기차)"""
        r = self.topic("베트남EV,중국인공지능", "베트남")
        self.assertEqual(r["kw_topic"], ["전기차", "AI"])

    def test_exception_kept_then_merged(self):
        """동의어: 통째로 남긴 사건 이름도 대표어로 (러-우사태, 러시아-우크라이나사태 → 우크라이나사태)"""
        r = self.topic("러-우사태,러시아-우크라이나사태,우크라이나사태")
        self.assertEqual(r["kw_topic"], ["우크라이나사태"])

    def test_not_merged_related_words(self):
        """동의어: 관련 있지만 다른 말은 합치지 않음 (배터리 ≠ 이차전지, 냉장고 ≠ 세탁기)"""
        r = self.topic("배터리,이차전지,2차전지,냉장고,세탁기")
        self.assertEqual(r["kw_topic"], ["배터리", "이차전지", "냉장고", "세탁기"])

    def test_synonym_file(self):
        """동의어: 사전의 대표어는 사전 안에서 다시 바뀌지 않음 (A→B, B→C 없음)"""
        self.assertFalse(set(self.SYN.values()) & set(self.SYN))


class CategoryTest(unittest.TestCase):
    """⑥ 대/중분류"""
    CAT = kc.load_categories()

    def run_one(self, kwrd, natn=""):
        return kc.process_article(kwrd, natn, GEO, kc.load_exceptions(), kc.load_stopwords(), kc.load_synonyms(), self.CAT)

    def test_article_gets_categories(self):
        """분류: 주제어마다 분야가 붙고 한 기사에 여러 분야 가능 (반덤핑 → 관세·무역규제, 태양광 → 에너지 전환)"""
        r = self.run_one("미국,반덤핑,관세,태양광")
        self.assertEqual(r["kw_category"], ["무역·통상>관세·무역규제", "에너지·환경>에너지 전환"])

    def test_synonym_then_category(self):
        """분류: 동의어로 바뀐 대표어 기준 (인공지능 → AI → AI·소프트웨어)"""
        self.assertEqual(self.run_one("인공지능")["kw_category"], ["기술·디지털>AI·소프트웨어"])

    def test_stopwords_not_categorized(self):
        """분류: 불용어는 분야를 붙이지 않음 (수출·경제)"""
        self.assertEqual(self.run_one("수출,경제")["kw_category"], [])

    def test_partial_match_fixed(self):
        """분류: 일부만 겹쳐 잘못 붙던 말 바로잡음 (관광산업 ≠ 광산, 현지진출 ≠ 지진)"""
        self.assertEqual(self.CAT["관광산업"], "산업·품목>품목 (→ HS 코드로 연결)")
        self.assertEqual(self.CAT["현지진출"], "기업·투자·진출>진출·마케팅")

    def test_framework(self):
        """분류: 분류 틀은 대분류 8개 · 중분류 23개"""
        pairs = {v.split(">") [0] for v in self.CAT.values()}, set(self.CAT.values())
        self.assertEqual(len(pairs[0]), 8)
        self.assertEqual(len(pairs[1]), 23)


class ProductTest(unittest.TestCase):
    """③ 품목 연결 (1·3단계)"""

    def test_hs_field_to_hs4(self):
        """품목: KOTRA HS 칸을 HS 4단위로 (330499 → 3304, 여러 개·10단위도 처리)"""
        self.assertEqual(kc.hs4_codes("330499"), ["3304"])
        self.assertEqual(kc.hs4_codes("1004,1404,1905"), ["1004", "1404", "1905"])
        self.assertEqual(kc.hs4_codes("8443313010"), ["8443"])
        self.assertEqual(kc.hs4_codes(""), [])

    def test_order(self):
        """품목: HS 칸이 있으면 HS, 없으면 산업분류(큰 범위), 기타·산업일반은 범위로 안 씀"""
        self.assertEqual(kc.product_link("220300", "농림수산식품"), (["2203"], ["농림수산식품"], "KOTRA HS"))
        self.assertEqual(kc.product_link("", "자동차/수송기기,전자/전기"), ([], ["자동차/수송기기", "전자/전기"], "산업분류"))
        self.assertEqual(kc.product_link("", "산업일반,기타"), ([], [], ""))


if __name__ == "__main__":
    unittest.main()
