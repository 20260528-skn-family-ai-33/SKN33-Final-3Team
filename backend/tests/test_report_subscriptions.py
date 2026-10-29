"""Periodic option registration and collector-compatible subscription scheduling."""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select, update, delete
from backend.tests import test_backend_api as fixtures
from backend.app.api.database import Store
from backend.app.api.subscriptions import enqueue_due, ready_days
from backend.scripts.register_report_options import plan, apply, REPORT_OPTIONS


class ReportSubscriptionTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup

    def register(self):
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            apply(store, plan(store, lock=True))

    def news_runs(self):
        with self.engine.begin() as c:
            for source in ('KOTRA_MARKET', 'KOTRA_SHORT_NEWS'):
                c.execute(self.meta.tables['service_option'].insert(), {
                    'option_code': source, 'option_type': 'SOURCE', 'label_ko': source,
                    'enabled': True, 'sort_order': 1, 'value_json': {}})
                c.execute(self.meta.tables['batch_run'].insert(), {
                    'source_code': source, 'job_name': 'NEWS_LOAD', 'target_table': 'article',
                    'period_start': date(2025, 12, 15), 'period_end': date(2025, 12, 21),
                    'status': 'SUCCESS', 'detail_json': {}})

    def ready(self):
        with self.engine.begin() as c:
            return ready_days(Store(self.app.state.database, c), date(2025, 12, 15), date(2025, 12, 21))

    def subscribe(self, uid, code='WEEKLY', **changes):
        filters = {'hs_items': [], 'countries': ['US'], 'formats': ['PDF'],
                   'enabled_since': '2025-12-01T00:00:00Z', 'last_observed_month': '2025-12', **changes}
        with self.engine.begin() as c:
            return c.execute(self.meta.tables['report_subscription'].insert().values(
                user_id=int(uid), period_code=code, enabled=True, filters_json=filters)).inserted_primary_key[0]

    def test_registration_is_idempotent_and_preserves_existing_metadata(self):
        with self.engine.begin() as c:
            t = self.meta.tables['service_option']
            c.execute(update(t).where(t.c.option_code == 'MONTHLY').values(enabled=False, label_ko='기존 월간', value_json={'keep': True}))
        self.register()
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            self.assertEqual(plan(store)['changes'], [])
            t = store.t('service_option')
            row = store.one(select(t).where(t.c.option_code == 'MONTHLY'))
            self.assertEqual(row['label_ko'], '기존 월간')
            self.assertEqual(row['value_json'], {'keep': True})
            self.assertTrue(row['enabled'])
            self.assertEqual(len(store.rows(select(t).where(t.c.option_code.in_([r['option_code'] for r in REPORT_OPTIONS])))), 6)
            self.assertIsNotNone(store.one(select(t).where(t.c.option_code == 'CUSTOMS')))

    def test_wrong_option_type_aborts_without_partial_inserts(self):
        with self.engine.begin() as c:
            c.execute(update(self.meta.tables['service_option']).where(
                self.meta.tables['service_option'].c.option_code == 'MONTHLY').values(option_type='SOURCE'))
        with self.assertRaises(ValueError): self.register()
        with self.engine.begin() as c:
            t = self.meta.tables['service_option']
            self.assertIsNone(c.scalar(select(t.c.option_code).where(t.c.option_code == 'WEEKLY')))

    def test_actual_collectors_article_runs_are_sufficient(self):
        self.news_runs()
        self.assertTrue(self.ready())  # No fictitious news_*_daily batch rows needed.

    def test_one_source_cannot_cover_another_missing_source(self):
        self.news_runs()
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(delete(t).where(t.c.source_code == 'KOTRA_MARKET'))
        self.assertFalse(self.ready())

    def test_latest_failed_or_running_load_blocks_publication(self):
        self.news_runs()
        for status in ('FAILED', 'RUNNING', 'SUCCESS'):
            with self.engine.begin() as c:
                c.execute(self.meta.tables['batch_run'].insert(), {
                    'source_code': 'KOTRA_MARKET', 'job_name': 'NEWS_LOAD', 'target_table': 'article',
                    'period_start': date(2025, 12, 18), 'period_end': date(2025, 12, 18),
                    'status': status, 'detail_json': {}})
            self.assertEqual(self.ready(), status == 'SUCCESS')

    def test_no_sources_and_incomplete_week_are_not_ready(self):
        self.assertFalse(self.ready())
        self.news_runs()
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(update(t).where(t.c.source_code == 'KOTRA_MARKET').values(period_end=date(2025, 12, 20)))
        self.assertFalse(self.ready())

    def test_weekly_enqueue_is_scoped_idempotent_and_preserves_conditions(self):
        self.register(); self.news_runs()
        first = int(self.signup()); second = int(self.signup('second@example.org'))
        self.subscribe(first); self.subscribe(second)
        at = datetime(2025, 12, 22, 6, tzinfo=ZoneInfo('Asia/Seoul'))
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            self.assertEqual(enqueue_due(store, at, user_id=first), 1)
            self.assertEqual(enqueue_due(store, at, user_id=first), 0)
            rows = store.rows(select(store.t('report')))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['user_id'], first)
            self.assertEqual(rows[0]['conditions_json']['period'], {'start':'2025-12-15','end':'2025-12-21'})
            self.assertEqual(rows[0]['conditions_json']['country_codes'], ['US'])
            self.assertEqual(enqueue_due(store, at), 1)  # Normal scheduler still processes other subscribers.

    def test_disabled_type_and_new_this_week_subscription_wait(self):
        self.register(); self.news_runs()
        uid = int(self.signup())
        self.subscribe(uid, enabled_since='2025-12-21T16:00:00Z')  # Monday KST.
        at = datetime(2025, 12, 22, 6, tzinfo=ZoneInfo('Asia/Seoul'))
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            self.assertEqual(enqueue_due(store, at, user_id=uid), 0)
            t = store.t('report_subscription')
            row = store.one(select(t).where(t.c.user_id == uid))
            c.execute(update(t).where(t.c.user_id == uid).values(filters_json={**row['filters_json'], 'enabled_since':'2025-12-01T00:00:00Z'}))
            option = store.t('service_option')
            c.execute(update(option).where(option.c.option_code == 'RPT_WEEKLY').values(enabled=False))
            self.assertEqual(enqueue_due(store, at, user_id=uid), 0)

    def test_api_saves_all_three_subscriptions_without_immediate_backfill(self):
        self.register()
        uid = int(self.signup())
        scope = self.client.get('/api/v1/users/me/interests').json()['interests']
        r = self.client.put('/api/v1/users/me/interests', json={'hs_items': [], 'country_codes':['US'],
            'expected_version':scope['version']}, headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(r.status_code, 200, r.text)
        version = self.client.get('/api/v1/users/me/subscriptions').json()['version']
        r = self.client.put('/api/v1/users/me/subscriptions', json={'period_codes':['WEEKLY','MONTHLY','QUARTERLY'],
            'formats':['PDF','DOCX'], 'use_saved_interests':True,'expected_version':version}, headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({x['period_code'] for x in r.json()['items'] if x['enabled']}, {'WEEKLY','MONTHLY','QUARTERLY'})
        with self.engine.begin() as c:
            self.assertEqual(enqueue_due(Store(self.app.state.database,c),user_id=uid),0)


if __name__ == '__main__': unittest.main()
