"""article 테이블에 있지만 Chroma(vector DB)의 kotra_article 컬렉션에 없는 기사만 조각으로 나눠 임베딩해 추가합니다.
news_keyword 컬렉션에는 article_tag의 키워드(주제어)를 추가합니다 (비슷한 키워드 찾기용).

실행 (저장소 루트에서): python -m backend.app.scheduler.jobs.news_embed [--rebuild] [--dry-run] [--keywords-only]
--keywords-only는 news_keyword 컬렉션(키워드)만 넣고 기사 본문 조각(kotra_article)은 건드리지 않습니다 (비용 거의 없음).
먼저 news_load로 MySQL에 기사를 넣어야 합니다. --dry-run은 OpenAI를 부르지 않고 새로 넣을 조각 수·글자 수만 셉니다.

- 조각: 본문(body_masked, 이메일·전화번호 가림)을 문단 단위로 약 CHUNK자씩, 앞 조각 끝 OVERLAP자를 겹쳐 나눕니다.
  조각마다 머리줄을 붙여 조각만 봐도 어떤 기사인지 알 수 있게 합니다.
    [2022-01-03 | CZ | 외부전문가 기고] [기고] 체코에서 거주용 부동산 구매정보
    필자는 2010년부터 체코에서 …
- id는 '<article_id>:<조각 번호>', 메타데이터는 article_id·external_id·source_code·title·published_date(YYYY-MM-DD)·
  published_ymd(정수, 기간 필터용)·category·countries·keywords(목록, $contains 필터용)·chunk·body_hash입니다.
- 이미 있는 기사는 다시 임베딩하지 않습니다. 본문·제목이 바뀐 기사(body_hash가 다름)만 그 기사의 조각을 지우고 다시 넣습니다.
  실패한 배치는 컬렉션에 들어가지 않으므로 다음 실행에서 다시 처리됩니다.
- 임베딩 모델: text-embedding-3-large (hs_code_embed와 같은 모델, 3,072차원).
  모델을 바꾸면 차원이 달라지므로 --rebuild로 컬렉션을 새로 만듭니다.
- 저장 위치: 저장소 루트 chroma_data/ (git 제외, hs_code 컬렉션과 같은 폴더). 검색은 search()·search_keywords()를 씁니다.
"""
import argparse
import hashlib
import logging
from datetime import datetime
from pathlib import Path

import chromadb
from dotenv import dotenv_values
from openai import OpenAI
from sqlalchemy import select

from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.news import Article, ArticleTag
from backend.app.scheduler.jobs.news_load import keyword_article_counts

MODEL = "text-embedding-3-large"
ARTICLE_COLLECTION = "kotra_article"
KEYWORD_COLLECTION = "news_keyword"
CHROMA_PATH = Path(__file__).resolve().parents[4] / "chroma_data"
LOG_DIR = Path(__file__).resolve().parents[4] / "data" / "news" / "logs"
BATCH = 100  # 조각 하나가 최대 약 1천 토큰이라 한 요청이 OpenAI 요청당 토큰 한도(30만)를 넘지 않게
KEYWORD_BATCH = 500
CHUNK, OVERLAP = 1000, 150
log = logging.getLogger("news_embed")


