"""공통 요청→검색→질문별 근거 요약→응답. 호출자는 전체 종료 예외를 처리합니다."""
import hashlib
import json
from pathlib import Path

from pydantic import HttpUrl, TypeAdapter

from backend.app.schemas.rag import (ArticleEvidence, EvidenceSpan, RagFailure, RagRequest, RagResponse,
                                    SummarySentence, validate_response_for)
from backend.app.services.rag.retrieval import search, requested_sources
from backend.app.services.rag.runtime import RagDeadlineExceeded, RagInferenceUnavailable
from backend.app.services.rag.store import atomic_json


def grounded_sentences(payload, candidate):
    if not isinstance(payload, dict) or set(payload) != {'sentences'} or not isinstance(payload['sentences'], list):
        raise ValueError('invalid_summary_output')
    if len(payload['sentences']) > 2:
        raise ValueError('summary_sentence_limit')
    evidence, summaries = [], []
    for number, sentence in enumerate(payload['sentences']):
        if set(sentence) != {'text', 'quote', 'chunk_id'}:
            raise ValueError('invalid_sentence_fields')
        quote, chunk_id = sentence['quote'], sentence['chunk_id']
        if not isinstance(quote, str) or not quote.strip() or chunk_id not in candidate.chunks or quote not in candidate.chunks[chunk_id]:
            raise ValueError('quote_not_in_original_chunk')
        evidence_id = hashlib.sha256((candidate.article.identity + candidate.version + str(number) + quote).encode()).hexdigest()
        evidence.append(EvidenceSpan(evidence_id=evidence_id, chunk_id=chunk_id, quote=quote))
        summaries.append(SummarySentence(text=sentence['text'], evidence_ids=(evidence_id,)))
    return evidence, summaries


