"""UN 상태 전체를 잠금 하에 백업하고, 무결성 확인 후 새 경로로 복원한다. DB에는 쓰지 않는다."""
import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from filelock import FileLock

MANIFEST = 'backup_manifest.json'
LOCKS = {'continuation.lock', 'refresh.lock'}


# 경로를 우회하는 심볼릭 링크는 거부하고 모든 상태·응답 캐시의 해시를 기록합니다.
def inventory(folder):
    result = {}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():
            raise ValueError('상태 백업/복원에 심볼릭 링크를 사용할 수 없습니다')
        relative = path.relative_to(folder).as_posix()
        if path.is_file() and relative not in LOCKS | {MANIFEST}:
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if 'checkpoint.json' not in result:
        raise ValueError('초기 체크포인트가 없는 상태는 백업/복원할 수 없습니다')
    return result


# 기존 폴더는 덮어쓰지 않고 완성된 임시 복사본만 최종 경로에 공개합니다.
def copy_verified(source, destination, hashes, *, backup):
    if destination.exists():
        raise ValueError('대상은 아직 존재하지 않는 새 폴더여야 합니다')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.un-state-', dir=destination.parent) as temp:
        staged = Path(temp)/'state'
        staged.mkdir()
        for name in hashes:
            target = staged/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source/name, target)
        if inventory(staged) != hashes:
            raise ValueError('복사 도중 상태 변경/파일 해시 불일치')
        if backup:
            (staged/MANIFEST).write_text(json.dumps({'version':1,'files':hashes}, ensure_ascii=False, indent=2), encoding='utf-8')
        # 작업자는 스케줄러를 중지하고 새 경로로 복원해야 합니다. 기존 runtime은 합치지 않습니다.
        if destination.exists():
            raise ValueError('복사 도중 대상 폴더가 생성되었습니다')
        staged.rename(destination)


def backup_state(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir() or source == destination or source in destination.parents:
        raise ValueError('백업 대상은 상태 폴더 밖의 새 경로여야 합니다')
    # 실행기와 동일한 잠금 순서로 실행 중인 상태의 일부만 백업되는 것을 막습니다.
    with FileLock(str(source/'continuation.lock'), timeout=0), FileLock(str(source/'refresh.lock'), timeout=0):
        hashes = inventory(source)
        copy_verified(source, destination, hashes, backup=True)
    return len(hashes)


def restore_state(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents:
        raise ValueError('복원 대상은 백업 밖의 새 폴더여야 합니다')
    manifest = json.loads((source/MANIFEST).read_text(encoding='utf-8'))
    hashes = inventory(source)
    if manifest.get('version') != 1 or manifest.get('files') != hashes:
        raise ValueError('백업 파일 누락/변조: 복원 중단')
    copy_verified(source, destination, hashes, backup=False)
    return len(hashes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup','restore'])
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    count = (backup_state if args.operation=='backup' else restore_state)(args.source,args.destination)
    print(f'{args.operation} 완료: {count}개 파일. 스케줄러 시작 전 DB·범위 및 취소 상태를 확인하세요.')


if __name__ == '__main__':
    main()
