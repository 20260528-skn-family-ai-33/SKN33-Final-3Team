import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.agents.tools import search_news
from backend.app.schemas.rag import ALL_SOURCES, RagRequest
from backend.app.services.rag.store import GenerationStore
from backend.tests.rag_fixtures import request_payload
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors


class ChatNewsToolTest(unittest.TestCase):
    def test_latest_uses_live_gateway_and_published_originals_without_catalog(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, NEWS_VECTOR_ROOT=root):
            store = GenerationStore(Path(root), dimensions=8)
            store.publish([document(1, published_date='2026-09-25')], ALL_SOURCES, vectors, vocabulary={})
            gateway = FakeGateway()
            request = RagRequest.model_validate(request_payload('chat', order_by='published_date_desc',
                                                                answer_format='summary'))
            with patch('backend.app.agents.tools.OpenAI') as client, \
                 patch('backend.app.services.rag.factory.GenerationStore', return_value=store), \
                 patch('backend.app.services.rag.factory.ModelGateway', return_value=gateway) as model:
                result = search_news(request)
            model.assert_called_once_with(client.return_value.__enter__.return_value)
            client.return_value.__exit__.assert_called_once()
            self.assertEqual(gateway.embed_calls, 1)
            self.assertEqual([a.external_id for a in result.articles], ['1'])
            self.assertEqual(result.articles[0].evidence[0].quote, document().body)
            self.assertEqual(result.latest_scope, 'retrieved_candidates')
            self.assertFalse((Path(root) / 'article_catalog.sqlite3').exists())

    def test_team_collection_is_read_without_renaming_or_rebuilding(self):
        with tempfile.TemporaryDirectory() as root:
            store = GenerationStore(Path(root), dimensions=8)
            report = store.publish([document(1)], ALL_SOURCES, vectors, vocabulary={})
            status = Path(root) / 'generations' / report['generation'] / 'status.json'
            before = status.read_bytes()
            snapshot = store.snapshot()
            try:
                self.assertEqual(snapshot.collection('total_article').count(), 1)
                self.assertEqual([c.name for c in snapshot.client.list_collections()], ['total_article'])
            finally:
                snapshot.client.close()
            self.assertEqual(status.read_bytes(), before)

    def test_normal_search_reports_missing_index_and_closes_client(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, NEWS_VECTOR_ROOT=root):
            with patch('backend.app.agents.tools.OpenAI') as client:
                result = search_news(RagRequest.model_validate(request_payload('chat')))
            self.assertEqual(result.status, 'failed')
            client.return_value.__exit__.assert_called_once()
