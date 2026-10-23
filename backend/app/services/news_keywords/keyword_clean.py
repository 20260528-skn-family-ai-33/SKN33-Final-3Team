"""해외시장뉴스 키워드(kwrd)를 정제하고 국가·도시·권역을 별도 칸으로 분리합니다.

① 정제: HTML 문자·해시태그·따옴표·공백·대소문자를 정리하고 붙어 있는 키워드를 나눕니다.
② 지역 분리: 지역 사전(rule_data.GEO_DICT)으로 국가·도시·권역을 찾아 별도 칸으로 옮기고, 중국경제 → 중국 + 경제처럼 복합어를 나눕니다.
   국가 필터(country_all)는 KOTRA 국가 칸(natn)을 기준으로 하고, natn이 특정 나라가 아닐 때(KOTRA 본사·전세계)만
   키워드 속 나라로 보충합니다. 도시는 국가 필터에 넣지 않습니다(예: '두바이초콜릿'은 UAE 기사가 아님).
   예외: 나라를 떼고 남는 말이 나누지 않을 끝말(rule_data.SPLIT_EXCEPTIONS)로 끝나면(…사태·…선거·…무역관·…엑스포 등) 나누지 않고 통째로 남깁니다.
③ 연도 분리: '2024', '2026년'처럼 연도만 있는 키워드는 지우지 않고 언급 연도(kw_year) 칸으로 옮깁니다.
④ 동의어: 동의어 사전(build/build_synonyms.py가 규칙으로 만든 data/news_keywords/synonyms.csv)으로 같은 뜻의 다른 표기를 대표어 하나로 바꿉니다 (EV·전기자동차 → 전기차, 인공지능 → AI).
④-2 상위어: 상위어 사전(build/build_hypernyms.py가 규칙으로 만든 data/news_keywords/hypernyms.csv)으로 하위어를 상위어로 바꿉니다 (전기차보조금·전기차충전소 → 전기차, 진단기기 → 의료).
   팀 결정(2026-10-21)으로 동의어 자동 검수 대신 상위어로 묶습니다. 바꾸기 전 주제어는 kw_sub 칸에 남깁니다
   (품목 HS 연결 등 세부 단어가 필요한 곳용). 결과 주제어(kw_topic)를 검색·집계·급증 판정에 모두 씁니다.
   분야(⑤)는 상위어와 바꾸기 전 하위어 모두로 붙입니다 (경제성장률 → 성장이어도 거시경제 분야 유지).
⑤ 대/중분류: 기사에 분야 칸(kw_category)을 붙입니다. 분류표(data/news_keywords/topic_categories.csv)를 먼저 쓰고,
   표에 없는 주제어는 분류표 생성과 같은 판정(category_rules.classify, 코드 안 규칙)을 적용합니다. 규칙(포함)·LLM 판단 같은
   추정도 확정하고(팀 결정 2026-10-22), 규칙이 없는 말은 이유·고유 기사 수·예시 기사와 함께 unclassified_topics.csv에 저장합니다.
   검수 파일은 읽지 않습니다 — 규칙과 예외는 모두 코드(rule_data.py · category_rules.py · build/*.py)에 있습니다.
   분야는 주제어 단위라 검색에서는 순위 보조 가점으로만 씁니다 (최종 관련도는 검색 단계에서 기사 문맥으로 판단).
⑥ 품목 연결(1·3단계): KOTRA HS 칸(hsCdNm)을 HS 6단위(hs6, 요구사항 기본 단위)·4단위(hs4)로, 없으면 KOTRA 산업분류(indstCl)로 큰 범위만 남깁니다.
   2단계(키워드·제목의 품목어 → HS 사전)는 HS 사전 완료 후 추가합니다.
원본 kwrd는 그대로 두고 결과 칸을 새로 추가합니다.
불용어는 회의 결정(2026-10-14)으로 없앴습니다. 보기 싫은 키워드는 사용자 키워드 블락으로 조회할 때 거릅니다 (keyword_block.py).

실행: python backend/app/services/news_keywords/keyword_clean.py --input <news_raw.csv 또는 .parquet>
확인: python backend/app/services/news_keywords/keyword_clean.py --check "우크라이나사태,중국경제" --natn 중국
"""
import argparse
import csv
import html
import importlib.util
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("category_rules", BASE / "category_rules.py")
cr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(cr)
rd = cr.rd   # 규칙 데이터 (rule_data.py: 지역 사전 · 나누지 않을 끝말 · 분야 LLM 판단 · 규칙 수정)
# 결과와 규칙으로 만든 사전은 저장소 루트의 data/news_keywords/에 저장합니다 (git 제외 폴더).
OUT_DIR = Path(__file__).resolve().parents[4] / "data" / "news_keywords"
SYNONYMS_PATH = OUT_DIR / "synonyms.csv"
HYPERNYMS_PATH = OUT_DIR / "hypernyms.csv"
CATEGORIES_PATH = OUT_DIR / "topic_categories.csv"
SKIP = "none"   # 처음 사전을 만드는 단계에서만: --hypernyms none처럼 그 사전을 일부러 빼고 실행


