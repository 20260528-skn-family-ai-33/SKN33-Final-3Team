"""단신속보 CSV를 정제·마스킹하고, LLM으로 요약·키워드·품목을 뽑아 HS 6단위 코드를 붙인 뒤 MySQL과 Chroma에 적재합니다.

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.short_news_load [--csv <CSV>] [--limit 20]
--csv를 주지 않으면 shortnews_collect.py의 가장 최근 결과(shortnews_outputs/<실행시각>/shortnews_raw.csv)를 씁니다.

입력 CSV 컬럼: nat, nttSj(제목), kotraNewsUrl, othbcDt(발행일), nttSn(기사 ID), kbc(무역관), regn(권역),
본문은 nttCn(수집 원본 HTML) 또는 body(텍스트로 바꾼 본문) 중 하나. nttCn이면 HTML을 텍스트로 바꿔 씁니다.

1. 정제·마스킹 (body_masked)
   - HTML → 텍스트 (문단·줄바꿈 태그는 줄바꿈, &nbsp; 등 문자 참조 변환), \\xa0·\\r 정리, URL 제거, URL만 있던 '원문링크:' 같은 출처 표시 줄 삭제, 본문 첫 줄이 제목 반복이면 삭제
   - '문의 :' 뒤 연락처, 이메일, 전화번호를 [연락처]·[이메일]·[전화번호]로 가립니다. 공인 이름 등 기사 내용은 가리지 않습니다.
2. 분석 (기사당 LLM 1~2회, 결과는 shortnews_outputs/short_news_analysis.jsonl에 쌓아 재실행 시 이어서 함)
   - 1차: 3문장 이내 요약, 키워드 최대 5개, 품목명 최대 5개
   - HS 후보: hs_code 최신 판의 6단위 품목명·원문 부분 일치 + 4단위 이름에 품목명이 있으면 그 아래 6단위
     + Chroma hs_code 컬렉션 유사도 상위 5개 (6단위)
   - 2차: LLM이 후보 안에서만 최대 3개를 고릅니다. 후보에 없는 코드는 버립니다 (LLM이 코드를 만들지 않음, FR-904-03).
   - 기사 단위로 --workers개(기본 16)를 동시에 처리합니다. 실패한 기사는 캐시에 남지 않아 다음 실행에서 다시 분석합니다.
3. MySQL: article(category='short_news')에 없는 기사만 넣고, 같은 트랜잭션으로 article_tag에 키워드·HS 행을 넣습니다.
   국가 태그는 country 테이블이 채워진 뒤 따로 추가합니다. 500건마다 커밋하므로 중간에 멈춰도 다시 실행하면 이어집니다.
4. Chroma short_news 컬렉션: DB에 있지만 컬렉션에 없는 기사만 추가합니다.
   임베딩 = 제목 + 요약 + 키워드, 문서 = 마스킹한 본문 원문, 메타데이터 = 기사 정보·요약·키워드·HS 코드.
- 실행마다 batch_run에 한 건 기록합니다 (job SHORT_NEWS_LOAD, 대상 article, 수신 = CSV 기사 수, 요청 = OpenAI 호출 수).
"""
import argparse
import csv
import html
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

import chromadb
from dotenv import dotenv_values
from openai import OpenAI
from sqlalchemy import func, insert, select

from backend.app.db.batch_log import Source, track_batch
from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.article import Article, ArticleTag
from backend.app.models.hs_code import HsCodeMst
from backend.app.scheduler.jobs import hs_code_embed as he

SOURCE = Source("KOTRA_SHORT_NEWS", "KOTRA 단신속보뉴스",
                {"provider": "KOTRA", "url": "https://www.data.go.kr/data/15122662/openapi.do", "dataset_id": "15122662",
                 "format": "json"})
