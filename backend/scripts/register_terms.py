"""docs/terms의 약관을 service_option에 명시적으로 등록한다. 기본은 읽기 전용 계획이다.

앱 시작 때 자동 등록하지 않는다. 먼저 migrate_integration으로 option_type을 확장해야 한다.
같은 코드의 본문이 바뀌면 갱신한다. 공개 후 내용을 바꿀 때는 새 버전 코드를 추가해 다시 동의를 받는다.
"""
import argparse
import json
from pathlib import Path
from sqlalchemy import MetaData, select, update
from backend.app.api.config import Settings


DOCS = Path(__file__).resolve().parents[2] / 'docs' / 'terms'
TERMS = (('TERMS_SERVICE_V1', 'SERVICE_TERMS', 'service-terms.md'),
         ('TERMS_PRIVACY_V1', 'PRIVACY_TERMS', 'privacy-consent.md'),
         ('TERMS_NOTIFICATION_V1', 'NOTIFICATION_TERMS', 'notification-consent.md'),
         ('PRIVACY_POLICY_V1', 'PRIVACY_POLICY', 'privacy-policy.md'))


def parse(text):
    """제목은 표시 이름으로, 작성자용 안내(인용문)와 끝의 참고 문단을 뺀 나머지는 본문으로 나눈다."""
    lines = text.strip().splitlines()
    body = '\n'.join(line for line in lines[1:] if not line.startswith('>')).split('\n---\n')[0].strip()
    return lines[0].removeprefix('# ').strip(), body


def rows():
    result = []
    for code, kind, name in TERMS:
        label, body = parse((DOCS / name).read_text(encoding='utf-8'))
        # effective_at을 넣으면 시행일 전에는 약관이 비활성이 되어 가입 테스트도 막힌다. 시행일은 본문에 적는다.
        result.append({'option_code': code, 'option_type': kind, 'label_ko': label, 'sort_order': 10,
                       'enabled': True, 'value_json': {'body': body}})
    return result


def plan(connection, table):
    wanted = rows()
    before = {r['option_code']: dict(r) for r in connection.execute(
        select(table).where(table.c.option_code.in_([r['option_code'] for r in wanted]))).mappings()}
    changes = []
    for row in wanted:
        current = before.get(row['option_code'])
        if current is None:
            changes.append({'action': 'INSERT', 'row': row})
        elif current['option_type'] != row['option_type']:
            raise ValueError('Unexpected option type: ' + row['option_code'])
        elif any(current[key] != row[key] for key in ('label_ko', 'enabled', 'value_json')):
            changes.append({'action': 'UPDATE', 'row': row})
    return changes


def apply(connection, table, changes):
    for change in changes:
        row = change['row']
        if change['action'] == 'INSERT':
            connection.execute(table.insert().values(**row))
        else:
            connection.execute(update(table).where(table.c.option_code == row['option_code']).values(
                label_ko=row['label_ko'], enabled=True, value_json=row['value_json']))


def register(engine, write=True):
    metadata = MetaData()
    metadata.reflect(engine, only=['service_option'])
    table = metadata.tables['service_option']
    with engine.begin() as connection:
        changes = plan(connection, table)
        if write:
            apply(connection, table, changes)
            assert not plan(connection, table), 'Terms registration incomplete'
    return [{'action': c['action'], 'code': c['row']['option_code']} for c in changes]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    settings = Settings()
    engine = settings.engine()
    try:
        changes = register(engine, write=args.apply)
        print(json.dumps({'target': settings.get('API_DB_TARGET', 'rds'), 'applied': args.apply, 'changes': changes}, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