class DictionaryMissing(FileNotFoundError):
    """사전 파일이 없음 — 빈 사전으로 조용히 진행하면 EV가 그대로 남고, 이어서 적재하면 DB 동의어 행이 지워질 수 있음."""


def require_dict(path, label, option):
    """사전 경로를 확인합니다. "none"이면 None(일부러 생략), 파일이 없으면 DictionaryMissing으로 멈춤."""
    if str(path).strip().lower() == SKIP:
        return None
    if not Path(path).exists():
        raise DictionaryMissing(f"{label} 사전이 없습니다: {path}\n"
                                f"  NEWS_KEYWORDS_USAGE.md의 '다시 만들기' 순서로 먼저 만드세요 "
                                f"(사전을 처음 만드는 단계에서만 --{option} none으로 생략)")
    return Path(path)

# 쉼표 외에 실제 데이터에서 키워드 구분자로 쓰인 것: 해시태그(#), 두 칸 이상 공백, 가운뎃점(·)
SPLIT = re.compile(r"[,、#·・]|\s{2,}")
QUOTES = re.compile(r"[\"'“”‘’「」『』《》〈〉<>\[\]{}]")
PAREN = re.compile(r"^(.+?)\((.+)\)$")  # 전기차(EV) → 전기차, EV
HANGUL = re.compile(r"[가-힣]")
SYMBOL_ONLY = re.compile(r"[\W_]+")
GEO_TYPES = {"국가": "country", "도시": "city", "권역": "region"}
YEAR = re.compile(r"((?:19|20)\d{2})년?")  # 연도만 있는 키워드 (비전2030 같은 이름은 해당 없음)


def normalize(word):
    """키워드 하나의 표기를 통일합니다. 영문은 대문자, 한글이 있으면 공백 제거, 영문만 있으면 공백 한 칸."""
    word = QUOTES.sub("", unicodedata.normalize("NFKC", word)).strip(" .-_").upper()
    if HANGUL.search(word):
        return re.sub(r"\s+", "", word)
    return re.sub(r"\s+", " ", word)


def clean_keywords(raw):
    """원본 kwrd 문자열을 정제된 키워드 목록으로 바꿉니다 (순서 유지, 중복 제거)."""
    text = unicodedata.normalize("NFKC", raw or "")
    for _ in range(2):  # &amp;#39; 처럼 두 번 감싼 HTML 문자까지 풉니다.
        text = html.unescape(text)
    words = []
    for part in SPLIT.split(text):
        part = part.strip()
        match = PAREN.match(part)
        for piece in (match.groups() if match else [part]):
            word = normalize(piece)
            if word and not SYMBOL_ONLY.fullmatch(word):
                words.append(word)
    return list(dict.fromkeys(words))


