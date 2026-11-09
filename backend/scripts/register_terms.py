"""docs/terms의 약관을 service_option에 명시적으로 등록한다. 기본은 읽기 전용 계획이다.

앱 시작 때 자동 등록하지 않는다. 먼저 migrate_integration으로 option_type을 확장해야 한다.
- 이미 동의받은 버전의 본문·제목은 바꾸지 않는다. 내용을 바꾸려면 TERMS에 새 버전 코드와 더 큰 정렬값을 추가해 다시 동의를 받는다.
  (실수 정정 등 꼭 같은 버전을 고쳐야 하면 --force)
- 회원이 이미 동의한 알림 약관이 있으면 새 알림 약관을 등록하지 않는다. 등록하면 기존 동의자가 발송 대상에서 빠진다.
  의도적으로 새 알림 약관으로 바꿀 때만 --replace-notification을 쓴다. 기존 알림 약관을 끄고(행·동의 기록은 남김) 새 버전을
  현재 약관으로 둔다. 기존 동의자는 재동의 안내를 받고 다시 동의할 때까지 새 알림을 받지 않는다(계획에 동의자 수를 표시).
- 현재 버전은 정렬값(sort_order)이 가장 큰 행이다. 같은 종류에 정렬값이 같은 다른 버전이 있으면 등록을 거부한다.
"""
import argparse
import json
from pathlib import Path
from sqlalchemy import MetaData, func, select, update
from backend.app.api.config import Settings


DOCS = Path(__file__).resolve().parents[2] / 'docs' / 'terms'
# (버전 코드, 종류, 문서, 정렬값). 다음 버전은 정렬값을 올려 추가한다. 문자열 비교에서는 V10 < V2라 코드로 정렬하지 않는다.
TERMS = (('TERMS_SERVICE_V1', 'SERVICE_TERMS', 'service-terms.md', 10),
         ('TERMS_PRIVACY_V1', 'PRIVACY_TERMS', 'privacy-consent.md', 10),
         ('TERMS_NOTIFICATION_V1', 'NOTIFICATION_TERMS', 'notification-consent.md', 10),
         ('PRIVACY_POLICY_V1', 'PRIVACY_POLICY', 'privacy-policy.md', 10))
CONTENT = ('label_ko', 'value_json')


def parse(text):
    """제목은 표시 이름으로, 작성자용 안내(인용문)와 끝의 참고 문단을 뺀 나머지는 본문으로 나눈다."""
    lines = text.strip().splitlines()
    body = '\n'.join(line for line in lines[1:] if not line.startswith('>')).split('\n---\n')[0].strip()
    return lines[0].removeprefix('# ').strip(), body


def rows():
    result = []
    for code, kind, name, order in TERMS:
        label, body = parse((DOCS / name).read_text(encoding='utf-8'))
        # effective_at을 넣으면 시행일 전에는 약관이 비활성이 되어 가입 테스트도 막힌다. 시행일은 본문에 적는다.
        result.append({'option_code': code, 'option_type': kind, 'label_ko': label, 'sort_order': order,
                       'enabled': True, 'value_json': {'body': body}})
    return result


