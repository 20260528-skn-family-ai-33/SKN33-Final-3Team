"""재구성 실행과 새 검색 기록의 연결 검증. 실제 품질 평가가 아닙니다."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from backend.app.schemas.rag import ALL_SOURCES, ArticleEvidence
from backend.app.services.rag.baseline import rebuild_legacy
from backend.app.services.rag.evaluate_service import record_retrieval
from backend.app.services.rag.evaluation import EvaluationDataset, RunConfiguration
from backend.app.services.rag.store import COLLECTIONS, MODEL, GenerationStore
from backend.tests.rag_fixtures import article_payload, configuration_payload, evaluation_payload
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors


class ExecutionTest(unittest.TestCase):
    def test_unreviewed_actual_holdout_is_rejected_before_search(self):
        payload = evaluation_payload()
        payload.update(synthetic=False, split='holdout')
        dataset = EvaluationDataset.model_validate(payload)
        configuration = RunConfiguration.model_validate(configuration_payload(finalized=True))
        with self.assertRaisesRegex(ValueError, 'holdout_review_required_before_execution'):
            record_retrieval(dataset, None, None, configuration, seconds_per_request=5)

    def test_newspaper_country_meaning_survives_serialization(self):
        article = ArticleEvidence.model_validate(article_payload(3))
        self.assertEqual(article.country_role, 'mentioned')
        self.assertEqual(ArticleEvidence.model_validate_json(article.model_dump_json()), article)
        with self.assertRaises(ValidationError):
            ArticleEvidence.model_validate(article_payload(3) | {'country_role': 'source_tag'})

    def test_reconstruction_is_separate_and_hash_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).parents[1] / 'app/scheduler/jobs/news_embed.py'
            relative = 'backend/app/scheduler/jobs/news_embed.py'
            saved = root/'snapshot'/relative
            saved.parent.mkdir(parents=True)
            saved.write_bytes(source.read_bytes())
            (root/'snapshot/manifest.json').write_text(json.dumps({'files': {
                relative: hashlib.sha256(saved.read_bytes()).hexdigest()}}))
            rows = root/'rows.json'
            rows.write_text(json.dumps([{'article_id':1, 'source_code':'KOTRA_MARKET', 'external_id':'synthetic-1',
                'title':'합성 기사', 'body_masked':'재구성 연결 검증용 합성 본문', 'published_date':'2026-09-15',
                'countries':['US'], 'keywords':['배터리'], 'category':''}]))
            report = rebuild_legacy(root/'snapshot', rows, root/'baseline', vectors)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['kind'], 'reconstructed_baseline')
            self.assertFalse(report['historical_measurement'])
            self.assertEqual(report['result']['articles'], 1)
            with self.assertRaises(FileExistsError):
                rebuild_legacy(root/'snapshot', rows, root/'baseline', vectors)
            saved.write_text(saved.read_text()+'\n# changed\n')
            with self.assertRaisesRegex(ValueError, 'preserved_code_hash_mismatch'):
                rebuild_legacy(root/'snapshot', rows, root/'other', vectors)
            self.assertFalse((root/'other').exists())

    def test_recording_pins_generation_and_preserves_execution_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GenerationStore(Path(directory)/'index', dimensions=8)
            report = store.publish([document(n, external_id=f'synthetic-{n}') for n in range(1,5)],
                                   ALL_SOURCES, vectors, vocabulary={})
            configuration = RunConfiguration.model_validate(configuration_payload(
                corpus_revision=report['generation'], embedding_model=MODEL, collections=COLLECTIONS))
            dataset = EvaluationDataset.model_validate(evaluation_payload())
            run = record_retrieval(dataset, store, FakeGateway(), configuration, seconds_per_request=5)
            self.assertTrue(all(r.outcome == 'recorded' and len(r.retrieved) == 4 for r in run.results))
            class FailingGateway(FakeGateway):
                def embed(self, texts, context):
                    raise TimeoutError('private failure detail')
            failed = record_retrieval(dataset, store, FailingGateway(), configuration, seconds_per_request=5)
            self.assertTrue(all(r.outcome == 'error' and not r.retrieved and r.error_code == 'TimeoutError'
                                for r in failed.results))
            self.assertNotIn('private failure detail', failed.model_dump_json())
            with self.assertRaisesRegex(ValueError, 'corpus_revision_mismatch'):
                record_retrieval(dataset, store, FakeGateway(), configuration.model_copy(update={'corpus_revision':'other'}),
                                 seconds_per_request=5)
            with self.assertRaisesRegex(ValueError, 'runtime_configuration_mismatch'):
                record_retrieval(dataset, store, FakeGateway(), configuration.model_copy(update={'chunk_chars':500}),
                                 seconds_per_request=5)


if __name__ == '__main__':
    unittest.main()