def load_geo(rows=None):
    """지역 사전(rule_data.GEO_DICT)을 (이름 → 항목, 복합어 분리에 쓸 이름 목록)으로 돌려줍니다.
    rows: [(이름, 종류, 코드, 대표 이름, 분리 Y/N)] — 테스트에서 바꿔 넣습니다."""
    cols = ("name", "type", "code", "canonical", "split")
    rows = [dict(zip(cols, r)) for r in (rd.GEO_DICT if rows is None else rows)]
    names = {normalize(r["name"]): r for r in rows}
    # 긴 이름부터 확인해야 인도네시아가 인도 + 네시아로 잘리지 않습니다.
    prefixes = sorted((n for n, r in names.items() if r["split"] == "Y"), key=len, reverse=True)
    return names, prefixes


def split_geo(word, geo):
    """키워드 하나를 (지역 항목 목록, 남는 주제어 또는 None)으로 나눕니다."""
    names, prefixes = geo
    if word in names:
        return [names[word]], None
    for prefix in prefixes:
        if not word.startswith(prefix):
            continue
        # 러시아-우크라이나처럼 이어 붙인 기호는 떼고 봅니다.
        rest = word[len(prefix):].lstrip("-–+&")
        # 남는 말이 한 글자면(중국산, 미국내) 나누지 않습니다. 가장 긴 지명에서 멈추고 더 짧은 지명으로 다시 자르지 않습니다
        # (인도네시아산 → 인도 + 네시아산 ✗, 유럽연합법 → 유럽 + 연합법 ✗).
        if len(rest) < 2:
            return [], word
        places, topic = split_geo(rest, geo)
        return [names[prefix], *places], topic
    return [], word


def load_exceptions(words=None):
    """나누지 않을 끝말(rule_data.SPLIT_EXCEPTIONS의 첫 칸). 남는 말(사태) 또는 단어 전체(두바이초콜릿)."""
    return frozenset(normalize(w) for w in (words if words is not None else (r[0] for r in rd.SPLIT_EXCEPTIONS)) if w)


def load_hypernyms(path=HYPERNYMS_PATH):
    """하위어 → 상위어 표(hypernyms.csv의 하위어·상위어 컬럼, build/build_hypernyms.py가 만듦)를 읽습니다. None·없는 파일이면 빈 표."""
    if path is None or not Path(path).exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {normalize(r["하위어"]): normalize(r["상위어"]) for r in csv.DictReader(fh) if r.get("하위어") and r.get("상위어")}


def load_synonyms(path=SYNONYMS_PATH):
    """같은 뜻 표기 → 대표어 표(synonyms.csv의 표기·대표어 컬럼)를 읽습니다. None·없는 파일이면 빈 표."""
    if path is None or not Path(path).exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {normalize(r["표기"]): normalize(r["대표어"]) for r in csv.DictReader(fh) if r.get("표기") and r.get("대표어")}


class Categories(dict):
    """분류표: 주제어 → "대분류>중분류" (미분류는 넣지 않음).
    표에 없는 주제어는 lookup()이 분류표 생성과 같은 판정(category_rules.classify, 코드 안 규칙)으로 정합니다.
    규칙(포함)·LLM 판단 같은 추정도 확정합니다 (팀 결정 2026-10-22). 분류표를 다시 만들어도 같은 기준입니다."""

    def __init__(self, table=(), listed=(), rules=True):
        super().__init__(table)
        self.listed = set(listed) | set(self)   # 표에 있는 주제어 (미분류 포함)
        self.rules = rules
        self._lists = cr.load_lists() if rules else ({}, {})
        self._cache = {}

    def judge(self, word):
        """(분야 "대분류>중분류" 또는 None, 미분류 이유 또는 "", 후보 분야 — 추정도 확정하므로 항상 "")."""
        if word in self:
            return self[word], "", ""
        if word not in self._cache:
            fixes, llm = self._lists
            big, mid, how, rule_txt = cr.classify(word, fixes, llm)
            if not self.rules:
                self._cache[word] = (None, "분류표에 없음 (규칙 적용 안 함)", "")
            elif big == "미분류":
                self._cache[word] = (None, cr.unclassified_reason(word, how, rule_txt, llm), "")
            else:
                self._cache[word] = (f"{big}>{mid}", "", "")
        return self._cache[word]

    def lookup(self, word):
        return self.judge(word)[0]


