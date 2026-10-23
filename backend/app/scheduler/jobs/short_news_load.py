"""단신속보 CSV를 정제·마스킹하고, LLM으로 요약·키워드를 뽑아 MySQL과 Chroma에 적재합니다. (HS 코드 태깅은 하지 않기로 결정, 2026-10-23)

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.short_news_load [--csv <CSV>] [--limit 20]
--csv를 주지 않으면 shortnews_collect.py의 가장 최근 결과(shortnews_outputs/<실행시각>/shortnews_raw.csv)를 씁니다.

입력 CSV 컬럼: nat, nttSj(제목), kotraNewsUrl, othbcDt(발행일), nttSn(기사 ID), kbc(무역관), regn(권역),
본문은 nttCn(수집 원본 HTML) 또는 body(텍스트로 바꾼 본문) 중 하나. nttCn이면 HTML을 텍스트로 바꿔 씁니다.

1. 정제·마스킹 (body_masked)
   - HTML → 텍스트 (문단·줄바꿈 태그는 줄바꿈, &nbsp; 등 문자 참조 변환), \xa0·\r 정리, URL 제거, URL만 있던 '원문링크:' 같은 출처 표시 줄 삭제, 본문 첫 줄이 제목 반복이면 삭제
   - '문의 :' 뒤 연락처, 이메일, 전화번호를 [연락처]·[이메일]·[전화번호]로 가립니다. 공인 이름 등 기사 내용은 가리지 않습니다.
2. 분석 (기사당 LLM 1회, 결과는 shortnews_outputs/short_news_analysis.jsonl에 쌓아 재실행 시 이어서 함)
   - 3문장 이내 요약, 키워드 최대 5개
   - 기사 단위로 --workers개(기본 16)를 동시에 처리합니다. 실패한 기사는 캐시에 남지 않아 다음 실행에서 다시 분석합니다.
3. 키워드 정리: LLM 키워드를 해외시장뉴스와 같은 규칙(keyword_clean.process_article: 정제·지역 분리·동의어·상위어)으로
   주제어로 바꿉니다. 사전 파일(data/news_keywords/synonyms.csv 등)이 없으면 정제·지역 분리만 합니다.
   ponytail: 사전에 없는 말도 그대로 태그로 넣음. 최신 분류(keyword_topic·Chroma news_keyword)에서 고르는 방식은 후속 작업
   MySQL: article(category='short_news')에 없는 기사만 넣고, 같은 트랜잭션으로 article_tag에 주제어 행을 넣습니다.
   --retag면 이미 넣은 기사의 키워드 태그도 지금 사전 기준으로 다시 넣습니다 (사전을 바꾼 뒤).
   국가 태그는 country 테이블이 채워진 뒤 따로 추가합니다. 500건마다 커밋하므로 중간에 멈춰도 다시 실행하면 이어집니다.
4. Chroma short_news 컬렉션: DB에 있지만 컬렉션에 없는 기사만 추가합니다.
   임베딩 = 제목 + 요약 + 키워드, 문서 = 마스킹한 본문 원문, 메타데이터 = 기사 정보·요약·키워드.
- 실행마다 batch_run에 한 건 기록합니다 (job SHORT_NEWS_LOAD, 대상 article, 수신 = CSV 기사 수, 요청 = OpenAI 호출 수).
"""
import argparse
import csv
import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

import chromadb
from dotenv import dotenv_values
from openai import OpenAI
from sqlalchemy import delete, insert, select

from backend.app.db.batch_log import Source, track_batch
from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.news import Article, ArticleTag
from backend.app.scheduler.jobs import hs_code_embed as he  # 같은 임베딩 모델·Chroma 폴더를 씀
from backend.app.services.news_keywords import keyword_clean as kc

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
KEYWORD_MAX = 5
WORKERS, RETRIES = 16, 3  # 16개 동시 요청 ≈ OpenAI 한도(5,000 RPM·2M TPM)의 절반 이하
DB_BATCH, EMBED_BATCH = 500, 100
log = logging.getLogger("short_news_load")

EXTRACT_PROMPT = """너는 KOTRA 단신속보 기사를 분석한다. 다음 JSON 객체로만 답한다.
{"summary": "...", "keywords": ["..."]}
- summary: 기사 핵심을 한국어 3문장 이내로 요약한다. 주체·수치·시점을 살리고 기사에 없는 내용은 쓰지 않는다.
- keywords: 기사 주제를 나타내는 핵심어 최대 5개. 명사형, 각 20자 이내. 국가명과 '증가'·'발표' 같은 일반어는 넣지 않는다."""

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


