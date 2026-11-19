import unittest
from datetime import datetime, date
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, Boolean, Date, DateTime, JSON, select
from backend.app.api.database import Database, Store
from backend.app.api.admin_metrics import current_counts, monthly_history, capture_month_end
from backend.app.scheduler.jobs.admin_monthly_snapshot import register_jobs
from apscheduler.schedulers.blocking import BlockingScheduler


class MonthlyMetricsTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        meta=MetaData()
        self.users=Table('users',meta,Column('user_id',Integer,primary_key=True),Column('created_at',DateTime))
        self.subs=Table('report_subscription',meta,Column('id',Integer,primary_key=True),Column('enabled',Boolean),Column('filters_json',JSON))
        self.history=Table('api_admin_monthly_snapshot',meta,Column('month',Date,primary_key=True),Column('total_members',Integer),Column('active_subscriptions',Integer),Column('observed_at',DateTime))
        meta.create_all(self.engine)
        self.db=Database(self.engine)
        self.addCleanup(self.engine.dispose)

    def test_kst_boundary_capture_is_immutable_and_off_boundary_is_not_backfilled(self):
        with self.engine.begin() as c:
            c.execute(self.users.insert(),{'user_id':1})
            c.execute(self.subs.insert(),{'enabled':True})
            s=Store(self.db,c)
            self.assertFalse(capture_month_end(s,datetime(2026,11,30,14,59,59)))
            self.assertTrue(capture_month_end(s,datetime(2026,11,30,15,0,1)))
            c.execute(self.users.insert(),{'user_id':2})
            self.assertFalse(capture_month_end(s,datetime(2026,11,30,15,0,5)))
            self.assertFalse(capture_month_end(s,datetime(2026,12,1,15)))
            self.assertFalse(capture_month_end(s,datetime(2026,11,30,15,1)))
            rows=s.rows(select(self.history))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['month'],date(2026,11,1))
            self.assertEqual(rows[0]['total_members'],1)

    def test_twelve_month_window_preserves_missing_and_zero_with_current_value(self):
        with self.engine.begin() as c:
            c.execute(self.history.insert(),[
                {'month':date(2025,1,1),'total_members':8,'active_subscriptions':2},
                {'month':date(2025,12,1),'total_members':0,'active_subscriptions':0}])
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

    def test_schedule_is_month_boundary_in_korea_without_startup_backfill(self):
        scheduler=BlockingScheduler()
        register_jobs(scheduler)
        self.assertEqual(len(scheduler.get_jobs()),1)
        trigger=scheduler.get_job('admin_monthly_snapshot').trigger
        next_run=trigger.get_next_fire_time(None,datetime.fromisoformat('2026-11-19T10:00:00+09:00'))
        self.assertEqual(next_run.isoformat(),'2026-12-01T00:00:00+09:00')

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

    def test_saved_snapshot_wins_and_unknown_join_dates_are_not_guessed(self):
        with self.engine.begin() as c:
            c.execute(self.users.insert(),{'user_id':1,'created_at':None})
            c.execute(self.history.insert(),{'month':date(2026,10,1),'total_members':7,'active_subscriptions':2})
            s=Store(self.db,c)
            rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
            self.assertIsNone(rows[-3]['total_members'])
            self.assertEqual(rows[-2]['total_members'],7)
            self.assertEqual(rows[-2]['member_source'],'SNAPSHOT')
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
            c.execute(self.history.insert(),{'month':date(2026,10,1),'total_members':7,'active_subscriptions':8})
            rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
            self.assertEqual(rows[-2]['active_subscriptions'],8)
            self.assertEqual(rows[-2]['subscription_source'],'SNAPSHOT')

    def test_missing_or_invalid_active_start_dates_are_not_zero(self):
        for value in [None,'invalid','2026-10-01T12:00:00']:
            with self.subTest(value=value), self.engine.begin() as c:
                c.execute(self.subs.delete())
                c.execute(self.subs.insert(),{'enabled':True,'filters_json':{'enabled_since':value}})
                s=Store(self.db,c)
                rows=monthly_history(s,current_counts(s),datetime(2026,11,19))
                self.assertIsNone(rows[-2]['active_subscriptions'])
                self.assertEqual(rows[-1]['active_subscriptions'],1)
