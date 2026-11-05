"""Explicit schema migration. Default is a read-only plan. Never run on app startup."""
import argparse
import json
from pathlib import Path
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable, CreateColumn
from backend.app.api.config import Settings
from backend.app.api.runtime_schema import RUNTIME_TABLES, EXTENSIONS


def plan(connection):
    inspector=inspect(connection); existing=set(inspector.get_table_names()); statements=[]
    legacy=existing & {'api_user_profile','api_report_file','api_keyword_block','api_auth_session','api_rate_limit'}
    if legacy:
        raise RuntimeError('Legacy application tables already exist; stop and preserve their data. '
                           '036-to-038 automatic migration is not supported; see docs/INTEGRATION.md: '
                           +','.join(sorted(legacy)))
    for table, additions in EXTENSIONS.items():
        if table not in existing: raise RuntimeError('Missing base table: '+table)
        columns={c['name']:c for c in inspector.get_columns(table)}
        for column in additions:
            if column.name in columns:
                actual=columns[column.name]
                expected=str(column.type.compile(dialect=connection.dialect)).lower()
                found=str(actual['type'].compile(dialect=connection.dialect)).lower()
                found=found.replace('tinyint(1)','tinyint')
                equivalent=found==expected or (expected=='bool' and found=='tinyint')
                if not equivalent or actual['nullable']!=column.nullable:
                    raise RuntimeError(f'Column definition mismatch: {table}.{column.name} ({found}/{expected})')
            else:
                ddl=str(CreateColumn(column).compile(dialect=connection.dialect))
                statements.append(f'ALTER TABLE `{table}` ADD COLUMN {ddl}')
    for table in RUNTIME_TABLES:
        if table.name not in existing:
            statements.append(str(CreateTable(table).compile(dialect=connection.dialect)))
            indexes={}
        else:
            columns={c['name']:c for c in inspector.get_columns(table.name)}
            if set(columns)!=set(table.c.keys()): raise RuntimeError('Table mismatch: '+table.name)
            for column in table.c:
                actual=columns[column.name]
                expected=str(column.type.compile(dialect=connection.dialect)).lower()
                found=str(actual['type'].compile(dialect=connection.dialect)).lower()
                if table.name == 'api_audit_event' and column.name == 'target_id' and connection.dialect.name == 'mysql':
                    # #63: 기존 감사 대상 키를 사전의 binary collation과 맞춘다. 데이터·열은 보존한다.
                    if getattr(actual['type'], 'length', None) != 120 or actual['nullable']:
                        raise RuntimeError('Table column mismatch: api_audit_event.target_id')
                    # SHOW CREATE TABLE은 테이블 기본값과 같은 collation을 생략할 수 있다.
                    # 실제 유효 값을 읽어 이미 적용된 스키마에서 반복 ALTER하지 않는다.
                    collation = connection.scalar(text("SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                                                       "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='api_audit_event' AND COLUMN_NAME='target_id'"))
                    if collation != 'utf8mb4_bin':
                        statements.append('ALTER TABLE `api_audit_event` MODIFY COLUMN `target_id` VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL')
                    continue
                if expected!=found or column.nullable!=actual['nullable']:
                    raise RuntimeError(f'Table column mismatch: {table.name}.{column.name}')
            if inspector.get_pk_constraint(table.name)['constrained_columns']!=list(table.primary_key.columns.keys()):
                raise RuntimeError('Primary key mismatch: '+table.name)
            indexes={i['name']:i for i in inspector.get_indexes(table.name)}
            actual_fks=inspector.get_foreign_keys(table.name)
            for constraint in table.foreign_key_constraints:
                wanted=list(constraint.columns.keys())
                if not any(f['constrained_columns']==wanted and f['referred_table']=='users' and f['referred_columns']==['user_id'] and f['options'].get('ondelete')=='CASCADE' for f in actual_fks):
                    raise RuntimeError('Foreign key mismatch: '+table.name)
        for index in table.indexes:
            from sqlalchemy.schema import CreateIndex
            if index.name not in indexes:
                statements.append(str(CreateIndex(index).compile(dialect=connection.dialect)))
            elif indexes[index.name]['column_names']!=list(index.columns.keys()):
                raise RuntimeError('Index mismatch: '+index.name)
    return statements


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--record',default='tmp/integration-migration.json')
    args=parser.parse_args(); settings=Settings(); engine=settings.engine()
    with engine.connect() as connection:
        steps=plan(connection)
        tables=['users','report','notification_setting']
        before={t:{'count':connection.scalar(text(f'SELECT COUNT(*) FROM `{t}`')),
            'ddl':connection.exec_driver_sql(f'SHOW CREATE TABLE `{t}`').first()[1]} for t in tables}
        record={'target':settings.get('API_DB_TARGET','rds'),'database':engine.url.database,
            'before':before,'statements':steps,'applied':False}
        output=Path(args.record); output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'target':record['target'],'database':record['database'],'steps':len(steps),'apply':args.apply}))
        if args.apply:
            if connection.scalar(text("SELECT GET_LOCK('borderless_migration_038',10)"))!=1:
                raise RuntimeError('Migration lock unavailable')
            try:
                # MySQL DDL auto-commits. plan() omits completed steps on a rerun.
                for statement in plan(connection): connection.exec_driver_sql(statement)
                connection.commit()
                assert not plan(connection), 'Migration incomplete'
                after={t:connection.scalar(text(f'SELECT COUNT(*) FROM `{t}`')) for t in tables}
                assert all(after[t]==before[t]['count'] for t in tables),'Row count changed during migration; inspect concurrent writes'
                record.update(applied=True,after_counts=after)
                output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'applied':True,'preserved_counts':after}))
            finally: connection.execute(text("SELECT RELEASE_LOCK('borderless_migration_038')"))
    engine.dispose()


if __name__=='__main__': main()
