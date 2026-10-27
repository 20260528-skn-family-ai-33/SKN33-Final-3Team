"""부모·감시 프로세스 중 하나가 종료돼도 이번 실행의 자식 그룹을 정리합니다."""
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

TERMINATE_GRACE_SECONDS = 5


class ProcessMonitorError(RuntimeError):
    """감시기 비정상 종료 또는 완료 메시지 누락. 자식 오류·시간 초과와 구분합니다."""


def stop_group(pid, child=None):
    """SIGTERM 뒤 남은 자손은 SIGKILL. PID는 이번 실행의 시작 메시지에서만 받습니다."""
    def send(sig):
        try:
            os.killpg(pid, sig)
            return True
        except ProcessLookupError:
            return False
    if send(signal.SIGTERM):
        deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline:
            if child is not None:
                child.poll()
            if not send(0):
                break
            time.sleep(0.05)
        send(signal.SIGKILL)
    if child is not None:
        child.wait()


def run_bounded(command, *, timeout, check=False, **options):
    """구조화된 결과로 실제 시간 초과와 자식 종료 코드(124 포함)를 구분합니다."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('작업 제한 시간은 양수여야 합니다')
    read_fd, write_fd = os.pipe()
    monitor = None
    worker_pid = None
    outcome = None
    confirmed = False
    try:
        monitor_command = [sys.executable, str(Path(__file__).resolve()), '--monitor',
                           str(timeout), str(write_fd), *command]
        monitor = subprocess.Popen(monitor_command, stdin=subprocess.PIPE, start_new_session=True,
                                   pass_fds=(write_fd,), bufsize=0, **options)
        os.close(write_fd)
        write_fd = None
        # 감시기 자체가 멈춘 경우에도 부모가 무한 대기하지 않게 합니다.
        deadline = time.monotonic() + timeout + TERMINATE_GRACE_SECONDS + 5
        buffer = b''
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessMonitorError('감시기 응답 제한 시간 초과')
            ready, _, _ = select.select([read_fd], [], [], min(remaining, 0.2))
            if not ready:
                continue
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                message = json.loads(line)
                if message.get('kind') == 'started' and worker_pid is None:
                    worker_pid = message['pid']
                    if type(worker_pid) is not int or worker_pid <= 1:
                        worker_pid = None
                        raise ProcessMonitorError('자식 식별자 오류')
                    # 부모가 자식 PID를 확보하기 전에는 실제 명령을 실행하지 않습니다.
                    monitor.stdin.write(b'1')
                    monitor.stdin.flush()
                elif message.get('kind') == 'finished' and worker_pid is not None and outcome is None:
                    outcome = message
                else:
                    raise ProcessMonitorError('감시기 메시지 순서 오류')
        code = monitor.wait(timeout=max(0.1, deadline - time.monotonic()))
        if (code != 0 or buffer or outcome is None
                or type(outcome.get('timed_out')) is not bool
                or type(outcome.get('returncode')) is not int):
            raise ProcessMonitorError('감시기 비정상 종료 또는 완료 결과 누락')
        confirmed = True
    except (BrokenPipeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        raise ProcessMonitorError('감시기 통신 중단') from exc
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if monitor is not None:
            monitor.stdin.close()
            try:
                monitor.wait(timeout=TERMINATE_GRACE_SECONDS + 2)
            except subprocess.TimeoutExpired:
                monitor.kill()
                monitor.wait()
            if not confirmed and worker_pid is not None:
                stop_group(worker_pid)
    if outcome['timed_out']:
        raise subprocess.TimeoutExpired(command, timeout)
    completed = subprocess.CompletedProcess(command, outcome['returncode'])
    if check:
        completed.check_returncode()
    return completed


def supervise(command, timeout, event_fd):
    def emit(message):
        os.write(event_fd, (json.dumps(message) + '\n').encode())
    # 시작 직후 감시기가 죽어도 자식은 승인 파이프의 EOF를 보고 실행 없이 종료합니다.
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', *command],
                             stdin=subprocess.PIPE, start_new_session=True, bufsize=0)
    timed_out = False
    try:
        emit({'kind': 'started', 'pid': child.pid})
        if not select.select([0], [], [], timeout)[0] or os.read(0, 1) != b'1':
            return 1
        child.stdin.write(b'1')
        child.stdin.flush()
        child.stdin.close()
        deadline = time.monotonic() + timeout
        while child.poll() is None:
            if select.select([0], [], [], 0)[0] and not os.read(0, 1):
                return 1  # 부모 강제 종료: finally에서 자식 그룹을 정리합니다.
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.05)
    finally:
        child.stdin.close()
        stop_group(child.pid, child)
    # 종료 코드와 별도의 필드로 시간 초과를 전달하고 정리가 끝난 뒤에만 완료합니다.
    emit({'kind': 'finished', 'returncode': child.returncode, 'timed_out': timed_out})
    return 0


def worker(command):
    if os.read(0, 1) != b'1':
        return 1
    with open(os.devnull, 'rb') as stream:
        os.dup2(stream.fileno(), 0)
    os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    if sys.argv[1] == '--worker':
        raise SystemExit(worker(sys.argv[2:]))
    raise SystemExit(supervise(sys.argv[4:], float(sys.argv[2]), int(sys.argv[3])))
