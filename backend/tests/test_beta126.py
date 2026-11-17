"""2차 베타: 첫 질문 제목과 출처명 검색. SQLite/FakeRedis만 사용한다."""
from datetime import date, datetime
import unittest

from sqlalchemy import select, update

from backend.tests import test_backend_api as base


class Beta126Test(unittest.TestCase):
    tearDown = base.BackendApiTest.tearDown
    signup = base.BackendApiTest.signup
    post = base.BackendApiTest.post

    def setUp(self):
        from langgraph.checkpoint.memory import InMemorySaver
        base.BackendApiTest.setUp(self)
        self.app.state.database.agent_checkpointer = InMemorySaver()

    def title(self, sid):
        rows = self.client.get('/api/v1/chat/sessions').json()['items']
        return next(row['title'] for row in rows if row['session_id'] == sid)

    def test_first_message_titles_both_queues_before_worker_and_survives_retry(self):
        self.signup()
        for enabled in ('false', 'true'):
            with self.subTest(agent=enabled):
                self.settings.values['API_CHAT_AGENT_ENABLED'] = enabled
                sid = self.post('chat/sessions', {}).json()['session']['session_id']
                self.assertEqual(self.title(sid), '새 대화')
                path = f'chat/sessions/{sid}/messages'
                body = {'question': '  미국 수출\n  동향과 한국 화장품의 시장 전망을 알려줘',
                        'client_message_id': f'first-{sid}'}
                invalid = self.post(path, {**body, 'question': '  '})
                self.assertEqual(invalid.status_code, 400)
                self.assertEqual(self.title(sid), '새 대화')
                accepted = self.post(path, body)
                self.assertEqual(accepted.status_code, 202, accepted.text)
                jid = accepted.json()['job']['job_id']
                expected = '미국 수출 동향과 한국 화장품의 시장'
                self.assertEqual(self.title(sid), expected)
                self.assertEqual(self.post(path, body).json()['job']['job_id'], jid)
                self.assertEqual(self.title(sid), expected)
                self.post(f'chat/jobs/{jid}/cancel', {})
                following = self.post(path, {'question': '다른 질문', 'client_message_id': f'next-{sid}'})
                self.assertEqual(following.status_code, 202, following.text)
                self.assertEqual(self.title(sid), expected)

    def test_manual_title_and_literal_default_first_message_are_preserved(self):
        self.signup()
        for initial in ('직접 정한 제목', '새 대화'):
            sid = self.post('chat/sessions', {}).json()['session']['session_id']
            if initial != '새 대화':
                response = self.client.patch(f'/api/v1/chat/sessions/{sid}', json={'title': initial},
                                             headers={'X-CSRF-Token': self.csrf})
                self.assertEqual(response.status_code, 200, response.text)
            path = f'chat/sessions/{sid}/messages'
            job = self.post(path, {'question': '새 대화', 'client_message_id': f'first-{sid}'}).json()['job']
            self.assertEqual(self.title(sid), initial)
            self.post(f"chat/jobs/{job['job_id']}/cancel", {})
            self.post(path, {'question': '두 번째 질문', 'client_message_id': f'next-{sid}'})
            self.assertEqual(self.title(sid), initial)

    def test_failed_admission_rolls_back_title_and_retry_uses_masked_message(self):
        from unittest.mock import patch
        from backend.app.api.common import ApiError
        self.signup()
        self.settings.values['API_CHAT_AGENT_ENABLED'] = 'true'
        sid = self.post('chat/sessions', {}).json()['session']['session_id']
        path = f'chat/sessions/{sid}/messages'
        body = {'question': 'alice@example.org\n수출 문의', 'client_message_id': 'rollback-title'}
        with patch('backend.app.agents.admission.ChatContext.acquire_locked',
                   side_effect=ApiError(503, 'REDIS_UNAVAILABLE', '테스트용 접수 실패')):
            failed = self.post(path, body)
        self.assertEqual(failed.status_code, 503, failed.text)
        self.assertEqual(self.title(sid), '새 대화')
        self.assertEqual(self.client.get('/api/v1/' + path).json()['items'], [])
        accepted = self.post(path, body)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertEqual(self.title(sid), '[이메일] 수출 문의')
        self.assertEqual(self.client.get('/api/v1/' + path).json()['items'][0]['content_masked'], '[이메일]\n수출 문의')

    def test_source_search_filters_all_rows_before_pagination_and_keeps_visibility(self):
        uid = int(self.signup())
        m = self.meta.tables
        sources = ['FNNEWS', 'FNNEWS', 'HANKYUNG', 'KOTRA_MARKET', 'KOTRA_SHORT_NEWS',
                   'FNNEWS', 'FNNEWS', 'OTHER', 'KOTRA_SHORTNEWS']
        with self.engine.begin() as c:
            for i, source in enumerate(sources, 1):
                c.execute(m['article'].insert(), {'article_id': i, 'source_code': source,
                    'external_id': f'beta126-{i}', 'title': '파이낸셜 관련 기사' if i == 8 else '무역 동향',
                    'body_masked': '본문', 'published_date': date(2025, 12, i), 'category': '경제',
                    'source_url': f'https://example.org/{i}', 'visibility_status': 'HIDDEN' if i == 6 else 'VISIBLE',
                    'admin_meta_json': {}})
            c.execute(m['keyword_topic'].insert(), {'keyword_text': 'blocked', 'status': 'UNCLASSIFIED'})
            c.execute(m['article_tag'].insert(), {'article_id': 7, 'keyword_text': 'blocked'})
            c.execute(update(m['notification_setting']).where(m['notification_setting'].c.user_id == uid)
                      .values(keyword_blocks_json=[{'kw_topic': 'blocked', 'selected_terms': ['blocked']}]))

        def search(q, **params):
            response = self.client.get('/api/v1/articles', params={'q': q, 'mode': 'SEARCH', **params})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        for q, ids in [('파이낸셜뉴스', ['2', '1']), ('파이낸셜', ['8']), ('한국경제', ['3']),
                       ('kOtRa', ['9', '5', '4']), (' KOTRA ', ['9', '5', '4']),
                       ('KOTRA 해외시장뉴스', ['4']), ('KOTRA 단신속보', ['9', '5']),
                       ('경제', []), ('한국', []), ('뉴스', []), ('kotr', []), ('fnnews', []),
                       ('KOTRA_SHORT', []), ('무역', ['9', '5', '4', '3', '2', '1']), ('%', []), ('_', [])]:
            with self.subTest(query=q):
                result = search(q)
                self.assertEqual([a['article_id'] for a in result['items']], ids)
                self.assertEqual(result['page']['total'], len(ids))
        page = search('파이낸셜뉴스', page=2, page_size=1)
        self.assertEqual(page['page']['total'], 2)
        self.assertEqual([a['article_id'] for a in page['items']], ['1'])
        self.assertEqual([a['article_id'] for a in search('KOTRA', tab='BREAKING')['items']], ['5'])
        self.assertEqual(search('파이낸셜뉴스', start_date='2025-12-02', end_date='2025-12-02')['page']['total'], 1)
        expected_names = {'1': '파이낸셜뉴스', '2': '파이낸셜뉴스', '3': '한국경제',
                          '4': 'KOTRA 해외시장뉴스', '5': 'KOTRA 단신속보', '8': 'OTHER', '9': 'KOTRA 단신속보'}
        self.assertEqual({a['article_id']: a['source_name'] for a in search('본문')['items']}, expected_names)
        for identity, expected in expected_names.items():
            with self.subTest(detail=identity):
                response = self.client.get(f'/api/v1/articles/{identity}')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['article']['source_name'], expected)

    def test_saved_report_gets_source_names_without_changing_saved_content(self):
        uid = int(self.signup())
        articles = [{'article_id': str(i), 'source_code': code, 'title': '저장 기사'}
                    for i, code in enumerate(('FNNEWS', 'KOTRA_SHORTNEWS', 'OTHER', None), 1)]
        content = {'schema_version': 1, 'sections': [
            {'section_code': 'SUMMARY', 'text': '저장된 요약'},
            {'section_code': 'NEWS', 'articles': articles}], 'meta': {'freshness': 'FROZEN'}}
        table = self.meta.tables['report']
        with self.engine.begin() as c:
            rid = c.execute(table.insert().values(user_id=uid, report_type='RPT_WEEKLY',
                generation_key='b' * 64, conditions_json={}, content_json=content,
                status='READY', retry_count=0, created_at=datetime(2026, 11, 17))).inserted_primary_key[0]
        response = self.client.get(f'/api/v1/reports/{rid}')
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([a['source_name'] for a in body['sections'][1]['articles']],
                         ['파이낸셜뉴스', 'KOTRA 단신속보', 'OTHER', '출처 미제공'])
        self.assertEqual(body['sections'][0], content['sections'][0])
        self.assertEqual(body['meta'], content['meta'])
        with self.engine.connect() as c:
            self.assertEqual(c.scalar(select(table.c.content_json).where(table.c.report_id == rid)), content)