def plan(connection, table, consents, force=False, replace_notification=False):
    def agreed(codes):
        return connection.scalar(select(func.count()).select_from(consents).where(consents.c.terms_version.in_(codes))) if codes else 0

    wanted = rows()
    existing = [dict(r) for r in connection.execute(select(table).where(table.c.option_type.in_({r['option_type'] for r in wanted}))).mappings()]
    by_code = {r['option_code']: r for r in existing}
    changes = []
    for row in wanted:
        current = by_code.get(row['option_code'])
        others = [r for r in existing if r['option_type'] == row['option_type'] and r['option_code'] != row['option_code'] and r['enabled']]
        if current is not None and current['option_type'] != row['option_type']:
            raise ValueError('Unexpected option type: ' + row['option_code'])
        notification = row['option_type'] == 'NOTIFICATION_TERMS'
        if notification and replace_notification and others:
            # 의도적인 전환: 다른 알림 약관을 끈다. consents는 이 전환으로 재동의가 필요해지는 회원 수다.
            # 지금 현재 약관(정렬값이 가장 큰 사용 중 행)의 동의자만 센다. 현재가 아닌 옛 약관의 동의자는 이미 재동의 안내 대상이다.
            enabled = [r for r in existing if r['option_type'] == row['option_type'] and r['enabled']]
            live = max(enabled, key=lambda r: (r['sort_order'], r['option_code']))['option_code']
            changes.extend({'action': 'DISABLE', 'row': {'option_code': r['option_code']},
                            'consents': agreed([r['option_code']]) if r['option_code'] == live else 0} for r in others)
            others = []
        if current is None and notification and agreed([r['option_code'] for r in others]):
            changes.append({'action': 'KEEP_EXISTING', 'row': row})  # 기존 동의자가 알림을 계속 받도록 현재 알림 약관을 유지한다
            continue
        tie = [r['option_code'] for r in others if r['sort_order'] == row['sort_order']]
        if tie:
            hint = ' 기존 알림 약관을 새 버전으로 바꾸려면 --replace-notification' if notification else ''
            raise ValueError(f"{row['option_code']}: 같은 종류의 {', '.join(tie)}와 정렬값이 같습니다. 새 버전은 정렬값을 올려 추가하세요.{hint}")
        if current is None:
            changes.append({'action': 'INSERT', 'row': row})
            continue
        if not any(current[key] != row[key] for key in (*CONTENT, 'enabled', 'sort_order')):
            continue
        if any(current[key] != row[key] for key in CONTENT) and agreed([row['option_code']]) and not force:
            raise ValueError(f"{row['option_code']}: 이미 동의받은 약관의 본문·제목은 바꿀 수 없습니다. 새 버전 코드를 추가하세요(꼭 필요하면 --force)")
        changes.append({'action': 'UPDATE', 'row': row})
    return changes


def apply(connection, table, changes):
    for change in changes:
        row = change['row']
        if change['action'] == 'DISABLE':  # 행·동의 기록은 남겨 이력을 보존한다
            connection.execute(update(table).where(table.c.option_code == row['option_code']).values(enabled=False))
        elif change['action'] == 'INSERT':
            connection.execute(table.insert().values(**row))
        elif change['action'] == 'UPDATE':
            connection.execute(update(table).where(table.c.option_code == row['option_code']).values(
                label_ko=row['label_ko'], enabled=True, sort_order=row['sort_order'], value_json=row['value_json']))


def register(engine, write=True, force=False, replace_notification=False):
    metadata = MetaData()
    metadata.reflect(engine, only=['service_option', 'user_consent'])
    table, consents = metadata.tables['service_option'], metadata.tables['user_consent']
    with engine.begin() as connection:
        changes = plan(connection, table, consents, force, replace_notification)
        if write:
            apply(connection, table, changes)
            left = [c for c in plan(connection, table, consents, force) if c['action'] != 'KEEP_EXISTING']
            assert not left, 'Terms registration incomplete'
    return [{'action': c['action'], 'code': c['row']['option_code'], **({'consents': c['consents']} if 'consents' in c else {})}
            for c in changes]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--force', action='store_true', help='이미 동의받은 버전의 본문도 덮어씁니다(정정용)')
    parser.add_argument('--replace-notification', action='store_true',
                        help='기존 알림 약관을 끄고 docs/terms의 알림 약관으로 바꿉니다. 기존 동의자는 재동의 전까지 알림을 받지 않습니다')
    args = parser.parse_args()
    settings = Settings()
    engine = settings.engine()
    try:
        changes = register(engine, write=args.apply, force=args.force, replace_notification=args.replace_notification)
        print(json.dumps({'target': settings.get('API_DB_TARGET', 'rds'), 'applied': args.apply, 'changes': changes}, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
