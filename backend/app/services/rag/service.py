"""공통 요청→검색→질문별 근거 요약→응답. 호출자는 전체 종료 예외를 처리합니다."""
import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from openai import APITimeoutError
from pydantic import HttpUrl, TypeAdapter

from backend.app.schemas.rag import (ArticleEvidence, ArticlePeriod, EvidenceSpan, RagFailure, RagRequest, RagResponse,
                                    SummarySentence, validate_response_for)
from backend.app.services.rag.retrieval import search, requested_sources
from backend.app.services.rag.runtime import RagDeadlineExceeded, RagInferenceUnavailable
from backend.app.services.rag.parallel_review import review_articles
from backend.app.services.rag.store import atomic_json


def latest_output(request, gateway, context):
    if request.answer_format == 'summary':
        return 'summary', True
    # These whole requests ask only for a publication-order list. For all other
    # wording the gateway determines whether a topic requires reading the body.
    if re.fullmatch(r'(?:최신|최근)\s*(?:기사|뉴스)(?:\s*목록)?(?:\s*\d+\s*(?:건|개))?'
                    r'(?:[을를만]?\s*(?:보여|알려)?\s*(?:줘|주세요))?[.!?]?', request.query.strip()):
        fmt, topical = 'list', False
    else:
        fmt, topical = gateway.latest_intent(request, context)
        fmt = request.answer_format or fmt
    c = request.conditions
    check_target = bool(c.products or c.geography.codes)
    return fmt, bool(topical or check_target or fmt == 'summary')


def grounded_sentences(payload, candidate, context=None):
    if not isinstance(payload, dict) or set(payload) != {'sentences'} or not isinstance(payload['sentences'], list):
        raise ValueError('invalid_summary_output')
    if len(payload['sentences']) > 2:
        raise ValueError('summary_sentence_limit')
    evidence, summaries = [], []
    invalid = None
    for number, sentence in enumerate(payload['sentences']):
        if set(sentence) - {'unverified_question_parts'} != {'text', 'quote', 'chunk_id'}:
            raise ValueError('invalid_sentence_fields')
        quote, chunk_id = sentence['quote'], sentence['chunk_id']
        try:
            if not isinstance(quote, str) or not quote.strip() or chunk_id not in candidate.chunks or quote not in candidate.chunks[chunk_id]:
                raise ValueError('quote_not_in_original_chunk')
            if hasattr(candidate.article, 'body'):
                quote = original_quote(quote, candidate.article)
        except ValueError as exc:
            invalid = exc
            if context:
                context.event('source_quote_omitted', article_key=candidate.article.identity,
                              sentence=number, reason=str(exc))
            continue
        evidence_id = hashlib.sha256((candidate.article.identity + candidate.version + str(number) + quote).encode()).hexdigest()
        evidence.append(EvidenceSpan(evidence_id=evidence_id, chunk_id=chunk_id, quote=quote))
        summaries.append(SummarySentence(text=sentence['text'], evidence_ids=(evidence_id,),
                                         unverified_question_parts=sentence.get('unverified_question_parts', ())))
    if invalid and not evidence:
        raise invalid
    return evidence, summaries


def original_quote(quote, article):
    """Restore whitespace lost in indexing without adding or changing any visible text."""
    if quote in article.body:
        return quote
    from backend.app.scheduler.jobs.news_embed import format_document
    header = format_document({'title':article.title, 'published_date':article.published_date,
                              'category':article.category, 'countries':article.countries}, '')
    quote = quote.removeprefix(header).strip()
    if quote and quote in article.body:
        return quote
    if quote:
        match = re.search(r'\s+'.join(re.escape(t) for t in quote.split()), article.body)
        if match:
            return match.group()
    raise ValueError('quote_not_in_stored_body')


