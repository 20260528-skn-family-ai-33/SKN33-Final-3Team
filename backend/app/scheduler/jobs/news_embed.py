"""기본 기사 실행·검색은 공통 세대 색인의 KOTRA 컬렉션을 사용합니다.

python -m backend.app.scheduler.jobs.news_embed [--rebuild] [--dry-run] [--keywords-only]
공통 본문 색인은 data/rag/index 아래에서 검증 후 교체하며 이전 세대를 보존합니다.
--keywords-only는 기존 전체 기사 태그 범위의 news_keyword 동기화를 유지합니다.
아래 구형 청크·동기화 함수와 명시적 client 주입 경로는 기준선 재구성과 호환 검증에 사용합니다.
본문 모델은 text-embedding-3-large, 3072차원이며 청킹은 1000자·150자 겹침입니다.
자세한 실행·검증 방법은 docs/SHORTNEWS_INGEST_HANDOFF.md를 따릅니다.
"""
import argparse
from backend.app.services.rag.resources import close_owned
import hashlib
import logging
from datetime import datetime
from copy import deepcopy
from pathlib import Path

import chromadb
from dotenv import dotenv_values
from openai import OpenAI
from sqlalchemy import select

from backend.app.db.session import ENV_PATH, make_engine, session_scope
from backend.app.models.news import Article, ArticleTag
from backend.app.scheduler.jobs.news_load import keyword_article_counts

from backend.app.services.news_collections import ARTICLE_COLLECTION

MODEL = "text-embedding-3-large"
KEYWORD_COLLECTION = "news_keyword"
CHROMA_PATH = Path(__file__).resolve().parents[4] / "chroma_data"
LOG_DIR = Path(__file__).resolve().parents[4] / "data" / "news" / "logs"
BATCH = 100  # 조각 하나가 최대 약 1천 토큰이라 한 요청이 OpenAI 요청당 토큰 한도(30만)를 넘지 않게
KEYWORD_BATCH = 500
GET_PAGE = 5000  # 컬렉션을 읽을 때 한 번에 가져오는 개수 (get_all)
CHUNK, OVERLAP = 1000, 150
DOCUMENT_FORMAT = "news-header-v1"
PRICE_PER_M = 0.13  # text-embedding-3-large 달러/100만 토큰 (비용 로그용 — OpenAI 가격이 바뀌면 고칠 것)
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


def format_document(row, chunk):
    """기존 공통 계약: 발행일·국가·원천 분류·제목 header + 정제 본문 청크."""
    day = str(row['published_date'])[:10]
    head = ' | '.join(x for x in (day, ','.join(row['countries']), row.get('category')) if x)
    return f"[{head}] {row['title']}\n{chunk}"


def build_docs(row):
    """기사 한 건 → {id: (문서, 메타데이터)}."""
    day = str(row["published_date"])[:10]
    meta = {"article_id": row["article_id"], "external_id": row["external_id"], "source_code": row["source_code"],
            "title": row["title"], "published_date": day, "published_ymd": int(day.replace("-", "")),
            "body_hash": body_hash(row)}
    if row["category"]:
        meta["category"] = row["category"]
    meta |= {k: row[k] for k in ("countries", "keywords") if row[k]}  # 빈 목록은 메타데이터에 넣을 수 없음
    return {f"{row['article_id']}:{n}": (format_document(row, c), meta | {"chunk": n})
            for n, c in enumerate(chunk_text(row["body_masked"]) or [row["title"]])}


class MissingEmbeddingKey(ValueError):
    pass