def load_categories(path=CATEGORIES_PATH, rules=True):
    """분류표(build_categories.py 결과)를 읽습니다. rules=True면 표에 없는 주제어에 공통 분류 규칙을 적용합니다.
    None·없는 파일이면 빈 표 (규칙은 그대로 적용)."""
    if path is None or not Path(path).exists():
        return Categories(rules=rules)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("주제어")]
    confirmed = lambda r: r.get("대분류") not in ("", "미분류")
    table = {normalize(r["주제어"]): f'{r["대분류"]}>{r["중분류"]}' for r in rows if confirmed(r)}
    return Categories(table, (normalize(r["주제어"]) for r in rows), rules)


def category_of(word, cat):
    """주제어 하나의 분야 ("대분류>중분류") 또는 None. cat이 일반 dict면 표에 있는 말만.
    Categories는 표가 비어 있어도(파일 없음) 규칙을 적용해야 하므로 비었는지(not cat)로 판단하지 않습니다."""
    if isinstance(cat, Categories):
        return cat.lookup(word)
    return (cat or {}).get(word)


def hs4_codes(hs_field):
    """KOTRA HS 칸 값(예: '330499', '1004,1404,1905', '8443313010')을 HS 4단위 코드 목록으로 바꿉니다."""
    codes = re.findall(r"\d{4,10}", hs_field or "")
    return list(dict.fromkeys(c[:4] for c in codes))


def hs6_codes(hs_field):
    """KOTRA HS 칸 값을 HS 6단위 코드 목록으로 바꿉니다 (요구사항 기본 단위). 4자리만 있는 코드는 6단위를 알 수 없어 뺍니다."""
    codes = re.findall(r"\d{4,10}", hs_field or "")
    return list(dict.fromkeys(c[:6] for c in codes if len(c) >= 6))


def product_link(hs_field, industry):
    """품목 연결 1·3단계: HS 칸이 있으면 HS 4단위, 없으면 산업분류(큰 범위)."""
    hs4 = hs4_codes(hs_field)
    inds = [x.strip() for x in (industry or "").split(",") if x.strip() and x.strip() not in ("기타", "산업일반")]
    if hs4:
        return hs4, inds, "KOTRA HS"
    if inds:
        return [], inds, "산업분류"
    return [], [], ""


def is_exception(word, topic, keep):
    """나누지 않을 키워드인지: 단어 전체가 목록에 있거나, 나라를 떼고 남는 말이 목록의 말로 끝나면 True."""
    return word in keep or bool(topic) and any(topic.endswith(e) for e in keep)


