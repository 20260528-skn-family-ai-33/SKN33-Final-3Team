"""Scrapy를 별도 프로세스로 실행합니다. 기존 예산 DB 경로를 반드시 지정합니다."""
import os
import subprocess
import sys
from pathlib import Path


def command(arguments, environ=None):
    environ = os.environ if environ is None else environ
    arguments = list(arguments)
    has_data = any(arg == '--data-dir' or arg.startswith('--data-dir=') for arg in arguments)
    if not has_data and not any(arg in ('-h','--help') for arg in arguments):
        directory = environ.get('NEWS_CRAWL_DATA_DIR')
        if not directory:
            raise ValueError('--data-dir 또는 NEWS_CRAWL_DATA_DIR로 기존 요청 예산 DB 폴더를 지정하세요')
        arguments = ['--data-dir', directory, *arguments]
    # 하위 프로세스의 작업 폴더와 무관하게 호출 위치 기준으로 파일 경로를 확정합니다.
    path_options = {'--data-dir', '--urls-file', '--exclude-data-dir', '--tag-dictionary'}
    for index, arg in enumerate(arguments):
        if arg in path_options and index + 1 < len(arguments):
            arguments[index + 1] = str(Path(arguments[index + 1]).expanduser().resolve())
        elif '=' in arg and arg.split('=', 1)[0] in path_options:
            option, value = arg.split('=', 1)
            arguments[index] = option + '=' + str(Path(value).expanduser().resolve())
    return [sys.executable, str(Path(__file__).with_name('crawl.py')), *arguments]


def main(arguments=None):
    try:
        args = command(sys.argv[1:] if arguments is None else arguments)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    # Twisted reactor는 기존 APScheduler 프로세스에 설치하지 않습니다.
    return subprocess.run(args, cwd=Path(__file__).parent, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