class RagService:
    def __init__(self, store, vocabulary, gateway, *, cache_dir=None, trace_dir=None,
                 candidate_limit=None, rerank=False, catalog=None):
        self.store, self.vocabulary, self.gateway = store, vocabulary, gateway
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.candidate_limit, self.rerank = candidate_limit, rerank
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
                return grounded_sentences(json.loads(path.read_text(encoding='utf-8')), candidate)
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
                result = grounded_sentences(payload, candidate)
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
        context.event('input')
        stage, articles, completed, failures = 'index', [], set(), []
        insufficient_evidence = False
        empty_summary_omitted = False
        snapshot = None
        found = None
        applied_sources = () if request.source_codes == 'ALL' else request.source_codes
        try:
            context.remaining()
            vocabulary = self.vocabulary
            if request.order_by == 'published_date_desc':
                stage = 'search'
                if self.catalog is None:
                    raise ValueError('latest_article_catalog_required')
                from backend.app.schemas.rag import ALL_SOURCES
                from backend.app.services.rag.retrieval import SearchResult
                applied_sources = ALL_SOURCES if request.source_codes == 'ALL' else request.source_codes
                found = SearchResult(candidates=self.catalog.iter_candidates(request, context),
                                     completed_sources=set(applied_sources))
            else:
                snapshot = self.store.snapshot()
                applied_sources = requested_sources(request, snapshot)
                context.event('index', generation=snapshot.generation)
                if vocabulary is None:
                    from backend.app.services.rag.vocabulary import Vocabulary
                    vocabulary = Vocabulary(**json.loads((self.store.root / 'generations' / snapshot.generation / 'vocabulary.json').read_text(encoding='utf-8')))
                stage = 'query_embedding'
                found = search(request, snapshot, vocabulary, self.gateway, context,
                               candidate_limit=self.candidate_limit, rerank=self.rerank)
            failures.extend(found.failures)
            completed = found.completed_sources
            insufficient_evidence = found.evidence_missing
            stage = 'summary'
            for candidate in found.candidates:
                context.remaining()
                try:
                    if request.order_by == 'published_date_desc':
                        article = candidate.article
                        if request.conditions.blocked_topics and not article.keywords_ready:
                            failures.append(RagFailure(stage='search', code='block_keywords_unverified',
                                                       source_code=article.source_code))
                            context.event('latest_filter_unverified', source_code=article.source_code.value,
                                          external_id=article.external_id)
                            continue
                        articles.append(ArticleEvidence(article_id=article.article_id,
                            source_code=article.source_code, external_id=article.external_id,
                            title=article.title, countries=article.countries,
                            published_date=article.published_date, source_url=article.source_url,
                            body_status='available' if article.body.strip() else 'missing',
                            content_version=candidate.version, evidence_role='unclassified',
                            reference_limitation='발행일 기준 최신 기사. 본문 미검수이며 답변 근거로 확정하지 않음.'))
                        context.event('latest_publication_selected', source_code=article.source_code.value,
                                      external_id=article.external_id, published_date=article.published_date.isoformat())
                        if request.target_count is not None and len(articles) >= request.target_count:
                            break
                        continue
                    evidence, summary = self._summary(request, candidate, context)
                    insufficient_evidence |= not summary
                    if not summary:
                        empty_summary_omitted = True
                        context.event('empty_summary_omitted',
                                      source_code=candidate.article.source_code.value,
                                      external_id=candidate.article.external_id)
                        continue
                    article = candidate.article
                    role, limitation = 'unclassified', None
                    if request.reference_scope == 'direct_and_context' or request.order_by == 'published_date_desc':
                        from backend.app.services.rag.retrieval import hs_match
                        outside = (request.conditions.geography.mode == 'selected' and not
                                   set(request.conditions.geography.codes) & set(article.countries))
                        outside |= bool(request.conditions.products) and not hs_match(request.conditions.products, article.products)
                        if outside and request.reference_scope == 'direct_and_context':
                            summary = [s.model_copy(update={'text': '참고(직접 답변 아님): ' + s.text})
                                       if not s.text.startswith('참고(직접 답변 아님):') else s for s in summary]
                            limitation = '국가·품목 태그가 질문 대상과 다르거나 미확정인 참고자료. 질문 대상의 사실로 전용하지 않음.'
                        context_count = sum(s.text.startswith('참고(직접 답변 아님):') for s in summary)
                        role = 'context_reference' if context_count == len(summary) else 'mixed' if context_count else 'direct_evidence'
                        if role in ('context_reference', 'mixed') and limitation is None:
                            limitation = '기사에 명시된 국가·품목·기간·정책 범위의 참고자료이며 질문 전체의 정답을 보장하지 않음.'
                    articles.append(ArticleEvidence(article_id=article.article_id, source_code=article.source_code,
                        external_id=article.external_id, title=article.title, countries=article.countries,
                        published_date=article.published_date, source_url=article.source_url,
                        body_status='available', content_version=candidate.version, evidence=evidence, summary=summary,
                        evidence_role=role, reference_limitation=limitation))
                except RagInferenceUnavailable:
                    failures.append(RagFailure(stage='summary', code='inference_unavailable'))
                    context.event('unreviewed_inference_unavailable')
                    break
                except RagDeadlineExceeded:
                    raise
                except Exception as exc:
                    failures.append(RagFailure(stage='summary', code=type(exc).__name__, source_code=candidate.article.source_code))
            if failures:
                status = 'partial' if articles else 'failed'
            elif insufficient_evidence and (not articles or empty_summary_omitted) and len(articles) < request.target_count:
                status = 'insufficient_evidence'
            elif not articles:
                status = 'no_results'
            elif request.target_count is not None and len(articles) < request.target_count:
                status = 'insufficient_results'
            else:
                status = 'ok'
            context.remaining()
        except RagDeadlineExceeded:
            context.event('global_deadline_exceeded')
            raise
        except Exception as exc:
            if context.deadline <= context.clock():
                context.event('global_deadline_exceeded')
                raise RagDeadlineExceeded('global_deadline_exceeded') from exc
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
            reason=None if status == 'ok' else status,
            reference_scope=request.reference_scope, order_by=request.order_by,
            latest_basis=('publication_date' if request.order_by == 'published_date_desc' else None),
            latest_scan_complete=(not failures if request.order_by == 'published_date_desc' else None))
        response = validate_response_for(request, response)
        context.remaining()  # 캐시·추적 저장과 응답 검증에 걸린 시간도 전체 상한에 포함합니다.
        return response


def rag_node(state, service, context):
    """합류 측이 이 patch만 적용합니다. 원래 State를 변경하지 않습니다."""
    response = service.run(state['rag_request'], context)
    return {'rag_result': response.model_dump(mode='json'), 'rag_trace': context.trace()}
