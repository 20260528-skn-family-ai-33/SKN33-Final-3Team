"""Report deletion contract, using SQLite and fake Redis only."""
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from backend.app.api.common import now
from backend.app.api.config import Settings
from backend.app.api.database import Store
from backend.app.api import reports
from backend.tests.api_support import create_test_app, reference_data, test_engine, SIGNUP_CONSENTS


class ReportDeletionTest(unittest.TestCase):
    def setUp(self):
        self.engine, self.meta = test_engine()
        reference_data(self.engine, self.meta)
        self.client = TestClient(create_test_app(Settings(values={'API_COOKIE_SECURE': 'false'}), self.engine))
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.client.close)
        self.uid, self.headers = self.signup('owner@example.org')

    def signup(self, email):
        response = self.client.post('/api/v1/auth/signup', json={
            'name': '검수', 'email': email, 'password': 'Sample!123',
            'password_confirm': 'Sample!123', **SIGNUP_CONSENTS})
        self.assertEqual(response.status_code, 201, response.text)
        return int(response.json()['user']['user_id']), {'X-CSRF-Token': response.json()['csrf_token']}

    def report(self, kind='RPT_ON_DEMAND', status='READY'):
        with self.engine.begin() as c:
            return c.execute(self.meta.tables['report'].insert().values(
                user_id=self.uid, report_type=kind, generation_key=f'{kind}-{status}',
                status=status, retry_count=0, created_at=now(),
                conditions_json={'hs_items': [], 'country_codes': [], 'period': {'start': '2025-01-01', 'end': '2025-12-31'}},
                content_json={'schema_version': 1, 'sections': [], 'meta': {}},
                files_json={'PDF': {'storage': 'local', 'key': 'not-read.pdf'}}
            )).inserted_primary_key[0]

    def test_deletion_hides_every_read_path_and_preserves_generation_key(self):
        for kind in ('RPT_ON_DEMAND', 'RPT_WEEKLY', 'RPT_MONTHLY', 'RPT_QUARTERLY'):
            with self.subTest(kind=kind):
                rid = self.report(kind)
                path = f'/api/v1/reports/{rid}'
                self.assertEqual(self.client.get(path).status_code, 200)
                for _ in range(2):
                    self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 204)
                for suffix in ('', '/status', '/files/pdf', '/files/docx', '/files/xlsx'):
                    self.assertEqual(self.client.get(path + suffix).status_code, 404)
                listing = self.client.get('/api/v1/reports?page_size=1').json()
                self.assertEqual(listing['items'], [])
                self.assertEqual(listing['page']['total'], 0)
                with self.engine.connect() as c:
                    row = c.execute(select(self.meta.tables['report']).where(self.meta.tables['report'].c.report_id == rid)).mappings().one()
                    self.assertEqual(row['generation_key'], f'{kind}-READY')
                    self.assertTrue(row['conditions_json']['deleted_at'])
                    self.assertIn('PDF', row['files_json'])

    def test_csrf_and_ownership_are_required(self):
        rid = self.report()
        path = f'/api/v1/reports/{rid}'
        self.assertEqual(self.client.delete(path).status_code, 403)
        _, other_headers = self.signup('other@example.org')
        self.assertEqual(self.client.delete(path, headers=other_headers).status_code, 404)
        with self.engine.connect() as c:
            conditions = c.scalar(select(self.meta.tables['report'].c.conditions_json).where(self.meta.tables['report'].c.report_id == rid))
            self.assertNotIn('deleted_at', conditions)

    def test_worker_completion_cannot_restore_deleted_report(self):
        for status in ('QUEUED', 'RUNNING', 'DELAYED', 'FAILED'):
            with self.subTest(status=status):
                rid = self.report(status=status)
                self.assertEqual(self.client.delete(f'/api/v1/reports/{rid}', headers=self.headers).status_code, 204)
                # Worker completion writes status/content/files, never conditions_json.
                with self.engine.begin() as c:
                    t = self.meta.tables['report']
                    c.execute(update(t).where(t.c.report_id == rid).values(status='READY', content_json={'schema_version': 1, 'sections': [], 'meta': {}}))
                self.assertEqual(self.client.get(f'/api/v1/reports/{rid}').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/reports').json()['items'], [])

    def test_delete_removes_only_notifications_for_that_report(self):
        rid = self.report()
        other = self.report('RPT_MONTHLY')
        with self.engine.begin() as c:
            for target in (rid, other):
                event = c.execute(self.meta.tables['alert_event'].insert().values(
                    event_type='REPORT', report_id=target, event_key=f'report-{target}',
                    payload_json={}, created_at=now())).inserted_primary_key[0]
                c.execute(self.meta.tables['user_alert'].insert().values(
                    user_id=self.uid, event_id=event, is_interest_match=False, created_at=now()))
        response = self.client.delete(f'/api/v1/reports/{rid}', headers=self.headers)
        self.assertEqual(response.status_code, 204)
        with self.engine.connect() as c:
            a, e = self.meta.tables['user_alert'], self.meta.tables['alert_event']
            self.assertEqual(c.scalars(select(e.c.report_id).join(a, a.c.event_id == e.c.event_id)).all(), [other])

    def test_deleted_periodic_report_does_not_emit_an_alert_on_completion(self):
        rid = self.report('RPT_MONTHLY', 'QUEUED')
        with self.engine.begin() as c:
            t = self.meta.tables['report']
            c.execute(update(t).where(t.c.report_id == rid).values(subscription_id=1))
        self.assertEqual(self.client.delete(f'/api/v1/reports/{rid}', headers=self.headers).status_code, 204)
        storage = MagicMock()
        storage.write.return_value = {'key': 'test.pdf'}
        job = {'subject_user_id': self.uid, 'payload_json': {'report_id': str(rid)}, 'claim_token': 'test'}
        with patch('backend.app.api.jobs.report_step'), \
             patch('backend.app.api.reports.build_document', return_value={'sections': [], 'meta': {}}), \
             patch('backend.app.services.report_context.build_context', return_value={}), \
             patch('backend.app.api.reports.report_pdf', return_value=b'pdf'), \
             patch('backend.app.api.reports.report_xlsx', return_value=b'xlsx'), \
             patch('backend.app.api.report_storage.report_storage', return_value=storage), \
             patch('backend.app.api.notifications.settings') as settings:
            with self.engine.begin() as c:
                reports.generate(Store(self.client.app.state.database, c), job, Settings(values={}))
            settings.assert_not_called()
        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/reports').json()['items'], [])
