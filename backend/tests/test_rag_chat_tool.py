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

                def respond(request, article_allowed=None, started=None):
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
        with patch.object(rag_store, 'persistent_client', side_effect=fake_open),              patch.dict(rag_store._shared_clients, clear=True), patch.object(rag_store, '_latest_shared', None):
            first = rag_store.shared_client(Path('g1'))
            first.close()                                   # 요청이 끝나도 닫지 않는다
            again = rag_store.shared_client(Path('g1'))
            self.assertEqual(len(opened), 1)
            opened[0].close.assert_not_called()
            again.get_collection('x'); opened[0].get_collection.assert_called_once_with('x')
            newer = rag_store.shared_client(Path('g2'))     # 새 세대가 열려도 g1은 아직 검색 중(again)이라 열어 둔다
            self.assertEqual(len(opened), 2)
            opened[0].close.assert_not_called()
            again.close()                                   # 마지막 사용자가 돌려주면 이전 세대를 닫는다(D-105)
            opened[0].close.assert_called_once()
            newer.close(); opened[1].close.assert_not_called()   # 최신 세대는 계속 열어 둔다

    def test_graph_cards_keep_place_of_hidden_articles(self):
        # compose는 digest 순서로 [n]을 붙였다. 검색 뒤 숨겨진 기사도 자리를 남겨 [2]가 3번째 기사를 가리키지 않게 한다
        from backend.app.api.chat_news import verified_cards, UNAVAILABLE_TITLE
        items = [{'article_id': i, 'source_code': 'KOTRA', 'external_id': str(i), 'published_date': '2026-09-25',
                  'source_url': 'https://example.com', 'evidence_role': 'direct', 'reference_limitation': None,
                  'summary': []} for i in (1, 2)]
        store = Mock(); store.rows.return_value = [{'article_id': 2, 'source_code': 'KOTRA', 'external_id': '2',
            'published_date': '2026-09-25', 'title': '보이는 기사', 'source_url': 'https://example.com', 'body_masked': ''}]
        cards, _ = verified_cards(store, Mock(), items, keep_missing=True)
        self.assertEqual([c['title'] for c in cards], [UNAVAILABLE_TITLE, '보이는 기사'])
        self.assertEqual(len(verified_cards(store, Mock(), items)[0]), 1)   # 기존 채팅은 그대로 뺀다
        # 숨겨진 기사를 인용한 문장은 그 기사 내용이므로 안내 문구로 바꾼다. 문장 수는 verify 번호와 맞춰 그대로다
        from backend.app.agents.runtime import redact_hidden_citations, HIDDEN_ARTICLE_SENTENCE
        texts = ['숨긴 기사 내용 [1]', '보이는 기사 내용 [2]', '숫자만 있는 문장']
        self.assertEqual(redact_hidden_citations(texts, cards), [HIDDEN_ARTICLE_SENTENCE, *texts[1:]])

    def test_graph_cards_mark_corrected_articles_and_redact_their_citations(self):
        # 검색 뒤 본문이 정정돼 인용 원문이 사라진 기사도 그 기사를 인용한 답변 문장을 가린다
        from backend.app.api.chat_news import verified_cards
        from backend.app.agents.runtime import redact_hidden_citations, HIDDEN_ARTICLE_SENTENCE
        item = {'article_id': 1, 'source_code': 'KOTRA', 'external_id': '1', 'published_date': '2026-09-25',
                'source_url': 'https://example.com', 'evidence_role': 'direct', 'reference_limitation': None,
                'summary': [{'text': '수출이 늘었다', 'quotes': ['옛 원문'], 'unverified_question_parts': []}]}
        store = Mock(); store.rows.return_value = [{'article_id': 1, 'source_code': 'KOTRA', 'external_id': '1',
            'published_date': '2026-09-25', 'title': '정정된 기사', 'source_url': 'https://example.com', 'body_masked': '새 본문'}]
        cards, _ = verified_cards(store, Mock(), [item], keep_missing=True)
        self.assertTrue(cards[0]['evidence_changed'])
        self.assertEqual(redact_hidden_citations(['수출이 늘었습니다 [1]'], cards), [HIDDEN_ARTICLE_SENTENCE])
        self.assertNotIn('evidence_changed', verified_cards(store, Mock(), [item])[0][0])   # 기존 채팅은 그대로

    def test_warm_index_returns_handle_when_probe_fails(self):
        # 예열 시험 검색이 실패해도 빌린 핸들을 돌려줘야 새 세대가 열린 뒤 이전 세대가 닫힌다
        from backend.app.agents import tools
        snapshot = Mock(); snapshot.dimensions = 4
        snapshot.collection.return_value.query.side_effect = RuntimeError('probe failed')
        with patch('backend.app.services.rag.store.GenerationStore') as store, self.assertLogs(tools.__name__, 'WARNING'):
            store.return_value.snapshot.return_value = snapshot
            tools.warm_article_index().join(5)
        snapshot.client.close.assert_called_once()

    def test_search_budget_counts_filter_time(self):
        # 공개·차단 필터 조회로 예산을 다 쓰면 RAG를 시작하지 않는다. 검색은 예약된 30초 안에서 끝나야 한다
        import time
        from backend.app.services.rag.runtime import RagDeadlineExceeded
        with patch('backend.app.agents.tools.create_service') as service, self.assertRaises(RagDeadlineExceeded):
            search_news(RagRequest.model_validate(request_payload('chat')), started=time.monotonic() - 31)
        service.assert_not_called()

    def test_shared_client_opens_outside_the_borrow_lock(self):
        # 새 세대를 여는 수십 초 동안 다른 검색이 이미 열린 핸들을 빌릴 수 있어야 한다
        from backend.app.services.rag import store as rag_store
        held = []
        def fake_open(path):
            held.append(rag_store._shared_lock.locked()); return Mock(name=str(path))
        with patch.object(rag_store, 'persistent_client', side_effect=fake_open), \
             patch.dict(rag_store._shared_clients, clear=True), patch.object(rag_store, '_latest_shared', None):
            rag_store.shared_client(Path('g1')).close()
        self.assertEqual(held, [False])

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
