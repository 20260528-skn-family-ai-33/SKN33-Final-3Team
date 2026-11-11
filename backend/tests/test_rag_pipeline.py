import copy
import hashlib
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
import tempfile
import time
import unittest

from backend.app.schemas.rag import ALL_SOURCES, RagRequest
from backend.app.services.rag.documents import ArticleDocument
from backend.app.services.rag.store import GenerationStore
from backend.app.services.rag.service import RagService, rag_node
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.app.services.rag.parallel_review import review_articles
from backend.app.services.rag.retrieval import Candidate
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
        self.bad_quote = False
        self.fail = set()
        self.empty = False
    def embed(self, texts, context):
        context.remaining()
        self.embed_calls += 1
        return vectors(texts)
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

    def test_period_filter_and_cross_tag_body_candidates(self):
        self.publish([document(1), document(2,countries=['PL']), document(3,published_date='2026-08-31')])
        self.assertEqual(self.call().returned_count, 2)
        payload = request_payload()
        payload['conditions']['geography']['codes'] = ['US','PL']
        self.assertEqual(self.call(payload).returned_count, 2)
        payload['use_case'] = 'chat'
        payload['conditions']['geography'] = {'mode':'all'}
        self.assertEqual(self.call(payload).returned_count, 2)

    def test_blocking_precedes_summary_model_calls(self):
        self.publish([document(n) for n in range(1,5)])
        payload = request_payload()
        payload['conditions']['blocked_topics'] = ['배터리']
        payload['conditions']['products'] = [{'hs_code':'8507','hs_ver':'HS2022'}]
        result = self.call(payload)
        self.assertEqual(result.status, 'no_results')
        self.assertEqual(self.gateway.summary_calls, [])


    def test_omitted_scope_matches_question_body_across_tags_and_keeps_other_filters(self):
        text = '프랑스 수입사는 독일산 화장품을 독일에서 수입했다.'
        self.publish([document(1, source='KOTRA_MARKET',countries=['FR'],body=text,
                               products=[{'hs_code':'4202','hs_ver':'HS2022'}]),
                      document(2, source='KOTRA_MARKET',countries=['DE'],body='독일 영화가 상영됐다.'),
                      document(3, source='FNNEWS',countries=['FR'],body=text),
                      document(4, source='KOTRA_MARKET',countries=['FR'],body=text,published_date='2026-08-01'),
                      document(5, source='KOTRA_MARKET',countries=['FR'],body=text,kw_topic=['차단'])])
        seen=[]
        def judge(request,candidate,context):
            seen.append(candidate.article.external_id)
            if candidate.article.body != text:
                return {'sentences':[]}
            return {'sentences':[{'text':text,'quote':text,'chunk_id':next(iter(candidate.chunks))}]}
        self.gateway.summarize=judge
        p=request_payload('chat',query='독일산 화장품 수출을 설명해 줘',source_codes=['KOTRA_MARKET'])
        p['conditions'].update(geography={'mode':'selected','codes':['DE']},
            products=[{'hs_code':'3304','hs_ver':'HS2022'}],blocked_topics=['차단'])
        self.vocabulary.products['HS2022:3304']=['화장품']
        result=self.call(p)
        self.assertEqual(set(seen),{'1','2'})
        self.assertEqual([(a.external_id,a.evidence_role) for a in result.articles],[('1','direct_evidence')])
        self.assertEqual(result.articles[0].evidence[0].quote,text)

    def test_unready_keywords_only_prevent_use_when_block_topics_must_be_checked(self):
        self.publish([document(1,keywords_ready=False),document(2)])
        p=request_payload('chat')
        self.assertEqual(self.call(p).returned_count,2)
        self.gateway.summary_calls.clear()
        p['conditions']['blocked_topics']=['차단']
        result=self.call(p)
        self.assertEqual([a.external_id for a in result.articles],['2'])
        self.assertEqual((result.status,result.failures[0].code),('partial','block_keywords_unverified'))
        self.assertNotIn(document(1).key,self.gateway.summary_calls)

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
        result = self.call()
        self.assertEqual(result.status, 'insufficient_evidence')
        self.assertEqual(result.returned_count, 0)
        self.assertEqual(result.articles, ())

    def test_empty_summary_is_not_counted_as_an_evidence_article(self):
        self.publish([document(1), document(2)])
        original = self.gateway.summarize
        def summarize(request, candidate, context):
            if candidate.article.key == document(2).key:
                return {'sentences': []}
            return original(request, candidate, context)
        self.gateway.summarize = summarize
        result = self.call()
        self.assertEqual(result.status, 'insufficient_evidence')
        self.assertEqual(result.returned_count, 1)
        self.assertEqual(result.articles[0].external_id, '1')
        self.assertTrue(result.articles[0].summary)


    def test_report_only_question_parts_unconfirmed_by_all_returned_evidence(self):
        request = request_payload('chat', query='운송 경로와 최종 판매지를 설명해 줘')
        route = document(1, body='화물은 A항을 거쳐 B항으로 이동했다.')
        destination = document(2, body='최종 판매지는 C도시의 소매점이다.')
        def summarize(req, candidate, ctx):
            key = next(iter(candidate.chunks))
            missing = '최종 판매지' if candidate.article.key == route.key else '운송 경로'
            return {'sentences':[{'text':candidate.article.body, 'quote':candidate.article.body, 'chunk_id':key,
                                  'unverified_question_parts':[missing]}]}
        self.gateway.summarize = summarize
        self.publish([route])
        first = self.call(request)
        self.assertEqual(first.unverified_question_parts, ('최종 판매지',))
        self.assertEqual(self.call(request).unverified_question_parts, first.unverified_question_parts)
        self.publish([route, destination])
        both = self.call(request)
        self.assertEqual(both.unverified_question_parts, ())
        self.assertEqual(both.returned_count, 2)

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