JOB_NAME = "SHORT_NEWS_LOAD"
CATEGORY = "short_news"
COLLECTION = "short_news"
CHAT_MODEL = "gpt-4.1-mini"
OUT_DIR = Path(__file__).resolve().parents[1] / "collectors" / "shortnews" / "shortnews_outputs"
CACHE_PATH = OUT_DIR / "short_news_analysis.jsonl"
LOG_DIR = OUT_DIR / "logs"
REQUIRED = ("nttSn", "nttSj", "othbcDt", "kotraNewsUrl")  # 본문은 nttCn(HTML) 또는 body(텍스트)
HTML_BLOCKS = {"p", "div", "br", "li", "tr", "td", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
BODY_MAX = 4000      # LLM에 보내는 본문 앞부분 글자 수 (99%가 4,300자 이하)
KEYWORD_MAX, PRODUCT_MAX, HS_MAX = 5, 5, 3
SQL_MAX, VEC_K = 10, 5  # 품목명 하나당 이름 일치 후보 수, 유사도 후보 수
WORKERS, RETRIES = 16, 3  # 16개 동시 요청 ≈ OpenAI 한도(5,000 RPM·2M TPM)의 절반 이하
DB_BATCH, EMBED_BATCH = 500, 100
log = logging.getLogger("short_news_load")

EXTRACT_PROMPT = """너는 KOTRA 단신속보 기사를 분석한다. 다음 JSON 객체로만 답한다.
{"summary": "...", "keywords": ["..."], "products": ["..."]}
- summary: 기사 핵심을 한국어 3문장 이내로 요약한다. 주체·수치·시점을 살리고 기사에 없는 내용은 쓰지 않는다.
- keywords: 기사 주제를 나타내는 핵심어 최대 5개. 명사형, 각 20자 이내. 국가명과 '증가'·'발표' 같은 일반어는 넣지 않는다.
- products: 기사가 수출입·생산·가격·규제의 대상으로 직접 다루는 구체적 물품의 품목명 최대 5개.
  HS 품목표에서 찾기 쉬운 한국어 일반명으로 바꿔 쓴다 (예: LNG → 액화천연가스, 블레이드 배터리 → 리튬이온 축전지).
  넣지 않는 것: 서비스·인프라·건설사업·산업 분야(철도, 데이터센터, 전력망, 영화), 포괄 범주(농산물, 자동차 부품, 광물자원),
  배경으로만 언급된 물품. 금리·환율·재정·정책·사건 기사는 빈 목록이다. 대부분의 기사는 0~2개다."""

PICK_PROMPT = """기사 정보와 HS 6단위 후보 목록이 주어진다. 기사의 품목과 품목명이 직접 일치하는 후보 코드만 최대 3개 고른다.
기사가 그 물품 자체의 수출입·생산·가격·규제를 다룰 때만 고른다. 사업·산업에 쓰일 수 있는 자재나 연관 물품
(철도 건설 기사의 자갈·침목, 영화산업 기사의 영화필름, 주택 시장 기사의 조립식 건축물, 전기차 기사의 제동장치)은 고르지 않는다.
확실하지 않으면 빈 목록이다. 보통 0~1개다.
후보 목록에 없는 코드는 절대 쓰지 않는다. 다음 JSON 객체로만 답한다.
{"hs_codes": ["......"]}"""

URL = re.compile(r"https?:?/*\S*|www\.\S+")  # 'https//', 'https:'처럼 깨진 링크도 지움
SOURCE_LABEL = re.compile(r"[\W_]*(?:(?:원문|기사|자료원?|링크|출처|source)[\s\W_]*)+", re.I)  # URL을 지운 뒤 남은 출처 표시
TITLE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
CONTACT = re.compile(r"(문의\s*[:：]).*")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[-\x20]?\(?\d{1,4}\)?[-\x20]\d{3,4}[-\x20]\d{3,4}"
                   r"|\(?0\d{1,3}\)?[-\x20]\d{3,4}-\d{4})(?![\w.])")


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"short_news_load_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in HTML_BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in HTML_BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(markup):
    """수집 원본 nttCn(HTML)을 줄 단위 텍스트로. 태그가 아닌 '<공평경쟁…>' 같은 꺾쇠 글자는 남깁니다."""
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.parts).split("\n") if line.strip())


