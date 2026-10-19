"""해외시장뉴스 키워드 정리(① 정제 · ② 지역 · ③ 연도 · ④ 동의어 · ⑤ 분야 · ⑥ 품목)와 블락·사전 생성 코드를 실제 데이터에서 나온 예시로 확인합니다.

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


class BlockTest(unittest.TestCase):
    """사용자 키워드 블락 — 불용어 대신 (회의 결정 2026-10-14). 정리 결과는 그대로 두고 보여 줄 때만 거름"""
    spec = importlib.util.spec_from_file_location("keyword_block", kc.BASE / "keyword_block.py")
    kb = importlib.util.module_from_spec(spec); spec.loader.exec_module(kb)

    def test_no_stopwords(self):
        """블락: 불용어 없음 — 넓은 말(수출·경제)도 주제어에 그대로 남음"""
        r = kc.process_article("수출,경제,동향,전기차,공급망", "미국", GEO)
        self.assertEqual(r["kw_topic"], ["수출", "경제", "동향", "전기차", "공급망"])
        self.assertFalse((kc.BASE / "stopwords.csv").exists())

    def test_block_word_is_representative(self):
        """블락: 입력한 말을 대표어로 저장 (ev → 전기차, 화장품시장 → 화장품)"""
        syn = kc.load_synonyms()
        self.assertEqual(self.kb.to_block_word("ev", syn), "전기차")
        self.assertEqual(self.kb.to_block_word("화장품시장", syn), "화장품")

    def test_hide_articles_and_issues(self):
        """블락: 블락한 키워드가 있는 기사·이슈·알림은 숨김, 다른 기사는 그대로"""
        df = pd.DataFrame({"bbstxSn": ["1", "2", "3"], "kw_topic": ["박람회|화장품", "화장품", ""]})
        self.assertEqual(self.kb.hide_articles(df, {"박람회"}).bbstxSn.tolist(), ["2", "3"])
        self.assertEqual(len(self.kb.hide_articles(df, set())), 3)
        issues = pd.DataFrame({"key": ["관세", "박람회", "330499"], "type": ["키워드", "키워드", "품목(HS6)"]})
        self.assertEqual(self.kb.hide_keys(issues, {"박람회"}).key.tolist(), ["관세", "330499"])
        self.assertEqual(self.kb.hide_keys([{"key": "박람회"}, {"key": "관세"}], {"박람회"}), [{"key": "관세"}])


class SynonymTest(unittest.TestCase):
    """④ 동의어"""
    SYN = kc.load_synonyms()

    def topic(self, kwrd, natn=""):
        return kc.process_article(kwrd, natn, GEO, kc.load_exceptions(), self.SYN)

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
    """⑤ 대/중분류"""
    CAT = kc.load_categories()

    def run_one(self, kwrd, natn=""):
        return kc.process_article(kwrd, natn, GEO, kc.load_exceptions(), kc.load_synonyms(), self.CAT)

    def test_article_gets_categories(self):
        """분류: 주제어마다 분야가 붙고 한 기사에 여러 분야 가능 (반덤핑 → 관세·무역규제, 태양광 → 에너지 전환)"""
        r = self.run_one("미국,반덤핑,관세,태양광")
        self.assertEqual(r["kw_category"], ["무역·통상>관세·무역규제", "에너지·환경>에너지 전환"])

    def test_synonym_then_category(self):
        """분류: 동의어로 바뀐 대표어 기준 (인공지능 → AI → AI·소프트웨어)"""
        self.assertEqual(self.run_one("인공지능")["kw_category"], ["기술·디지털>AI·소프트웨어"])

    def test_broad_words_categorized(self):
        """분류: 넓은 말도 뜻이 분명하면 분야 (수출 → 수출입 일반, 전시회 → 전시회·행사), 너무 넓은 말(한국)은 미분류"""
        self.assertEqual(self.run_one("수출,전시회,한국")["kw_category"], ["무역·통상>수출입 일반", "기업·투자·진출>전시회·행사"])

    def test_partial_match_fixed(self):
        """분류: 일부만 겹쳐 잘못 붙던 말 바로잡음 (리튬이온배터리 ≠ 리튬 광물, 파리협정 ≠ 통상 협정)"""
        self.assertEqual(self.CAT["리튬이온배터리"], "산업·품목>품목 (→ HS 코드로 연결)")
        self.assertEqual(self.CAT["파리협정"], "에너지·환경>친환경·ESG·기후")

    def test_framework(self):
        """분류: 분류 틀은 대분류 8개 · 중분류 23개"""
        pairs = {v.split(">") [0] for v in self.CAT.values()}, set(self.CAT.values())
        self.assertEqual(len(pairs[0]), 8)
        self.assertEqual(len(pairs[1]), 23)


class ProductTest(unittest.TestCase):
    """⑥ 품목 연결 (1·3단계)"""

    def test_hs_field_to_hs4(self):
        """품목: KOTRA HS 칸을 HS 4단위로 (330499 → 3304, 여러 개·10단위도 처리)"""
        self.assertEqual(kc.hs4_codes("330499"), ["3304"])
        self.assertEqual(kc.hs4_codes("1004,1404,1905"), ["1004", "1404", "1905"])
        self.assertEqual(kc.hs4_codes("8443313010"), ["8443"])
        self.assertEqual(kc.hs4_codes(""), [])

    def test_hs_field_to_hs6(self):
        """품목: HS 6단위(요구사항 기본) — 10단위는 앞 6자리, 4자리만 있는 코드는 6단위를 알 수 없어 뺌"""
        self.assertEqual(kc.hs6_codes("8443313010"), ["844331"])
        self.assertEqual(kc.hs6_codes("8415,330499,847960"), ["330499", "847960"])
        self.assertEqual(kc.hs6_codes("8542"), [])

    def test_order(self):
        """품목: HS 칸이 있으면 HS, 없으면 산업분류(큰 범위), 기타·산업일반은 범위로 안 씀"""
        self.assertEqual(kc.product_link("220300", "농림수산식품"), (["2203"], ["농림수산식품"], "KOTRA HS"))
        self.assertEqual(kc.product_link("", "자동차/수송기기,전자/전기"), ([], ["자동차/수송기기", "전자/전기"], "산업분류"))
        self.assertEqual(kc.product_link("", "산업일반,기타"), ([], [], ""))


class BuildTest(unittest.TestCase):
    """사전 생성 코드 (build/)"""

    def test_category_assign_order(self):
        """분류 생성: 규칙(일치·포함)을 먼저 대조 → 틀린 규칙 결과는 수정 목록 → 규칙에 안 걸린 말만 LLM 판단 → 나머지 검수 필요"""
        import importlib.util as iu
        spec = iu.spec_from_file_location("bc", kc.BASE / "build" / "build_categories.py")
        bc = iu.module_from_spec(spec); spec.loader.exec_module(bc)
        fixes, llm = bc.load_lists()
        self.assertEqual(bc.assign("반덤핑", fixes, llm)[:3], ("무역·통상", "관세·무역규제", "규칙(일치)"))
        self.assertEqual(bc.assign("태양광발전소", fixes, llm)[2], "규칙(포함)")
        self.assertEqual(bc.assign("관광산업", fixes, llm), ("산업·품목", "품목 (→ HS 코드로 연결)", "규칙 수정", "에너지·환경 > 화석연료·자원"))
        self.assertEqual(bc.assign("제조업", fixes, llm)[2], "LLM 판단")
        self.assertIsNone(bc.rule("제조업"))                       # LLM 판단은 규칙에 안 걸린 말에만
        self.assertTrue(all(bc.rule(w) is None for w in llm))
        self.assertEqual(bc.assign("한국", fixes, llm)[2], "검수 필요")

    def test_category_team_decisions(self):
        """분류 생성: 팀 검수가 최우선 — X는 고른 분류로, 못 정한 말을 O(미분류 유지)로 확인하면 검수 필요에서 빠짐"""
        import importlib.util as iu
        spec = iu.spec_from_file_location("bc", kc.BASE / "build" / "build_categories.py")
        bc = iu.module_from_spec(spec); spec.loader.exec_module(bc)
        fixes, llm = bc.load_lists()
        team = bc.load_decisions()
        self.assertEqual(bc.assign("관광", fixes, llm, team)[:3], ("경제·시장", "시장·소비 동향", "팀 검수"))
        self.assertEqual(bc.assign("상반기", fixes, llm, team)[:3], ("미분류", "미분류", "팀 검수"))
        self.assertEqual(bc.assign("반덤핑", fixes, llm, team)[2], "규칙(일치)")   # 팀이 O로 확인한 분류는 원래 방법 그대로

    def test_synonym_auto_o(self):
        """동의어 후보: 임베딩 유사도 0.96 이상 미반영 후보만 자동 O (동시 등장·0.96 미만·이미 반영은 그대로)"""
        import importlib.util as iu
        import pandas as pd
        spec = iu.spec_from_file_location("sc", kc.BASE / "build" / "synonym_candidates.py")
        sc = iu.module_from_spec(spec); spec.loader.exec_module(sc)
        P = sc.PENDING
        df = pd.DataFrame({"근거": ["임베딩", "임베딩", "동시 등장 (5건, 60%)", "같은 뜻"], "임베딩 유사도": ["0.97", "0.95", "0.98", ""],
                           "현재 상태": [P, P, P, "1차 반영"], "팀 확인(O/X)": "", "의견": ""})
        self.assertEqual(sc.auto_mark(df)["팀 확인(O/X)"].tolist(), ["O", "", "", ""])

    def test_suffix_rule(self):
        """동의어 접미사 규칙: OO산업·OO시장·OO업 → OO (기본 단어가 있고 예외가 아닐 때), 팀 X는 규칙보다 우선"""
        import importlib.util as iu
        import pandas as pd
        from collections import Counter
        spec = iu.spec_from_file_location("bs", kc.BASE / "build" / "build_synonyms.py")
        bs = iu.module_from_spec(spec); spec.loader.exec_module(bs)
        docs = Counter({"화장품": 400, "화장품시장": 38, "화장품산업": 10, "뷰티": 200, "뷰티시장": 19, "미래": 9,
                        "미래산업": 14, "서비스": 40, "서비스업": 11, "물": 30, "물산업": 3, "건설": 140, "건설업": 11})
        pairs = {(w, b) for w, b, _ in bs.suffix_pairs(docs)}
        self.assertEqual(pairs, {("화장품시장", "화장품"), ("화장품산업", "화장품"), ("뷰티시장", "뷰티"), ("건설업", "건설")})
        bs.GROUPS, keep = [], bs.GROUPS
        try:
            dec = pd.DataFrame({"표기": ["건설업"], "대표어 후보": ["건설"], "판정": ["X"]})
            df, _ = bs.build(docs, dec)
        finally:
            bs.GROUPS = keep
        m = dict(zip(df.표기, df.대표어))
        self.assertEqual(m.get("화장품시장"), "화장품")
        self.assertNotIn("건설업", m)                          # 팀이 X로 정한 쌍은 규칙으로도 안 묶음
        self.assertFalse(set(m.values()) & set(m))            # 사슬 없음

if __name__ == "__main__":
    unittest.main()
