"""Register periodic report options explicitly; default is a read-only plan.

No startup seeding, no subscription changes, and no changes to unrelated options.
Existing labels/metadata are retained. A mismatched option type aborts the transaction.
"""
import argparse
import json
from pathlib import Path
from sqlalchemy import select, update
from backend.app.api.config import Settings
from backend.app.api.database import Database, Store
from backend.app.api.common import clean, now


REPORT_OPTIONS = [
    {'option_code': code, 'option_type': kind, 'label_ko': label, 'sort_order': order,
     'enabled': True, 'value_json': {}}
    for code, kind, label, order in (
        ('WEEKLY', 'REPORT_PERIOD', '주간', 1),
        ('MONTHLY', 'REPORT_PERIOD', '월간', 2),
        ('QUARTERLY', 'REPORT_PERIOD', '분기', 3),
        ('RPT_WEEKLY', 'REPORT_TYPE', '주간 리포트', 1),
        ('RPT_MONTHLY', 'REPORT_TYPE', '월간 리포트', 2),
        ('RPT_QUARTERLY', 'REPORT_TYPE', '분기 리포트', 3),
    )]


def plan(store, lock=False):
    table = store.t('service_option')
    stmt = select(table).where(table.c.option_code.in_([r['option_code'] for r in REPORT_OPTIONS]))
    before = {r['option_code']: r for r in store.rows(stmt.with_for_update() if lock else stmt)}
    changes = []
    for wanted in REPORT_OPTIONS:
        current = before.get(wanted['option_code'])
        if current is None:
            changes.append({'action': 'INSERT', 'after': wanted})
        elif current['option_type'] != wanted['option_type']:
            raise ValueError('Unexpected option type: ' + wanted['option_code'])
        elif not current['enabled']:
            changes.append({'action': 'ENABLE', 'before': current,
                            'after': {**current, 'enabled': True}})
    return {'before': list(before.values()), 'changes': changes}


def apply(store, planned):
    table = store.t('service_option')
    for change in planned['changes']:
        if change['action'] == 'INSERT':
            store.c.execute(table.insert().values(**change['after']))
        else:
            store.c.execute(update(table).where(table.c.option_code == change['after']['option_code']).values(enabled=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--record', default='runtime/migration-backups/report-options-' + now().strftime('%Y%m%d-%H%M%S') + '.json')
    args = parser.parse_args()
    settings = Settings()
    db = Database(settings.engine())
    record = {'target': settings.get('API_DB_TARGET', 'rds'), 'applied': False}
    path = Path(args.record)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with db.engine.begin() as c:
            store = Store(db, c)
            planned = plan(store, lock=args.apply)
            record.update(clean(planned))
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            if args.apply:
                apply(store, planned)
                assert not plan(store)['changes'], 'Report option registration incomplete'
        record['applied'] = args.apply
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'target': record['target'], 'applied': args.apply,
                          'changes': [{'action': r['action'], 'code': r['after']['option_code']} for r in planned['changes']],
                          'record': str(path)}, ensure_ascii=False))
    finally:
        db.engine.dispose()


if __name__ == '__main__':
    main()
