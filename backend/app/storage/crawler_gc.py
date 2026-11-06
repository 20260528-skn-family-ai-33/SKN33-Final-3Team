"""Inspect retired crawler chunks. Deletion requires stopped writers and --apply."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from .objects import Conflict, S3Objects, StorageError, json_bytes
from .sqlite_state import POINTER, PREFIX


def referenced(meta):
    parts = meta['parts'].values() if meta.get('schema') == 2 else (meta,)
    return {part['key'] for group in parts if group for part in group['chunks']}


def collect(store, *, apply=False, writers_stopped=False, min_age_days=7, now=None):
    if min_age_days < 1 or (apply and not writers_stopped):
        raise ValueError('Deletion requires stopped writers and a retention of at least one day')
    body, etag = store.get(POINTER)
    state = json.loads(body)
    active = {store.key(key) for key in referenced(state)}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=min_age_days)
    maintenance = None
    if apply:
        # Fence existing writers; new readers/writers refuse this pointer. After a
        # killed maintenance process, rerun only after confirming writers stopped.
        maintenance = uuid4().hex
        etag = store.put(POINTER, json_bytes({**state, 'maintenance': maintenance}), etag=etag)
    deleted, candidates = 0, []
    try:
        options = {'Prefix': store.key(PREFIX + '/_chunks') + '/'}
        while True:
            response = store._call('list_objects_v2', **options)
            for item in response.get('Contents', []):
                key = item['Key']
                if key not in active and item['LastModified'] < cutoff:
                    candidates.append(key)
                    if apply:
                        current, current_etag = store.get(POINTER)
                        if current_etag != etag or json.loads(current).get('maintenance') != maintenance:
                            raise Conflict('Crawler pointer changed during cleanup', retryable=True)
                        store._call('delete_object', Key=key)
                        deleted += 1
            if not response.get('IsTruncated'):
                break
            options['ContinuationToken'] = response['NextContinuationToken']
    finally:
        if maintenance:
            state.pop('maintenance', None)
            store.put(POINTER, json_bytes(state), etag=etag)
    return {'apply': apply, 'candidates': candidates, 'deleted': deleted}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true')
    parser.add_argument('--min-age-days', type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(collect(S3Objects(), apply=args.apply, writers_stopped=args.writers_stopped,
                             min_age_days=args.min_age_days)))


if __name__ == '__main__':
    main()
