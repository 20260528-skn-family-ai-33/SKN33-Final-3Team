"""Windows 기본 경로 제한을 넘기기 전에 짧은 영속 상태 경로를 안내합니다."""
import ntpath
import sys


def windows_path_units(path):
    # Win32 경로 길이는 UTF-16 단위로 계산합니다. 이모지는 두 단위입니다.
    return len(ntpath.abspath(str(path)).encode('utf-16-le')) // 2


def windows_path_supported(path):
    return sys.platform != 'win32' or windows_path_units(path) < 260


def validate_state_path(state_dir):
    # 현재 가장 긴 캐시 경로와 원자적 저장의 UUID 임시 접미사까지 미리 확보합니다.
    # 긴 경로 설정 여부에 의존하지 않는 보수적인 Windows 지원 기준입니다.
    suffix = ntpath.join('continuations', '0' * 32, 'requests',
                         '0' * 64 + '.json.' + '0' * 32 + '.tmp')
    candidate = ntpath.join(str(state_dir), suffix)
    if not windows_path_supported(candidate):
        raise ValueError(
            f'UN Windows 상태 경로가 너무 깁니다: 예상 임시 파일 {windows_path_units(candidate)} UTF-16 단위 '
            '(최대 259). --state-dir 또는 --un-state-dir에 C:\\skn-state\\un-data처럼 짧은 영속 경로를 '
            '지정하세요. 기존 상태는 전체 백업·복원으로 옮기고 초기화하거나 삭제하지 마세요.')
