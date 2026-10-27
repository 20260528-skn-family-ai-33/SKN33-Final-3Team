"""실제 자식 프로세스로 종료 신호·중복 실행·재시작을 검증합니다. API/DB 호출 없음."""
import subprocess
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[2]
CHILD = '''
import sys,time,threading,signal
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from backend.app.scheduler import shortnews_worker as worker
root=Path(sys.argv[1]); worker.ROOT=root
def task():
    (root/'started').touch()
    while not (root/'release').exists(): time.sleep(0.02)
    (root/'finished').touch()
def build():
    scheduler=BlockingScheduler(timezone='Asia/Seoul')
    scheduler.add_job(task,'date',id='local_validation')
    return scheduler
worker.build_scheduler=build
def stop_requested():
    while not (root/'stop').exists(): time.sleep(0.02)
    # Windows terminate()는 핸들러를 호출하지 않으므로 제어 요청으로 같은 종료 경로를 검증합니다.
    while not callable(signal.getsignal(signal.SIGTERM)): time.sleep(0.02)
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
threading.Thread(target=stop_requested,daemon=True).start()
sys.argv=[sys.argv[0]]
worker.main()
'''


class WorkerProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def start(self):
        process = subprocess.Popen([sys.executable, '-u', '-c', CHILD, str(self.root)], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                   encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def await_started(self, process):
        deadline = time.monotonic() + 5
        while not (self.root / 'started').exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail('worker exited before starting: ' + process.stderr.read())
            time.sleep(0.02)
        self.assertTrue((self.root / 'started').exists())

    @unittest.skipIf(os.name == 'nt', 'Windows terminate()는 SIGTERM 핸들러를 실행하지 않음')
    def test_sigterm_waits_for_running_job_and_releases_lock(self):
        process = self.start()
        self.await_started(process)
        process.terminate()
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
        (self.root / 'release').touch()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertTrue((self.root / 'finished').exists())
        with FileLock(str(self.root / 'data/shortnews/worker.lock'), timeout=0):
            pass

    def test_stop_handler_waits_for_job_completion(self):
        process = self.start()
        self.await_started(process)
        (self.root / 'stop').touch()
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.2)
        (self.root / 'release').touch()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertTrue((self.root / 'finished').exists())

    def test_second_worker_is_rejected_while_first_runs(self):
        first = self.start()
        self.await_started(first)
        second = self.start()
        self.assertNotEqual(second.wait(timeout=5), 0)
        self.assertIsNone(first.poll())

    def test_force_killed_worker_can_be_restarted(self):
        first = self.start()
        self.await_started(first)
        first.kill()
        first.wait(timeout=5)
        (self.root / 'started').unlink()
        restarted = self.start()
        self.await_started(restarted)
        (self.root / 'stop').touch()
        (self.root / 'release').touch()
        self.assertEqual(restarted.wait(timeout=5), 0)


if __name__ == '__main__': unittest.main()