def process_article(kwrd, natn, geo, keep=frozenset(), syn=None, cat=None, hyp=None):
    """기사 한 건의 키워드를 정제하고 지역 칸을 채운 결과를 돌려줍니다.

    keep: 나라를 떼고 남는 말이 이 안에 있으면 키워드를 나누지 않고 통째로 주제어에 남깁니다.
    """
    words = clean_keywords(kwrd)
    found = {"country": [], "city": [], "region": []}
    topics, subs, compounds, years, kept = [], [], [], [], []
    for word in words:
        year = YEAR.fullmatch(word)
        if year:
            years.append(year.group(1))
            continue
        places, topic = split_geo(word, geo)
        for place in places:
            found[GEO_TYPES[place["type"]]].append(place)
        # 우크라이나사태 → '사태'만 남기지 않고 통째로 유지 (국가 정보는 그대로 기록).
        # 남는 말이 예외 목록의 말로 끝나거나(대통령선거 ← 선거), 단어 전체가 목록에 있으면(두바이초콜릿) 유지합니다.
        if places and is_exception(word, topic, keep):
            kept.append(word)
            topic = word
        elif places and topic:
            compounds.append(word)
        if topic:
            sub = (syn or {}).get(topic, topic)            # 동의어는 대표어로 (EV → 전기차)
            subs.append(sub)
            topics.append((hyp or {}).get(sub, sub))       # 하위어는 상위어로 (전기차보조금 → 전기차)
    natn_place = geo[0].get(normalize(natn or ""))
    natn_code = natn_place["code"] if natn_place and natn_place["type"] == "국가" else ""
    kw_countries = list(dict.fromkeys(p["code"] for p in found["country"]))
    # 국가 필터는 KOTRA 국가 칸 기준. 특정 나라가 아닌 기사(KOTRA 본사·전세계)만 키워드 속 나라로 보충합니다.
    # 도시는 소속 국가로 바꾸지 않습니다: '두바이' 키워드 기사 127건 중 122건은 이미 natn이 UAE이고,
    # 나머지는 '두바이초콜릿' 같은 디저트 기사라 UAE로 넣으면 오히려 틀립니다.
    country_all = [natn_code] if natn_code else kw_countries
    return {
        "kw_clean": words,
        "kw_topic": list(dict.fromkeys(topics)),
        "kw_sub": list(dict.fromkeys(subs)),           # 상위어로 바꾸기 전 주제어 (동의어만 적용)
        # 분야: 주제어마다 대분류>중분류 (분류표 먼저, 표에 없으면 공통 규칙 — 한 기사에 여러 개 가능)
        # 상위어로 바꾸기 전 하위어의 분야도 붙임: 건강기능식품 → 건강(팀이 미분류로 둔 넓은 말)이어도 품목 분야는 남게
        "kw_category": list(dict.fromkeys(c for c in (category_of(t, cat) for t in topics + subs) if c)),
        "kw_year": list(dict.fromkeys(years)),
        "natn_code": natn_code,
        "geo_country": kw_countries,
        "geo_city": list(dict.fromkeys(p["canonical"] for p in found["city"])),
        "geo_region": list(dict.fromkeys(p["code"] for p in found["region"])),
        "country_all": country_all,
        "natn_repeated": bool(natn_code) and natn_code in kw_countries,
        "compounds": compounds,
        "kept": kept,
    }


USE_COLS = ["bbstxSn", "othbcDt", "natn", "newsTitl", "kwrd", "hsCdNm", "indstCl", "infoCl"]  # 본문(newsBdt)은 크고 쓰지 않으므로 읽지 않습니다.


def read_input(path, since=None):
    path = Path(path)
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig",
                         usecols=lambda c: c in USE_COLS)
    missing = {"kwrd", "natn"} - set(df.columns)
    if missing:
        raise ValueError(f"입력 파일에 필요한 컬럼이 없습니다: {sorted(missing)}")
    df = df.fillna("")
    if since:
        df = df[df["othbcDt"].astype(str) >= since]
    return df.reset_index(drop=True)


