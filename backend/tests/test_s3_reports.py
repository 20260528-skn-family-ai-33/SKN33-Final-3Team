"""Report worker/API use S3 bytes and retain the existing ownership boundary."""
import unittest
import os
import zipfile
from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch
from sqlalchemy import select

from backend.tests import test_backend_api as base
from backend.tests.test_s3_storage import MemoryS3
from backend.app.api.worker import run_once
from backend.app.storage.objects import S3Objects


class S3ReportIntegrationTests(unittest.TestCase):
    def setUp(self):
        from backend.app.api.report_storage import _s3_report_storage
        _s3_report_storage.cache_clear()
        self.addCleanup(_s3_report_storage.cache_clear)
        base.BackendApiTest.setUp(self)
    tearDown = base.BackendApiTest.tearDown
    signup = base.BackendApiTest.signup
    post = base.BackendApiTest.post

    def request_report(self):
        self.signup()
        self.settings.values.update(ARTIFACT_STORAGE='s3', ARTIFACT_S3_BUCKET='test-bucket')
        preview = self.post('reports/preview', {'conditions': {'period': {'start': '2025-12-01', 'end': '2025-12-31'},
            'hs_items': [{'hs_code': '330499', 'hs_ver': 'HS2022'}]}})
        self.assertEqual(preview.status_code, 200, preview.text)
        response = self.post('reports', {'preview_id': preview.json()['preview_id'], 'confirm_conditions': True,
            'sections': ['KPI', 'TOP_COUNTRIES'], 'formats': ['PDF', 'DOCX']}, **{'Idempotency-Key': 's3-report'})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['job']['report_id']

    def test_ready_and_download_all_formats_without_local_report_files(self):
        rid = self.request_report()
        client = MemoryS3()
        storage = S3Objects(self.settings.values, client=client)
        with patch('backend.app.api.report_storage.S3Objects', return_value=storage), \
             patch('backend.app.api.reports.report_pdf', return_value=b'%PDF-test'), \
             patch('backend.app.api.reports.report_docx', return_value=b'PK-docx'):
            self.assertTrue(run_once(self.app.state.database, self.settings))
            with self.engine.connect() as connection:
                table = self.meta.tables['report']
                row = connection.execute(select(table).where(table.c.report_id == int(rid))).mappings().one()
                self.assertEqual(row['status'], 'READY')
                self.assertEqual(set(row['files_json']), {'PDF', 'DOCX', 'XLSX'})
                self.assertTrue(all(m['storage'] == 's3' for m in row['files_json'].values()))
            self.assertFalse(list(self.settings.files.rglob('report.*')))
            for fmt in ('pdf', 'docx', 'xlsx'):
                response = self.client.get(f'/api/v1/reports/{rid}/files/{fmt}')
                self.assertEqual(response.status_code, 200, response.text[:50])
                self.assertTrue(response.content.startswith(b'%PDF' if fmt == 'pdf' else b'PK'))
            self.signup('bob@example.org')
            self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/files/pdf').status_code, 404)

    def test_partial_upload_failure_never_marks_report_ready(self):
        rid = self.request_report()
        client = MemoryS3()
        client.fail = lambda key: 'report.xlsx.' in key
        storage = S3Objects(self.settings.values, client=client)
        with patch('backend.app.api.report_storage.S3Objects', return_value=storage), \
             patch('backend.app.api.reports.report_pdf', return_value=b'%PDF-test'), \
             patch('backend.app.api.reports.report_docx', return_value=b'PK-docx'):
            self.assertTrue(run_once(self.app.state.database, self.settings))
        with self.engine.connect() as connection:
            table = self.meta.tables['report']
            row = connection.execute(select(table).where(table.c.report_id == int(rid))).mappings().one()
            self.assertNotEqual(row['status'], 'READY')
            self.assertFalse(row['files_json'])
        self.assertTrue(any('report.pdf.' in key for key in client.data))

    @unittest.skipUnless(os.environ.get('RUN_REPORT_PDF_TESTS') == '1', 'Requires installed Playwright Chromium')
    def test_subscription_formats_generate_notify_and_download_with_s3(self):
        from backend.tests.test_report_subscriptions import ReportSubscriptionTest
        from backend.app.api.database import Store
        from backend.app.api.subscriptions import enqueue_due
        from pypdf import PdfReader

        ReportSubscriptionTest.register(self)
        with self.engine.begin() as connection:
            connection.execute(self.meta.tables['article'].insert(), {
                'article_id': 1, 'source_code': 'KOTRA_MARKET', 'external_id': 'subscription-formats',
                'title': '구독 발행 검수 기사', 'published_date': date(2025, 12, 1),
                'source_url': 'https://example.org/test-news', 'body_masked': '합성 기사 내용',
                'category': '통상·규제', 'visibility_status': 'VISIBLE', 'admin_meta_json': {}})
        self.settings.values.update(ARTIFACT_STORAGE='s3', ARTIFACT_S3_BUCKET='test-bucket')
        storage = S3Objects(self.settings.values, client=MemoryS3())
        with patch('backend.app.api.report_storage.S3Objects', return_value=storage):
            for index, selected in enumerate((['PDF'], ['DOCX'], ['PDF', 'DOCX'])):
                with self.subTest(formats=selected):
                    uid = int(self.signup(f'formats{index}@example.org'))
                    headers = {'X-CSRF-Token': self.csrf}
                    interests = self.client.get('/api/v1/users/me/interests').json()['interests']
                    response = self.client.put('/api/v1/users/me/interests', headers=headers, json={
                        'hs_items': [], 'country_codes': ['US'], 'expected_version': interests['version']})
                    self.assertEqual(response.status_code, 200, response.text)
                    response = self.post('users/me/consents', {'terms_type': 'NOTIFICATION',
                        'terms_version': 'TERMS_V1', 'action': 'AGREE', 'enable_notifications': True})
                    self.assertEqual(response.status_code, 200, response.text)
                    version = self.client.get('/api/v1/users/me/subscriptions').json()['version']
                    with patch('backend.app.api.reports.now', return_value=datetime(2025, 12, 16)):
                        response = self.client.put('/api/v1/users/me/subscriptions', headers=headers, json={
                            'period_codes': ['MONTHLY'], 'formats': selected,
                            'use_saved_interests': True, 'expected_version': version})
                    self.assertEqual(response.status_code, 200, response.text)
                    with self.engine.begin() as connection:
                        self.assertEqual(enqueue_due(Store(self.app.state.database, connection),
                                                     datetime(2026, 1, 15), user_id=uid), 1)
                    self.assertTrue(run_once(self.app.state.database, self.settings))
                    with self.engine.connect() as connection:
                        table = self.meta.tables['report']
                        row = connection.execute(select(table).where(table.c.user_id == uid)).mappings().one()
                    rid = str(row['report_id'])
                    self.assertEqual(row['status'], 'READY', row['content_json'])
                    self.assertEqual(set(row['files_json']), set(selected) | {'XLSX'})
                    self.assertTrue(all(item['storage'] == 's3' for item in row['files_json'].values()))
                    alerts = self.client.get('/api/v1/notifications').json()['items']
                    self.assertEqual(len(alerts), 1)
                    self.assertEqual(alerts[0]['event_type'], 'REPORT')
                    self.assertEqual(alerts[0]['target_id'], rid)
                    for fmt in (*selected, 'XLSX'):
                        response = self.client.get(f'/api/v1/reports/{rid}/files/{fmt.lower()}')
                        self.assertEqual(response.status_code, 200, response.text[:60])
                        if fmt == 'PDF':
                            self.assertTrue(PdfReader(BytesIO(response.content)).pages)
                        else:
                            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                                self.assertIn('word/document.xml' if fmt == 'DOCX' else 'xl/workbook.xml', archive.namelist())
                    if 'DOCX' not in selected:
                        self.assertEqual(self.client.get(f'/api/v1/reports/{rid}/files/docx').status_code, 404)


if __name__ == '__main__':
    unittest.main()