def openai_embed(env=ENV_PATH):
    import os
    key = (os.environ.get("OPENAI_API_KEY") or {k.lower(): v for k, v in dotenv_values(env).items()}.get("openai_api_key", "") or "").strip()
    if not key:
        raise MissingEmbeddingKey("OPENAI_API_KEY missing")
    client = OpenAI(api_key=key, max_retries=0)  # SDK 내부 재시도를 끄고 호출별 사용량·실패를 기록합니다.

    def embed(texts):
        embed.usage['model_calls'] += 1
        res = None
        try:
            res = client.embeddings.create(model=MODEL, input=texts)
        finally:
            from backend.app.services.news_usage import record_usage
            def counted(value):
                embed.usage['tokens'] += value['tokens'] or 0
                embed.usage['reported_responses'] += int(value['reported'])
            record_usage(counted, MODEL, res)
        return [d.embedding for d in sorted(res.data, key=lambda d: d.index)]
    embed.usage = {"tokens": 0, 'model_calls': 0, 'reported_responses': 0}
    embed.close = client.close
    return embed


def usage_text(embed):
    """지금까지 쓴 토큰과 예상 비용 (openai_embed만 셈, 테스트용 embed는 빈 문자열)."""
    usage = getattr(embed, "usage", None)
    if usage is None:
        return ""
    return f" | 토큰 {usage['tokens']:,} (약 ${usage['tokens'] / 1e6 * PRICE_PER_M:.2f})"


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


def get_all(col, include, page=GET_PAGE):
    """컬렉션 전체를 page개씩 나눠 읽습니다. 한 번에 읽으면 Chroma(SQLite)가 약 3만 2천 개부터
    'too many SQL variables'로 실패합니다 (total_article은 조각이 10만 개 넘게 들어감)."""
    out = {"ids": [], **{k: [] for k in include}}
    while True:
        got = col.get(include=include, limit=page, offset=len(out["ids"]))
        for k in out:
            out[k] += got[k]
        if len(got["ids"]) < page:
            return out


def plan(rows, col):
    """(새로 넣을 {id: (문서, 메타데이터)}, 지울 article_id 목록, 그대로 둘 기사 수, 메타데이터만 바꿀 {id: 메타데이터}).
    col이 None이면 빈 컬렉션으로 봄 (--rebuild --dry-run).
    - 조각 문서가 바뀌었거나(본문·제목·머리줄의 발행일·국가·분류) 조각이 일부만 있는 기사(중간에 멈춘 실행)는 지우고 다시 넣음
      — 임베딩은 머리줄까지 포함한 문서로 계산하므로 문서만 바꾸면 검색 벡터와 내용이 어긋남
    - 문서는 같고 키워드 등만 바뀐 기사는 임베딩 없이 메타데이터만 갱신. 없어진 키는 None으로 지움"""
    have = {}
    if col is not None:
        got = get_all(col, ["metadatas", "documents"])
        for i, m, d in zip(got["ids"], got["metadatas"], got["documents"]):
            have.setdefault(m["article_id"], {})[i] = (d, m)
    todo, stale, same, meta = {}, [], 0, {}
    for r in rows:
        old = have.get(r["article_id"])
        docs = build_docs(r)
        if old is None:
            todo |= docs
            continue
        if set(docs) != set(old) or any(old[i][0] != docs[i][0] or old[i][1].get("body_hash") != body_hash(r)
                                        for i in docs):
            stale.append(r["article_id"])
            todo |= docs
            continue
        same += 1
        for i, (_, m) in docs.items():
            prev = old[i][1]
            if m != prev:   # 빈 목록이 되어 빠진 키(countries·keywords·category)는 None으로 보내야 지워짐
                meta[i] = m | {k: None for k in prev if k not in m}
    return todo, stale, same, meta


