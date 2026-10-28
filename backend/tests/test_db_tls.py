"""선택적 TLS 설정과 기존 DB 연결 설정의 호환성 검증. 네트워크 연결 없음."""
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymysql
from sqlalchemy.dialects.mysql.pymysql import MySQLDialect_pymysql
from backend.app.db.session import mysql_url


class DatabaseTlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / 'test.env'
        self.env.write_text('MYSQL_HOST=example.test\nMYSQL_USER=test\nMYSQL_PASSWORD=test\nMYSQL_DATABASE=test\n')
        # Windows OpenSSL needs system paths; keep credentials and DB options isolated.
        system_env = {key: value for key, value in os.environ.items()
                      if key.upper() in {'SYSTEMROOT', 'WINDIR'}}
        env_patch = patch.dict(os.environ, system_env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_existing_non_tls_configuration_is_unchanged(self):
        url = mysql_url(self.env)
        self.assertEqual(dict(url.query), {'charset': 'utf8mb4'})
        self.assertEqual((url.host, url.database, url.port), ('example.test', 'test', 3306))

    def test_relative_ca_is_resolved_and_driver_validates_identity(self):
        ca = self.root / 'test.pem'
        ca.touch()
        with self.env.open('a') as stream:
            stream.write('MYSQL_SSL_CA=test.pem\n')
        url = mysql_url(self.env)
        _, args = MySQLDialect_pymysql().create_connect_args(url)
        self.assertEqual(args['ssl']['ca'], str(ca.resolve()))
        self.assertIs(args['ssl']['check_hostname'], True)
        # 실제 PyMySQL의 SSLContext 생성 경로에서 인증서 검증이 꺼지지 않는지 확인합니다.
        with patch.object(ssl, 'create_default_context', return_value=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)):
            ctx = pymysql.connections.Connection._create_ssl_ctx(None, args['ssl'])
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_environment_override_and_missing_ca_fail_before_connecting(self):
        with patch.dict(os.environ, {'MYSQL_SSL_CA': str(self.root / 'absent.pem')}):
            with self.assertRaisesRegex(ValueError, 'MYSQL_SSL_CA'):
                mysql_url(self.env)


if __name__ == '__main__':
    unittest.main()