def latest_collect_csv(out_dir=OUT_DIR):
    """shortnews_collect.py가 만든 가장 최근 shortnews_raw.csv (실행시각 폴더 이름순)."""
    found = sorted(out_dir.glob("*/shortnews_raw.csv"))
    if not found:
        raise FileNotFoundError(f"{out_dir}에 수집 결과(shortnews_raw.csv)가 없습니다. 수집을 먼저 실행하거나 --csv를 주세요.")
    return found[-1]


def clean(body, title):
    lines = []
    for line in body.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = URL.sub("", line).rstrip()
        if line.strip() and not SOURCE_LABEL.fullmatch(line.strip()):
            lines.append(line)
    same = lambda a, b: "".join(a.split()) == "".join(b.split())  # 공백·줄바꿈 차이는 무시
    if lines and same(lines[0].strip().lstrip("□○ㅇ*-· "), TITLE_PREFIX.sub("", title)):
        lines = lines[1:]
    return "\n".join(lines).strip()


def mask(text):
    return PHONE.sub("[전화번호]", EMAIL.sub("[이메일]", CONTACT.sub(r"\1 [연락처]", text)))


def read_articles(path):
    """CSV → 기사 dict 목록. 필수값이 없거나 날짜가 잘못된 행은 건너뛰고, 같은 기사 ID는 처음 것만 씁니다."""
    csv.field_size_limit(2**31 - 1)  # nttCn HTML 한 칸이 기본 한도 131,072자를 넘음 (Windows C long 최대값)
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        missing = set(REQUIRED) - fields
        if missing or not fields & {"nttCn", "body"}:
            raise ValueError(f"{path}에 {', '.join(sorted(missing) or ['본문(nttCn 또는 body)'])} 컬럼이 없습니다.")
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]
    for r in rows:
        r["body"] = r.get("body") or html_to_text(r.get("nttCn", ""))
        for k in ("nttSj", "nat", "kbc", "regn"):  # 수집 원본 제목 3천여 건에 '&middot;' 같은 문자 참조가 있음
            r[k] = html.unescape(r.get(k, "")).strip()
    out, seen, skipped = [], set(), 0
    for r in rows:
        try:
            published = date.fromisoformat(r["othbcDt"])
        except ValueError:
            published = None
        if not all(r[k] for k in (*REQUIRED, "body")) or published is None or r["nttSn"] in seen:
            skipped += 1
            continue
        seen.add(r["nttSn"])
        body = mask(clean(r["body"], r["nttSj"])) or mask(r["body"])  # 정제 후 비면 원문을 마스킹만 해서 씀
        out.append({"external_id": r["nttSn"], "title": r["nttSj"][:600], "published_date": published,
                    "source_url": r["kotraNewsUrl"], "body_masked": body,
                    "nat": r.get("nat", ""), "regn": r.get("regn", ""), "kbc": r.get("kbc", "")})
    if skipped:
        log.warning(f"CSV {len(rows):,}행 중 {skipped:,}행 제외 (필수값 없음·날짜 오류·ID 중복)")
    return out


def openai_chat(env=ENV_PATH, model=CHAT_MODEL):
    key = {k.lower(): v for k, v in dotenv_values(env).items()}.get("openai_api_key", "").strip()
    if not key:
        raise ValueError(f"{env}에 OPENAI_API_KEY가 없습니다.")
    client = OpenAI(api_key=key)

    def ask(system, user):
        # max_tokens: 반복 출력으로 8만 자짜리 깨진 JSON이 나온 사례가 있어 응답 길이를 막음 (정상 응답은 수백 토큰)
        res = client.chat.completions.create(model=model, temperature=0, response_format={"type": "json_object"},
                                             max_tokens=1500,
                                             messages=[{"role": "system", "content": system},
                                                       {"role": "user", "content": user}])
        return json.loads(res.choices[0].message.content)
    return ask


def str_list(value, limit, max_len):
    """LLM이 준 목록에서 비어 있지 않은 문자열만 중복 없이 limit개까지."""
    items = [v.strip()[:max_len] for v in (value if isinstance(value, list) else []) if isinstance(v, str) and v.strip()]
    return list(dict.fromkeys(items))[:limit]


