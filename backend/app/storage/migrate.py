"""Explicit, non-destructive migration. Inventory by default; --apply writes S3.

python -m backend.app.storage.migrate --dataset external
python -m backend.app.storage.migrate --dataset external --apply
python -m backend.app.storage.migrate --dataset models --restore --destination /empty/cache
"""
import argparse
import json
from pathlib import Path
import tempfile

from .datasets import DATASETS
from .objects import ROOT, S3Objects, StorageError, digest, file_digest, json_bytes
from .workspace import Workspace, atomic_write, files, workspace


def migrate(root, prefix, store):
    """Idempotent adoption; a different published namespace is never overwritten."""
    current = Workspace(root, prefix, store=store)
    current.read_manifest(missing=True)
    local = {name: file_digest(path) for name, path in current.local_files()}
    remote = {name: meta['sha256'] for name, meta in current.state['files'].items()}
    if remote and local != remote:
        raise StorageError('Published dataset differs; stop writers and review migration source')
    with workspace(root, prefix, store=store, migrate=True) as active:
        # Recheck after taking the remote lease to close the preflight/claim race.
        latest = {name: meta['sha256'] for name, meta in active.state['files'].items()}
        if latest and latest != local:
            raise StorageError('Published dataset changed during migration')
        active.publish(expected=local)
        for meta in active.state['files'].values():
            store.verify(meta['key'], meta['sha256'])
        receipt = {'schema': 1, 'prefix': prefix, 'files': active.state['files']}
        body = json_bytes(receipt)
        store.immutable(prefix + '/_migrations/' + digest(body) + '.json', body)
    return {'prefix': prefix, 'files': len(local), 'verified': True}


def migrate_reports(root, store):
    count = 0
    for name, path in files(root):
        if path.suffix.lower() not in ('.pdf', '.docx', '.xlsx', '.html'):
            continue
        meta = store.immutable_file('reports/' + name, path, file_digest(path))
        store.verify(meta['key'], meta['sha256'])
        count += 1
    return {'prefix': 'reports', 'files': count, 'verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', choices=[*DATASETS, 'reports', 'trade-news'], required=True)
    parser.add_argument('--source', type=Path, help='Explicit legacy directory; required for trade-news')
    parser.add_argument('--allow-empty', action='store_true', help='Explicitly initialize an empty optional dataset')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true')
    action.add_argument('--restore', action='store_true')
    parser.add_argument('--destination', type=Path, help='Empty/new directory for verified export')
    args = parser.parse_args()
    if args.source is not None and not args.source.is_dir():
        parser.error('--source must be an existing directory')
    if args.dataset == 'reports':
        prefix, source = 'reports', args.source or ROOT / 'runtime/reports'
    elif args.dataset == 'trade-news':
        if args.source is None and not args.restore:
            parser.error('--source is required for trade-news')
        prefix, source = 'raw/trade-news', args.source
    else:
        prefix, default = DATASETS[args.dataset]
        source = args.source or default
    if args.restore:
        if args.dataset == 'reports':
            parser.error('Reports use DB files_json object keys, not a dataset manifest')
        target = args.destination
        if target is None or (target.exists() and any(target.iterdir())):
            parser.error('--destination must be new or empty')
        with workspace(target, prefix, write=False):
            pass
        if args.dataset == 'trade-news':
            from .sqlite_state import restore
            restore(target / 'articles.sqlite3')
        print(json.dumps({'restored': str(target), 'prefix': prefix}))
        return
    with tempfile.TemporaryDirectory(prefix='s3-migration-') as temporary:
        if args.dataset == 'un-inputs':
            original = args.source or ROOT / 'data/un_data'
            source = Path(temporary)
            for name in ('scope.json', 'un_data.csv', 'required_country.csv', 'required_hs.csv',
                         'hs2022_exclusions.csv', 'hs2022_exclusions_summary.json'):
                atomic_write(source / name, (original / name).read_bytes())
        inventory = [{'file': name, 'bytes': p.stat().st_size, 'sha256': file_digest(p)}
                     for name, p in files(source, prefix)]
        if not args.apply:
            print(json.dumps({'prefix': prefix, 'apply': False, 'files': inventory}, ensure_ascii=False, indent=2))
            return
        if not inventory and not args.allow_empty:
            parser.error('No source files; check the path or explicitly use --allow-empty')
        store = S3Objects()
        if args.dataset == 'trade-news':
            from .sqlite_state import migrate_database
            migrate_database(source / 'articles.sqlite3', store)
        result = migrate_reports(source, store) if args.dataset == 'reports' else migrate(source, prefix, store)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
