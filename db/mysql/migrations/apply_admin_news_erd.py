"""기사 관리자 ERD 마이그레이션. 기본은 조회, --apply일 때만 백업 후 적용.

저장소 루트에서 .venv/bin/python db/mysql/migrations/apply_admin_news_erd.py --apply
MySQL DDL은 자동 커밋되므로 실패 시 성공한 단계는 남습니다. 백업과 journal.json을
확인한 뒤 같은 명령으로 재개할 수 있습니다. 기존 데이터나 테이블을 삭제하지 않습니다.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from backend.app.db.news_lock import article_load_lock, database_identity
from backend.app.db.session import make_engine
from db.mysql.migrations.admin_news_schema import Contract, SchemaMismatch, normalize

SQL = Path(__file__).with_name('20261023_admin_news_erd.sql')
BACKUP_TABLES = ('article', 'article_tag', 'batch_run', 'news_keyword_daily',
                 'news_product_daily', 'keyword_category', 'keyword_topic', 'service_option')


def steps():
    chunks = re.split(r'^-- @step (\w+) (\w+) ([\w-]+)\n', SQL.read_text(), flags=re.M)
    return [(*chunks[i:i + 3], chunks[i + 3].strip().removesuffix(';'))
            for i in range(1, len(chunks), 4)]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(',', ':'))


def secure_json(path, value):
    # 백업에는 기존 기사 본문이 포함됩니다. Git 제외 경로, 소유자 전용 권한입니다.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def snapshot(conn, columns=None):
    result = {}
    for name in BACKUP_TABLES:
        definitions = inspect(conn).get_columns(name)
        selected = columns[name] if columns else [c['name'] for c in definitions]
        json_columns = {c['name'] for c in definitions if str(c['type']).upper() == 'JSON'}
        projection = ','.join(f'`{column}`' for column in selected)
        rows = [list(row) for row in conn.exec_driver_sql(f'SELECT {projection} FROM `{name}`')]
        for row in rows:
            for index, column in enumerate(selected):
                if column in json_columns and row[index] is not None:
                    # SQL NULL(None)과 JSON null('null')은 구분합니다.
                    value = json.loads(row[index]) if isinstance(row[index], str) else row[index]
                    row[index] = canonical(value)
        rows.sort(key=canonical)
        result[name] = {'columns': selected, 'rows': rows,
                       'count': len(rows), 'sha256': hashlib.sha256(canonical(rows).encode()).hexdigest()}
    return result


def expect_backfill(expected, table, column, sql):
    """실제로 실행한 NULL 보충만 예상 결과에 반영. 기존 비NULL·다른 필드/행은 그대로 비교."""
    match = re.fullmatch(r"UPDATE (\w+) SET (\w+)='(.*)' WHERE \2 IS NULL", sql)
    if not match or (match[1], match[2]) != (table, column):
        raise ValueError('허용되지 않은 데이터 보충 SQL')
    value = canonical(json.loads(match[3]))
    data = expected[table]
    if column not in data['columns']:
        return 0  # 이번 실행에서 추가된 컬럼은 기존 데이터 비교 대상이 아닙니다.
    index = data['columns'].index(column)
    changed = 0
    for row in data['rows']:
        if row[index] is None:
            row[index] = value
            changed += 1
    data['rows'].sort(key=canonical)
    data['sha256'] = hashlib.sha256(canonical(data['rows']).encode()).hexdigest()
    return changed


def needed(conn, kind, table, name, sql, contract=None):
    inspector = inspect(conn)
    contract = contract or Contract(SQL)
    if kind == 'table':
        if not inspector.has_table(table):
            return True
        contract.validate(conn, kind, table, name)
        return False
    if kind == 'view':
        # CREATE OR REPLACE로 최신 정의를 복원하며 SELECT 결과 열 이름은 유지합니다.
        return True
    if not inspector.has_table(table):
        return True
    if kind in ('column', 'required', 'data', 'enum'):
        columns = {c['name']: c for c in inspector.get_columns(table)}
        if kind == 'column':
            if name not in columns:
                return True
            contract.validate(conn, kind, table, name)
            return False
        if kind == 'required':
            if name in columns:
                contract.validate(conn, kind, table, name)
            return name not in columns or columns[name]['nullable']
        if kind == 'enum':
            contract.validate(conn, kind, table, name)
            return name not in columns['target_table']['type'].enums
        return name not in columns or bool(conn.exec_driver_sql(
            f'SELECT EXISTS(SELECT 1 FROM `{table}` WHERE `{name}` IS NULL)').scalar_one())
    if kind == 'index':
        if name not in {i['name'] for i in inspector.get_indexes(table)}:
            return True
        contract.validate(conn, kind, table, name)
        return False
    if kind == 'constraint':
        exists = conn.execute(text('SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS '
            'WHERE CONSTRAINT_SCHEMA=DATABASE() AND TABLE_NAME=:t AND CONSTRAINT_NAME=:n'),
            {'t': table, 'n': name}).scalar_one()
        if exists:
            contract.validate(conn, kind, table, name)
        return not exists
    if kind == 'trigger':
        current = conn.execute(text('SELECT ACTION_STATEMENT FROM information_schema.TRIGGERS '
            'WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=:n'), {'n': name}).scalar_one_or_none()
        if current:
            expected = sql[sql.index('BEGIN'):]
            if normalize(current) != normalize(expected):
                raise ValueError(f'기존 트리거 정의가 다릅니다: {name}')
        return current is None
    raise ValueError(f'알 수 없는 단계: {kind}')


def run(apply=False, include_triggers=False):
    engine = make_engine()
    report = {'status': 'PLAN', 'steps': []}
    try:
        with article_load_lock(engine), engine.connect() as conn:
            if engine.dialect.name != 'mysql':
                raise ValueError('MySQL에서만 실행할 수 있습니다')
            if not conn.exec_driver_sql('SELECT VERSION()').scalar_one().startswith('8.4.'):
                raise SchemaMismatch('기준 스키마는 MySQL 8.4입니다. 서버 버전과 ERD 정의를 검토하세요')
            contract = Contract(SQL)
            tasks = steps()
            existing = set(inspect(conn).get_table_names())
            if not (set(BACKUP_TABLES) | {'users'}).issubset(existing):
                raise SchemaMismatch('기본 기사·배치·분류 테이블이 없습니다')
            # 뒤 단계의 불일치도 첫 DDL 전에 확인합니다.
            contract.validate_existing(conn, tasks, existing)
            plan = []
            for kind, table, name, sql in tasks:
                required = needed(conn, kind, table, name, sql, contract)
                plan.append({'kind': kind, 'table': table, 'name': name, 'needed': required,
                             'deferred': required and kind == 'trigger' and not include_triggers})
            if not apply:
                report['steps'] = plan
                return report
            lock = 'admin_news_erd:' + database_identity(engine.url)[:40]
            if conn.execute(text('SELECT GET_LOCK(:name,0)'), {'name': lock}).scalar_one() != 1:
                raise RuntimeError('다른 ERD 적용 작업이 실행 중입니다')
            backup = None
            try:
                if conn.exec_driver_sql("SELECT COUNT(*) FROM batch_run WHERE status='RUNNING'").scalar_one():
                    raise RuntimeError('실행 중인 배치가 있습니다. 종료 후 다시 실행하세요')
                conn.exec_driver_sql('SET SESSION lock_wait_timeout=15')
                conn.commit()
                backup = ROOT / 'data/db_backups' / datetime.now().strftime('admin_news_erd_%Y%m%d_%H%M%S_%f')
                backup.mkdir(parents=True, mode=0o700)
                os.chmod(backup, 0o700)
                before = snapshot(conn)
                expected = copy.deepcopy(before)
                names = inspect(conn).get_table_names() + inspect(conn).get_view_names()
                ddl = {n: conn.exec_driver_sql(f'SHOW CREATE TABLE `{n}`').first()[1] for n in names}
                triggers = [dict(r) for r in conn.exec_driver_sql('SHOW TRIGGERS').mappings()]
                secure_json(backup / 'before.json', {'database_id': database_identity(engine.url),
                    'data': before, 'ddl': ddl, 'triggers': triggers,
                    'migration_sha256': hashlib.sha256(SQL.read_bytes()).hexdigest()})
                conn.commit()
                report['backup'] = str(backup)
                # 본문은 출력하지 않고 성공한 DDL마다 적용 기록을 저장합니다.
                blocked = []
                report['backfilled_rows'] = {}
                for kind, table, name, sql in tasks:
                    execute = needed(conn, kind, table, name, sql, contract)
                    if execute and kind == 'trigger' and not include_triggers:
                        blocked.append(name)
                        report['steps'].append({'kind': kind, 'table': table, 'name': name,
                                                'status': 'DEFERRED'})
                        secure_json(backup / 'journal.json', report)
                        continue
                    if execute:
                        try:
                            result = conn.exec_driver_sql(sql)
                            conn.commit()
                            if kind == 'data':
                                expect_backfill(expected, table, name, sql)
                                report['backfilled_rows'][f'{table}.{name}'] = result.rowcount
                        except DBAPIError as exc:
                            conn.rollback()
                            code = exc.orig.args[0] if exc.orig.args else None
                            if kind == 'trigger' and code == 1419:
                                # RDS 설정은 변경하지 않습니다. 독립적인 뷰 적용은 계속합니다.
                                blocked.append(name)
                                report['steps'].append({'kind': kind, 'table': table, 'name': name,
                                    'status': 'BLOCKED', 'mysql_error': code})
                                secure_json(backup / 'journal.json', report)
                                continue
                            report.update(status='FAILED', failed_step=name, mysql_error=code)
                            secure_json(backup / 'journal.json', report)
                            raise
                    report['steps'].append({'kind': kind, 'table': table, 'name': name,
                                            'status': 'APPLIED' if execute else 'EXISTS'})
                    secure_json(backup / 'journal.json', report)
                after = snapshot(conn, {n: v['columns'] for n, v in before.items()})
                report['preserved'] = {n: {'rows': v['count'],
                    'unchanged': expected[n]['sha256'] == after[n]['sha256']} for n, v in before.items()}
                if not all(v['unchanged'] for v in report['preserved'].values()):
                    report['status'] = 'FAILED_PRESERVATION'
                    secure_json(backup / 'journal.json', report)
                    raise RuntimeError('검증 중 기존 행의 변경이 발견됐습니다. 백업과 현재 값을 비교하세요')
                contract.validate_existing(conn, tasks, set(inspect(conn).get_table_names()))
                for kind, table, name, sql in tasks:
                    if kind != 'view' and kind != 'trigger' and needed(conn, kind, table, name, sql, contract):
                        raise SchemaMismatch(f'적용 후 미완료 단계: {table}.{name}')
                report['blocked_triggers'] = blocked
                report['status'] = 'PARTIAL' if blocked else 'SUCCESS'
                secure_json(backup / 'journal.json', report)
                return report
            except Exception as exc:
                if not report['status'].startswith('FAILED'):
                    report['status'] = 'FAILED'
                report['error_type'] = type(exc).__name__
                if backup is not None:
                    secure_json(backup / 'journal.json', report)
                raise
            finally:
                conn.rollback()
                conn.execute(text('SELECT RELEASE_LOCK(:name)'), {'name': lock})
    finally:
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--include-triggers', action='store_true',
                        help='AWS 준비 후 명시적으로 트리거까지 적용. 기본값은 보류')
    args = parser.parse_args()
    try:
        result = run(args.apply, args.include_triggers)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result['status'] == 'PARTIAL':
            raise SystemExit(2)
    except Exception as exc:
        # DB 오류의 SQL 파라미터·기사 원문·접속 문자열을 출력하지 않습니다.
        print(f'ERD 적용 중단: {type(exc).__name__}. data/db_backups의 journal과 실제 스키마를 확인하세요.',
              file=sys.stderr)
        raise SystemExit(1) from None
