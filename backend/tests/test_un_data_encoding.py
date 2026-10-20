"""CP949 기본 텍스트 인코딩을 모의하여 UN JSON 경로를 실제 실행한다."""
import io
import json
import hashlib
import unittest
from contextlib import contextmanager
from datetime import date
from unittest import mock
from backend.tests import test_un_data_refresh as fixtures
from backend.tests import test_un_data_bootstrap as bootstrap
from backend.app.scheduler.jobs import un_data_refresh as job
from backend.app.scheduler.collectors.un_data.api import Client


@contextmanager
def cp949_default():
    original = io.open

    def open_cp949(file, mode='r', buffering=-1, encoding=None, errors=None,
                   newline=None, closefd=True, opener=None):
        if 'b' not in mode and encoding in (None, 'locale'):
            encoding = 'cp949'
        return original(file, mode, buffering, encoding, errors, newline, closefd, opener)

    original_text_encoding = io.text_encoding
    def simulated_text_encoding(encoding, stacklevel=2):
        return 'locale' if encoding is None else original_text_encoding(encoding, stacklevel)

    with mock.patch('io.open', side_effect=open_cp949), mock.patch('io.text_encoding', side_effect=simulated_text_encoding):
        yield


class EncodingTest(unittest.TestCase):
    setUp = fixtures.MonthlyRefreshTest.setUp
    tearDown = fixtures.MonthlyRefreshTest.tearDown

    def korean_scope(self):
        cfg = json.loads(self.scope.read_text(encoding='utf-8'))
        cfg['description'] = '국가별 수집 범위 🌍'
        self.scope.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')

    def test_initialize_reads_utf8_scope(self):
        self.korean_scope()
        bootstrap.BootstrapTest.prepare(self)
        with cp949_default():
            checkpoint = job.initialize_csv(self.engine, self.csv, self.scope, self.state, date(2024,2,2))
        self.assertEqual(checkpoint['initialization'], 'CSV_DB_VERIFIED')

    def test_refresh_reads_utf8_scope(self):
        self.korean_scope()
        path = self.state/'checkpoint.json'
        cp = json.loads(path.read_text(encoding='utf-8'))
        cp['scope_sha256'] = hashlib.sha256(self.scope.read_bytes()).hexdigest()
        job.save(path, cp)
        with cp949_default():
            result = job.run(self.engine, fixtures.FakeClient([],[]), self.scope, self.state, date(2024,2,2))
        self.assertEqual(result['status'], 'DB_VERIFIED')

    def test_refresh_reads_utf8_checkpoint(self):
        path = self.state/'checkpoint.json'
        cp = json.loads(path.read_text(encoding='utf-8'))
        cp['note'] = '정상 점검 기록 🌍'
        job.save(path, cp)
        with cp949_default():
            result = job.run(self.engine, fixtures.FakeClient([],[]), self.scope, self.state, date(2024,2,2))
        self.assertEqual(result['status'], 'DB_VERIFIED')

    def test_api_response_utf8_roundtrip(self):
        payload = {'count':1, 'data':[{'name':'한국 · Côte d’Ivoire 🌍'}]}
        session = mock.Mock()
        session.get.return_value = mock.Mock(status_code=200, text=json.dumps(payload, ensure_ascii=False))
        path = self.path/'response.json'
        with cp949_default():
            rows = Client('test-key',session,interval=0).get('getDa/C/M/HS',{},path)
        self.assertEqual(rows, payload['data'])
        stored = json.loads(path.read_bytes().decode('utf-8'))
        self.assertEqual(stored['response'], payload)
        self.assertNotIn('test-key', path.read_text(encoding='utf-8'))

    def test_atomic_json_save_utf8_roundtrip(self):
        payload = {'note':'정상 점검 🌍', 'country':'Côte d’Ivoire'}
        path = self.path/'state.json'
        with cp949_default():
            job.save(path,payload)
        self.assertEqual(json.loads(path.read_bytes().decode('utf-8')),payload)

    def test_simulation_rejects_implicit_encoding(self):
        path = self.path/'utf8.json'
        path.write_text('{"note":"한글 🌍"}',encoding='utf-8')
        with cp949_default():
            with self.assertRaises(UnicodeDecodeError):path.read_text()
            with self.assertRaises(UnicodeEncodeError):path.write_text('🌍')
