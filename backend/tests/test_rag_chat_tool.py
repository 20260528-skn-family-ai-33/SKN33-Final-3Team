import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.app.agents.tools import search_news
from backend.app.schemas.rag import RagRequest, RagResponse
from backend.tests.rag_fixtures import request_payload
from backend.tests.test_rag_pipeline import document


class ChatNewsToolTest(unittest.TestCase):
    def test_latest_returns_catalog_article_without_model(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, NEWS_VECTOR_ROOT=root):
            article = document(1)
            with sqlite3.connect(Path(root) / 'article_catalog.sqlite3') as db:
                db.execute('CREATE TABLE articles(source_code,external_id,published_date,payload)')
                db.execute('INSERT INTO articles VALUES (?,?,?,?)', (
                    article.source_code.value, article.external_id, article.published_date.isoformat(),
                    json.dumps(article.model_dump(mode='json'))))
            request = RagRequest.model_validate(request_payload('chat', order_by='published_date_desc'))
            with patch('backend.app.agents.tools.OpenAI', side_effect=AssertionError):
                result = search_news(request)
            self.assertIsInstance(result, RagResponse)
            self.assertEqual([a.external_id for a in result.articles], [article.external_id])

    def test_team_generation_can_be_searched_without_migrating_it(self):
        from backend.app.schemas.rag import ALL_SOURCES
        from backend.app.services.rag.search_store import SearchStore
        from backend.tests.test_rag_pipeline import document, vectors

        with tempfile.TemporaryDirectory() as root:
            store = SearchStore(Path(root), dimensions=8)
            report = store.publish([document(1)], ALL_SOURCES, vectors)
            folder = Path(root) / 'generations' / report['generation']
            # Reproduce the independently published team's collection contract.
            client = store.client_factory(folder / 'chroma')
            try:
                client.get_collection('kotra_article').modify(name='total_article')
            finally:
                client.close()
            status = folder / 'status.json'
            metadata = json.loads(status.read_text())
            metadata['collections'] = {'total_article':metadata['collections']['kotra_article']}
            status.write_text(json.dumps(metadata))
            snapshot = store.snapshot()
            try:
                self.assertEqual(snapshot.collection('kotra_article').count(), 1)
            finally:
                snapshot.client.close()
            self.assertEqual(json.loads(status.read_text()), metadata)

    def test_normal_search_reports_missing_index_and_closes_client(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, NEWS_VECTOR_ROOT=root):
            with patch('backend.app.agents.tools.OpenAI') as client:
                result = search_news(RagRequest.model_validate(request_payload('chat')))
            self.assertEqual(result.status, 'failed')
            client.return_value.__exit__.assert_called_once()