class ParallelReviewTest(unittest.TestCase):
    def candidates(self):
        return [Candidate(document(i), float(i), 'synthetic', {}) for i in range(1, 11)]

    def test_three_workers_keep_rank_and_stop_at_five_without_extra_work(self):
        barrier, lock = threading.Barrier(3), threading.Lock()
        started, active, maximum = [], [0], [0]

        def review(candidate, child):
            number = candidate.article.article_id
            with lock:
                started.append(number)
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
            try:
                if number <= 3:
                    barrier.wait(timeout=2)
                if number == 1:
                    time.sleep(0.03)  # Later ranks finish before the first rank.
                return [], ['verified']
            finally:
                with lock:
                    active[0] -= 1

        context = ExecutionContext(time.monotonic()+3)
        results, reason = review_articles(self.candidates(), review, context, 5)
        self.assertEqual(maximum[0], 3)
        self.assertEqual(sorted(started), [1, 2, 3, 4, 5])
        self.assertEqual([c.article.article_id for c, value, error in results], [1, 2, 3, 4, 5])
        self.assertTrue(all(error is None for _, _, error in results))
        self.assertEqual(reason, 'target_met')

    def test_deadline_keeps_finished_later_ranks_and_does_not_wait_for_sdk_call(self):
        release, drained = threading.Event(), threading.Event()
        lock, live, late_steps = threading.Lock(), [0], []

        def review(candidate, child):
            number = candidate.article.article_id
            if number in (2, 3):
                return [], ['verified']
            with lock:
                live[0] += 1
            try:
                release.wait(timeout=2)  # Simulate a synchronous in-flight request.
                child.remaining()
                late_steps.append(number)
                return [], ['late']
            finally:
                with lock:
                    live[0] -= 1
                    if live[0] == 0:
                        drained.set()

        context = ExecutionContext(time.monotonic()+0.12)
        start = time.monotonic()
        try:
            results, reason = review_articles(self.candidates(), review, context, 5)
            self.assertLess(time.monotonic()-start, 0.5)
            self.assertEqual(reason, 'timeout')
            self.assertEqual([c.article.article_id for c, value, error in results if error is None], [2, 3])
            trace = copy.deepcopy(context.trace())
        finally:
            release.set()
        self.assertTrue(drained.wait(timeout=2))
        self.assertEqual(late_steps, [])
        self.assertEqual(context.events, trace['events'])

    def test_rejections_and_one_failed_worker_allow_all_ten_candidates(self):
        def review(candidate, child):
            number = candidate.article.article_id
            if number == 4:
                raise ValueError('invalid_quote')
            return [], ['verified'] if number >= 6 else []

        results, reason = review_articles(self.candidates(), review, ExecutionContext(time.monotonic()+3), 5)
        self.assertEqual(len(results), 10)
        self.assertEqual([c.article.article_id for c, value, error in results if value and value[1]], [6, 7, 8, 9, 10])
        self.assertEqual(sum(error is not None for _, _, error in results), 1)
        self.assertEqual(reason, 'target_met')


