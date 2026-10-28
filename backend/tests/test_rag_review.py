"""구현 계획 대조에서 확인한 경계·복구 오류의 회귀 검증."""
import json
import math
import unittest
from unittest.mock import patch

from backend.app.schemas.rag import ALL_SOURCES
from backend.app.services.rag.vocabulary import Vocabulary
from backend.tests.rag_fixtures import request_payload
from backend.tests import test_rag_pipeline as fixtures
from backend.tests.test_rag_pipeline import document


class ReviewRegressionTest(unittest.TestCase):
    setUp = fixtures.PipelineTest.setUp
    publish = fixtures.PipelineTest.publish
    call = fixtures.PipelineTest.call

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


if __name__ == '__main__':
    unittest.main()