class HsMatcher:
    """hs_code 최신 판의 4·6단위 이름과 Chroma hs_code 컬렉션으로 품목명의 6단위 후보를 찾습니다."""

    def __init__(self, engine, hs_col, embed):
        with session_scope(engine) as s:
            # ponytail: 'HS2022' > 'HS2017' 문자열 비교. HS 판 이름 형식이 바뀌면 hb.hs_ver_for로 교체
            self.ver = s.scalar(select(func.max(HsCodeMst.hs_ver)))
            rows = s.execute(select(HsCodeMst.hs_code, HsCodeMst.name_ko, HsCodeMst.original_name_ko)
                             .where(HsCodeMst.hs_ver == self.ver, HsCodeMst.depth.in_((4, 6)))).all()
        self.names = {c: (n, o or "") for c, n, o in rows}
        self.six = [c for c in self.names if len(c) == 6]
        self.col, self.embed, self.lock = hs_col, embed, threading.Lock()

    def candidates(self, products):
        found = []
        for p in products:
            if len(p) < 2:
                continue
            hit = [c for c in self.six if p in self.names[c][0] or p in self.names[c][1]]
            hit += [c for c in self.six if c not in hit and p in self.names.get(c[:4], ("",))[0]]
            found += hit[:SQL_MAX]
        vectors = self.embed(products)
        with self.lock:  # 여러 스레드가 같은 컬렉션을 조회하므로 조회만 직렬화 (임베딩 호출은 병렬)
            res = self.col.query(query_embeddings=vectors, n_results=VEC_K, include=["metadatas"],
                                 where={"$and": [{"hs_ver": self.ver}, {"depth": 6}]})
        found += [m["hs_code"] for ms in res["metadatas"] for m in ms]
        return list(dict.fromkeys(found))

    def describe(self, code):
        parent = self.names.get(code[:4], ("",))[0][:80]
        return f"{code}: {self.names.get(code, ('',))[0]} (호 {code[:4]}: {parent})"


def analyze(article, ask, matcher):
    """기사 하나 → (분석 결과, OpenAI 호출 수). 요약이 없으면 ValueError."""
    got = ask(EXTRACT_PROMPT, f"제목: {article['title']}\n본문:\n{article['body_masked'][:BODY_MAX]}")
    summary = got.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError(f"요약 없음: {got}")
    keywords = str_list(got.get("keywords"), KEYWORD_MAX, 120)
    products = str_list(got.get("products"), PRODUCT_MAX, 50)
    codes, calls = [], 1
    if products:
        cands = matcher.candidates(products)
        calls += 1
        if cands:
            picked = ask(PICK_PROMPT, f"제목: {article['title']}\n요약: {summary.strip()}\n품목: {', '.join(products)}\n"
                                      f"후보:\n" + "\n".join(matcher.describe(c) for c in cands))
            calls += 1
            codes = [c for c in str_list(picked.get("hs_codes"), HS_MAX * 2, 10) if c in cands][:HS_MAX]
    return ({"external_id": article["external_id"], "summary": summary.strip(), "keywords": keywords,
             "products": products, "hs_codes": codes, "hs_ver": matcher.ver}, calls)


def load_cache(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["external_id"]: r for r in map(json.loads, filter(str.strip, f))}


def analyze_all(articles, ask, matcher, cache_path, workers=WORKERS):
    """캐시에 없는 기사만 분석해 캐시 파일 끝에 한 줄씩 덧붙입니다. ({external_id: 결과}, 호출 수)"""
    results = load_cache(cache_path)
    todo = [a for a in articles if a["external_id"] not in results]
    log.info(f"분석 대상 {len(articles):,}건 | 캐시 완료 {len(articles) - len(todo):,}건 | 요청 {len(todo):,}건")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    calls, failed, started = 0, 0, time.time()

    def work(article):
        n = 0
        for attempt in range(1, RETRIES + 1):
            try:
                result, used = analyze(article, ask, matcher)
                return result, n + used
            except Exception as e:  # 네트워크·형식 오류는 재시도, 끝내 실패하면 다음 실행에서 다시 분석
                n += 1
                if attempt == RETRIES:
                    log.warning(f"분석 실패 {article['external_id']}: {type(e).__name__}: {e}")
                    return None, n
                time.sleep(5 * attempt)

    with ThreadPoolExecutor(workers) as pool, open(cache_path, "a", encoding="utf-8") as f:
        for i, fut in enumerate(as_completed(pool.submit(work, a) for a in todo), 1):
            result, used = fut.result()
            calls += used
            if result is None:
                failed += 1
            else:
                results[result["external_id"]] = result
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                f.flush()
            if i % 200 == 0 or i == len(todo):
                left = (time.time() - started) / i * (len(todo) - i)
                log.info(f"분석 {i:,}/{len(todo):,} | 실패 {failed:,} | 남은 시간 약 {left / 60:.1f}분")
    return results, calls


