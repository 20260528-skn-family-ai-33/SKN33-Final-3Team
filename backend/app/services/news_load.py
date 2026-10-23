"""해외시장뉴스 원본·키워드 결과를 article·article_tag·news_*_daily 행으로 바꿉니다 (DB 접속 없음).

입력
- 원본: collectors/news/news_collect.py가 만든 news_raw CSV (bbstxSn·newsTitl·newsBdt·othbcDt·kotraNewsUrl·infoCl …)
- 키워드: services/news_keywords/keyword_clean.py 결과 news_keywords_clean.csv (kw_topic·country_all·hs6)

규칙
- body_masked: HTML 태그·스크립트를 벗겨 글만 남기고 이메일·전화번호를 [이메일]·[전화번호]로 가립니다.
  본문이 비면 요약(cntntSumar), 그것도 없으면 제목을 넣습니다 (NOT NULL 컬럼).
- title: 원본 제목의 HTML 문자(&#39;·&middot;·&lsquo; 등, 약 2,400건)를 글자로 바꿔 넣습니다.
- 키워드 태그: kw_topic(동의어 정리 후 주제어). 사용자 블락은 저장값을 바꾸지 않고 보여 줄 때 거릅니다 (keyword_block.py).
- 국가 태그: country_all 중 country 테이블에 있는 코드만. 품목 태그: hs6 중 hs_code 테이블에 있는 (코드, 판)만.
  hs_ver는 발행일에 적용되는 판입니다 (hs_code_build.hs_ver_for: 2022~2027 → HS2022).
- 일별 집계: 한 기사는 같은 날 같은 키워드·품목을 1건으로 셉니다 (DISTINCT article_id).
  국가별 행과 국가 조건 없는 전체 행(country_code NULL)을 함께 만듭니다. 품목은 HS6 태그와 그 앞 4자리 HS4를 셉니다.
"""
import html
import re
from collections import defaultdict
from html.parser import HTMLParser

SOURCE_CODE = "KOTRA_MARKET"
RAW_COLUMNS = ["bbstxSn", "newsTitl", "newsBdt", "othbcDt", "kotraNewsUrl", "infoCl", "cntntSumar"]
CLEAN_COLUMNS = ["bbstxSn", "othbcDt", "kw_topic", "country_all", "hs6"]
LIMITS = {"title": 600, "source_url": 2048, "category": 120, "keyword_text": 120}
# 보통 주소(a@b.com, 원문이 일부 가린 sales@tco-****.com)와 도메인 끝(.com)이 빠진 소문자 주소(info@nexus4013).
# Tech@SG·Autism@Work·BE@RBRICK 같은 프로그램·상품 이름은 대문자가 있어 그대로 둡니다.
EMAIL = re.compile(r"[\w.+*-]+@[\w*-]+(?:\.[\w*-]+)+|(?<![\w@.])[a-z0-9._%+-]{2,}@[a-z0-9-]{3,}(?![\w@.])")
# 02-123-4567, 010.1234.5678, +82-2-1234-5678, (02) 123-4567, +1 (212) 555-1234
PHONE = re.compile(r"(?<![\w.-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{1,4}\)[ .-]?|\d{1,4}[.-])(?:\d{1,4}[.-])?\d{3,4}[.-]\d{4}(?![\w-])")
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote"}
SKIP_TAGS = {"script", "style", "head", "title"}


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)  # &nbsp;·&amp; 같은 HTML 문자는 글자로 바뀜
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html):
    """HTML → 글. 이미지(data: URI)는 속성이라 사라지고, 문단은 줄바꿈 하나로 남습니다."""
    if not html:
        return ""
    p = _TextParser()
    p.feed(str(html))
    p.close()
    lines = (re.sub(r"[ \t\r\f\v ​]+", " ", line).strip() for line in "".join(p.parts).split("\n"))
    return "\n".join(line for line in lines if line)


def _phone(m):
    """HS 코드 범위(6210.4010-6210, 1902-1904-1905)는 번호가 아니라 그대로 둡니다: 구분기호가 섞였거나 모두 4자리면 제외."""
    s = m.group()
    groups = re.findall(r"\d+", s)
    if "+" not in s and "(" not in s and (len(set(re.findall(r"[.-]", s))) > 1 or all(len(g) == 4 for g in groups)):
        return s
    return "[전화번호]"


def mask(text):
    return PHONE.sub(_phone, EMAIL.sub("[이메일]", text))


def split_values(value):
    return list(dict.fromkeys(x.strip() for x in str(value or "").split("|") if x.strip()))


def _cut(value, column):
    return str(value or "").strip()[:LIMITS[column]]


def plain_title(value):
    """제목의 HTML 문자(&#39;·&middot;·&lsquo; …)를 글자로 바꿉니다. &amp;#39;처럼 두 번 감싼 것까지 풉니다."""
    text = str(value or "")
    for _ in range(2):
        text = html.unescape(text)
    return _cut(re.sub(r"\s+", " ", text), "title")


def article_row(raw):
    """원본 CSV 한 행(dict) → article 행."""
    title = plain_title(raw.get("newsTitl"))
    body = mask(html_to_text(raw.get("newsBdt"))) or mask(html_to_text(raw.get("cntntSumar"))) or title
    return {"source_code": SOURCE_CODE, "external_id": str(raw["bbstxSn"]).strip(),
            "title": title, "published_date": str(raw["othbcDt"])[:10],
            "source_url": _cut(raw.get("kotraNewsUrl"), "source_url"), "body_masked": body,
            "category": _cut(raw.get("infoCl"), "category") or None}


def tag_rows(clean, article_id, countries, hs_codes, hs_ver):
    """키워드 결과 한 행 → article_tag 행 목록. countries·hs_codes는 DB에 있는 값 집합 (FK)."""
    rows = [{"article_id": article_id, "keyword_text": k} for k in dict.fromkeys(
        _cut(k, "keyword_text") for k in split_values(clean.get("kw_topic"))) if k]
    rows += [{"article_id": article_id, "country_code": c} for c in split_values(clean.get("country_all")) if c in countries]
    rows += [{"article_id": article_id, "hs_code": h, "hs_ver": hs_ver} for h in split_values(clean.get("hs6"))
             if (h, hs_ver) in hs_codes]
    # 한 번에 INSERT할 수 있게 모든 행의 컬럼을 맞춤 (종류가 다른 칸은 NULL)
    return [{"country_code": None, "hs_code": None, "hs_ver": None, "keyword_text": None, **r} for r in rows]


def daily_counts(article_dates, targets, article_countries):
    """{(날짜, 대상, 국가 또는 None): 고유 기사 수}.

    article_dates: {article_id: 발행일}, targets: [(article_id, 대상)], article_countries: {article_id: {국가}}
    """
    seen = defaultdict(set)
    for article_id, target in targets:
        day = article_dates[article_id]
        seen[(day, target, None)].add(article_id)
        for c in article_countries.get(article_id, ()):
            seen[(day, target, c)].add(article_id)
    return {key: len(ids) for key, ids in seen.items()}


def product_targets(hs_tags, hs_codes):
    """HS6 태그 [(article_id, hs6, hs_ver)] → 집계 대상 [(article_id, (코드, 판))]. 사전에 있는 HS4도 함께 셉니다."""
    out = []
    for article_id, code, ver in hs_tags:
        out.append((article_id, (code, ver)))
        if (code[:4], ver) in hs_codes:
            out.append((article_id, (code[:4], ver)))
    return out