def sync(rows, col, embed):
    """{"added": 추가 조각, "articles": 새로 넣은 기사, "changed": 다시 넣은 기사, "existing": 그대로 둔 기사,
    "meta_updated": 메타데이터만 갱신한 조각}"""
    todo, stale, same, meta = plan(rows, col)
    for article_id in stale:
        col.delete(where={"article_id": article_id})
    ids = list(meta)
    for i in range(0, len(ids), KEYWORD_BATCH):   # 임베딩 없이 메타데이터만 (OpenAI 호출 없음)
        col.update(ids=ids[i:i + KEYWORD_BATCH], metadatas=[meta[x] for x in ids[i:i + KEYWORD_BATCH]])
    ids = list(todo)
    for i in range(0, len(ids), BATCH):
        part = ids[i:i + BATCH]
        texts = [todo[x][0] for x in part]
        col.add(ids=part, documents=texts, metadatas=[todo[x][1] for x in part], embeddings=embed(texts))
        log.info(f"기사 조각 임베딩 {min(i + BATCH, len(ids)):,}/{len(ids):,}{usage_text(embed)}")
    return {"added": len(ids), "articles": len({todo[x][1]["article_id"] for x in ids}) - len(stale),
            "changed": len(stale), "existing": same, "meta_updated": len(meta)}


def keyword_counts(rows):
    """{키워드: 그 키워드가 붙은 기사 수}."""
    out = {}
    for r in rows:
        for k in set(r["keywords"]):
            out[k] = out.get(k, 0) + 1
    return out


def sync_keywords(counts, col, embed):
    """컬렉션을 DB 키워드와 맞춥니다. 없는 키워드만 임베딩해 추가하고, 기사 수가 바뀐 키워드는 메타데이터만 갱신하고,
    DB에서 사라진 키워드(사전이 바뀌어 동의어·하위어가 된 말)는 지웁니다 (추천에 예전 키워드가 남지 않게).
    id·문서 모두 키워드 글자 그대로, 메타데이터는 keyword·article_count. DB 키워드가 하나도 없으면 지우지 않습니다."""
    got = get_all(col, ["metadatas"])
    have = dict(zip(got["ids"], got["metadatas"]))
    gone = sorted(set(have) - set(counts)) if counts else []
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
        log.info(f"키워드 임베딩 {min(i + KEYWORD_BATCH, len(todo)):,}/{len(todo):,}{usage_text(embed)}")
    for i in range(0, len(gone), KEYWORD_BATCH):
        col.delete(ids=gone[i:i + KEYWORD_BATCH])
    return {"added": len(todo), "existing": len(counts) - len(todo), "count_updated": len(changed), "deleted": len(gone)}


def search(query, n=5, country=None, keyword=None, date_from=None, date_to=None, client=None, embed=None, *, root=None, source_codes='ALL'):
    """공통 활성 세대의 가까운 본문 조각을 반환합니다. 출처·국가·키워드·기간은 top-k 전에 적용합니다.

    client 명시 주입은 기존 컬렉션 호환 경로이며 소유권은 호출자에게 있습니다.
    사용자 차단, 실시간 DB 노출 확인, 관련성 판정과 기사 단위 중복 제거는 이 helper의 기능이 아닙니다.
    """
    from backend.app.schemas.rag import ArticleSource
    selected = None if source_codes == 'ALL' else tuple(dict.fromkeys(ArticleSource(s) for s in source_codes))
    if selected == ():
        raise ValueError('empty_source_scope')
    if client is not None and root is not None:
        raise ValueError('ambiguous_search_storage')
    snapshot = None
    provider = embed
    primary = None
    try:
        if client is None:
            from backend.app.services.rag.store import GenerationStore
            snapshot = GenerationStore(root).snapshot()
            if selected is not None and not set(selected) <= snapshot.sources:
                raise ValueError('requested_source_not_ready')
            selected = selected or tuple(sorted(snapshot.sources, key=lambda s: s.value))
            col = snapshot.collection(ARTICLE_COLLECTION)
        else:
            # 검색이 빈 컬렉션을 새로 만들지 않도록 읽기 전용 API만 사용합니다.
            col = client.get_collection(ARTICLE_COLLECTION, embedding_function=None)
            if (col.metadata or {}).get('embedding_model') != MODEL:
                raise ValueError('incompatible_article_embedding')
            selected = selected or tuple(ArticleSource)
        where = [{'source_code': {'$in': [s.value for s in selected]}}]
        where += [{k: {'$contains': v}} for k, v in (('countries', country), ('keywords', keyword)) if v]
        if snapshot is not None:
            where.append({'record_type': 'body'})
        where += [{'published_ymd': {op: int(d.replace('-', ''))}} for op, d in (('$gte', date_from), ('$lte', date_to)) if d]
        if provider is None:
            provider = openai_embed()
        res = col.query(query_embeddings=provider([query]), n_results=n,
                        where=where[0] if len(where) == 1 else {'$and': where},
                        include=['documents', 'metadatas', 'distances'])
        return list(zip(res['ids'][0], res['distances'][0], res['documents'][0], res['metadatas'][0]))
    except BaseException as error:
        primary = error
        raise
    finally:
        owned = [('snapshot', snapshot.client.close)] if snapshot is not None else []
        if embed is None and provider is not None:
            owned.append(('embedding_provider', provider.close))
        close_owned(owned, primary=primary)