def summarize(df, results):
    raw = [k.strip() for s in df["kwrd"] for k in str(s).split(",") if k.strip()]
    clean = [w for r in results for w in r["kw_clean"]]
    topic = [w for r in results for w in r["kw_topic"]]
    compounds = Counter(w for r in results for w in r["compounds"])
    kept = Counter(w for r in results for w in r["kept"])
    has_natn = [r for r in results if r["natn_code"]]
    unmatched_natn = sorted({str(n) for n, r in zip(df["natn"], results) if n and not r["natn_code"]})
    return {
        "articles": len(df),
        "articles_without_keywords": sum(1 for r in results if not r["kw_clean"]),
        "raw": {"mentions": len(raw), "kinds": len(set(raw))},
        "clean": {"mentions": len(clean), "kinds": len(set(clean))},
        "topic_only": {"mentions": len(topic), "kinds": len(set(topic))},
        "category": {"articles_with_category": sum(1 for r in results if r["kw_category"]),
                     "top": Counter(c for r in results for c in r["kw_category"]).most_common(25)},
        "split_rules": {
            "html_entity": sum("&" in k and ";" in k for k in raw),
            "hashtag": sum("#" in k for k in raw),
            "double_space": sum(bool(re.search(r"\S\s{2,}\S", k)) for k in raw),
            "parenthesis": sum(bool(PAREN.match(k.strip())) for k in raw),
        },
        "geo": {
            "articles_with_country": sum(1 for r in results if r["geo_country"]),
            "articles_with_city": sum(1 for r in results if r["geo_city"]),
            "articles_with_region": sum(1 for r in results if r["geo_region"]),
            "articles_country_from_keywords": sum(1 for r in results if not r["natn_code"] and r["country_all"]),
            "articles_with_year": sum(1 for r in results if r["kw_year"]),
            "compound_split_mentions": sum(compounds.values()),
            "compound_split_kinds": len(compounds),
            "compound_top": compounds.most_common(30),
            "kept_whole_mentions": sum(kept.values()),
            "kept_whole_kinds": len(kept),
            "kept_whole_top": kept.most_common(30),
            "natn_repeated_articles": sum(1 for r in has_natn if r["natn_repeated"]),
            "natn_repeated_rate": round(sum(r["natn_repeated"] for r in has_natn) / max(len(has_natn), 1), 3),
            "natn_not_country": unmatched_natn,
        },
    }


UNCLASSIFIED_COLUMNS = ["주제어", "기사수", "미분류 이유", "후보 분야", "최근 기사"]


def unclassified_topics(df, results, cat, examples=3):
    """분야를 정하지 못한 주제어 전체 (중복 없이). 기사수 = 그 주제어가 나온 고유 기사 수 (같은 기사 ID가 여러 줄이어도 1건).
    규칙(포함)으로 추정만 된 말은 후보 분야와 함께 넣습니다. 빈도로 빼지 않고 많은 순으로 정렬만 합니다."""
    col = lambda name: df[name].astype(str).tolist() if name in df.columns else [""] * len(df)
    dates, titles, ids = col("othbcDt"), col("newsTitl"), col("bbstxSn")
    arts = defaultdict(dict)                 # 주제어 → {기사 ID: 행 번호} — 같은 기사는 한 번만
    for i, r in enumerate(results):
        key = ids[i] or f"#{i}"               # 기사 ID가 없으면 행 번호로 구분
        for t in r["kw_topic"]:
            arts[t].setdefault(key, i)
    rows = []
    for t, by_id in arts.items():
        c, why, cand = cat.judge(t) if isinstance(cat, Categories) else (category_of(t, cat), "분류표에 없음", "")
        if c:
            continue
        recent = sorted(by_id.values(), key=lambda i: (dates[i], ids[i]), reverse=True)[:examples]
        rows.append({"주제어": t, "기사수": len(by_id), "미분류 이유": why, "후보 분야": cand,
                     "최근 기사": " / ".join(f"[{dates[i][:10]}] {titles[i]} ({ids[i]})" for i in recent)})
    out = pd.DataFrame(rows, columns=UNCLASSIFIED_COLUMNS)
    return out.sort_values(["기사수", "주제어"], ascending=[False, True]).reset_index(drop=True)


