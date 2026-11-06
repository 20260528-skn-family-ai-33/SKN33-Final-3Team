import os
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.app.agents.tools import search_news
from backend.app.schemas.rag import ALL_SOURCES, RagRequest, RagResponse
from backend.app.services.rag.runtime import RagDeadlineExceeded
from backend.app.services.rag.store import GenerationStore
from backend.tests.rag_fixtures import request_payload, response_payload
from backend.tests.test_rag_pipeline import FakeGateway, document, vectors


class ChatNewsToolTest(unittest.TestCase):
    def test_graph_adapter_preserves_partial_and_stops_after_timeout(self):
        from backend.app.agents.runtime import Services
        from backend.app.agents.state import Conditions
        from backend.tests.test_agent_state import state

        for status, count in (('ok', 5), ('partial', 2), ('failed', 0), ('deadline', 0)):
            with self.subTest(status=status):
                agent = state()
                task = agent.plan.tasks[0]
                payload = request_payload('chat')
                task.conditions = Conditions(article_period=payload['conditions']['period'])
                step = next(s for s in task.steps if s.tool == 'search_articles')
                execution = Mock()
                execution.call.side_effect = lambda node, timeout, callback: callback(timeout)
                execution.transaction.return_value = 'articles_saved'   # 인용 근거 저장(D-102)
                services = Services(execution, {}, client=Mock())
                allowed = Mock(name='article_allowed')

                def respond(request, article_allowed=None):
                    self.assertIs(article_allowed, allowed)   # 공개·사용자 차단 필터를 RAG에 넘긴다
                    if status == 'deadline':
                        raise RagDeadlineExceeded('global_deadline_exceeded')
                    failures = [] if status == 'ok' else [{'stage': 'timeout', 'code': 'candidate_timeout'}]
                    return RagResponse.model_validate(response_payload(
                        request.model_dump(mode='json'), status=status, count=count, failures=failures))

                with patch.object(services, 'read_store', return_value=nullcontext(None)), \
                     patch('backend.app.api.chat_news.eligible_filter', return_value=(None, allowed)), \
                     patch('backend.app.agents.tools.search_news', side_effect=respond) as search:
                    result = services.tool(agent, step)
                self.assertEqual(search.call_count, 1)
                self.assertEqual(execution.call.call_args.args[:2], ('news', 30))
                self.assertEqual(result.article_ids, list(range(1, count + 1)))
                self.assertEqual(result.status, 'OK' if count else 'NO_DATA')
                if count:
                    self.assertEqual(result.result_id, 'articles_saved')
                    self.assertEqual(result.digest['rag_status'], status)
                    self.assertEqual(result.completeness, 'COMPLETE' if status == 'ok' else 'PARTIAL')
                    self.assertTrue(all(a['evidence_sufficient'] for a in result.digest['items']))
                if status == 'partial':
                    self.assertTrue(any('시간' in reason for reason in result.limitations))

    def test_shared_client_opens_each_generation_once_and_ignores_request_close(self):
        from backend.app.services.rag import store as rag_store
        opened = []
        def fake_open(path):
            handle = Mock(name=str(path)); opened.append(handle); return handle
        with patch.object(rag_store, 'persistent_client', side_effect=fake_open),              patch.dict(rag_store._shared_clients, clear=True):
            first = rag_store.shared_client(Path('g1'))
            first.close()                                   # 요청이 끝나도 닫지 않는다
            again = rag_store.shared_client(Path('g1'))
            self.assertEqual(len(opened), 1)
            opened[0].close.assert_not_called()
            again.get_collection('x'); opened[0].get_collection.assert_called_once_with('x')
            rag_store.shared_client(Path('g2'))             # 새 세대가 열리면 이전 세대를 닫는다
            self.assertEqual(len(opened), 2)
            opened[0].close.assert_called_once()

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