def tag_rows(article_id, result):
    """article_tag 행. executemany는 첫 행의 컬럼으로 문장을 만들기 때문에 모든 행에 같은 컬럼을 채웁니다."""
    base = {"article_id": article_id, "country_code": None, "hs_code": None, "hs_ver": None, "keyword_text": None}
    return ([base | {"keyword_text": k} for k in result["keywords"]]
            + [base | {"hs_code": c, "hs_ver": result["hs_ver"]} for c in result["hs_codes"]])


def load_db(engine, articles, results):
    """분석이 끝났고 DB에 없는 기사만 article·article_tag에 넣습니다. {"inserted", "tags", "existing"}"""
    src = Article.source_code == SOURCE.code
    with session_scope(engine) as s:
        have = set(s.scalars(select(Article.external_id).where(src)))
        new = [a for a in articles if a["external_id"] in results and a["external_id"] not in have]
        tags = 0
        for i in range(0, len(new), DB_BATCH):
            chunk = new[i:i + DB_BATCH]
            s.execute(insert(Article), [{"source_code": SOURCE.code, "category": CATEGORY,
                                         **{k: a[k] for k in ("external_id", "title", "published_date", "source_url",
                                                              "body_masked")}} for a in chunk])
            ids = dict(s.execute(select(Article.external_id, Article.article_id).where(
                src, Article.external_id.in_([a["external_id"] for a in chunk]))).all())
            rows = [t for a in chunk for t in tag_rows(ids[a["external_id"]], results[a["external_id"]])]
            if rows:
                s.execute(insert(ArticleTag), rows)
            s.commit()  # 기사와 태그를 함께 커밋. 멈춰도 다음 실행은 남은 기사부터
            tags += len(rows)
            log.info(f"DB 적재 {min(i + DB_BATCH, len(new)):,}/{len(new):,}")
    return {"inserted": len(new), "tags": tags, "existing": len(have)}


def get_news_collection(client, model=he.MODEL):
    col = client.get_or_create_collection(COLLECTION, embedding_function=None,
                                          metadata={"hnsw:space": "cosine", "embedding_model": model})
    if (col.metadata or {}).get("embedding_model") != model:
        raise ValueError(f"컬렉션 {COLLECTION}의 임베딩 모델이 {(col.metadata or {}).get('embedding_model')}입니다 (현재 {model}).")
    return col


def sync_chroma(engine, articles, results, col, embed):
    """DB에 있지만 컬렉션에 없는 기사만 추가합니다. 추가한 수."""
    extra = {a["external_id"]: a for a in articles}
    with session_scope(engine) as s:
        rows = s.execute(select(Article.article_id, Article.external_id, Article.title, Article.published_date,
                                Article.source_url, Article.body_masked)
                         .where(Article.source_code == SOURCE.code)).all()
    have = set(col.get(include=[])["ids"])
    todo = [r for r in rows if f"{SOURCE.code}:{r.external_id}" not in have and r.external_id in results]
    for i in range(0, len(todo), EMBED_BATCH):
        chunk = todo[i:i + EMBED_BATCH]
        texts, metas = [], []
        for r in chunk:
            res, a = results[r.external_id], extra.get(r.external_id, {})
            texts.append(f"{r.title}\n{res['summary']}\n키워드: {', '.join(res['keywords'])}")
            metas.append({"article_id": r.article_id, "external_id": r.external_id, "source_code": SOURCE.code,
                          "title": r.title, "published_date": r.published_date.isoformat(),
                          "published_ymd": int(r.published_date.strftime("%Y%m%d")),  # 기간 필터($gte·$lte)용
                          "source_url": r.source_url, "nat": a.get("nat", ""), "regn": a.get("regn", ""),
                          "kbc": a.get("kbc", ""), "summary": res["summary"], "keywords": ", ".join(res["keywords"]),
                          "hs_codes": ", ".join(res["hs_codes"]), "hs_ver": res["hs_ver"]})
        col.add(ids=[f"{SOURCE.code}:{r.external_id}" for r in chunk], documents=[r.body_masked for r in chunk],
                metadatas=metas, embeddings=embed(texts))
        log.info(f"Chroma 추가 {min(i + EMBED_BATCH, len(todo)):,}/{len(todo):,}")
    return len(todo)


