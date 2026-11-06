"""단일 컬렉션에서 요청 출처만 검색하고 고유 기사로 합칩니다."""
from dataclasses import dataclass, field
import math

from backend.app.schemas.rag import ALL_SOURCES, COLLECTION_BY_SOURCE, RagFailure
from backend.app.services.rag.documents import from_metadata
from backend.app.services.rag.runtime import RagDeadlineExceeded
from backend.app.services.rag.store import COLLECTIONS


@dataclass
class Candidate:
    article: object
    distance: float
    version: str
    chunks: dict
    category_match: bool = False


@dataclass
class SearchResult:
    candidates: list = field(default_factory=list)
    completed_sources: set = field(default_factory=set)
    failures: list = field(default_factory=list)
    evidence_missing: bool = False


def requested_sources(request, snapshot):
    if request.source_codes == 'ALL':
        return tuple(s for s in ALL_SOURCES if s in snapshot.sources)
    return request.source_codes


def filter_where(request, record_type='body', *, sources):
    c = request.conditions
    clauses = [{'record_type': record_type}, {'published_ymd': {'$gte': int(c.period.start.strftime('%Y%m%d'))}},
               {'published_ymd': {'$lte': int(c.period.end.strftime('%Y%m%d'))}}]
    clauses.append({'source_code': {'$in': [s.value for s in sources]}})
    return {'$and': clauses}


def search(request, snapshot, vocabulary, gateway, context, *, candidate_limit=None, keep_reserves=False, article_allowed=None):
    context.remaining()
    final_count = request.target_count
    if candidate_limit is not None and (type(candidate_limit) is not int or candidate_limit < 1 or
                                        (final_count is not None and candidate_limit < final_count)):
        raise ValueError('invalid_candidate_limit')
    target = candidate_limit or final_count
    requested = requested_sources(request, snapshot)
    if not requested:
        return SearchResult(failures=[RagFailure(stage='index', code='no_ready_sources')])
    vector = gateway.embed([vocabulary.query(request)], context)[0]
    if (len(vector) != snapshot.dimensions or not all(math.isfinite(float(v)) for v in vector)
            or not any(float(v) for v in vector)):
        raise ValueError('invalid_query_embedding')
    categories = vocabulary.query_categories(request)
    result = SearchResult()
    seen = set()
    accepted = {}
    columns = {}
    for name in COLLECTIONS:
        sources = {s for s in requested if COLLECTION_BY_SOURCE[s] == name}
        ready = sources & snapshot.sources
        for source in sources - ready:
            result.failures.append(RagFailure(stage='index', code='source_not_indexed', source_code=source))
        if not ready:
            continue
        try:
            columns[name] = [snapshot.collection(name), ready, False, -math.inf]
        except Exception as exc:
            for source in ready:
                result.failures.append(RagFailure(stage='index', code=type(exc).__name__, source_code=source))
    window = max(15, (target or 5) * 3)
    while columns:
        context.remaining()
        cutoff = sorted(c.distance for c in result.candidates)[target-1] if target is not None and len(result.candidates) >= target else math.inf
        for name, (col, ready, exhausted, frontier) in list(columns.items()):
            if exhausted or frontier > cutoff:
                continue
            try:
                total = col.count()
                if total == 0:
                    columns[name][2] = True
                    result.completed_sources.update(ready)
                    continue
                if window == max(15, (target or 5) * 3):
                    missing = col.get(where=filter_where(request, 'missing_body', sources=ready), include=['metadatas'])
                    for meta in missing['metadatas']:
                        unavailable = from_metadata(meta, '')
                        if (not set(request.conditions.blocked_topics) & set(unavailable.kw_topic)
                                and (article_allowed is None or article_allowed(unavailable))):
                            result.evidence_missing = True
                n = min(window, total)
                page = col.query(query_embeddings=[vector], n_results=n, where=filter_where(request, sources=ready),
                                 include=['documents', 'metadatas', 'distances'])
                context.remaining()
                ids = page['ids'][0]
                distances = page['distances'][0]
                if any(not math.isfinite(float(d)) for d in distances):
                    raise ValueError('invalid_distance')
                columns[name][3] = max(distances, default=math.inf)
                columns[name][2] = len(ids) < n or n == total
                for key, text, meta, distance in zip(ids, page['documents'][0], page['metadatas'][0], page['distances'][0]):
                    article = from_metadata(meta, text)
                    if article.source_code not in ready or COLLECTION_BY_SOURCE[article.source_code] != name:
                        raise ValueError('source_collection_mismatch')
                    if article.key in seen:
                        if article.key in accepted:
                            accepted[article.key].distance = min(accepted[article.key].distance, float(distance))
                        continue
                    seen.add(article.key)
                    c = request.conditions
                    if not c.period.start <= article.published_date <= c.period.end:
                        raise ValueError('period_filter_mismatch')
                    if article_allowed is not None and not article_allowed(article):
                        continue
                    if c.blocked_topics and not article.keywords_ready:
                        failure = RagFailure(stage='search', code='block_keywords_unverified', source_code=article.source_code)
                        if failure not in result.failures:
                            result.failures.append(failure)
                        context.event('block_keywords_unverified', article_key=article.identity)
                        continue
                    if set(c.blocked_topics) & set(article.kw_topic):
                        continue  # 기사 내용을 모델에 전달하기 전
                    rows = col.get(where={'article_key': article.identity}, include=['documents', 'metadatas'])
                    chunks = {i: d for i, d, m in sorted(zip(rows['ids'], rows['documents'], rows['metadatas']),
                              key=lambda r: r[2]['chunk']) if m['content_version'] == meta['content_version']}
                    if not chunks or not article.source_url:
                        result.evidence_missing = True
                        continue
                    candidate = Candidate(article, float(distance), meta['content_version'], chunks,
                                          bool(categories & set(article.categories)))
                    result.candidates.append(candidate)
                    accepted[article.key] = candidate
                result.completed_sources.update(ready)
                context.event('search', collection=name, fetched_chunks=len(ids), window=window)
            except RagDeadlineExceeded:
                raise
            except Exception as exc:
                context.remaining()
                result.completed_sources.difference_update(ready)
                for source in ready:
                    result.failures.append(RagFailure(stage='search', code=type(exc).__name__, source_code=source))
                del columns[name]
        cutoff = sorted(c.distance for c in result.candidates)[target-1] if target is not None and len(result.candidates) >= target else math.inf
        # 목표 수를 채웠어도 아직 보지 않은 더 좋은 후보·동점 후보를 확인합니다.
        if all(row[2] or row[3] > cutoff for row in columns.values()):
            break
        window *= 2
    result.candidates.sort(key=lambda x: (x.distance, not x.category_match, x.article.key))
    result.candidates = result.candidates[:target]
    context.event('candidate_pool', count=len(result.candidates), limit=target,
                  articles=[{'source_code': c.article.source_code.value,
                             'external_id': c.article.external_id, 'distance': c.distance}
                            for c in result.candidates])
    if not keep_reserves:
        result.candidates = result.candidates[:final_count]
    return result
