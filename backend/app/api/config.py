"""API 전용 설정. 기존 수집기의 MYSQL_* 설정을 변경하지 않는다."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy import URL, create_engine

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Settings:
    values: dict = field(default_factory=lambda: {**dotenv_values(ROOT / '.env'), **os.environ})

    def get(self, key, default=None):
        return self.values.get(key) or default

    @property
    def origins(self):
        return [s.strip() for s in self.get('API_CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if s.strip()]

    @property
    def secure_cookie(self):
        return self.get('API_COOKIE_SECURE', 'true').lower() == 'true'

    @property
    def files(self):
        return Path(self.get('API_REPORT_DIRECTORY', str(ROOT / 'runtime' / 'reports'))).resolve()

    def engine(self):
        prefix = 'RDS_MYSQL_' if self.get('API_DB_TARGET', 'rds') == 'rds' else 'MYSQL_'
        required = [prefix + k for k in ('HOST', 'USER', 'PASSWORD', 'DATABASE')]
        if any(not self.get(k) for k in required):
            raise ValueError('API DB 연결 설정이 필요합니다: ' + ', '.join(required))
        url = URL.create('mysql+pymysql', host=self.get(prefix+'HOST'),
                         port=int(self.get(prefix+'PORT', 3306)), username=self.get(prefix+'USER'),
                         password=self.get(prefix+'PASSWORD'), database=self.get(prefix+'DATABASE'),
                         query={'charset': 'utf8mb4'})
        args = {'connect_timeout': 8, 'read_timeout': 30, 'write_timeout': 30}
        ca = self.get(prefix+'SSL_CA')
        if prefix.startswith('RDS') and not ca:
            raise ValueError('RDS_MYSQL_SSL_CA를 설정하세요. RDS는 TLS 인증서와 호스트를 검증합니다.')
        if ca:
            args.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
        return create_engine(url, pool_pre_ping=True, pool_recycle=1800, connect_args=args)
