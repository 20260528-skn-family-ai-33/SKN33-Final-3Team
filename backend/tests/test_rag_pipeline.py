import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from backend.app.schemas.rag import ALL_SOURCES, RagRequest
from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.service import RagService, rag_node
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.app.services.rag.vocabulary import Vocabulary
from backend.tests.rag_fixtures import request_payload


def vectors(texts):
    return [[b/255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def document(n=1, source=None, **changes):
    value = dict(article_id=n, source_code=source or ALL_SOURCES[(n-1)%4], external_id=str(n),
                 title=f'합성 기사 {n}', body='미국 배터리 수출에 관한 검증용 문장입니다.',
                 source_url=f'https://example.org/{n}', countries=['US'], published_date='2026-09-15',
                 kw_topic=['배터리'], kw_sub=['배터리'], keywords_ready=True)
    value.update(changes)
    return ArticleDocument(**value)


class FakeGateway:
    model = 'fake'
    prompt_version = 'test-v1'
    def __init__(self):
        self.embed_calls = 0
        self.summary_calls = []
        self.product_calls = []
        self.bad_quote = False
        self.fail = set()
        self.empty = False
    def embed(self, texts, context):
        context.remaining()
        self.embed_calls += 1
        return vectors(texts)
    def product_relevant(self, terms, chunks, context):
        self.product_calls.append(chunks)
        return any(any(t in chunk for t in terms) for chunk in chunks.values())
    def summarize(self, request, candidate, context):
        self.summary_calls.append(candidate.article.key)
        if candidate.article.key in self.fail:
            raise RuntimeError('private')
        if self.empty:
            return {'sentences': []}
        key = next(iter(candidate.chunks))
        return {'sentences': [{'text': '합성 기사 근거 요약.', 'chunk_id': key,
                              'quote': '없는 인용' if self.bad_quote else candidate.chunks[key]}]}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = GenerationStore(self.root/'index', dimensions=8)
        self.gateway = FakeGateway()
        self.vocabulary = Vocabulary(products={'HS2022:8507': ['배터리']})
        self.service = RagService(self.store, self.vocabulary, self.gateway,
                                  cache_dir=self.root/'cache', trace_dir=self.root/'traces')

    def publish(self, docs, **kwargs):
        return self.store.publish(docs, ALL_SOURCES, vectors, **kwargs)

    def call(self, payload=None):
        return self.service.run(RagRequest.model_validate(payload or request_payload()), ExecutionContext(time.monotonic()+30))

    def test_four_sources_shared_collection_and_one_query_embedding(self):
        report = self.publish([document(n) for n in range(1,9)])
        self.assertEqual(report['collections'], {'total_article':8})
        result = self.call()
        self.assertEqual((result.status, result.returned_count), ('ok',5))
        self.assertEqual(self.gateway.embed_calls, 1)
        self.assertEqual(len({a.key for a in result.articles}), 5)
        self.assertTrue(all(a.evidence and a.summary for a in result.articles))

    def test_reuse_and_failed_publish_keep_old_snapshot(self):
        docs = [document(n) for n in range(1,5)]
        first = self.publish(docs)
        old = self.store.snapshot()
        second = self.publish(docs)
        self.assertEqual((second['embedded'], second['reused']), (0,4))
        before = (self.store.root/'active.json').read_bytes()
        def fail(stage):
            if stage == 'before_publish':
                raise RuntimeError('injected')
        with self.assertRaises(RuntimeError):
            self.publish([document(1, body='변경 본문')], hook=fail)
        self.assertEqual((self.store.root/'active.json').read_bytes(), before)
        self.assertEqual(old.generation, first['generation'])
        self.assertEqual(old.collection('total_article').count(), 4)
        self.assertEqual(self.call().returned_count, 4)
        self.publish([document(1, body='변경 본문')])
        self.assertEqual(self.call().returned_count, 1)

    def test_embedding_failure_and_invalid_vector_never_publish(self):
        self.publish([document()])
        before = self.store.snapshot().generation
        for encoder in (lambda texts: [], lambda texts:[[float('nan')]*8 for t in texts],
                        lambda texts:[[1.0] for t in texts]):
            with self.assertRaises(ValueError):
                self.store.publish([document(body='new')], ALL_SOURCES, encoder)
            self.assertEqual(self.store.snapshot().generation, before)

    def test_incremental_source_refresh_preserves_other_sources(self):
        self.publish([document(n) for n in range(1,5)])
        self.store.publish([document(1, body='새 본문')], ['KOTRA_MARKET'], vectors)
        self.assertEqual(self.call().returned_count, 4)
        with self.assertRaises(ValueError):
            self.store.publish([document(2)], ['KOTRA_MARKET'], vectors)

    def test_local_seven_and_shortage(self):
        self.publish([document(n) for n in range(1,9)])
        self.assertEqual(self.call(request_payload(limit=7)).returned_count, 7)
        self.publish([document(n) for n in range(1,4)])
        result = self.call(request_payload(limit=7))
        self.assertEqual((result.status,result.returned_count), ('insufficient_results',3))

    def test_date_country_filter_and_explicit_all(self):
        self.publish([document(1), document(2,countries=['PL']), document(3,published_date='2026-08-31')])
        self.assertEqual(self.call().returned_count, 1)
        payload = request_payload()
        payload['conditions']['geography']['codes'] = ['US','PL']
        self.assertEqual(self.call(payload).returned_count, 2)
        payload['use_case'] = 'chat'
        payload['conditions']['geography'] = {'mode':'all'}
        self.assertEqual(self.call(payload).returned_count, 2)

    def test_blocking_precedes_product_and_summary_model_calls(self):
        self.publish([document(n) for n in range(1,5)])
        payload = request_payload()
        payload['conditions']['blocked_topics'] = ['배터리']
        payload['conditions']['products'] = [{'hs_code':'8507','hs_ver':'HS2022'}]
        result = self.call(payload)
        self.assertEqual(result.status, 'no_results')
        self.assertEqual(self.gateway.product_calls, [])
        self.assertEqual(self.gateway.summary_calls, [])

    def test_hs_strict_market_and_semantic_short_newspaper(self):
        self.publish([document(n) for n in range(1,5)])
        payload = request_payload()
        payload['conditions']['products'] = [{'hs_code':'8507','hs_ver':'HS2022'}]
        result = self.call(payload)
        self.assertEqual({a.source_code.value for a in result.articles}, {'KOTRA_SHORT_NEWS','HANKYUNG','FNNEWS'})
        self.assertEqual(len(self.gateway.product_calls), 3)
        payload['use_case'] = 'chat'
        result = self.call(payload)
        self.assertEqual({a.source_code.value for a in result.articles}, {'KOTRA_SHORT_NEWS','HANKYUNG','FNNEWS'})

    def test_result_shortage_refills_after_blocked_candidates(self):
        docs = [document(n, kw_topic=['차단'] if n<20 else ['배터리']) for n in range(1,29)]
        self.publish(docs)
        payload = request_payload(limit=7)
        payload['conditions']['blocked_topics'] = ['차단']
        self.assertEqual(self.call(payload).returned_count, 7)
        self.assertTrue(all(int(key[1]) >= 20 for key in self.gateway.summary_calls))

    def test_wrong_quote_retries_once_and_is_failed(self):
        self.publish([document()])
        self.gateway.bad_quote = True
        result = self.call()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(len(self.gateway.summary_calls), 2)
        self.assertEqual(result.failures[0].stage, 'summary')

    def test_partial_and_insufficient_evidence_are_distinct(self):
        self.publish([document(1), document(2)])
        self.gateway.fail = {document(2).key}
        self.assertEqual(self.call().status, 'partial')
        self.gateway.fail.clear()
        self.gateway.empty = True
        self.service.cache_dir = None
        self.assertEqual(self.call().status, 'insufficient_evidence')

    def test_missing_index_not_normal_no_results(self):
        self.assertEqual(self.call().status,'failed')
        self.store.publish([], ['KOTRA_MARKET'], vectors)
        self.assertEqual(self.call().status,'no_results')  # ALL은 준비된 출처만 요청
        self.publish([])
        self.assertEqual(self.call().status,'no_results')

    def test_missing_body_is_metadata_only_and_never_sent_to_model(self):
        for article in (document(body=''), document(body='본문 대신 저장된 요약', metadata_missing=['body_origin'])):
            report=self.publish([article])
            self.assertEqual(report['embedded'],0)
            self.assertEqual(report['reused'],0)
            result=self.call()
            self.assertEqual(result.status,'insufficient_evidence')
            self.assertEqual(self.gateway.summary_calls,[])

    def test_unindexed_source_is_partial_when_other_source_has_evidence(self):
        self.store.publish([document()],['KOTRA_MARKET'],vectors)
        payload = request_payload()
        payload['source_codes'] = [s.value for s in ALL_SOURCES]
        result=self.call(payload)
        self.assertEqual(result.status,'partial')
        self.assertEqual(result.returned_count,1)
        self.assertEqual(len(result.failures),3)

    def test_shared_external_id_in_different_publishers_is_not_deduplicated(self):
        self.publish([document(3,external_id='same'),document(4,external_id='same')])
        self.assertEqual(self.call().returned_count,2)

    def test_weekly_uses_five_default_and_seven_maximum(self):
        self.publish([document(n,published_date='2026-10-14') for n in range(1,9)])
        payload=request_payload('report',report={'report_type':'weekly','article_purpose':'weekly_news'})
        payload['conditions']['period']={'start':'2026-10-12','end':'2026-10-18'}
        self.assertEqual(self.call(payload).returned_count,5)
        payload['limit']=7
        self.assertEqual(self.call(payload).returned_count,7)

    def test_dictionary_change_cannot_mix_versions_between_sources(self):
        self.store.publish([document(n) for n in range(1,5)],ALL_SOURCES,vectors,vocabulary={'synonyms':{'EV':'전기차'}})
        before=self.store.snapshot().generation
        with self.assertRaisesRegex(ValueError,'dictionary_changed'):
            self.store.publish([document()],['KOTRA_MARKET'],vectors,vocabulary={'synonyms':{}})
        self.assertEqual(self.store.snapshot().generation,before)

    def test_request_pins_generation_during_publication(self):
        self.publish([document()])
        observed=[]
        def during_write(stage):
            if stage=='before_publish':
                observed.append(self.call().returned_count)
        self.publish([document(n) for n in range(1,6)],hook=during_write)
        self.assertEqual(observed,[1])
        self.assertEqual(self.call().returned_count,5)

    def test_node_only_returns_owned_fields_and_global_timeout_propagates(self):
        self.publish([document()])
        state = {'rag_request': request_payload(), 'other_result': {'normal':42}}
        before = copy.deepcopy(state)
        patch = rag_node(state,self.service,ExecutionContext(time.monotonic()+30))
        self.assertEqual(state,before)
        self.assertEqual(set(patch), {'rag_result','rag_trace'})
        with self.assertRaises(RagDeadlineExceeded):
            self.service.run(RagRequest.model_validate(request_payload()),ExecutionContext(time.monotonic()-1))

    def test_cache_is_separated_by_question_conditions_and_content(self):
        self.publish([document()])
        self.call(); self.call()
        self.assertEqual(len(self.gateway.summary_calls),1)
        self.call(request_payload(query='다른 질문'))
        self.assertEqual(len(self.gateway.summary_calls),2)
        self.publish([document(body='변경 본문')]);self.call()
        self.assertEqual(len(self.gateway.summary_calls),3)
        for path in (self.root/'traces').glob('*.json'):
            self.assertNotIn('변경 본문', path.read_text())


if __name__=='__main__': unittest.main()
