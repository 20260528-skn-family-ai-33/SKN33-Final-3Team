"""Periodic option registration and collector-compatible subscription scheduling."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo
from sqlalchemy import select, update, delete
from backend.tests import test_backend_api as fixtures
from backend.app.api.database import Store
from backend.app.api.config import Settings
from backend.app.api.worker import run_loop
from backend.app.api.subscriptions import enqueue_due, ready_days, scheduled_periods
from backend.scripts.register_report_options import plan, apply, REPORT_OPTIONS


class ReportSubscriptionTest(unittest.TestCase):
    setUp = fixtures.BackendApiTest.setUp
    tearDown = fixtures.BackendApiTest.tearDown
    signup = fixtures.BackendApiTest.signup

    def register(self):
        with self.engine.begin() as c:
            store = Store(self.app.state.database, c)
            apply(store, plan(store, lock=True))

    def news_runs(self, sources=('HANKYUNG', 'FNNEWS')):
        with self.engine.begin() as c:
            for source in sources:
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
            c.execute(delete(t).where(t.c.source_code == 'HANKYUNG'))
        self.assertFalse(self.ready())

    def test_latest_failed_or_running_load_blocks_publication(self):
        self.news_runs()
        for status in ('FAILED', 'RUNNING', 'SUCCESS'):
            with self.engine.begin() as c:
                c.execute(self.meta.tables['batch_run'].insert(), {
                    'source_code': 'HANKYUNG', 'job_name': 'NEWS_LOAD', 'target_table': 'article',
                    'period_start': date(2025, 12, 18), 'period_end': date(2025, 12, 18),
                    'status': status, 'detail_json': {}})
            self.assertEqual(self.ready(), status == 'SUCCESS')

    def test_no_sources_and_incomplete_week_are_not_ready(self):
        self.assertFalse(self.ready())
        self.news_runs()
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(update(t).where(t.c.source_code == 'HANKYUNG').values(period_end=date(2025, 12, 20)))
        self.assertFalse(self.ready())

    def test_manual_kotra_history_does_not_block_two_current_weeks(self):
        self.register(); self.news_runs(('KOTRA_MARKET', 'KOTRA_SHORT_NEWS'))
        uid = self.signup()
        self.subscribe(uid, enabled_since='2025-12-15T00:00:00Z')
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(update(t).where(t.c.target_table == 'article').values(
                period_start=date(2024, 1, 1), period_end=date(2024, 12, 31)))
        self.news_runs()
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(delete(t).where(t.c.source_code.in_(('HANKYUNG', 'FNNEWS'))))
            for offset in range(14):
                day = date(2025, 12, 15) + timedelta(days=offset)
                for source in ('HANKYUNG', 'FNNEWS'):
                    c.execute(t.insert(), {'source_code': source, 'job_name': 'TRADE_NEWS_LOAD',
                        'target_table': 'article', 'period_start': day, 'period_end': day,
                        'status': 'SUCCESS', 'detail_json': {}})
            store = Store(self.app.state.database, c)
            self.assertEqual(enqueue_due(store, datetime(2025, 12, 22)), 1)
            self.assertEqual(enqueue_due(store, datetime(2025, 12, 29)), 1)
            self.assertEqual(enqueue_due(store, datetime(2025, 12, 29)), 0)

    def test_optional_shortnews_failure_only_blocks_when_explicitly_required(self):
        self.news_runs(('HANKYUNG', 'FNNEWS', 'KOTRA_SHORT_NEWS'))
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(update(t).where(t.c.source_code == 'KOTRA_SHORT_NEWS').values(status='FAILED'))
        self.assertTrue(self.ready())
        self.settings.values['API_WEEKLY_NEWS_SOURCES'] = 'HANKYUNG,FNNEWS,KOTRA_SHORT_NEWS'
        self.assertFalse(self.ready())
        with self.engine.begin() as c:
            t = self.meta.tables['batch_run']
            c.execute(update(t).where(t.c.source_code == 'KOTRA_SHORT_NEWS').values(status='SUCCESS'))
        self.assertTrue(self.ready())

    def test_required_source_must_exist_and_remain_enabled(self):
        self.news_runs(('HANKYUNG',))
        self.assertFalse(self.ready())
        self.news_runs(('FNNEWS',))
        self.assertTrue(self.ready())
        with self.engine.begin() as c:
            t = self.meta.tables['service_option']
            c.execute(update(t).where(t.c.option_code == 'FNNEWS').values(enabled=False))
        self.assertFalse(self.ready())

    def test_invalid_or_empty_required_sources_do_not_publish(self):
        self.news_runs()
        for value in ('', ' ', ',', 'HANKYUNG,TYPO', 'HANKYUNG,,FNNEWS'):
            with self.subTest(value=value):
                self.settings.values['API_WEEKLY_NEWS_SOURCES'] = value
                with self.assertRaisesRegex(ValueError, 'API_WEEKLY_NEWS_SOURCES'):
                    self.ready()

    def test_first_worker_schedule_uses_explicit_settings(self):
        self.register(); self.news_runs(('KOTRA_MARKET',))
        uid = self.signup()
        self.subscribe(uid, enabled_since='2025-12-15T00:00:00Z')
        settings = Settings(values={**self.settings.values, 'API_WEEKLY_NEWS_SOURCES': 'KOTRA_MARKET'})
        # First schedule runs before run_once normally attaches settings to Database.
        with patch('backend.app.api.worker.run_once', return_value=False), \
                patch('backend.app.api.subscriptions.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2025, 12, 22, tzinfo=ZoneInfo('Asia/Seoul'))
            run_loop(self.app.state.database, settings, once=True)
        with self.engine.begin() as c:
            rows = Store(self.app.state.database, c).rows(select(self.meta.tables['report']))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['conditions_json']['period'], {'start':'2025-12-15','end':'2025-12-21'})

    def test_invalid_weekly_config_preserves_other_cycles_and_recovers(self):
        self.register(); self.news_runs()
        for index, (value, order) in enumerate((
                ('', ('WEEKLY', 'MONTHLY', 'QUARTERLY')),
                ('HANKYUNG,TYPO', ('MONTHLY', 'QUARTERLY', 'WEEKLY')))):
            with self.subTest(value=value, order=order):
                uid = int(self.signup(f'cycles{index}@example.org'))
                for code in order:
                    since = '2025-10-21T00:00:00Z' if code == 'QUARTERLY' else '2025-12-16T00:00:00Z'
                    self.subscribe(uid, code, enabled_since=since)
                self.settings.values['API_WEEKLY_NEWS_SOURCES'] = value
                with self.assertLogs('backend.app.api.subscriptions', level='WARNING') as logged:
                    with self.engine.begin() as c:
                        self.assertEqual(enqueue_due(Store(self.app.state.database, c), datetime(2026,1,20), user_id=uid), 2)
                self.assertEqual(len(logged.output), 1)
                self.assertIn('API_WEEKLY_NEWS_SOURCES', logged.output[0])
                self.assertIn('weekly', logged.output[0])
                # Inspect after commit: monthly/quarterly inserts were not rolled back.
                with self.engine.begin() as c:
                    t = self.meta.tables['report']
                    self.assertEqual(set(c.execute(select(t.c.report_type).where(t.c.user_id == uid)).scalars()),
                                     {'RPT_MONTHLY', 'RPT_QUARTERLY'})
                self.settings.values['API_WEEKLY_NEWS_SOURCES'] = 'HANKYUNG,FNNEWS'
                with self.engine.begin() as c:
                    store = Store(self.app.state.database, c)
                    self.assertEqual(enqueue_due(store, datetime(2026,1,20), user_id=uid), 1)
                    self.assertEqual(enqueue_due(store, datetime(2026,1,20), user_id=uid), 0)

    def test_invalid_weekly_config_does_not_affect_monthly_only_worker(self):
        self.register(); uid = self.signup()
        self.subscribe(uid, 'MONTHLY', enabled_since='2025-12-16T00:00:00Z')
        self.settings.values['API_WEEKLY_NEWS_SOURCES'] = 'TYPO'
        with patch('backend.app.api.worker.run_once', return_value=False), \
                patch('backend.app.api.subscriptions.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026,1,15,tzinfo=ZoneInfo('Asia/Seoul'))
            run_loop(self.app.state.database, self.settings, once=True)
        with self.engine.begin() as c:
            self.assertEqual(len(Store(self.app.state.database, c).rows(select(self.meta.tables['report']))), 1)

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

    def test_monthly_waits_until_15th_even_when_data_was_ready_at_signup(self):
        self.register(); uid=int(self.signup())
        self.subscribe(uid, 'MONTHLY', enabled_since='2025-12-16T00:00:00Z')
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            # 14:59 UTC is still Jan 14 KST; midnight KST starts publication.
            self.assertEqual(enqueue_due(store,datetime(2026,1,14,14,59,tzinfo=timezone.utc)),0)
            self.assertEqual(enqueue_due(store,datetime(2026,1,14,15,tzinfo=timezone.utc)),1)
            self.assertEqual(enqueue_due(store,datetime(2026,1,16,tzinfo=ZoneInfo('Asia/Seoul'))),0)
            row=store.one(select(store.t('report')))
            self.assertEqual(row['conditions_json']['period'],{'start':'2025-12-01','end':'2025-12-31'})

    def test_quarterly_waits_until_january_20th(self):
        self.register(); uid=int(self.signup())
        self.subscribe(uid,'QUARTERLY',enabled_since='2025-10-21T00:00:00Z')
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(enqueue_due(store,datetime(2026,1,19,23,59)),0)
            self.assertEqual(enqueue_due(store,datetime(2026,1,20)),1)
            row=store.one(select(store.t('report')))
            self.assertEqual(row['conditions_json']['period'],{'start':'2025-10-01','end':'2025-12-31'})
            self.assertEqual(row['conditions_json']['compare_period'],{'start':'2024-10-01','end':'2024-12-31'})

    def test_weekly_late_data_is_not_lost_after_next_monday(self):
        self.register(); self.news_runs(); uid=int(self.signup())
        self.subscribe(uid,enabled_since='2025-12-15T00:00:00Z')
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c); t=store.t('batch_run')
            c.execute(update(t).where(t.c.target_table=='article').values(status='FAILED'))
            self.assertEqual(enqueue_due(store,datetime(2025,12,21,23,59)),0)
            self.assertEqual(enqueue_due(store,datetime(2025,12,22)),0)
            c.execute(update(t).where(t.c.target_table=='article').values(status='SUCCESS'))
            self.assertEqual(enqueue_due(store,datetime(2025,12,30)),1)
            self.assertEqual(enqueue_due(store,datetime(2025,12,30)),0)
            row=store.one(select(store.t('report')))
            self.assertEqual(row['conditions_json']['period'],{'start':'2025-12-15','end':'2025-12-21'})

    def test_monthly_catches_up_multiple_ready_months_after_restart(self):
        self.register(); uid=int(self.signup())
        self.subscribe(uid,'MONTHLY',enabled_since='2025-10-16T00:00:00Z')
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c)
            self.assertEqual(enqueue_due(store,datetime(2026,1,16)),3)
            self.assertEqual(enqueue_due(store,datetime(2026,1,16)),0)
            rows=store.rows(select(store.t('report')).order_by(store.t('report').c.report_id))
            self.assertEqual([r['conditions_json']['period']['start'] for r in rows],
                             ['2025-10-01','2025-11-01','2025-12-01'])

    def test_due_monthly_waits_for_failed_import_then_retries(self):
        self.register(); uid=int(self.signup())
        self.subscribe(uid,'MONTHLY',enabled_since='2025-12-16T00:00:00Z')
        with self.engine.begin() as c:
            store=Store(self.app.state.database,c); t=store.t('batch_run')
            c.execute(t.insert(),{'source_code':'CUSTOMS','job_name':'HS6_AGGREGATION',
                'target_table':'import_summary','period_start':date(2025,12,1),
                'period_end':date(2025,12,31),'status':'FAILED','detail_json':{}})
            self.assertEqual(enqueue_due(store,datetime(2026,1,15)),0)
            c.execute(update(t).where(t.c.target_table=='import_summary').values(status='SUCCESS'))
            self.assertEqual(enqueue_due(store,datetime(2026,1,16)),1)
            self.assertEqual(enqueue_due(store,datetime(2026,1,16)),0)


class PublicationCalendarTest(unittest.TestCase):
    def dates(self, code, since, at):
        return [p for p,_ in scheduled_periods(code,since,at)]

    def test_quarter_boundaries_and_year_rollover(self):
        for due,start,end in [('2026-01-20','2025-10-01','2025-12-31'),
                              ('2026-04-20','2026-01-01','2026-03-31'),
                              ('2026-07-20','2026-04-01','2026-06-30'),
                              ('2026-10-20','2026-07-01','2026-09-30')]:
            with self.subTest(due=due):
                since=due[:7]+'-01T00:00:00Z'
                self.assertEqual(self.dates('QUARTERLY',since,datetime.fromisoformat(due).replace(day=19)),[])
                self.assertEqual(self.dates('QUARTERLY',since,datetime.fromisoformat(due)),[{'start':start,'end':end}])

    def test_same_day_subscription_starts_next_cycle_and_never_backfills(self):
        for code,since,at in [('WEEKLY','2025-12-21T15:00:00Z',datetime(2025,12,28)),
                              ('MONTHLY','2026-01-14T15:00:00Z',datetime(2026,2,14)),
                              ('QUARTERLY','2026-01-19T15:00:00Z',datetime(2026,4,19))]:
            with self.subTest(code=code):
                self.assertEqual(self.dates(code,since,at),[])

    def test_monthly_february_leap_year_and_no_early_publication(self):
        since='2024-02-16T00:00:00Z'
        self.assertEqual(self.dates('MONTHLY',since,datetime(2024,3,14)),[])
        self.assertEqual(self.dates('MONTHLY',since,datetime(2024,3,15)),
                         [{'start':'2024-02-01','end':'2024-02-29'}])

    def test_weekly_monday_year_rollover(self):
        since='2025-12-29T00:00:00Z'
        self.assertEqual(self.dates('WEEKLY',since,datetime(2026,1,4)),[])
        self.assertEqual(self.dates('WEEKLY',since,datetime(2026,1,5)),
                         [{'start':'2025-12-29','end':'2026-01-04'}])


if __name__ == '__main__': unittest.main()