def search_keywords(query, n=10, client=None, embed=None, *, root=None):
    """질의와 뜻이 가까운 키워드 [(키워드, 거리, 기사 수)]."""
    from backend.app.services.rag.store import persistent_client, resolve_keyword_root
    if client is not None and root is not None:
        raise ValueError('ambiguous_search_storage')
    owned = None
    provider = embed
    primary = None
    try:
        if client is None:
            path = resolve_keyword_root(root)
            if not (path / 'chroma.sqlite3').exists():
                raise ValueError('keyword_index_not_ready')
            owned = persistent_client(path)
            client = owned
        col = client.get_collection(KEYWORD_COLLECTION, embedding_function=None)
        if (col.metadata or {}).get('embedding_model') != MODEL:
            raise ValueError('incompatible_keyword_embedding')
        if provider is None:
            provider = openai_embed()
        res = col.query(query_embeddings=provider([query]), n_results=n, include=['metadatas', 'distances'])
        return [(i, d, m['article_count']) for i, d, m in zip(res['ids'][0], res['distances'][0], res['metadatas'][0])]
    except BaseException as error:
        primary = error
        raise
    finally:
        resources = [('keyword_client', owned.close)] if owned is not None else []
        if embed is None and provider is not None:
            resources.append(('embedding_provider', provider.close))
        close_owned(resources, primary=primary)




def sync_all_keywords(engine, path, embed, *, dry_run=False, rebuild=False):
    """모든 호출자가 동일 잠금 안에서 전체 DB 조회→키워드 동기화를 수행합니다."""
    from contextlib import nullcontext, closing
    from filelock import FileLock
    from sqlalchemy import inspect
    from backend.app.services.rag.store import persistent_client
    path = Path(path)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
    with nullcontext() if dry_run else FileLock(str(path) + '.lock'):
        if not {'article','article_tag'} <= set(inspect(engine).get_table_names()):
            raise ValueError('keyword_tables_missing')
        counts = keyword_article_counts(engine, require_schema=True)
        if dry_run:
            return {'status':'planned','distinct_keywords':len(counts),'deletion_planned':False}
        with closing(persistent_client(path)) as client:
            col = get_collection(client, KEYWORD_COLLECTION, rebuild=rebuild)
            return {**sync_keywords(counts,col,embed),'status':'complete','distinct_keywords':len(counts),
                    'empty_policy':'preserve_existing' if not counts else None}