class SearchRecoveryTest(unittest.TestCase):
    setUp = PipelineTest.setUp
    publish = PipelineTest.publish
    call = PipelineTest.call

    def test_chat_refills_after_body_review_up_to_ten_unique_articles(self):
        # Tie distances so the fixed-width article keys give a predictable rank.
        def equal_vectors(texts):
            return [[1.0] + [0.0] * 7 for _ in texts]

        docs = [document(i, source='KOTRA_MARKET', external_id=f'{i:02}', countries=['FR'])
                for i in range(1, 18)]
        self.store.publish(docs, ALL_SOURCES, equal_vectors)
        self.gateway.embed = lambda texts, context: equal_vectors(texts)
        self.service.cache_dir = None
        summarize = self.gateway.summarize
        for first_supported, last_reviewed, expected_ids in (
            (1, 5, [1, 2, 3, 4, 5]),
            (6, 10, [6, 7, 8, 9, 10]),
            (8, 10, [8, 9, 10]),
            (10, 10, [10]),
            (11, 10, []),
        ):
            with self.subTest(first_supported=first_supported):
                reviewed = []

                def review(request, candidate, context):
                    reviewed.append(candidate.article.article_id)
                    if candidate.article.article_id < first_supported:
                        return {'sentences': []}
                    return summarize(request, candidate, context)

                with patch.object(self.gateway, 'summarize', side_effect=review):
                    result = self.call(request_payload('chat'))
                self.assertEqual(sorted(reviewed), list(range(1, last_reviewed + 1)))
                self.assertEqual([a.article_id for a in result.articles], expected_ids)
                self.assertEqual(result.status, 'ok' if len(expected_ids) == 5 else 'insufficient_evidence')

    def test_refill_preserves_global_ranking_even_after_target_is_met(self):
        docs = [document(n, source='KOTRA_MARKET', kw_topic=['차단'] if n < 15 else ['배터리'])
                for n in range(1, 21)]
        docs += [document(n, source='HANKYUNG') for n in range(100, 106)]
        def controlled_vectors(texts):
            values = []
            for text in texts:
                number = int(text.split('\n')[0].split()[-1])
                angle = number / 100 if number < 100 else 1.0 + number / 1000
                values.append([math.cos(angle), math.sin(angle), *([0.] * 6)])
            return values
        self.store.publish(docs, ALL_SOURCES, controlled_vectors)
        self.gateway.embed = lambda texts, context: [[1., *([0.] * 7)]]
        request = request_payload()
        request['conditions']['blocked_topics'] = ['차단']
        result = self.call(request)
        self.assertEqual([a.article_id for a in result.articles], list(range(15, 20)))

    def test_cache_storage_failure_keeps_valid_summary(self):
        self.publish([document()])
        self.service.trace_dir = None
        self.service.cache_dir.write_text('cannot be used as a directory')
        result = self.call()
        self.assertEqual(result.status, 'insufficient_results')
        self.assertEqual(len(result.articles[0].summary), 1)
        self.assertEqual(len(self.gateway.summary_calls), 1)

    def test_zero_query_vector_is_an_error_not_normal_empty_result(self):
        self.publish([document()])
        self.gateway.embed = lambda texts, context: [[0.] * 8]
        result = self.call()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(self.gateway.summary_calls, [])

    def test_trace_storage_time_counts_towards_global_deadline(self):
        from backend.app.schemas.rag import RagRequest
        from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
        from backend.app.services.rag.store import atomic_json
        self.publish([document()])
        self.service.cache_dir = None
        clock = [0.]
        context = ExecutionContext(1., clock=lambda: clock[0])
        def slow_trace(path, payload):
            atomic_json(path, payload)
            clock[0] = 2.
        with patch('backend.app.services.rag.service.atomic_json', side_effect=slow_trace):
            with self.assertRaises(RagDeadlineExceeded):
                self.service.run(RagRequest.model_validate(request_payload()), context)

    def test_corrupted_stored_vector_does_not_replace_active_generation(self):
        self.publish([document()])
        previous = self.store.snapshot().generation
        def corrupt(stage):
            if stage == 'batch_written':
                for folder in (self.store.root/'generations').iterdir():
                    report = json.loads((folder/'status.json').read_text())
                    if report['status'] == 'building':
                        client = self.store.client_factory(folder/'chroma')
                        col = client.get_collection('total_article', embedding_function=None)
                        key = col.get(include=[])['ids'][0]
                        col.update(ids=[key], embeddings=[[1.] * 8])
        with self.assertRaisesRegex(ValueError, 'stored_embedding_content_mismatch'):
            self.publish([document(body='변경 본문')], hook=corrupt)
        self.assertEqual(self.store.snapshot().generation, previous)
        failed = [json.loads(p.read_text()) for p in (self.store.root/'generations').glob('*/status.json')
                  if json.loads(p.read_text())['status'] == 'failed']
        self.assertEqual(failed[0]['stage'], 'verification')
        self.assertEqual(failed[0]['embedded'], 1)
        self.publish([document(body='변경 본문')])
        self.assertNotEqual(self.store.snapshot().generation, previous)

    def test_progress_is_durable_before_interruption_and_previous_read_still_works(self):
        self.publish([document()])
        previous = self.store.snapshot().generation
        def interrupt(stage):
            if stage == 'batch_written':
                active_writes = [json.loads(p.read_text()) for p in (self.store.root/'generations').glob('*/status.json')
                                 if json.loads(p.read_text())['status'] == 'building']
                self.assertEqual(active_writes[0]['embedded'], 1)
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.publish([document(body='새 본문')], hook=interrupt)
        self.assertEqual(self.store.snapshot().generation, previous)
        self.assertEqual(self.call().returned_count, 1)

    def test_canonical_question_and_confirmed_product_expand_dictionary_aliases(self):
        from backend.app.schemas.rag import RagRequest
        vocab = Vocabulary(synonyms={'EV':'전기차'}, hypernyms={'전기오토바이':'전기차'},
                           products={'HS2022:8507':['전기차']})
        request = request_payload(query='현지 동향')
        request['conditions']['products'] = [{'hs_code':'8507','hs_ver':'HS2022'}]
        expanded = vocab.query(RagRequest.model_validate(request))
        self.assertIn('EV', expanded)
        self.assertIn('전기오토바이', expanded)

