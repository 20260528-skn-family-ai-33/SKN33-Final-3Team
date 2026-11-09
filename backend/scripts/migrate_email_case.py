"""Plan the email case-sensitive unique-index migration; --apply opts into DDL."""
import argparse
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn
from sqlalchemy import Column, String
from backend.app.api.config import Settings


def plan(connection):
    if connection.dialect.name != 'mysql':
        raise RuntimeError('This migration requires MySQL.')
    column = next(c for c in inspect(connection).get_columns('users') if c['name'] == 'email')
    if (getattr(column['type'], 'length', None) != 254 or column['nullable']
            or column.get('default') is not None or column.get('computed')):
        raise RuntimeError('Unexpected users.email definition; inspect before migrating.')
    collation = connection.scalar(text(
        "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users' AND COLUMN_NAME='email'"))
    if collation == 'utf8mb4_bin':
        return []
    # Keep the unique index and every stored address/password unchanged.
    definition = Column('email', String(254, collation='utf8mb4_bin'), nullable=False,
                        comment=column.get('comment'))
    return ['ALTER TABLE `users` MODIFY COLUMN ' + str(CreateColumn(definition).compile(dialect=connection.dialect))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    engine = Settings().engine()
    try:
        with engine.connect() as connection:
            statements = plan(connection)
            print('Database:', engine.url.database)
            print('\n'.join(statements) if statements else 'Already case-sensitive; no changes needed.')
            if args.apply:
                for statement in statements:
                    connection.exec_driver_sql(statement)
                connection.commit()
                if plan(connection):
                    raise RuntimeError('Email migration incomplete.')
                print('Email case-sensitive migration applied.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