def run_sources(sources, *, db_url=None, root=None, embed=None, dry_run=False, progress=None):
    """명시한 출처의 본문 + DB 전체 대표어 후보를 동기화합니다.

    complete/planned/partial/failed. body·keywords에 단계 상태와 건수/오류를 기록합니다.
    root 주입 시 본문 세대와 시험 키워드 저장소를 그 아래로 격리합니다.
    운영 기본과 명시 root 모두 본문 세대 root/keywords의 news_keyword를 사용합니다.
    dry_run은 저장소를 열거나 디렉터리를 만들지 않고 DB 기준 계획만 반환합니다.
    """
    from contextlib import nullcontext
    from filelock import FileLock
    from sqlalchemy import inspect
    from backend.app.schemas.rag import ArticleSource
    from backend.app.scheduler.jobs import rag_index
    from backend.app.services.rag.store import GenerationStore, resolve_root, resolve_keyword_root
    scope = tuple(dict.fromkeys(ArticleSource(s) for s in sources))
    if not scope:
        raise ValueError('empty_source_scope')
    selected = [s.value for s in scope]
    folder = resolve_root(root)
    keyword_path = resolve_keyword_root(root)
    result = {'status':'running', 'sources':selected,
              'body':{'status':'not_started', 'sources':selected, 'collection':ARTICLE_COLLECTION},
              'keywords':{'status':'not_started', 'scope':'all_database_sources', 'collection':KEYWORD_COLLECTION},
              'usage':{}, 'error_code':None}
    provider = embed
    stage = 'body'
    def measured(texts):
        nonlocal provider
        if provider is None:
            provider = openai_embed()  # 차이가 없는 실행에서는 키/유료 클라이언트도 만들지 않습니다.
        before = getattr(provider, 'usage', {}).get('tokens')
        before_reports = getattr(provider, 'usage', {}).get('reported_responses')
        try:
            return provider(texts)
        finally:
            after = getattr(provider, 'usage', {}).get('tokens')
            usage = result['usage'].setdefault(stage, {'tokens':0, 'estimated_usd':None, 'reported':False, 'model_calls':0, 'reported_responses':0, 'unreported_calls':0})
            usage['model_calls'] += 1
            after_reports = getattr(provider, 'usage', {}).get('reported_responses')
            reported = (before is not None and after is not None and
                        (after_reports > before_reports if before_reports is not None and after_reports is not None else True))
            usage['reported_responses' if reported else 'unreported_calls'] += 1
            usage['reported'] = usage['reported_responses'] > 0 and usage['unreported_calls'] == 0
            if reported:
                usage['tokens'] += after - before
                usage['estimated_usd'] = usage['tokens'] * PRICE_PER_M / 1e6
            checkpoint()
    def checkpoint():
        if progress is not None and not dry_run:
            progress(deepcopy(result))
    engine = None
    try:
        # 한 root의 두 호출은 body→keywords 전 구간 직렬화. publish.lock은 내부 세대 보호용입니다.
        if not dry_run:
            folder.mkdir(parents=True, exist_ok=True)
        with nullcontext() if dry_run else FileLock(str(folder / 'sync.lock')):
            engine = make_engine(db_url)
            result['body']['status'] = 'running'
            checkpoint()
            details = rag_index.refresh(engine, scope, store=GenerationStore(root), embed=measured, raw_market=False, dry_run=dry_run)
            result['body'].update(details, status='planned' if dry_run else 'complete', sources=selected)
            stage = 'keywords'
            result['keywords']['status'] = 'running'
            checkpoint()
            result['keywords'].update(sync_all_keywords(engine, keyword_path, measured, dry_run=dry_run))
            result['status'] = 'planned' if dry_run else 'complete'
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {'source_database_mismatch','source_database_unbound','single_collection_migration_required','article_document_migration_required','legacy_direct_index_migration_required','keyword_tables_missing','dictionary_changed_refresh_all_ready_sources'} else type(exc).__name__
        result[stage].update(status='failed', error_code=code)
        result['error_code'] = code
        result['status'] = 'partial' if result['body']['status'] in ('complete','planned') else 'failed'
    finally:
        for name, resource, method in [('database', engine, 'dispose'), ('embedding', provider if embed is None else None, 'close')]:
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception as exc:
                    result.setdefault('cleanup_errors', []).append({'resource':name,'error_type':type(exc).__name__})
                    if result['status'] in ('complete', 'planned'):
                        result.update(status='partial', error_code='resource_cleanup_failed')
    return result


