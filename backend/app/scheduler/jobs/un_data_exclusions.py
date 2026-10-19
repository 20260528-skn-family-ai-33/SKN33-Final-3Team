"""담당자가 확인한 특정 실패 발표만 제외. 승인/취소와 감사 기록을 한 파일로 저장."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from filelock import FileLock
from backend.app.scheduler.jobs import un_data_refresh as job


def matches(record, dataset):
    version = job.release_version(dataset)
    approved = record['version']
    return approved[0] == version[0] and job.version_time(approved) == job.version_time(version)


def manage(state_dir, action='list', *, key=None, release=None, approver=None, reason=None):
    state_dir = Path(state_dir)
    job.require(state_dir.is_dir(), '상태 디렉터리가 없습니다')
    with FileLock(str(state_dir/'continuation.lock'), timeout=0), FileLock(str(state_dir/'refresh.lock'), timeout=0):
        path = state_dir/'pending_datasets.json'
        pending = json.loads(path.read_text(encoding='utf-8'))
        checkpoint = json.loads((state_dir/'checkpoint.json').read_text(encoding='utf-8'))
        job.require(pending['version'] == 1, '지원하지 않는 상태 버전')
        for field in ('scope_sha256', 'target_sha256'):
            job.require(pending[field] == checkpoint[field], '체크포인트/재시도 대상 불일치')
        exclusions = pending.setdefault('approved_exclusions', {})
        audit = pending.setdefault('exclusion_audit', [])
        if action == 'list':
            return {'failed': {k: v for k, v in pending['datasets'].items() if v.get('status') == 'FAILED'},
                    'approved': exclusions, 'catalog_errors': pending.get('catalog_errors', {}), 'audit': audit}
        job.require(action in ('approve', 'revoke'), '잘못된 승인 작업')
        job.require(isinstance(approver, str) and approver.strip() and isinstance(reason, str) and reason.strip(),
                    '담당자와 사유가 필요합니다')
        job.require(isinstance(release, str) and release.strip(), '검토한 발표 시각이 필요합니다')
        if action == 'approve':
            entry = pending['datasets'].get(key)
            job.require(entry is not None and entry.get('status') == 'FAILED', '현재 실패한 데이터셋만 승인 가능합니다')
            dataset = entry['dataset']
            job.require(dataset['lastReleased'] == release, '발표 버전 변경: 다시 검토하세요')
            job.require(key not in exclusions, '기존 승인을 먼저 취소하세요')
            record = {'dataset': dataset, 'version': job.release_version(dataset),
                      'approver': approver.strip(), 'reason': reason.strip(),
                      'approved_at': datetime.now(timezone.utc).isoformat(),
                      'failure': {f: entry.get(f) for f in ('error_type', 'error')}}
            exclusions[key] = record
        else:
            job.require(key in exclusions, '취소할 승인이 없습니다')
            record = exclusions[key]
            job.require(record['dataset']['lastReleased'] == release, '승인 발표 버전 불일치')
            # 새 버전/성공한 버전을 오래된 승인 취소로 덮어쓰지 않습니다.
            newer = pending.get('known_versions', {}).get(key)
            success = pending.get('completed_versions', {}).get(key)
            if (newer is None or job.version_time(newer) <= job.version_time(record['version'])) and success is None:
                pending['datasets'].setdefault(key, {'dataset': record['dataset'], 'status': 'PENDING'})
            del exclusions[key]
            pending['published_from'] = checkpoint['last_successful_check_date']
        audit.append({'action': action, 'scope': key, 'version': record['version'],
                      'approver': approver.strip(), 'reason': reason.strip(),
                      'at': datetime.now(timezone.utc).isoformat(), 'approval': record})
        job.save(path, pending)
        return audit[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'approve', 'revoke'])
    parser.add_argument('--state-dir', type=Path, default=job.DEFAULT_STATE)
    parser.add_argument('--scope-key')
    parser.add_argument('--release')
    parser.add_argument('--approver')
    parser.add_argument('--reason')
    args = parser.parse_args()
    result = manage(args.state_dir, args.action, key=args.scope_key, release=args.release,
                    approver=args.approver, reason=args.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
