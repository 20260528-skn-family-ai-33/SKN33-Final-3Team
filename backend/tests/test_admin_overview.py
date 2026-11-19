"""Current admin totals, without inventing historical monthly snapshots."""
import unittest
from types import SimpleNamespace
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, Boolean, String, Date, DateTime, JSON, update
from backend.app.api import admin
from backend.tests import test_backend_api as fixtures


class OverviewCountsTest(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        meta=MetaData()
        self.users=Table('users',meta,Column('user_id',Integer,primary_key=True),Column('account_status',String),Column('created_at',DateTime))
        self.subs=Table('report_subscription',meta,Column('subscription_id',Integer,primary_key=True),Column('user_id',Integer),Column('enabled',Boolean),Column('filters_json',JSON))
        Table('api_admin_monthly_snapshot', meta, Column('month', Date, primary_key=True), Column('total_members', Integer), Column('active_subscriptions', Integer), Column('observed_at', DateTime))
        meta.create_all(self.engine)
        self.tables=meta.tables
        self.addCleanup(self.engine.dispose)

    def overview(self):
        with self.engine.connect() as c:
            return admin.overview(SimpleNamespace(t=lambda name:self.tables[name],one=lambda stmt:dict(c.execute(stmt).mappings().one()),rows=lambda stmt:list(c.execute(stmt).mappings())))

    def test_empty_counts_are_zero_not_missing(self):
        result=self.overview()
        self.assertEqual(result['current'],{'total_members':0,'active_subscriptions':0})
        self.assertEqual(len(result['history']),12)
        self.assertTrue(all(row['total_members'] == 0 for row in result['history'][:-1]))

    def test_total_matches_all_members_and_enabled_counts_periods_not_people(self):
        with self.engine.begin() as c:
            c.execute(self.users.insert(),[{'user_id':1,'account_status':'ACTIVE'},{'user_id':2,'account_status':'WITHDRAWN'}])
            c.execute(self.subs.insert(),[{'user_id':1,'enabled':True},{'user_id':1,'enabled':True},{'user_id':1,'enabled':False}])
        self.assertEqual(self.overview()['current'],{'total_members':2,'active_subscriptions':2})
        with self.engine.begin() as c:
            c.execute(update(self.subs).values(enabled=False))
        self.assertEqual(self.overview()['current']['active_subscriptions'],0)


class OverviewAccessTest(unittest.TestCase):
    setUp=fixtures.BackendApiTest.setUp
    tearDown=fixtures.BackendApiTest.tearDown
    signup=fixtures.BackendApiTest.signup

    def test_overview_requires_admin_and_returns_current_counts(self):
        self.assertEqual(self.client.get('/api/v1/admin/overview').status_code,401)
        uid=self.signup()
        self.assertEqual(self.client.get('/api/v1/admin/overview').status_code,403)
        with self.engine.begin() as c:
            t=self.meta.tables['users']
            c.execute(update(t).where(t.c.user_id==int(uid)).values(role='ADMIN'))
        response=self.client.get('/api/v1/admin/overview')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['current'],{'total_members':1,'active_subscriptions':0})
        self.assertEqual(len(response.json()['history']),12)
        self.assertEqual(response.json()['history'][-1]['total_members'],1)
        self.assertTrue(response.json()['as_of'].endswith('Z'))