def search(query, n=5, where=None, client=None, embed=None):
    """질의와 가까운 단신속보 [(id, 거리, 메타데이터)]. where 예: {"nat": "중국"}"""
    col = get_news_collection(client or chromadb.PersistentClient(path=str(he.CHROMA_PATH)))
    res = col.query(query_embeddings=(embed or he.openai_embed())([query]), n_results=n, where=where,
                    include=["metadatas", "distances"])
    return list(zip(res["ids"][0], res["distances"][0], res["metadatas"][0]))


def run(csv_path=None, db_url=None, limit=None, client=None, ask=None, embed=None, log_dir=LOG_DIR, cache_path=CACHE_PATH,
        workers=WORKERS):
    """성공 0, 실패 1. client·ask·embed는 테스트에서 바꿔 넣습니다. limit은 CSV 앞쪽(최신) N건만 처리합니다."""
    log_path = setup_log(log_dir)
    engine = None
    try:
        csv_path = csv_path or latest_collect_csv()
        log.info(f"단신속보 적재 시작 | {csv_path} | 모델 {CHAT_MODEL}·{he.MODEL}")
        articles = read_articles(csv_path)[:limit]
        engine = make_engine(db_url)
        client = client or chromadb.PersistentClient(path=str(he.CHROMA_PATH))
        embed = embed or he.openai_embed()
        dates = [a["published_date"] for a in articles]
        with track_batch(SOURCE, JOB_NAME, "article", min(dates), max(dates), engine=engine, logger=log) as batch:
            batch.received_count = len(articles)
            matcher = HsMatcher(engine, he.get_collection(client), embed)
            results, calls = analyze_all(articles, ask or openai_chat(), matcher, cache_path, workers)
            db = load_db(engine, articles, results)
            added = sync_chroma(engine, articles, results, get_news_collection(client), embed)
            batch.request_count = calls + -(-added // EMBED_BATCH)
            missing = len(articles) - sum(a["external_id"] in results for a in articles)
            if missing:
                batch.fail()  # 분석 실패 기사가 있으면 실패로 기록 (다음 실행에서 이어서 처리)
    except Exception:
        log.exception(f"실패: 단신속보 적재 중 오류 (커밋한 배치는 남고 다음 실행에서 이어서 처리) | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    log.info(f"완료: 기사 {len(articles):,}건 | 분석 누락 {missing:,} | DB 신규 {db['inserted']:,}·기존 {db['existing']:,}"
             f"·태그 {db['tags']:,} | Chroma 추가 {added:,} | 로그: {log_path}")
    return 1 if missing else 0


def main():
    parser = argparse.ArgumentParser(description="단신속보 CSV를 요약·키워드·HS 태깅해 MySQL article·article_tag와 Chroma에 적재")
    parser.add_argument("--csv", type=Path, help="단신속보 CSV (기본: shortnews_collect.py의 가장 최근 shortnews_raw.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--limit", type=int, help="CSV 앞쪽(최신) N건만 처리 (시험용)")
    parser.add_argument("--workers", type=int, default=WORKERS, help="동시 LLM 요청 수")
    args = parser.parse_args()
    raise SystemExit(run(args.csv, args.db_url, args.limit, workers=args.workers))


if __name__ == "__main__":
    main()