def analyze(article, ask):
    """기사 하나 → (분석 결과, OpenAI 호출 수). 요약이 없으면 ValueError."""
    got = ask(EXTRACT_PROMPT, f"제목: {article['title']}\n본문:\n{article['body_masked'][:BODY_MAX]}")
    summary = got.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError(f"요약 없음: {got}")
    return ({"external_id": article["external_id"], "summary": summary.strip(),
             "keywords": str_list(got.get("keywords"), KEYWORD_MAX, 120)}, 1)


def load_cache(path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["external_id"]: r for r in map(json.loads, filter(str.strip, f))}


def analyze_all(articles, ask, cache_path, workers=WORKERS):
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
                result, used = analyze(article, ask)
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
    return [base | {"keyword_text": k} for k in result["topics"]]


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


def classify_keywords(results):
    """LLM 키워드 → 해외시장뉴스와 같은 규칙의 주제어(results의 "topics"). 주제어 종류 수."""
    for path in (kc.SYNONYMS_PATH, kc.HYPERNYMS_PATH):
        if not path.exists():
            log.warning(f"{path.name}이 없어 동의어·상위어 없이 정리합니다 (NEWS_KEYWORDS_USAGE.md로 사전을 만들면 반영)")
    geo, keep, syn, cat, hyp = kc.load_geo(), kc.load_exceptions(), kc.load_synonyms(), kc.load_categories(), kc.load_hypernyms()
    for r in results.values():
        r["topics"] = kc.process_article(",".join(r["keywords"]), "", geo, keep, syn, cat, hyp)["kw_topic"]
    return len({t for r in results.values() for t in r["topics"]})


def retag_keywords(engine, results):
    """이미 적재한 기사의 키워드 태그를 지금 사전 기준 주제어로 바꿉니다 (키워드 외 태그는 그대로). 바꾼 기사 수."""
    with session_scope(engine) as s:
        ids = dict(s.execute(select(Article.external_id, Article.article_id)
                             .where(Article.source_code == SOURCE.code)).all())
        todo = [(ids[e], r) for e, r in results.items() if e in ids]
        for i in range(0, len(todo), DB_BATCH):
            chunk = todo[i:i + DB_BATCH]
            s.execute(delete(ArticleTag).where(ArticleTag.article_id.in_([a for a, _ in chunk]),
                                               ArticleTag.keyword_text.is_not(None)))
            rows = [t for a, r in chunk for t in tag_rows(a, r) if t["keyword_text"]]
            if rows:
                s.execute(insert(ArticleTag), rows)
    return len(todo)


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
                          "kbc": a.get("kbc", ""), "summary": res["summary"], "keywords": ", ".join(res["topics"])})
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
        workers=WORKERS, retag=False):
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
            results, calls = analyze_all(articles, ask or openai_chat(), cache_path, workers)
            topics = classify_keywords(results)
            db = load_db(engine, articles, results)
            if retag:
                log.info(f"키워드 태그 다시 넣음: 기사 {retag_keywords(engine, results):,}건")
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
             f"·태그 {db['tags']:,} | 주제어 {topics:,}종 | Chroma 추가 {added:,} | 로그: {log_path}")
    return 1 if missing else 0


def main():
    parser = argparse.ArgumentParser(description="단신속보 CSV를 요약·키워드 태깅해 MySQL article·article_tag와 Chroma에 적재")
    parser.add_argument("--csv", type=Path, help="단신속보 CSV (기본: shortnews_collect.py의 가장 최근 shortnews_raw.csv)")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--limit", type=int, help="CSV 앞쪽(최신) N건만 처리 (시험용)")
    parser.add_argument("--workers", type=int, default=WORKERS, help="동시 LLM 요청 수")
    parser.add_argument("--retag", action="store_true", help="이미 넣은 기사의 키워드 태그도 지금 사전 기준으로 다시 넣음")
    args = parser.parse_args()
    raise SystemExit(run(args.csv, args.db_url, args.limit, workers=args.workers, retag=args.retag))


if __name__ == "__main__":
    main()