def run(db_url=None, rebuild=False, dry_run=False, client=None, embed=None, log_dir=LOG_DIR, keywords_only=False, raw_market=None):
    """기본 세대 경로 유지. 주입 클라이언트는 빌려 쓰고 공통 키워드 잠금에 참여합니다."""
    from contextlib import nullcontext
    from filelock import FileLock
    try:
        lock = nullcontext()
        if client is not None and not dry_run:
            settings = client.get_settings()
            if settings.is_persistent:
                path = Path(settings.persist_directory)
                path.parent.mkdir(parents=True, exist_ok=True)
                lock = FileLock(str(path) + '.lock')
        with lock:
            return _run(db_url, rebuild, dry_run, client, embed, log_dir, keywords_only, raw_market)
    except Exception as exc:
        log.error('기사 색인 초기화·종료 실패: %s', type(exc).__name__)
        return 1


def _run(db_url=None, rebuild=False, dry_run=False, client=None, embed=None, log_dir=LOG_DIR, keywords_only=False, raw_market=None):
    """성공 0, 실패 1. client·embed는 테스트에서 바꿔 넣습니다. keywords_only면 키워드 컬렉션만."""
    if client is None and not keywords_only:
        from backend.app.scheduler.jobs import rag_index
        report = rag_index.run(['KOTRA_MARKET'], db_url=db_url, embed=embed, raw_market=raw_market, dry_run=dry_run)
        published = report.get('index', {}).get('status') == 'ready'
        if report['status'] not in ('complete', 'planned') and not published:
            return 1
        # 게시 후 정리·보고 실패라도 키워드는 갱신합니다. 전체 종료 코드는 실패를 유지합니다.
        keywords_code = run(db_url=db_url, dry_run=dry_run, embed=embed, log_dir=log_dir, keywords_only=True)
        return keywords_code if report['status'] in ('complete', 'planned') else 1
    if client is None and keywords_only:
        engine = None
        provider = embed
        def lazy(texts):
            nonlocal provider
            if provider is None:
                provider = openai_embed()
            return provider(texts)
        try:
            engine = make_engine(db_url)
            from contextlib import nullcontext
            from filelock import FileLock
            from backend.app.services.rag.store import GenerationStore, resolve_root, resolve_keyword_root
            from backend.app.db.news_lock import engine_identity
            folder = resolve_root()
            if not dry_run:
                folder.mkdir(parents=True, exist_ok=True)
            with nullcontext() if dry_run else FileLock(str(folder / 'sync.lock')):
                store = GenerationStore(folder)
                with nullcontext() if dry_run else store.lock:
                    store.bind_database(engine_identity(engine), dry_run=dry_run)
                sync_all_keywords(engine, resolve_keyword_root(folder), lazy, dry_run=dry_run, rebuild=rebuild)
            return 0
        except Exception:
            return 1
        finally:
            try:
                if engine is not None:
                    engine.dispose()
            finally:
                if embed is None and provider is not None:
                    provider.close()
    log_path = setup_log(log_dir)
    what = "키워드 임베딩(--keywords-only)" if keywords_only else "기사·키워드 임베딩"
    log.info(f"{what}(미등록 항목 추가) 시작 | 모델 {MODEL} | Chroma {CHROMA_PATH if client is None else '(주입)'}"
             f"{' | 미리 보기(--dry-run)' if dry_run else ''}")
    engine = None
    owned_embed = embed is None
    try:
        engine = make_engine(db_url)
        # 키워드만이면 본문(body_masked)은 읽지 않고 article_tag의 키워드별 기사 수만
        rows = None if keywords_only else read_rows(engine)
        counts = keyword_article_counts(engine) if keywords_only else keyword_counts(rows)
        client = client or chromadb.PersistentClient(path=str(CHROMA_PATH))
        # --rebuild --dry-run: 컬렉션을 지우지 않고, 지운 뒤(빈 컬렉션)를 가정해 셈
        fresh = rebuild and dry_run
        articles = None if keywords_only or fresh else get_collection(client, ARTICLE_COLLECTION, rebuild)
        keywords = None if fresh else get_collection(client, KEYWORD_COLLECTION, rebuild)
        if dry_run:
            have_kw = set() if keywords is None else set(get_all(keywords, [])["ids"])
            new_kw, gone_kw = set(counts) - have_kw, (have_kw - set(counts)) if counts else set()
            kw_txt = f"키워드 {len(new_kw):,}개 추가 · {len(gone_kw):,}개 삭제"
            if keywords_only:
                log.info(f"미리 보기(키워드만): {kw_txt} | 글자 {sum(map(len, new_kw)):,}자 | 로그: {log_path}")
                return 0
            todo, stale, same, meta = plan(rows, articles)
            chars = sum(len(d) for d, _ in todo.values()) + sum(map(len, new_kw))
            log.info(f"미리 보기: 기사 {len(rows):,}건 중 새로/다시 넣을 기사 {len(rows) - same:,}건 (변경 {len(stale):,}) | "
                     f"조각 {len(todo):,}개 · 메타데이터만 갱신 {len(meta):,}개 · {kw_txt} | 글자 {chars:,}자 | 로그: {log_path}")
            return 0
        embed = embed or openai_embed()
        result = None if keywords_only else sync(rows, articles, embed)
        kw = sync_keywords(counts, keywords, embed)
    except Exception:
        log.exception(f"실패: 임베딩 중 오류 (이미 추가한 배치는 남고 다음 실행에서 나머지를 추가함)"
                      f"{usage_text(embed)} | 로그: {log_path}")
        return 1
    finally:
        try:
            if engine is not None:
                engine.dispose()
        finally:
            if owned_embed and embed is not None:
                embed.close()
    if keywords_only:
        log.info(f"완료(키워드만): 키워드 추가 {kw['added']:,} · 기사 수 갱신 {kw['count_updated']:,} · 삭제 {kw['deleted']:,} "
                 f"(컬렉션 {keywords.count():,}개){usage_text(embed)} | 로그: {log_path}")
        return 0
    log.info(f"완료: DB 기사 {len(rows):,}건 | 새 기사 {result['articles']:,} · 변경 {result['changed']:,} · "
             f"그대로 {result['existing']:,} | 조각 추가 {result['added']:,} · 메타데이터 갱신 {result['meta_updated']:,} "
             f"(컬렉션 {articles.count():,}개) | 키워드 추가 {kw['added']:,} · 기사 수 갱신 {kw['count_updated']:,} · "
             f"삭제 {kw['deleted']:,} (컬렉션 {keywords.count():,}개){usage_text(embed)} | 로그: {log_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="article 테이블의 미등록 기사를 임베딩해 Chroma total_article 컬렉션에 추가")
    parser.add_argument("--db-url", help="SQLAlchemy DB URL (기본: .env의 MYSQL_* 값)")
    parser.add_argument("--rebuild", action="store_true", help="컬렉션을 지우고 전부 다시 임베딩 (모델 변경 시)")
    parser.add_argument("--dry-run", action="store_true", help="OpenAI를 부르지 않고 새로 넣을 조각 수·글자 수만 출력")
    parser.add_argument("--keywords-only", action="store_true", help="키워드(news_keyword)만 임베딩, 기사 본문은 건드리지 않음")
    args = parser.parse_args()
    raise SystemExit(run(args.db_url, args.rebuild, args.dry_run, keywords_only=args.keywords_only))


if __name__ == "__main__":
    main()
