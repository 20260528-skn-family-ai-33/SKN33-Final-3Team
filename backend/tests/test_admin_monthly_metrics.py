import unittest
from datetime import datetime
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, Boolean, DateTime, JSON, update
from backend.app.api.database import Database, Store
from backend.app.api.admin_metrics import current_counts, monthly_history


class MonthlyMetricsTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        meta=MetaData()
        self.users=Table('users',meta,Column('user_id',Integer,primary_key=True),Column('created_at',DateTime))
        self.subs=Table('report_subscription',meta,Column('id',Integer,primary_key=True),Column('enabled',Boolean),Column('filters_json',JSON))
        meta.create_all(self.engine)
        self.db=Database(self.engine)
        self.addCleanup(self.engine.dispose)

    def test_twelve_month_window_preserves_missing_and_zero_with_current_value(self):
        with self.engine.begin() as c:
            s=Store(self.db,c)
            rows=monthly_history(s,{'total_members':2,'active_subscriptions':1},datetime(2025,12,31,15))
            self.assertEqual(len(rows),12)
            self.assertEqual((rows[0]['month'],rows[-1]['month']),('2025-02','2026-01'))
            self.assertEqual(rows[0]['total_members'],0)
            self.assertEqual(rows[0]['active_subscriptions'],0)
            self.assertEqual(rows[-2]['total_members'],0)
            self.assertEqual(rows[-1]['total_members'],2)
            self.assertTrue(rows[-1]['is_current'])
            self.assertFalse(rows[-2]['is_current'])

    def test_registration_backfill_uses_kst_month_end_and_cumulative_members(self):
        with self.engine.begin() as c:
            c.execute(self.users.insert(),[
                {'user_id':1,'created_at':datetime(2026,9,1)},
                {'user_id':2,'created_at':datetime(2026,10,31,14,59,59)},
                {'user_id':3,'created_at':datetime(2026,10,31,15,0,0)}])
            s=Store(self.db,c)
            rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
            by_month={row['month']:row for row in rows}
            self.assertEqual(by_month['2026-09']['total_members'],1)
            self.assertEqual(by_month['2026-10']['total_members'],2)
            self.assertEqual(by_month['2026-10']['member_source'],'REGISTRATION')
            self.assertEqual(by_month['2026-10']['active_subscriptions'],0)
            self.assertEqual(by_month['2026-11']['total_members'],3)

    def test_unknown_join_dates_are_not_guessed(self):
        with self.engine.begin() as c:
            c.execute(self.users.insert(),{'user_id':1,'created_at':None})
            s=Store(self.db,c)
            rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
            self.assertIsNone(rows[-3]['total_members'])
            self.assertIsNone(rows[-2]['total_members'])
            self.assertIsNone(rows[-2]['member_source'])
            self.assertEqual(rows[-1]['total_members'],1)

    def test_active_cohort_start_dates_use_kst_boundary_and_exclude_cancelled(self):
        with self.engine.begin() as c:
            c.execute(self.subs.insert(),[
                {'enabled':True,'filters_json':{'enabled_since':'2026-10-31T14:59:59Z'}},
                {'enabled':True,'filters_json':{'enabled_since':'2026-10-31T15:00:00Z'}},
                {'enabled':True,'filters_json':{'enabled_since':'2026-10-01T09:00:00+09:00'}},
                {'enabled':False,'filters_json':{'enabled_since':'2026-09-01T00:00:00Z'}}])
            s=Store(self.db,c)
            rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
            self.assertEqual(rows[-2]['active_subscriptions'],2)
            self.assertEqual(rows[-2]['subscription_source'],'START_DATE')
            self.assertEqual(rows[-1]['active_subscriptions'],3)
            c.execute(update(self.subs).values(enabled=False))
            rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
            self.assertEqual(rows[-2]['active_subscriptions'],0)
            self.assertEqual(rows[-2]['subscription_source'],'START_DATE')

    def test_missing_or_invalid_active_start_dates_are_not_zero(self):
        for value in [None,'invalid','2026-10-01T12:00:00']:
            with self.subTest(value=value), self.engine.begin() as c:
                c.execute(self.subs.delete())
                c.execute(self.subs.insert(),{'enabled':True,'filters_json':{'enabled_since':value}})
                s=Store(self.db,c)
                rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
                self.assertIsNone(rows[-2]['active_subscriptions'])
                self.assertEqual(rows[-1]['active_subscriptions'],1)
