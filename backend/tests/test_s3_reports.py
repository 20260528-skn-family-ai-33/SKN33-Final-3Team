"""Report worker/API use S3 bytes and retain the existing ownership boundary."""
import unittest
from unittest.mock import patch
from sqlalchemy import select

from backend.tests import test_backend_api as base
from backend.tests.test_s3_storage import MemoryS3
from backend.app.api.worker import run_once
from backend.app.storage.objects import S3Objects


class S3ReportIntegrationTests(unittest.TestCase):
    setUp = base.BackendApiTest.setUp
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


if __name__ == '__main__':
    unittest.main()