def snapshot_originals(store, snapshot, catalog, candidates, context):
    from backend.app.services.rag.documents import ArticleDocument
    if not candidates:
        return {}
    try:
        originals = catalog.originals(candidates) if catalog else {}
    except (sqlite3.Error, ValueError) as exc:
        context.event('catalog_source_unavailable', error_type=type(exc).__name__)
        originals = {}
    needed = {c.article.key:c.version for c in candidates if c.article.key not in originals}
    if needed:
        path = store.root / 'generations' / snapshot.generation / 'articles.jsonl'
        if not path.is_file():
            return originals
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                context.remaining()
                data = json.loads(line)
                key = data['source_code'], data['external_id']
                if key in needed:
                    doc = ArticleDocument.model_validate(data)
                    if doc.version == needed[key]:
                        originals[key] = doc
                        del needed[key]
                        if not needed:
                            break
    return originals


def generation_vocabulary(store, snapshot):
    from backend.app.services.rag.vocabulary import Vocabulary
    path = store.root / 'generations' / snapshot.generation / 'vocabulary.json'
    return Vocabulary(**json.loads(path.read_text(encoding='utf-8')))


class RagService:
    def __init__(self, store, vocabulary, gateway, *, cache_dir=None, trace_dir=None,
                 catalog=None, article_allowed=None):
        self.store, self.vocabulary, self.gateway = store, vocabulary, gateway
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.article_allowed = article_allowed
        self.catalog = catalog

    def _summary(self, request, candidate, context):
        TypeAdapter(HttpUrl).validate_python(candidate.article.source_url)
        identity = {'request': request.cache_payload(), 'version': candidate.version,
                    'chunks': candidate.chunks, 'article': candidate.article.identity,
                    'model': self.gateway.model, 'prompt': self.gateway.prompt_version}
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / (digest + '.json') if self.cache_dir else None
        if path:
            try:
                result = grounded_sentences(json.loads(path.read_text(encoding='utf-8')), candidate, context)
                context.remaining()
                context.event('summary_cache_hit', article_key=candidate.article.identity, sentences=len(result[1]))
                return result
            except FileNotFoundError:
                pass
            except OSError:
                context.event('summary_cache_read_failed')
            except (ValueError, TypeError, KeyError):
                context.event('summary_cache_invalid')
        for attempt in range(2):
            context.remaining()
            try:
                payload = self.gateway.summarize(request, candidate, context)
                context.remaining()
                result = grounded_sentences(payload, candidate, context)
                context.remaining()
                if path:
                    try:
                        atomic_json(path, payload)
                    except OSError:
                        context.event('summary_cache_write_failed')
                return result
            except (ValueError, TypeError, KeyError):
                if attempt:
                    raise
                context.event('summary_output_retry')

    def run(self, request, context):
        request = RagRequest.model_validate(request)
        regular_chat = request.use_case == 'chat' and request.order_by == 'relevance'
        context.event('input')
        stage, articles, completed, failures = 'index', [], set(), []
        insufficient_evidence = False
        empty_summary_omitted = False
        snapshot = None
        found = None
        answer_format = request.answer_format or 'summary'
        review_body = True
        unreviewed_latest = False
        latest_period = None
        applied_sources = () if request.source_codes == 'ALL' else request.source_codes
        try:
            context.remaining()
            vocabulary = self.vocabulary
            search_request = request
            candidate_limit = None
            if request.order_by == 'published_date_desc':
                stage = 'search'
                period = request.conditions.period
                start = date.fromordinal(max(period.start.toordinal(), period.end.toordinal() - 13))
                latest_period = ArticlePeriod(start=start, end=period.end)
                search_request = request.model_copy(update={
                    'conditions':request.conditions.model_copy(update={'period':latest_period})})
                candidate_limit = 40
                answer_format, review_body = latest_output(request, self.gateway, context)
                context.event('latest_output', answer_format=answer_format, review_body=review_body)
            elif regular_chat:
                candidate_limit = 10
            snapshot = self.store.snapshot()
            applied_sources = requested_sources(request, snapshot)
            context.event('index', generation=snapshot.generation)
            if vocabulary is None:
                vocabulary = generation_vocabulary(self.store, snapshot)
            stage = 'query_embedding'
            found = search(search_request, snapshot, vocabulary, self.gateway, context,
                           candidate_limit=candidate_limit, keep_reserves=True, article_allowed=self.article_allowed)
            if latest_period is not None:
                found.candidates.sort(key=lambda c: (-c.article.published_date.toordinal(), c.article.key))
                unreviewed_latest = found.evidence_missing
                context.event('latest_candidate_scope', limit=40, retrieved_count=len(found.candidates),
                              period=latest_period.model_dump(mode='json'), scope='retrieved_candidates')
            failures.extend(found.failures)
            completed = found.completed_sources
            insufficient_evidence = found.evidence_missing
            stage = 'summary'
            originals = snapshot_originals(self.store, snapshot, self.catalog, found.candidates, context)
            parallel_summaries = {}
            if regular_chat:
                available = [c for c in found.candidates if c.article.key in originals]
                for candidate in available:
                    candidate.article = originals[candidate.article.key]
                results, stopped = review_articles(available,
                    lambda candidate, child: self._summary(request, candidate, child), context, request.target_count)
                for candidate, value, error in results:
                    if error is None:
                        parallel_summaries[candidate.article.key] = value
                    elif not isinstance(error, (RagDeadlineExceeded, APITimeoutError, TimeoutError, RagInferenceUnavailable)):
                        failures.append(RagFailure(stage='summary', code=type(error).__name__,
                                                   source_code=candidate.article.source_code))
                if stopped == 'timeout':
                    failures.append(RagFailure(stage='timeout', code='candidate_timeout'))
                elif stopped == 'inference_unavailable':
                    failures.append(RagFailure(stage='summary', code='inference_unavailable'))
                    context.event('unreviewed_inference_unavailable')
            for candidate in found.candidates:
                if not regular_chat:
                    context.remaining()
                try:
                    if candidate.article.key not in originals:
                        failures.append(RagFailure(stage='evidence', code='source_version_unavailable',
                                                   source_code=candidate.article.source_code))
                        context.event('source_version_unavailable', article_key=candidate.article.identity)
                        continue
                    if regular_chat and candidate.article.key not in parallel_summaries:
                        continue
                    candidate.article = originals[candidate.article.key]
                    article = candidate.article
                    if not review_body:
                        articles.append(ArticleEvidence(article_id=article.article_id,
                            source_code=article.source_code, external_id=article.external_id,
                            title=article.title, countries=article.countries,
                            published_date=article.published_date, source_url=article.source_url,
                            body_status='available' if article.body.strip() else 'missing',
                            content_version=candidate.version, evidence_role='unclassified',
                            reference_limitation='최근 14일의 벡터 검색 후보 최대 40건 안에서 발행일순으로 선택한 목록. 본문 미검수.'))
                        context.event('latest_publication_selected', source_code=article.source_code.value,
                                      external_id=article.external_id, published_date=article.published_date.isoformat())
                        if request.target_count is not None and len(articles) >= request.target_count:
                            break
                        continue
                    if regular_chat:
                        evidence, summary = parallel_summaries[candidate.article.key]
                    else:
                        context.event('summary_candidate', article_key=candidate.article.identity)
                        evidence, summary = self._summary(request, candidate, context)
                        context.remaining()
                    insufficient_evidence |= not summary
                    if not summary:
                        empty_summary_omitted = True
                        context.event('empty_summary_omitted',
                                      source_code=candidate.article.source_code.value,
                                      external_id=candidate.article.external_id)
                        continue
                    article = candidate.article
                    context_count = sum(s.text.startswith('참고(직접 답변 아님):') for s in summary)
                    role = 'context_reference' if context_count == len(summary) else 'mixed' if context_count else 'direct_evidence'
                    limitation = '원문이 설명하는 대상과 범위에서 참고할 자료입니다.' if context_count else None
                    articles.append(ArticleEvidence(article_id=article.article_id, source_code=article.source_code,
                        external_id=article.external_id, title=article.title, countries=article.countries,
                        published_date=article.published_date, source_url=article.source_url,
                        body_status='available', content_version=candidate.version,
                        evidence=evidence if answer_format == 'summary' else (),
                        summary=summary if answer_format == 'summary' else (),
                        evidence_role=role, reference_limitation=limitation))
                    context.event('article_returned', article_key=article.identity, role=role,
                                  evidence_ids=[e.evidence_id for e in evidence] if answer_format == 'summary' else [])
                    if request.target_count is not None and len(articles) >= request.target_count:
                        break
                except RagInferenceUnavailable:
                    failures.append(RagFailure(stage='summary', code='inference_unavailable'))
                    context.event('unreviewed_inference_unavailable')
                    break
                except RagDeadlineExceeded:
                    raise
                except Exception as exc:
                    if regular_chat and isinstance(exc, (APITimeoutError, TimeoutError)):
                        failures.append(RagFailure(stage='timeout', code='candidate_timeout',
                                                   source_code=candidate.article.source_code))
                        break
                    failures.append(RagFailure(stage='summary', code=type(exc).__name__, source_code=candidate.article.source_code))
            if failures:
                status = 'partial' if articles else 'failed'
            elif insufficient_evidence and (not articles or empty_summary_omitted) and (request.target_count is None or len(articles) < request.target_count):
                status = 'insufficient_evidence'
            elif not articles:
                status = 'no_results'
            elif request.target_count is not None and len(articles) < request.target_count:
                status = 'insufficient_results'
            else:
                status = 'ok'
            if not any(f.stage == 'timeout' for f in failures):
                context.remaining()
        except Exception as exc:
            if isinstance(exc, RagDeadlineExceeded) or context.work_deadline <= context.clock():
                context.event('global_deadline_exceeded')
                if not (regular_chat and articles):
                    raise RagDeadlineExceeded('global_deadline_exceeded') from exc
                failures.append(RagFailure(stage='timeout', code='global_deadline_exceeded'))
            else:
                failures.append(RagFailure(stage=stage, code=type(exc).__name__))
            status = 'partial' if articles else 'failed'
        finally:
            if found is not None and hasattr(found.candidates, 'close'):
                found.candidates.close()
            if snapshot is not None:
                try:
                    snapshot.client.close()
                except Exception as exc:
                    context.event('snapshot_close_failed', error_type=type(exc).__name__)
                    failures.append(RagFailure(stage='index', code='snapshot_close_failed'))
                    status = 'partial' if articles else 'failed'
            context.event('result', status=locals().get('status', 'terminated'), returned_count=len(articles),
                          failure_codes=[f.code for f in failures])
            if self.trace_dir:
                name = hashlib.sha256(context.trace_id.encode()).hexdigest()
                try:
                    atomic_json(self.trace_dir / (name + '.json'), context.trace())
                except OSError:
                    context.event('trace_write_failed')
        response = RagResponse(request_id=request.request_id, use_case=request.use_case, status=status,
            applied_conditions=request.conditions, requested_source_codes=request.source_codes,
            applied_source_codes=applied_sources, target_count=request.target_count, returned_count=len(articles),
            articles=articles, completed_sources=sorted(completed, key=lambda s: s.value), failures=failures,
            unverified_question_parts=RagResponse.common_unverified_parts(articles),
            reason=None if status == 'ok' else status,
            order_by=request.order_by, answer_format=answer_format,
            latest_basis=('publication_date' if request.order_by == 'published_date_desc' else None),
            latest_scope='retrieved_candidates' if latest_period is not None else None,
            latest_candidate_limit=40 if latest_period is not None else None,
            latest_period=latest_period,
            latest_scope_description=('요청 기준일을 포함한 최근 14일 내 벡터 검색 후보 최대 40건 안의 발행일순'
                                      if latest_period is not None else None),
            latest_scan_complete=(not failures and not unreviewed_latest if request.order_by == 'published_date_desc' else None))
        response = validate_response_for(request, response)
        # Finalization does no inference. A slow snapshot close or trace write must
        # not discard chat evidence already accepted before the work deadline.
        if not (regular_chat and articles):
            context.remaining(finalizing=True)
        return response


def rag_node(state, service, context):
    """합류 측이 이 patch만 적용합니다. 원래 State를 변경하지 않습니다."""
    response = service.run(state['rag_request'], context)
    return {'rag_result': response.model_dump(mode='json'), 'rag_trace': context.trace()}
