"""공통 batch_run 연결. 수집 잠금 안에서만 사용하고 기사 판정·대기열은 변경하지 않습니다."""
import hashlib
import re
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from news.run_lifecycle import atomic_json, read_record

KST = ZoneInfo('Asia/Seoul')
SOURCES = {'hankyung': 'HANKYUNG', 'fnnews': 'FNNEWS'}


def make_history_engine():
    # 독립 CLI도 백엔드와 동일한 SQLAlchemy+PyMySQL 설정을 사용합니다.
    repository = Path(__file__).resolve().parents[6]
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    from backend.app.db.session import make_engine
    return make_engine()


def now():
    return datetime.now(KST).isoformat()


def db_time(value):
    # batch_run의 DATETIME은 시간대가 없으므로 KST로 통일해 기록합니다.
    return datetime.fromisoformat(value).astimezone(KST).replace(tzinfo=None, microsecond=0)


def collection_result(root, attempt):
    """표시용 보고서 대신 기존 실행 확정 기록을 읽습니다."""
    state = read_record(root / 'current_run.json') or {}
    ref = state.get('history', {}).get(attempt)
    if not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{32}\.json', ref):
        return None
    return read_record(root / 'run_records' / ref)


class BatchHistory:
    """출처별 한 행. 기록 오류는 수집/적재 결과와 분리하며 다음 명시 실행에서 복구합니다.

    job_name의 실행 ID로 DB 커밋 직후 강제 종료되어도 같은 행을 다시 찾습니다.
    기존 ERD에 컬럼을 추가하거나 SOURCE를 자동 생성하지 않습니다.
    """
    def __init__(self, root, kind, sites, period_start, period_end, *, attempt=None,
                 started_at=None, mapping=None, engine_factory=None):
        self.root = Path(root)
        self.engine_factory = engine_factory or make_history_engine
        self.engine = None
        self.destination = None
        self.db_failed = False
        self.warnings = []
        self.mapping = mapping or SOURCES
        attempt = attempt or uuid4().hex
        if not re.fullmatch(r'[a-f0-9]{32}', attempt) or kind not in ('COLLECT', 'LOAD'):
            raise ValueError('배치 실행 식별자 오류')
        self.path = self.root / 'batch_runs' / (attempt + '.json')
        start = started_at or now()
        fallback = datetime.fromisoformat(start).astimezone(KST).date()
        self.record = {'version': 1, 'attempt_id': attempt, 'kind': kind,
            'job_name': 'TRADE_NEWS_' + kind + ':' + attempt, 'target_table': 'article',
            'period_start': (period_start or period_end or fallback).isoformat(),
            'period_end': (period_end or period_start or fallback).isoformat(),
            'started_at': start, 'finished_at': None, 'status': 'RUNNING',
            'timezone': 'Asia/Seoul', 'destination': None, 'sync_pending': True,
            'sources': {site: {'source_code': self.mapping[site], 'request_count': 0,
                               'received_count': 0, 'run_id': None} for site in dict.fromkeys(sites)}}

    def warn(self, exc):
        # 접속 주소/비밀번호/SQL 매개변수가 섞인 예외 원문은 기록하지 않습니다.
        name = type(exc).__name__
        if name not in self.warnings:
            self.warnings.append(name)
            print(f'batch_run 이력 기록 대기 ({name}). 기사 처리 결과와 별도입니다.', file=sys.stderr)

    def persist(self, path=None, record=None):
        try:
            atomic_json(path or self.path, record or self.record)
            return True
        except Exception as exc:
            self.warn(exc)
            return False

    def _connect(self):
        if self.engine is None:
            self.engine = self.engine_factory()
            # 비밀번호를 저장하지 않으며 다른 DB에 과거 이력을 잘못 복원하지 않습니다.
            identity = str(self.engine.url.set(password=None))
            self.destination = hashlib.sha256(identity.encode()).hexdigest()

    def _write_rows(self, record):
        from sqlalchemy import MetaData, Table, select
        from backend.app.db.session import session_scope
        from backend.app.models.batch import ServiceOption
        run_ids = {}
        with session_scope(self.engine) as session:
            batch = Table('batch_run', MetaData(), autoload_with=session.connection(), resolve_fks=False)
            for row in record['sources'].values():
                option = session.get(ServiceOption, row['source_code'])
                if option is None or option.option_type != 'SOURCE' or not option.enabled:
                    raise ValueError('활성 SOURCE 없음')
                query = select(batch).where(
                    batch.c.job_name == record['job_name'], batch.c.source_code == row['source_code'])
                saved = session.execute(query).mappings().one_or_none()
                values = dict(status=record['status'],
                    finished_at=db_time(record['finished_at']) if record['finished_at'] else None,
                    request_count=row['request_count'], received_count=row['received_count'])
                if saved is None:
                    values.update(source_code=row['source_code'], job_name=record['job_name'],
                        target_table='article', period_start=date.fromisoformat(record['period_start']),
                        period_end=date.fromisoformat(record['period_end']), started_at=db_time(record['started_at']))
                    # 관리자 ERD의 필수 JSON은 신규 행에만 공급합니다. 기존 상세 정보는 보존합니다.
                    if 'detail_json' in batch.c:
                        values['detail_json'] = {'schema_version': 1}
                    run_id = session.execute(batch.insert().values(**values)).inserted_primary_key[0]
                else:
                    if (saved['target_table'] != 'article' or saved['period_start'].isoformat() != record['period_start']
                            or saved['period_end'].isoformat() != record['period_end']):
                        raise ValueError('이력 식별자 충돌')
                    run_id = saved['run_id']
                    session.execute(batch.update().where(batch.c.run_id == run_id).values(**values))
                run_ids[row['source_code']] = run_id
        # 트랜잭션 커밋이 끝난 ID만 공개합니다. 실패한 flush의 ID는 남기지 않습니다.
        for row in record['sources'].values():
            row['run_id'] = run_ids[row['source_code']]

    def sync(self, path=None, record=None):
        path, record = path or self.path, record or self.record
        record['sync_pending'] = True
        # 로컬 추적 파일부터 확정한 다음 DB를 씁니다.
        if not self.persist(path, record) or self.db_failed:
            return
        try:
            self._connect()
            if record['destination'] not in (None, self.destination):
                # 다른 연결의 과거 파일은 건드리지 않고 이번 실행은 계속 기록합니다.
                self.warn(ValueError('다른 DB의 실행 이력'))
                return
            record['destination'] = self.destination
            if not self.persist(path, record):
                return
            self._write_rows(record)
            record['sync_pending'] = False
            record.pop('sync_error_type', None)
        except Exception as exc:
            self.db_failed = True  # 장애 DB에 이벤트마다 반복 접속하지 않습니다.
            record['sync_error_type'] = type(exc).__name__
            self.warn(exc)
        self.persist(path, record)

    def _collection_outcome(self, record):
        bundle = collection_result(self.root, record['attempt_id'])
        current = (bundle or {}).get('current', {})
        # SUCCESS는 기존 실행 완료 판정입니다. 전 기사 확보/모든 후보 통과를 뜻하지 않습니다.
        record['status'] = 'SUCCESS' if current.get('status') == 'completed' else 'FAILED'
        record['finished_at'] = current.get('finished_at') or now()
        record['outcome'] = {'execution_status': current.get('status', 'unconfirmed'),
                             'sites': (bundle or {}).get('summary', {}).get('sites', {})}

    def start(self):
        # 호출자는 같은 crawler.lock을 보유합니다. 과거 RUNNING은 더 이상 실행 중일 수 없습니다.
        for path in sorted((self.root / 'batch_runs').glob('*.json')):
            if path == self.path:
                continue
            try:
                old = read_record(path)
                if old is None or old.get('version') != 1:
                    raise ValueError('읽을 수 없는 이력')
                if old['status'] == 'RUNNING':
                    if old['kind'] == 'COLLECT':
                        self._collection_outcome(old)
                    else:
                        old.update(status='FAILED', finished_at=now(), outcome={'status': 'interrupted'})
                    old['recovered_at'] = now()
                    old['sync_pending'] = True
                if old['sync_pending']:
                    self.sync(path, old)
            except Exception as exc:
                self.warn(exc)
        self.sync()
        return self

    def event(self, event):
        key = {'request': 'request_count', 'article_parsed': 'received_count'}.get(event.get('event'))
        if key is None:
            return
        site = event.get('site')
        if site in self.record['sources']:
            self.record['sources'][site][key] += 1
            self.sync()

    def received(self, articles):
        for article in articles:
            self.record['sources'][article['source_code']]['received_count'] += 1
        self.sync()

    def finish(self, result=None):
        try:
            if self.record['kind'] == 'COLLECT':
                self._collection_outcome(self.record)
            else:
                result = result or {'status': 'interrupted'}
                self.record['status'] = 'SUCCESS' if result['status'] == 'complete' else 'FAILED'
                self.record['finished_at'] = now()
                # 기사 본문과 DB 접속 정보 없이 적재 건수만 보조 기록합니다.
                self.record['outcome'] = {key: result[key] for key in
                    ('status', 'inserted', 'existing', 'failed', 'unprocessed', 'stored_articles') if key in result}
            self.sync()
        except Exception as exc:
            self.warn(exc)
        finally:
            if self.engine is not None:
                try:
                    self.engine.dispose()
                except Exception as exc:
                    self.warn(exc)
        return {'attempt_id': self.record['attempt_id'], 'status': self.record['status'],
                'sync_pending': self.record['sync_pending'], 'warning_types': self.warnings,
                'run_ids': {site: row['run_id'] for site, row in self.record['sources'].items()}}