def setup_log(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"news_embed_{datetime.now():%Y%m%d_%H%M%S}.log"
    log.setLevel(logging.INFO)
    for h in log.handlers:
        h.close()
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        log.addHandler(h)
    return path


def read_rows(engine):
    """기사 목록 [{article_id, …, countries, keywords}]. 국가·키워드는 article_tag에서 모읍니다."""
    with session_scope(engine) as s:
        cols = (Article.article_id, Article.external_id, Article.source_code, Article.title, Article.published_date,
                Article.category, Article.body_masked)
        rows = {r[0]: dict(zip(("article_id", "external_id", "source_code", "title", "published_date", "category",
                                "body_masked"), r), countries=[], keywords=[]) for r in s.execute(select(*cols))}
        for article_id, country, keyword in s.execute(
                select(ArticleTag.article_id, ArticleTag.country_code, ArticleTag.keyword_text)
                .order_by(ArticleTag.article_id, ArticleTag.tag_id)):
            if article_id in rows:
                if country:
                    rows[article_id]["countries"].append(country)
                if keyword:
                    rows[article_id]["keywords"].append(keyword)
    return list(rows.values())


def chunk_text(text, size=CHUNK, overlap=OVERLAP):
    """문단(줄) 단위로 size자 안팎씩 묶습니다. 문단 하나가 size보다 길면 그 문단만 글자 수로 자릅니다.
    다음 조각은 앞 조각의 끝 overlap자로 시작해 문맥이 끊기지 않게 합니다."""
    pieces = []
    for line in (text or "").split("\n"):
        line = line.strip()
        while len(line) > size:
            pieces.append(line[:size])
            line = line[size - overlap:]
        if line:
            pieces.append(line)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > size:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n" + p if overlap else p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def body_hash(r):
    return hashlib.sha256(f"{r['title']}\n{r['body_masked']}".encode("utf-8")).hexdigest()[:16]


def build_docs(row):
    """기사 한 건 → {id: (문서, 메타데이터)}."""
    day = str(row["published_date"])[:10]
    head = " | ".join(x for x in (day, ",".join(row["countries"]), row["category"]) if x)
    meta = {"article_id": row["article_id"], "external_id": row["external_id"], "source_code": row["source_code"],
            "title": row["title"], "published_date": day, "published_ymd": int(day.replace("-", "")),
            "body_hash": body_hash(row)}
    if row["category"]:
        meta["category"] = row["category"]
    meta |= {k: row[k] for k in ("countries", "keywords") if row[k]}  # 빈 목록은 메타데이터에 넣을 수 없음
    return {f"{row['article_id']}:{n}": (f"[{head}] {row['title']}\n{c}", meta | {"chunk": n})
            for n, c in enumerate(chunk_text(row["body_masked"]) or [row["title"]])}


def openai_embed(env=ENV_PATH):
    key = {k.lower(): v for k, v in dotenv_values(env).items()}.get("openai_api_key", "").strip()
    if not key:
        raise ValueError(f"{env}에 OPENAI_API_KEY가 없습니다.")
    client = OpenAI(api_key=key, max_retries=8)  # 처음 전체 임베딩은 요청이 많아 분당 한도(429)에 걸리면 기다렸다 재시도
    return lambda texts: [d.embedding for d in client.embeddings.create(model=MODEL, input=texts).data]


def get_collection(client, name, rebuild=False):
    if rebuild and name in [c.name for c in client.list_collections()]:
        client.delete_collection(name)
        log.info(f"컬렉션 {name}을 지우고 새로 만듭니다 (--rebuild).")
    col = client.get_or_create_collection(name, embedding_function=None,
                                          metadata={"hnsw:space": "cosine", "embedding_model": MODEL})
    if (col.metadata or {}).get("embedding_model") != MODEL:
        raise ValueError(f"컬렉션 {name}의 임베딩 모델이 {(col.metadata or {}).get('embedding_model')}입니다 "
                         f"(현재 {MODEL}). 차원이 달라 --rebuild로 다시 만들어야 합니다.")
    return col


def plan(rows, col):
    """(새로 넣을 {id: (문서, 메타)}, 지울 article_id 목록, 그대로 둘 기사 수)."""
    have = {}
    for m in col.get(include=["metadatas"])["metadatas"]:
        have[m["article_id"]] = m["body_hash"]
    todo, stale, same = {}, [], 0
    for r in rows:
        old = have.get(r["article_id"])
        if old == body_hash(r):
            same += 1
            continue
        if old is not None:
            stale.append(r["article_id"])
        todo |= build_docs(r)
    return todo, stale, same


def sync(rows, col, embed):
    """{"added": 추가 조각, "articles": 새로 넣은 기사, "changed": 다시 넣은 기사, "existing": 그대로 둔 기사}"""
    todo, stale, same = plan(rows, col)
    for article_id in stale:
        col.delete(where={"article_id": article_id})
    ids = list(todo)
    for i in range(0, len(ids), BATCH):
        part = ids[i:i + BATCH]
        texts = [todo[x][0] for x in part]
        col.add(ids=part, documents=texts, metadatas=[todo[x][1] for x in part], embeddings=embed(texts))
        log.info(f"기사 조각 임베딩 {min(i + BATCH, len(ids)):,}/{len(ids):,}")
    return {"added": len(ids), "articles": len({todo[x][1]["article_id"] for x in ids}) - len(stale),
            "changed": len(stale), "existing": same}


def keyword_counts(rows):
    """{키워드: 그 키워드가 붙은 기사 수}."""
    out = {}
    for r in rows:
        for k in set(r["keywords"]):
            out[k] = out.get(k, 0) + 1
    return out


def sync_keywords(counts, col, embed):
    """컬렉션에 없는 키워드만 추가합니다. id·문서 모두 키워드 글자 그대로, 메타데이터는 keyword·article_count.
    DB에서 사라진 키워드는 지우지 않습니다 (hs_code_embed와 같음, 최신으로 다시 만들려면 --rebuild)."""
    got = col.get(include=["metadatas"])
    have = dict(zip(got["ids"], got["metadatas"]))
    todo = [k for k in sorted(counts) if k not in have]
    # 이미 있는 키워드는 다시 임베딩하지 않고 기사 수만 최신으로 (메타데이터만 바꾸므로 OpenAI 호출 없음)
    changed = [k for k in sorted(counts) if k in have and have[k].get("article_count") != counts[k]]
    for i in range(0, len(changed), KEYWORD_BATCH):
        part = changed[i:i + KEYWORD_BATCH]
        col.update(ids=part, metadatas=[{"keyword": k, "article_count": counts[k]} for k in part])
    for i in range(0, len(todo), KEYWORD_BATCH):
        part = todo[i:i + KEYWORD_BATCH]
        col.add(ids=part, documents=part, metadatas=[{"keyword": k, "article_count": counts[k]} for k in part],
                embeddings=embed(part))
        log.info(f"키워드 임베딩 {min(i + KEYWORD_BATCH, len(todo)):,}/{len(todo):,}")
    return {"added": len(todo), "existing": len(counts) - len(todo), "count_updated": len(changed)}


def search(query, n=5, country=None, keyword=None, date_from=None, date_to=None, client=None, embed=None):
    """질의와 가까운 기사 조각 [(id, 거리, 문서, 메타데이터)]. 국가·키워드·기간(YYYY-MM-DD)으로 좁힐 수 있습니다."""
    col = get_collection(client or chromadb.PersistentClient(path=str(CHROMA_PATH)), ARTICLE_COLLECTION)
    where = [{k: {"$contains": v}} for k, v in (("countries", country), ("keywords", keyword)) if v]
    where += [{"published_ymd": {op: int(d.replace("-", ""))}} for op, d in (("$gte", date_from), ("$lte", date_to)) if d]
    res = col.query(query_embeddings=(embed or openai_embed())([query]), n_results=n,
                    where=(where[0] if len(where) == 1 else {"$and": where}) if where else None,
                    include=["documents", "metadatas", "distances"])
    return list(zip(res["ids"][0], res["distances"][0], res["documents"][0], res["metadatas"][0]))


def search_keywords(query, n=10, client=None, embed=None):
    """질의와 뜻이 가까운 키워드 [(키워드, 거리, 기사 수)]."""
    col = get_collection(client or chromadb.PersistentClient(path=str(CHROMA_PATH)), KEYWORD_COLLECTION)
    res = col.query(query_embeddings=(embed or openai_embed())([query]), n_results=n, include=["metadatas", "distances"])
    return [(i, d, m["article_count"]) for i, d, m in zip(res["ids"][0], res["distances"][0], res["metadatas"][0])]


def run(db_url=None, rebuild=False, dry_run=False, client=None, embed=None, log_dir=LOG_DIR, keywords_only=False):
    """성공 0, 실패 1. client·embed는 테스트에서 바꿔 넣습니다. keywords_only면 키워드 컬렉션만."""
    log_path = setup_log(log_dir)
    what = "키워드 임베딩(--keywords-only)" if keywords_only else "기사·키워드 임베딩"
    log.info(f"{what}(미등록 항목 추가) 시작 | 모델 {MODEL} | Chroma {CHROMA_PATH if client is None else '(주입)'}"
             f"{' | 미리 보기(--dry-run)' if dry_run else ''}")
    engine = None
    try:
        engine = make_engine(db_url)
        # 키워드만이면 본문(body_masked)은 읽지 않고 article_tag의 키워드별 기사 수만
        rows = None if keywords_only else read_rows(engine)
        counts = keyword_article_counts(engine) if keywords_only else keyword_counts(rows)
        client = client or chromadb.PersistentClient(path=str(CHROMA_PATH))
        articles = None if keywords_only else get_collection(client, ARTICLE_COLLECTION, rebuild)
        keywords = get_collection(client, KEYWORD_COLLECTION, rebuild)
        if dry_run and keywords_only:
            new_kw = set(counts) - set(keywords.get(include=[])["ids"])
            log.info(f"미리 보기(키워드만): 키워드 {len(new_kw):,}개 | 글자 {sum(map(len, new_kw)):,}자 | 로그: {log_path}")
            return 0
        if dry_run:
            todo, stale, same = plan(rows, articles)
            new_kw = set(counts) - set(keywords.get(include=[])["ids"])
            chars = sum(len(d) for d, _ in todo.values()) + sum(map(len, new_kw))
            log.info(f"미리 보기: 기사 {len(rows):,}건 중 새로/다시 넣을 기사 {len(rows) - same:,}건 (변경 {len(stale):,}) | "
                     f"조각 {len(todo):,}개 · 키워드 {len(new_kw):,}개 | 글자 {chars:,}자 | 로그: {log_path}")
            return 0
        embed = embed or openai_embed()
        result = None if keywords_only else sync(rows, articles, embed)
        kw = sync_keywords(counts, keywords, embed)
    except Exception:
        log.exception(f"실패: 임베딩 중 오류 (이미 추가한 배치는 남고 다음 실행에서 나머지를 추가함) | 로그: {log_path}")
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    if keywords_only:
        log.info(f"완료(키워드만): 키워드 추가 {kw['added']:,} · 기사 수 갱신 {kw['count_updated']:,} "
                 f"(컬렉션 {keywords.count():,}개) | 로그: {log_path}")
        return 0
    log.info(f"완료: DB 기사 {len(rows):,}건 | 새 기사 {result['articles']:,} · 변경 {result['changed']:,} · "
             f"그대로 {result['existing']:,} | 조각 추가 {result['added']:,} (컬렉션 {articles.count():,}개) | "
             f"키워드 추가 {kw['added']:,} · 기사 수 갱신 {kw['count_updated']:,} (컬렉션 {keywords.count():,}개) | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="article 테이블의 미등록 기사를 임베딩해 Chroma kotra_article 컬렉션에 추가")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--rebuild", action="store_true", help="컬렉션을 지우고 전부 다시 임베딩 (모델 변경 시)")
    parser.add_argument("--dry-run", action="store_true", help="OpenAI를 부르지 않고 새로 넣을 조각 수·글자 수만 출력")
    parser.add_argument("--keywords-only", action="store_true", help="키워드(news_keyword)만 임베딩, 기사 본문은 건드리지 않음")
    args = parser.parse_args()
    raise SystemExit(run(args.db_url, args.rebuild, args.dry_run, keywords_only=args.keywords_only))


if __name__ == "__main__":
    main()