class IndexConcurrencyTest(unittest.TestCase):
    def test_concurrent_cold_snapshots_keep_owned_handles_alive(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore(Path(directory)/'index', dimensions=8)
            store.publish([document()], ALL_SOURCES, vectors)
            for _ in range(3):
                barrier = threading.Barrier(4)
                def read(index):
                    if index < 4:
                        barrier.wait(timeout=10)
                    snapshot = store.snapshot()
                    try:
                        return snapshot.collection('total_article').count()
                    finally:
                        snapshot.client.close()
                with ThreadPoolExecutor(max_workers=4) as pool:
                    self.assertEqual(list(pool.map(read, range(12))), [1]*12)


class StateMatrixTest(unittest.TestCase):
    def test_twenty_state_paths_preserve_other_results(self):
        cases = [
            ('chat_success','chat','ok'), ('local_success','local_issue','ok'),
            ('weekly_success','report','ok'), ('monthly_success','report','ok'),
            ('quarterly_success','report','ok'), ('on_demand_success','report','ok'),
            ('local_limit_seven','local_issue','ok'), ('all_countries','chat','ok'),
            ('multiple_countries','local_issue','ok'), ('date_boundary','local_issue','ok'),
            ('cross_country_tags','local_issue','ok'), ('no_period_match','local_issue','no_results'),
            ('blocked_all','local_issue','no_results'), ('result_shortage','local_issue','insufficient_results'),
            ('evidence_withheld','local_issue','insufficient_evidence'), ('source_partial','local_issue','partial'),
            ('summary_partial','local_issue','partial'), ('summary_failed','local_issue','failed'),
            ('index_failed','local_issue','failed'), ('global_deadline','local_issue','global_deadline'),
        ]
        self.results=[]
        with tempfile.TemporaryDirectory() as folder:
            for name,use_case,expected in cases:
                with self.subTest(case=name):
                    store=GenerationStore(Path(folder)/name,dimensions=8)
                    gateway=FakeGateway()
                    service=RagService(store,Vocabulary(),gateway)
                    request=request_payload(use_case,request_id='state-'+name)
                    docs=[document(n) for n in range(1,8)]
                    sources=ALL_SOURCES
                    if use_case=='report':
                        report_type=name.removesuffix('_success')
                        request.update(report={'report_type':report_type,'article_purpose':'weekly_news' if report_type=='weekly' else 'local_issues'})
                        if report_type=='weekly':request['conditions']['period']={'start':'2026-09-14','end':'2026-09-20'}
                        else:request['limit']=3
                    if name=='local_limit_seven':request['limit']=7
                    if name=='all_countries':request['conditions']['geography']={'mode':'all','codes':[]}
                    if name=='multiple_countries':request['conditions']['geography']['codes']=['US','PL']
                    if name=='date_boundary':request['conditions']['period']={'start':'2026-09-15','end':'2026-09-15'}
                    if name=='cross_country_tags':
                        request['conditions']['geography']['codes']=['PL']
                    if name=='no_period_match':request['conditions']['period']={'start':'2026-08-01','end':'2026-08-31'}
                    if name=='blocked_all':request['conditions']['blocked_topics']=['배터리']
                    if name=='result_shortage':docs=docs[:2]
                    if name=='evidence_withheld':gateway.empty=True
                    if name=='source_partial':
                        sources=['KOTRA_MARKET'];docs=[docs[0]]
                        request['source_codes']=[s.value for s in ALL_SOURCES]
                    if name=='summary_partial':
                        # 문서 header가 바뀌어 순위가 달라져도 한 건 성공·나머지 실패를 재현합니다.
                        summarize=gateway.summarize
                        def partial_summary(request,candidate,context):
                            if gateway.summary_calls:raise RuntimeError('synthetic_summary_failure')
                            return summarize(request,candidate,context)
                        gateway.summarize=partial_summary
                    if name=='summary_failed':gateway.fail={d.key for d in docs}
                    if name!='index_failed':store.publish(docs,sources,vectors)
                    state={'rag_request':request,'trade_result':{'amount':123,'rows':[1,2]},
                           'country_result':{'confirmed':['US']},'report_result':{'saved':True}}
                    before=copy.deepcopy(state)
                    context=ExecutionContext(time.monotonic()+(-1 if name=='global_deadline' else 30))
                    if name=='global_deadline':
                        with self.assertRaises(RagDeadlineExceeded):rag_node(state,service,context)
                        status='global_deadline';owned=True
                    else:
                        patch=rag_node(state,service,context)
                        status=patch['rag_result']['status'];owned=set(patch)=={'rag_result','rag_trace'}
                        self.assertEqual(status,expected,(name,patch['rag_result']))
                        self.assertTrue(owned)
                    self.assertEqual(state,before)
                    self.results.append({'case':name,'synthetic':True,'expected':expected,'actual':status,
                                         'other_state_preserved':state==before,'owned_fields_only':owned})

class VocabularyContextTest(unittest.TestCase):
    def test_confirmed_hs6_expands_article_query_with_code_and_name(self):
        payload = self.request('관련 기사 찾아줘').model_dump()
        payload['conditions']['products'] = [{'hs_code': '330499', 'hs_ver': 'HS2022'}]
        request = RagRequest.model_validate(payload)
        vocabulary = Vocabulary(products={'HS2022:330499': ['기초화장품']})
        self.assertEqual(vocabulary.query(request).splitlines(), ['관련 기사 찾아줘', '기초화장품', '330499'])

    def vocabulary(self):
        return Vocabulary(synonyms={'환경산업': '환경'}, hypernyms={'환경보호': '환경'},
                          categories={'환경': '친환경'})

    def request(self, query):
        payload = request_payload('chat', query=query)
        payload['conditions']['geography'] = {'mode': 'all', 'codes': []}
        return RagRequest.model_validate(payload)

    def test_business_context_does_not_expand_to_ecology(self):
        for query in ('경영환경 개선 원인은?', '경영 환경을 분석해줘'):
            with self.subTest(query=query):
                v = self.vocabulary()
                self.assertEqual(v.query(self.request(query)), query)
                self.assertEqual(v.query_categories(self.request(query)), set())

    def test_explicit_ecology_context_still_expands(self):
        for query in ('환경보호 정책', '환경 산업 성장', '경영환경과 환경보호 정책을 비교해줘'):
            with self.subTest(query=query):
                v = self.vocabulary()
                self.assertTrue({'환경산업', '환경', '환경보호'} <= set(v.query(self.request(query)).splitlines()))
                self.assertEqual(v.query_categories(self.request(query)), {'친환경'})

    def test_other_dictionary_matches_and_body_subterms_unchanged(self):
        v = Vocabulary(synonyms={'EV': '전기차'}, hypernyms={'전기차': '자동차'})
        self.assertTrue({'EV', '전기차', '자동차'} <= set(v.query(self.request('EV 수출')).splitlines()))
        body_v = Vocabulary(synonyms={'환경': '환경'})
        self.assertEqual(body_v.body_subterms('경영환경', ['환경']), ['환경'])


if __name__=='__main__': unittest.main()
