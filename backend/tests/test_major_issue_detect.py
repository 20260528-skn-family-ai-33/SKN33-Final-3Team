"""주요 이슈 판정(FR-204)과 전달을 외부 연결 없이 확인합니다."""
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.api.config import Settings
from backend.app.api.database import Store
from backend.app.scheduler.jobs import major_issue_detect as job
from backend.tests.api_support import SIGNUP_CONSENTS, create_test_app, reference_data, test_engine

T = date(2026, 10, 14)
ALL_DAYS = (T - timedelta(days=62), T)


class WindowTest(unittest.TestCase):
    def test_recent_and_baseline_do_not_overlap(self):
        recent, baseline = job.windows(T)
        self.assertEqual(recent, (date(2026, 10, 8), T))
        self.assertEqual(baseline, (date(2026, 8, 13), date(2026, 10, 7)))
        self.assertEqual((recent[1] - recent[0]).days + 1, 7)
        self.assertEqual((baseline[1] - baseline[0]).days + 1, 56)

    def test_only_exact_zero_average_uses_one(self):
        self.assertEqual(job.spike(3, 0), (0.0, 3.0))
        self.assertEqual(job.spike(2, 4), (0.5, 4.0))  # 0<평균<1은 원값 유지
        self.assertEqual(job.spike(24, 64), (8.0, 3.0))


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.meta = test_engine()
        reference_data(self.engine, self.meta)
        settings = Settings(values={'API_COOKIE_SECURE': 'false', 'API_HS_VECTOR_ENABLED': 'false'})
        self.app = create_test_app(settings, self.engine)
        self.database = self.app.state.database
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)
        response = self.client.post('/api/v1/auth/signup', json={'name': '시험', 'email': 'major@example.org', 'password': 'Sample!123',
                                                                  'password_confirm': 'Sample!123', **SIGNUP_CONSENTS})  # 가입 필수 동의(#76)
        self.csrf = response.json()['csrf_token']
        self.uid = int(response.json()['user']['user_id'])
        tables = self.meta.tables
        with self.engine.begin() as c:
            for code in (job.MARKET, job.SHORT_NEWS):
                c.execute(tables['service_option'].insert(), {'option_code': code, 'option_type': 'SOURCE', 'label_ko': code, 'enabled': True, 'sort_order': 5, 'value_json': {}})

    def consent(self):
        response = self.client.post('/api/v1/users/me/consents', headers={'X-CSRF-Token': self.csrf}, json={'terms_type': 'NOTIFICATION', 'terms_version': 'TERMS_V1', 'action': 'AGREE', 'enable_notifications': True})
        self.assertEqual(response.status_code, 200, response.text)

    def collected(self, *sources):
        with self.engine.begin() as c:
            for source in sources:
                c.execute(self.meta.tables['batch_run'].insert(), {'source_code': source, 'job_name': 'NEWS', 'target_table': 'article', 'period_start': ALL_DAYS[0], 'period_end': ALL_DAYS[1], 'status': 'SUCCESS', 'detail_json': {}})

    def article(self, identity, day, *, source=job.MARKET, category='경제', title='기사', visible='VISIBLE'):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article'].insert(), {'article_id': identity, 'source_code': source, 'external_id': str(identity), 'title': title, 'published_date': day, 'source_url': 'https://example.org/' + str(identity), 'body_masked': '본문', 'category': category, 'visibility_status': visible, 'admin_meta_json': {}})

    def keyword(self, keyword, day, count, *, article=None):
        with self.engine.begin() as c:
            c.execute(self.meta.tables['news_keyword_daily'].insert(), {'metric_date': day, 'keyword_text': keyword, 'country_code': None, 'day_count': count})
            if article:
                c.execute(self.meta.tables['article_tag'].insert(), {'article_id': article, 'keyword_text': keyword})

    def events(self):
        with self.engine.begin() as c:
            return [dict(r) for r in c.execute(select(self.meta.tables['alert_event'])).mappings()]

    def test_keyword_at_threshold_creates_event_with_evidence_and_delivers_once(self):
        self.consent(); self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T, title='관세 기사')
        self.keyword('관세', T, 24, article=1)
        self.keyword('관세', T - timedelta(days=7), 64)
        self.keyword('평이', T, 23)
        self.keyword('평이', T - timedelta(days=7), 64)

        first = job.detect(self.database, T)
        self.assertEqual((first['events'], first['deliveries'], first['skipped']), (1, 1, []))
        event = self.events()[0]
        self.assertEqual((event['event_type'], event['article_id']), ('MAJOR_ISSUE', 1))
        payload = event['payload_json']
        self.assertEqual((payload['reason'], payload['recent_week_count'], payload['baseline_weekly'], payload['ratio']), ('WEEKLY_NEWS_SPIKE', 24, 8.0, 3.0))
        self.assertEqual((payload['window_start'], payload['window_end'], payload['baseline_start'], payload['baseline_end']), ('2026-10-08', '2026-10-14', '2026-08-13', '2026-10-07'))
        self.assertEqual(payload['target'], {'keyword_text': '관세'})

        again = job.detect(self.database, T)
        self.assertEqual((again['events'], again['deliveries']), (0, 0))
        self.assertEqual(len(self.events()), 1)

        item = self.client.get('/api/v1/notifications', params={'type': 'MAJOR_ISSUE'}).json()['items'][0]
        self.assertEqual((item['title'], item['target_type'], item['target_id']), ('주요 이슈: 관세', 'ARTICLE', '1'))
        self.assertIn('24건', item['message'])
        self.assertEqual((item['issue']['label'], item['issue']['current_count'], item['issue']['ratio']), ('관세', 24, 3.0))
        self.assertEqual(self.client.get('/api/v1/issues/major').json()['items'][0]['label'], '관세')

    def test_baseline_outside_window_is_not_counted(self):
        self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T)
        self.keyword('관세', T, 3, article=1)
        self.keyword('관세', T - timedelta(days=63), 1000)  # 직전 56일보다 앞
        job.detect(self.database, T)
        self.assertEqual(self.events()[0]['payload_json']['baseline_weekly'], 0.0)

    def test_incomplete_collection_skips_only_affected_judgment(self):
        self.collected(job.MARKET)  # 단신 수집 기록 없음
        self.keyword('관세', T, 24)
        self.article(1, T)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article_tag'].insert(), {'article_id': 1, 'hs_code': '330499', 'hs_ver': 'HS2022'})
            c.execute(self.meta.tables['news_product_daily'].insert(), {'metric_date': T, 'hs_code': '330499', 'hs_ver': 'HS2022', 'country_code': None, 'day_count': 9})
        result = job.detect(self.database, T)
        self.assertEqual(result['skipped'], ['keyword'])
        event = self.events()[0]
        self.assertEqual(event['payload_json']['label'], '기초화장품')
        self.assertEqual(event['payload_json']['target'], {'hs_code': '330499', 'hs_ver': 'HS2022'})

    def test_failed_day_in_window_blocks_spike_judgment(self):
        self.collected(job.SHORT_NEWS)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['batch_run'].insert(), {'source_code': job.MARKET, 'job_name': 'NEWS', 'target_table': 'article', 'period_start': ALL_DAYS[0], 'period_end': T - timedelta(days=1), 'status': 'SUCCESS', 'detail_json': {}})
            c.execute(self.meta.tables['batch_run'].insert(), {'source_code': job.MARKET, 'job_name': 'NEWS', 'target_table': 'article', 'period_start': T, 'period_end': T, 'status': 'FAILED', 'detail_json': {}})
        self.keyword('관세', T, 24)
        self.assertEqual(job.detect(self.database, T)['skipped'], ['keyword', 'product'])
        self.assertEqual(self.events(), [])

    def test_trade_regulation_only_market_source_and_once_per_article(self):
        self.consent()
        self.article(1, T, category='통상·규제', title='규제 강화')
        self.article(2, T, category='경제')
        self.article(3, T, category='통상·규제', source=job.SHORT_NEWS)
        self.article(4, T - timedelta(days=3), category='통상·규제')
        self.article(5, T, category='통상·규제', visible='EXCLUDED')
        result = job.detect(self.database, T)
        self.assertEqual((result['events'], result['deliveries'], result['skipped']), (1, 1, ['keyword', 'product']))
        payload = self.events()[0]['payload_json']
        self.assertEqual((payload['reason'], payload['label'], payload['article_ids']), ('KOTRA_TRADE_REGULATION', '규제 강화', [1]))
        self.assertEqual(job.detect(self.database, T + timedelta(days=1))['events'], 0)
        issue = self.client.get('/api/v1/issues/major').json()['items'][0]
        self.assertIsNone(issue['current_count'])
        self.assertIsNone(issue['window_start'])

    def test_notification_off_keeps_public_list_but_no_inbox(self):
        self.article(1, T, category='통상·규제')
        self.assertEqual(job.detect(self.database, T)['deliveries'], 0)
        self.assertEqual(len(self.client.get('/api/v1/issues/major').json()['items']), 1)
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 0)

    def test_blocked_keyword_is_not_delivered(self):
        self.consent(); self.collected(job.MARKET, job.SHORT_NEWS)
        table = self.meta.tables['notification_setting']
        with self.engine.begin() as c:
            c.execute(table.update().where(table.c.user_id == self.uid).values(keyword_blocks_json=[{'kw_topic': '관세', 'selected_terms': ['관세']}]))
        self.article(1, T)
        self.keyword('관세', T, 24, article=1)
        result = job.detect(self.database, T)
        self.assertEqual((result['events'], result['deliveries']), (1, 0))

    def test_synonym_of_blocked_topic_is_not_delivered(self):
        self.consent(); self.collected(job.MARKET, job.SHORT_NEWS)
        setting, topics = self.meta.tables['notification_setting'], self.meta.tables['keyword_topic']
        with self.engine.begin() as c:
            c.execute(setting.update().where(setting.c.user_id == self.uid).values(keyword_blocks_json=[{'kw_topic': '관세', 'selected_terms': ['관세']}]))
            c.execute(topics.insert(), {'keyword_text': '관세', 'status': 'UNCLASSIFIED'})
            c.execute(topics.insert(), {'keyword_text': '관세율', 'canonical_text': '관세', 'status': 'SYNONYM'})
        self.article(1, T)
        self.keyword('관세율', T, 24, article=1)
        result = job.detect(self.database, T)
        self.assertEqual((result['events'], result['deliveries']), (1, 0))
        self.assertEqual(self.events()[0]['payload_json']['target'], {'keyword_text': '관세율', 'kw_topic': '관세'})

    def test_judgment_counts_only_public_articles(self):
        self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T); self.article(2, T, visible='HIDDEN')
        self.keyword('관세', T, 4, article=1)  # 집계 4건 중 1건 숨김: 3건·주평균 0이라 3배
        self.keyword('미달', T, 3, article=1)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article_tag'].insert(), [{'article_id': 2, 'keyword_text': '관세'}, {'article_id': 2, 'keyword_text': '미달'}])
        job.detect(self.database, T)
        events = self.events()
        self.assertEqual([e['payload_json']['label'] for e in events], ['관세'])
        self.assertEqual(events[0]['payload_json']['recent_week_count'], 3)

    def test_hidden_hs6_article_is_removed_from_hs4_count_too(self):
        self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T, visible='HIDDEN')
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article_tag'].insert(), {'article_id': 1, 'hs_code': '330499', 'hs_ver': 'HS2022'})
            for code in ('330499', '3304'):
                c.execute(self.meta.tables['news_product_daily'].insert(), {'metric_date': T, 'hs_code': code, 'hs_ver': 'HS2022', 'country_code': None, 'day_count': 1})
        with self.engine.begin() as c:
            counts = job.product_counts(Store(self.database, c), T - timedelta(days=6), T)
        self.assertEqual(counts, {})

    def test_spike_without_visible_evidence_is_not_an_issue(self):
        self.consent(); self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T, visible='EXCLUDED')
        self.keyword('관세', T, 24, article=1)
        result = job.detect(self.database, T)
        self.assertEqual((result['events'], result['deliveries']), (0, 0))
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 0)

    def test_notification_opens_visible_evidence_when_representative_is_hidden(self):
        self.consent(); self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T); self.article(2, T)
        self.keyword('관세', T, 24, article=1)
        with self.engine.begin() as c:
            c.execute(self.meta.tables['article_tag'].insert(), {'article_id': 2, 'keyword_text': '관세'})
        job.detect(self.database, T)
        self.assertEqual(self.events()[0]['article_id'], 2)
        table = self.meta.tables['article']
        with self.engine.begin() as c:
            c.execute(table.update().where(table.c.article_id == 2).values(visibility_status='EXCLUDED'))
        item = self.client.get('/api/v1/notifications').json()['items'][0]
        self.assertEqual((item['target_type'], item['target_id']), ('ARTICLE', '1'))

    def test_event_without_any_evidence_opens_notification_detail(self):
        self.consent()
        tables = self.meta.tables
        with self.engine.begin() as c:
            event = c.execute(tables['alert_event'].insert(), {'event_type': 'MAJOR_ISSUE', 'event_key': 'x' * 64, 'created_at': datetime(2026, 10, 14), 'payload_json': {'label': '기존 사건', 'target': {}}}).inserted_primary_key[0]
            c.execute(tables['user_alert'].insert(), {'user_id': self.uid, 'event_id': event, 'is_interest_match': False, 'created_at': datetime(2026, 10, 14)})
        item = self.client.get('/api/v1/notifications').json()['items'][0]
        self.assertEqual((item['target_type'], item['target_id']), ('ANALYSIS', str(event)))

    def test_evidence_prefers_latest_published_over_higher_id(self):
        self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T)
        self.keyword('관세', T, 24, article=1)
        for identity in range(2, 7):
            self.article(identity, T - timedelta(days=2))
            with self.engine.begin() as c:
                c.execute(self.meta.tables['article_tag'].insert(), {'article_id': identity, 'keyword_text': '관세'})
        job.detect(self.database, T)
        event = self.events()[0]
        self.assertEqual(event['article_id'], 1)
        self.assertEqual(event['payload_json']['article_ids'], [1, 6, 5, 4, 3])

    def test_popup_group_and_ack_do_not_mark_read(self):
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        group = self.client.get('/api/v1/notifications/popups').json()['groups'][0]
        self.assertEqual(group['event_type'], 'MAJOR_ISSUE')
        self.assertEqual(self.client.post(f"/api/v1/notifications/popups/{group['delivery_id']}/ack", headers={'X-CSRF-Token': self.csrf}, json={}).status_code, 204)
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 1)
        self.assertEqual(self.client.get('/api/v1/notifications/popups').json()['groups'], [])

    def test_read_alert_is_not_popped_up(self):
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        table = self.meta.tables['user_alert']
        with self.engine.begin() as c:
            c.execute(table.update().where(table.c.user_id == self.uid).values(read_at=T))
        self.assertEqual(self.client.get('/api/v1/notifications/popups').json()['groups'], [])

    def patch_settings(self, **values):
        response = self.client.patch('/api/v1/users/me/notification-settings', headers={'X-CSRF-Token': self.csrf}, json=values)
        self.assertEqual(response.status_code, 200, response.text)

    def popup_titles(self):
        groups = self.client.get('/api/v1/notifications/popups').json()['groups']
        return [item['title'] for group in groups for item in group['notifications']]

    def test_turning_major_popup_on_shows_only_new_alerts(self):
        self.consent()
        self.patch_settings(major_issue_popup=False)
        self.article(1, T, category='통상·규제', title='꺼진 동안 받은 기사')
        job.detect(self.database, T)
        self.patch_settings(major_issue_popup=True)
        self.assertEqual(self.popup_titles(), [])
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 1)

        self.article(2, T + timedelta(days=1), category='통상·규제', title='다시 켠 뒤 기사')
        job.detect(self.database, T + timedelta(days=1))
        self.assertEqual(len(self.popup_titles()), 1)

    def test_saving_unchanged_settings_keeps_pending_popup(self):
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        self.patch_settings(major_issue_popup=True, notifications_enabled=True)
        self.assertEqual(len(self.popup_titles()), 1)

    def test_turning_other_popup_on_keeps_pending_major_popup(self):
        self.consent()
        self.patch_settings(report_popup=False)
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        self.patch_settings(report_popup=True)
        self.assertEqual(len(self.popup_titles()), 1)

    def test_enabling_all_notifications_skips_earlier_pending_popups(self):
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        self.patch_settings(notifications_enabled=False)
        self.patch_settings(notifications_enabled=True)
        self.assertEqual(self.popup_titles(), [])

    def test_agreeing_again_skips_earlier_pending_popups(self):
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        response = self.client.post('/api/v1/users/me/consents', headers={'X-CSRF-Token': self.csrf}, json={'terms_type': 'NOTIFICATION', 'terms_version': 'TERMS_V1', 'action': 'REVOKE'})
        self.assertEqual(response.status_code, 200, response.text)
        self.consent()
        self.assertEqual(self.popup_titles(), [])

    def test_major_popup_off_keeps_inbox_entry(self):
        self.consent()
        self.client.patch('/api/v1/users/me/notification-settings', headers={'X-CSRF-Token': self.csrf}, json={'major_issue_popup': False})
        self.article(1, T, category='통상·규제')
        self.assertEqual(job.detect(self.database, T)['deliveries'], 1)
        self.assertEqual(self.client.get('/api/v1/notifications/popups').json()['groups'], [])
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 1)

    def batch_row(self):
        t = self.meta.tables['batch_run']
        with self.engine.begin() as c:
            return c.execute(select(t).where(t.c.job_name == job.JOB_NAME)).mappings().first()

    def test_run_records_batch(self):
        self.collected(job.MARKET, job.SHORT_NEWS)
        self.article(1, T, category='통상·규제')
        job.run(T, engine=self.engine)
        row = self.batch_row()
        self.assertEqual((row['status'], row['target_table'], row['received_count']), ('SUCCESS', 'news_keyword_daily', 1))
        with self.engine.begin() as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(self.meta.tables['user_alert'])), 0)

    def test_skipped_judgment_is_failed_but_keeps_trade_regulation_event(self):
        self.article(1, T, category='통상·규제')
        job.run(T, engine=self.engine)
        self.assertEqual(self.batch_row()['status'], 'FAILED')
        self.assertEqual(len(self.events()), 1)
        users = self.meta.tables['users']
        with self.engine.begin() as c:
            c.execute(users.update().where(users.c.user_id == self.uid).values(role='ADMIN'))
        message = self.client.get('/api/v1/admin/alerts').json()['items'][0]['message']
        self.assertIn('주요 이슈 판정 미완료', message)
        self.assertNotIn('수집 실패', message)

    def test_final_run_rule(self):
        self.assertFalse(job.is_final_run(False, datetime(2026, 10, 15, 5, 0)))
        self.assertTrue(job.is_final_run(False, datetime(2026, 10, 15, 8, 0)))
        self.assertTrue(job.is_final_run(True, datetime(2026, 10, 15, 5, 0)))

    def test_existing_event_is_not_delivered_to_user_who_enabled_later(self):
        self.article(1, T, category='통상·규제')
        self.assertEqual(job.detect(self.database, T)['deliveries'], 0)
        self.consent()
        self.assertEqual(job.detect(self.database, T)['deliveries'], 0)
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count').json()['count'], 0)

    def test_judgment_run_does_not_replace_latest_collection_run(self):
        self.article(1, T, category='통상·규제')
        job.run(T, engine=self.engine)
        users = self.meta.tables['users']
        with self.engine.begin() as c:
            c.execute(users.update().where(users.c.user_id == self.uid).values(role='ADMIN'))
        items = self.client.get('/api/v1/admin/article-sources').json()['items']
        self.assertIsNone(next(i for i in items if i['source_code'] == job.MARKET)['latest_run_id'])

    def test_scheduler_requirements_include_bcrypt(self):
        text = (Path(__file__).resolve().parents[1] / 'requirements.txt').read_text(encoding='utf-8')
        self.assertRegex(text, r'(?m)^bcrypt\b')


if __name__ == '__main__':
    unittest.main()