def run(input_path, output_dir=OUT_DIR, since=None, synonyms_path=SYNONYMS_PATH, categories_path=CATEGORIES_PATH,
        hypernyms_path=HYPERNYMS_PATH):
    """사전 경로가 "none"이면 그 사전 없이, 파일이 없으면 DictionaryMissing으로 멈춤 (빈 사전으로 조용히 진행하지 않음)."""
    syn = load_synonyms(require_dict(synonyms_path, "동의어", "synonyms"))
    cat = load_categories(require_dict(categories_path, "분류표", "categories"))
    hyp = load_hypernyms(require_dict(hypernyms_path, "상위어", "hypernyms"))
    df = read_input(input_path, since)
    geo = load_geo()
    keep = load_exceptions()
    results = [process_article(k, n, geo, keep, syn, cat, hyp) for k, n in zip(df["kwrd"], df["natn"])]
    cols = [c for c in ["bbstxSn", "othbcDt", "natn", "infoCl", "newsTitl", "kwrd"] if c in df.columns]
    out = df[cols].copy()
    links = [product_link(h, i) for h, i in zip(df.get("hsCdNm", [""] * len(df)), df.get("indstCl", [""] * len(df)))]
    out["hs6"] = ["|".join(hs6_codes(h)) for h in df.get("hsCdNm", [""] * len(df))]
    out["hs4"] = ["|".join(x[0]) for x in links]
    out["industry"] = ["|".join(x[1]) for x in links]
    out["product_source"] = [x[2] for x in links]
    for col in ["kw_clean", "kw_topic", "kw_sub", "kw_category", "kw_year", "natn_code", "geo_country", "geo_city", "geo_region", "country_all"]:
        out[col] = [r[col] if isinstance(r[col], str) else "|".join(r[col]) for r in results]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_dir / "news_keywords_clean.csv", index=False, encoding="utf-8-sig")
    un = unclassified_topics(df, results, cat)
    un.to_csv(output_dir / "unclassified_topics.csv", index=False, encoding="utf-8-sig")
    report = summarize(df, results)
    changed = Counter(f"{s} → {hyp[s]}" for r in results for s in r["kw_sub"] if s in hyp)
    report["hypernym"] = {"rules": len(hyp), "replaced_mentions": sum(changed.values()), "replaced_kinds": len(changed),
                          "articles_changed": sum(1 for r in results if any(s in hyp for s in r["kw_sub"])),
                          "top": changed.most_common(30)}
    report["category"]["unclassified_topics"] = len(un)
    report["category"]["unclassified_reasons"] = un["미분류 이유"].value_counts().to_dict()
    src = Counter(x[2] for x in links)
    report["product"] = {"KOTRA HS": src["KOTRA HS"], "HS 6단위": int((out["hs6"] != "").sum()),
                         "산업분류만": src["산업분류"], "없음": src[""],
                         "hs4_top": Counter(c for x in links for c in x[0]).most_common(20),
                         "note": "2단계(키워드·제목 품목어 → HS 사전)는 HS 사전 완료 후"}
    (output_dir / "news_keywords_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, report


def explain(kwrd, natn, geo, keep, syn=None, cat=None, hyp=None):
    """키워드 하나하나가 어디로 갔는지 사람이 읽기 쉬운 표로 출력합니다 (--check)."""
    print(f"기사 국가 칸(natn): {natn or '(없음)'}")
    print(f"원본 키워드: {kwrd}" + chr(10))
    print(f"{'정제 후':<22}{'결과':<10}설명")
    print("-" * 70)
    for word in clean_keywords(kwrd):
        year = YEAR.fullmatch(word)
        if year:
            print(f"{word:<22}{'언급연도':<10}→ 연도 칸 {year.group(1)}")
            continue
        places, topic = split_geo(word, geo)
        where = " + ".join(f"{p['type']} {p['canonical']}({p['code']})" for p in places)
        if places and is_exception(word, topic, keep):
            hit = word if word in keep else next(e for e in sorted(keep, key=len, reverse=True) if topic and topic.endswith(e))
            print(f"{word:<22}{'예외유지':<10}→ 주제어 '{word}' 통째로 + {where} (예외 목록 '{hit}')")
        elif places and topic:
            t2 = (syn or {}).get(topic, topic)
            t3 = (hyp or {}).get(t2, t2)
            note = (f" → 동의어 '{t2}'" if t2 != topic else "") + (f" → 상위어 '{t3}'" if t3 != t2 else "")
            print(f"{word:<22}{'분리':<10}→ {where} + 주제어 '{topic}'" + note)
        elif places:
            print(f"{word:<22}{'지역':<10}→ {where}")
        else:
            t2 = (syn or {}).get(word, word)
            t3 = (hyp or {}).get(t2, t2)
            if t3 != t2:
                print(f"{word:<22}{'상위어':<10}→ " + (f"대표어 '{t2}' → " if t2 != word else "") + f"상위어 '{t3}'로")
            elif word in (syn or {}):
                print(f"{word:<22}{'동의어':<10}→ 대표어 '{syn[word]}'로 통일")
            else:
                print(f"{word:<22}{'주제어':<10}→ 주제어 '{word}'")
    r = process_article(kwrd, natn, geo, keep, syn, cat, hyp)
    print("-" * 70)
    for col in ["kw_topic", "kw_sub", "kw_category", "kw_year", "natn_code", "country_all", "geo_country", "geo_city", "geo_region"]:
        print(f"{col:<14}{' | '.join(r[col]) if isinstance(r[col], list) else r[col]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, help="news_raw.csv 또는 .parquet")
    parser.add_argument("--check", help="키워드 문자열 하나를 넣어 처리 과정을 표로 확인 (예: \"우크라이나사태,중국경제\")")
    parser.add_argument("--natn", default="", help="--check와 함께 쓸 기사 국가 칸 값 (예: 중국)")
    parser.add_argument("--synonyms", type=Path, default=SYNONYMS_PATH, help="동의어 사전 (none = 생략, 사전을 처음 만들 때만)")
    parser.add_argument("--categories", type=Path, default=CATEGORIES_PATH, help="분류표 (none = 생략)")
    parser.add_argument("--hypernyms", type=Path, default=HYPERNYMS_PATH, help="상위어 사전 (none = 생략)")
    parser.add_argument("--since", help="이 날짜 이후 기사만 (예: 2022-01-01)")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    try:
        if args.check is not None:
            explain(args.check, args.natn, load_geo(), load_exceptions(),
                    load_synonyms(require_dict(args.synonyms, "동의어", "synonyms")),
                    load_categories(require_dict(args.categories, "분류표", "categories")),
                    load_hypernyms(require_dict(args.hypernyms, "상위어", "hypernyms")))
            return
        if not args.input:
            parser.error("--input 또는 --check 중 하나가 필요합니다")
        _, report = run(args.input, args.output_dir, args.since, args.synonyms, args.categories, args.hypernyms)
    except DictionaryMissing as e:
        parser.exit(1, f"오류: {e}\n")
    print(f"기사 {report['articles']:,}건")
    print(f"키워드 종류: 원본 {report['raw']['kinds']:,} → 정제 {report['clean']['kinds']:,} → 지역 분리 후 주제어 {report['topic_only']['kinds']:,}")
    g = report["geo"]
    print(f"복합어 분리 {g['compound_split_kinds']:,}종 / 예외로 통째 유지 {g['kept_whole_kinds']:,}종 / 기사 국가 반복 {g['natn_repeated_rate']:.0%}")
    print(f"분야가 붙은 기사: {report['category']['articles_with_category']:,}건 / {report['articles']:,}건")
    print(f"미분류 주제어: {report['category']['unclassified_topics']:,}종 → {Path(args.output_dir) / 'unclassified_topics.csv'}")
    pr = report["product"]
    print(f"품목 연결(1·3단계): KOTRA HS {pr['KOTRA HS']:,}건 (6단위 {pr['HS 6단위']:,}건) / 산업분류만 {pr['산업분류만']:,}건 / 없음 {pr['없음']:,}건")
    print(f"결과: {Path(args.output_dir) / 'news_keywords_clean.csv'}")


if __name__ == "__main__":
    main()
