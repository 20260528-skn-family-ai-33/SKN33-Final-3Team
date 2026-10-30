"""Explicit, resumable cleanup after 038. Plan by default; back up before applying.

Stop API/worker writers before --apply. MySQL DDL commits independently, so the
backup is written exclusively before any change and partial runs can be resumed.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import MetaData, Table, inspect, select, text, update
from backend.app.api.config import Settings

REMOVED_COLUMNS = ('password_change_advised', 'scheduled_delete_at')
ROLE_CHECK = 'ck_users_api_role'


def without_file_sizes(files):
    if files is None:
        return None
    if not isinstance(files, dict) or any(not isinstance(v, dict) for v in files.values()):
        raise RuntimeError('Unexpected report.files_json shape; inspect before migrating')
    return {fmt: {k: v for k, v in metadata.items() if k != 'size'} for fmt, metadata in files.items()}


def inspect_changes(connection):
    inspector = inspect(connection)
    users = Table('users', MetaData(), autoload_with=connection)
    report = Table('report', MetaData(), autoload_with=connection)
    roles = dict(connection.execute(text('SELECT role, COUNT(*) FROM users GROUP BY role')).all())
    if set(roles) - {'USER', 'ADMIN', 'SUPER_ADMIN'}:
        raise RuntimeError('Unknown user roles; inspect before migrating')
    columns = [name for name in REMOVED_COLUMNS if name in users.c]
    files = []
    for row in connection.execute(select(report.c.report_id, report.c.files_json).where(report.c.files_json.is_not(None))).mappings():
        cleaned = without_file_sizes(row['files_json'])
        if cleaned != row['files_json']:
            files.append({'report_id': row['report_id'], 'before': row['files_json'], 'after': cleaned})
    checks = {c['name']: c for c in inspector.get_check_constraints('users')}
    if ROLE_CHECK in checks:
        normalized = checks[ROLE_CHECK]['sqltext'].lower().replace('`', '').replace('_utf8mb4', '')
        normalized = ''.join(normalized.split()).strip('()')
        if normalized != "rolein('user','admin'":
            raise RuntimeError('Unexpected role constraint definition')
    ddl = [f'ALTER TABLE `users` DROP COLUMN `{name}`' for name in columns]
    if ROLE_CHECK not in checks:
        ddl.append(f"ALTER TABLE `users` ADD CONSTRAINT `{ROLE_CHECK}` CHECK (`role` IN ('USER', 'ADMIN'))")
    return users, report, {'removed_columns': columns, 'converted_roles': roles.get('SUPER_ADMIN', 0),
                           'files': files, 'ddl': ddl}


def summary(changes):
    return {'drop_columns': changes['removed_columns'], 'convert_super_admin': changes['converted_roles'],
            'clean_file_records': len(changes['files']), 'ddl': changes['ddl']}


def apply(connection, backup_path):
    if connection.scalar(text("SELECT GET_LOCK('borderless_migration_038',10)")) != 1:
        raise RuntimeError('Migration lock unavailable')
    try:
        users, report, changes = inspect_changes(connection)
        if not changes['ddl'] and not changes['converted_roles'] and not changes['files']:
            connection.commit()
            return {'applied': False, 'remaining': summary(changes)}
        # Back up only the fields being changed; never passwords or session tokens.
        user_fields = [users.c.user_id, users.c.role, users.c.auth_version,
                       *(users.c[name] for name in changes['removed_columns'])]
        backup = {'database': connection.engine.url.database, 'changes': changes,
                  'users': [dict(r) for r in connection.execute(select(*user_fields)).mappings()],
                  'users_ddl': connection.exec_driver_sql('SHOW CREATE TABLE users').first()[1]}
        counts = {name: connection.scalar(text(f'SELECT COUNT(*) FROM `{name}`')) for name in ('users', 'report')}
        output = Path(backup_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(backup, stream, ensure_ascii=False, indent=2, default=str)
        if changes['converted_roles']:
            connection.execute(update(users).where(users.c.role == 'SUPER_ADMIN')
                               .values(role='ADMIN', auth_version=users.c.auth_version + 1))
        for row in changes['files']:
            connection.execute(update(report).where(report.c.report_id == row['report_id']).values(files_json=row['after']))
        connection.commit()
        for ddl in changes['ddl']:
            connection.exec_driver_sql(ddl)
        connection.commit()
        _, _, remaining = inspect_changes(connection)
        if remaining['ddl'] or remaining['converted_roles'] or remaining['files']:
            raise RuntimeError('Migration incomplete; rerun the plan')
        after = {name: connection.scalar(text(f'SELECT COUNT(*) FROM `{name}`')) for name in counts}
        if counts != after:
            raise RuntimeError('Concurrent row changes detected; inspect writers')
        return {'applied': True, 'preserved_counts': after, 'backup': str(output), 'remaining': summary(remaining)}
    finally:
        connection.execute(text("SELECT RELEASE_LOCK('borderless_migration_038')"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    settings = Settings()
    target = settings.get('API_DB_TARGET', 'rds')
    engine = settings.engine()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = args.backup or f'runtime/migration-backups/{target}-038-cleanup-{stamp}.json'
    try:
        with engine.connect() as connection:
            _, _, changes = inspect_changes(connection)
            print(json.dumps({'target': target, 'database': engine.url.database, **summary(changes)}))
            if args.apply:
                print(json.dumps(apply(connection, backup)))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
