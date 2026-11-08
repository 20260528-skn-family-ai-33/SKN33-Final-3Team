"""SQLite, local Chroma and model doubles exercise the real chat/RAG connection."""
from datetime import date
import unittest
from unittest.mock import patch

import httpx
from openai import APITimeoutError, OpenAIError
from sqlalchemy import update

from backend.app.api.chat_news import article_answer, matching_url
from backend.app.api.database import Store
from backend.app.services.rag.runtime import ExecutionContext, RagDeadlineExceeded
from backend.tests import test_backend_api as base
from backend.tests import test_chat_review_regressions as chat_fixtures
from backend.tests.chat_news_fixture import indexed_news


class ChatRagIntegrationTest(unittest.TestCase):
    setUp = base.BackendApiTest.setUp
    tearDown = base.BackendApiTest.tearDown
    signup = base.BackendApiTest.signup
    post = base.BackendApiTest.post
    ask = chat_fixtures.ChatReviewRegressionTest.ask

    def seed(self, count=1):
        with self.engine.begin() as conn:
            conn.execute(self.meta.tables['article'].insert(), [
                {'article_id': n, 'source_code': 'KOTRA_MARKET', 'external_id': str(n),
                 'title': f'합성 기사 {n}', 'published_date': date(2025, 12, 15),
                 'source_url': f'https://example.org/{n}',
                 'body_masked': f'중국 수입사가 미국 화장품을 수입했다. 합성 근거 {n}.',
                 'category': '경제·무역', 'visibility_status': 'VISIBLE', 'admin_meta_json': {}}
                for n in range(1, count + 1)])

    def answer(self, uid, question='미국 화장품 수출을 설명해줘', latest=False):
        conditions = {'country_codes': ['US'], 'hs_items': [{'hs_code':'330499','hs_ver':'HS2022'}],
                      'period': {'start':'2025-12-01','end':'2025-12-31'}}
        with self.engine.connect() as conn:
            return article_answer(Store(self.app.state.database, conn), uid, 'test', question,
                                  conditions, [], latest)

    def test_cross_country_and_product_tags_keep_body_evidence_after_header_changes(self):
        uid = int(self.signup()); self.seed()
        with self.engine.begin() as conn:
            conn.execute(self.meta.tables['article_tag'].insert(), {'article_id':1, 'country_code':'CN'})
        with indexed_news(self) as service:
            service.vocabulary.products['HS2022:330499'] = ['화장품']
            with self.engine.begin() as conn:
                conn.execute(update(self.meta.tables['article']).values(title='수정된 제목', category='다른 분류'))
            result = self.answer(uid)
        self.assertEqual(len(result['articles']), 1)
        card = result['articles'][0]
        self.assertEqual(card['title'], '수정된 제목')
        self.assertEqual(card['sentences'][0]['quotes'], ['중국 수입사가 미국 화장품을 수입했다. 합성 근거 1.'])
        self.assertEqual(card['evidence_role'], 'direct_evidence')

    def test_visibility_and_user_blocks_precede_summary_and_do_not_consume_five_slots(self):
        uid = int(self.signup()); self.seed(7)
        with self.engine.begin() as conn:
            conn.execute(update(self.meta.tables['article']).where(self.meta.tables['article'].c.article_id==1)
                         .values(visibility_status='HIDDEN'))
            conn.execute(self.meta.tables['keyword_topic'].insert(), {'keyword_text':'차단주제', 'status':'UNCLASSIFIED'})
            conn.execute(self.meta.tables['article_tag'].insert(), {'article_id':2, 'keyword_text':'차단주제'})
            conn.execute(update(self.meta.tables['notification_setting']).values(
                keyword_blocks_json=[{'kw_topic':'차단주제','selected_terms':['차단주제']}]))
        with indexed_news(self) as service:
            service.vocabulary.products['HS2022:330499'] = ['화장품']
            result = self.answer(uid)
            called = set(service.gateway.summary_calls)
        self.assertEqual(result['answer_status'], 'ANSWERED')
        self.assertEqual({a['article_id'] for a in result['articles']}, {'3','4','5','6','7'})
        self.assertEqual({key[1] for key in called}, {'3','4','5','6','7'})

    def test_changed_body_cannot_keep_stale_quotes(self):
        uid = int(self.signup()); self.seed()
        with indexed_news(self) as service:
            service.vocabulary.products['HS2022:330499'] = ['화장품']
            with self.engine.begin() as conn:
                conn.execute(update(self.meta.tables['article']).values(body_masked='수정된 본문'))
            result = self.answer(uid)
        self.assertEqual(result['answer_status'], 'PARTIAL')
        self.assertEqual(result['articles'][0]['sentences'], [])
        self.assertFalse(result['articles'][0]['evidence_sufficient'])

    def test_latest_summary_survives_worker_and_history_with_candidate_scope(self):
        self.signup(); self.seed(5)
        with indexed_news(self) as service:
            service.gateway.latest_intent = lambda request, context: ('summary', True)
            sid, _, job = self.ask('최신 기사 내용을 설명해줘', intent='RAG', latest_articles=True, period=None)
        self.assertEqual(job['status'], 'SUCCEEDED')
        answer = job['answer']
        self.assertEqual(answer['answer_format'], 'summary')
        self.assertIn('[1]', answer['text'])
        self.assertIn('후보 최대 40건 안', answer['text'])
        self.assertEqual(answer['article_period'], {'start':'2025-12-02','end':'2025-12-15'})
        self.assertTrue(answer['articles'][0]['sentences'][0]['quotes'])
        history = self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']
        self.assertEqual(history[-1]['answer'], answer)

    def test_latest_list_exposes_scope_without_invented_quotes(self):
        self.signup(); self.seed(5)
        with indexed_news(self):
            _, _, job = self.ask('최신 기사 목록', intent='LOCAL_ISSUES', latest_articles=True, period=None)
        answer = job['answer']
        self.assertEqual(answer['answer_format'], 'list')
        self.assertIn('후보 최대 40건 안', answer['text'])
        self.assertTrue(all(not a['sentences'] for a in answer['articles']))
        self.assertTrue(any('본문 관련성을 검수하지 않은' in w for w in answer['warnings']))

    def test_search_failures_become_saved_notices_without_unverified_db_fallback(self):
        self.signup(); self.seed()
        for n, error in enumerate((RagDeadlineExceeded('deadline'), OpenAIError('key missing'),
                                    OSError('index unavailable'))):
            with self.subTest(error=type(error).__name__), patch('backend.app.agents.tools.search_news', side_effect=error):
                sid, _, job = self.ask(f'기사 설명 {n}', intent='RAG')
            self.assertEqual(job['status'], 'SUCCEEDED')
            answer = job['answer']
            self.assertEqual((answer['answer_status'], answer['rag_status']), ('NO_DATA','failed'))
            self.assertIn('검색에 실패', answer['text'])
            self.assertEqual(answer['articles'], [])
            self.assertNotIn(str(error), answer['text'])
            history = self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']
            self.assertEqual(history[-1]['answer'], answer)

    def test_regular_search_deadline_preserves_only_previously_verified_cards_in_history(self):
        self.signup()
        self.seed(5)
        # None simulates an SDK timeout while the local deadline still has time.
        for accepted, expires_at in ((0, 29), (2, 29), (2, 31), (0, None), (2, None)):
            with self.subTest(accepted=accepted, expires_at=expires_at), indexed_news(self) as service:
                now = [0.]
                summarize = service.gateway.summarize
                attempted = []

                def timed_summary(request, candidate, context):
                    attempted.append(candidate.article.article_id)
                    if len(attempted) > accepted:
                        if expires_at is None:
                            raise APITimeoutError(request=httpx.Request('POST', 'https://example.org/'))
                        now[0] = expires_at
                    return summarize(request, candidate, context)

                def context_with_test_clock(deadline, **kwargs):
                    self.assertEqual(deadline, 30)
                    return ExecutionContext(deadline, clock=lambda: now[0], **kwargs)

                with patch('backend.app.agents.tools.time') as timer, \
                     patch('backend.app.agents.tools.ExecutionContext', side_effect=context_with_test_clock), \
                     patch.object(service.gateway, 'summarize', side_effect=timed_summary):
                    timer.monotonic.return_value = 0.
                    sid, _, job = self.ask(f'기사 설명 {accepted} {expires_at}', intent='RAG')
                answer = job['answer']
                self.assertEqual(len(attempted), accepted + 1)
                self.assertEqual([a['article_id'] for a in answer['articles']], list(map(str, attempted[:accepted])))
                if accepted:
                    self.assertEqual((answer['answer_status'], answer['rag_status']), ('PARTIAL', 'partial'))
                    self.assertTrue(any('시간' in w for w in answer['warnings']))
                    for card in answer['articles']:
                        self.assertTrue(card['sentences'][0]['quotes'])
                else:
                    self.assertEqual((answer['answer_status'], answer['rag_status']), ('NO_DATA', 'failed'))
                history = self.client.get(f'/api/v1/chat/sessions/{sid}/messages').json()['items']
                self.assertEqual(history[-1]['answer'], answer)

    def test_http_urls_compare_after_normalization_but_changed_or_unsafe_urls_do_not(self):
        self.assertEqual(matching_url('https://EXAMPLE.org', 'https://example.org/'), 'https://example.org/')
        self.assertIsNone(matching_url('https://example.org/changed', 'https://example.org/'))
        self.assertIsNone(matching_url('javascript:alert(1)', 'https://example.org/'))
