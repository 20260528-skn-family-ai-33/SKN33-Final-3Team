"""두 뉴스 적재기의 트랜잭션·공용 집계 직렬화 검증. 실제 API/운영 DB는 호출하지 않습니다."""
import subprocess
import sys
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from backend.app.db import news_lock
from backend.app.scheduler.jobs import news_load as news, shortnews_load as short
from backend.tests import test_news_load as news_fixture, test_shortnews_load as short_fixture


class SharedLoadLockTest(unittest.TestCase):
    def setUp(self):
        self.db = short_fixture.ShortnewsLoadTest()
        self.db.setUp()
        self.addCleanup(self.db.doCleanups)
        self.addCleanup(self.db.tearDown)

    def load(self, module):
        if module is news:
            return news.load(self.db.engine, [news_fixture.raw("1", "2026-10-22")],
                             [news_fixture.clean("1", "2026-10-22", "AI", "US", "854143")])
        return short.load(self.db.engine, [short_fixture.raw("1", "2026-10-22")], [short_fixture.clean("1")])

    def exercise_overlap(self, first, second, fail_first=False):
        entered, release, attempted, second_session = (threading.Event() for _ in range(4))
        results, errors = {}, {}
        rebuild = news.rebuild_daily
        scope = second.session_scope

        def pause_first(session, days):
            if threading.current_thread().name == "first-loader":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test release timed out")
                if fail_first:
                    raise RuntimeError("synthetic transaction failure")
            return rebuild(session, days)

        @contextmanager
        def observe_second(engine):
            second_session.set()
            with scope(engine) as session:
                yield session

        def worker(name, module):
            if name == "second":
                attempted.set()
            try:
                results[name] = self.load(module)
            except Exception as exc:
                errors[name] = exc

        with patch.object(news, "rebuild_daily", side_effect=pause_first), \
                patch.object(second, "session_scope", observe_second):
            one = threading.Thread(target=worker, args=("first", first), name="first-loader")
            two = threading.Thread(target=worker, args=("second", second), name="second-loader")
            one.start()
            try:
                self.assertTrue(entered.wait(4))
                two.start()
                self.assertTrue(attempted.wait(2))
                # 대기 중에는 DB의 읽기 스냅샷도 시작하지 않아야 합니다.
                self.assertFalse(second_session.wait(0.15))
            finally:
                release.set()
                one.join(5)
                if two.ident is not None:
                    two.join(5)
        self.assertFalse(one.is_alive())
        self.assertFalse(two.is_alive())
        self.assertTrue(second_session.is_set())
        self.assertEqual(set(errors), {"first"} if fail_first else set())
        self.assertIn("second", results)
        count = 1 if fail_first else 2
        self.assertEqual(len(self.db.rows("article")), count)
        self.assertEqual(len(self.db.rows("article_tag")), count * 3)
        for table in ("news_keyword_daily", "news_product_daily"):
            rows = self.db.rows(table)
            self.assertTrue(rows)
            self.assertTrue(all(row["day_count"] == count for row in rows))
        # 두 출처 모두 재적재해도 기사·태그·집계는 증가하지 않습니다.
        if not fail_first:
            self.load(first)
            self.load(second)
            self.assertEqual(len(self.db.rows("article")), 2)
            self.assertEqual(len(self.db.rows("article_tag")), 6)
            self.assertTrue(all(r["day_count"] == 2 for r in self.db.rows("news_keyword_daily")))

    def test_news_then_shortnews_waits_before_transaction_and_preserves_both_counts(self):
        self.exercise_overlap(news, short)

    def test_shortnews_then_news_waits_before_transaction_and_preserves_both_counts(self):
        self.exercise_overlap(short, news)

    def test_rollback_releases_lock_and_other_loader_can_commit(self):
        self.exercise_overlap(news, short, fail_first=True)

    def test_separate_process_lock_blocks_then_releases(self):
        news_lock.LOAD_LOCK_DIR.mkdir()
        path = news_lock.LOAD_LOCK_DIR / (news_lock.database_identity(self.db.url) + ".lock")
        script = """import sys
from filelock import FileLock
with FileLock(sys.argv[1]):
    print('locked', flush=True)
    sys.stdin.readline()
"""
        process = subprocess.Popen([sys.executable, "-u", "-c", script, str(path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        entered, attempted = threading.Event(), threading.Event()
        def acquire():
            attempted.set()
            with news_lock.article_load_lock(self.db.engine):
                entered.set()
        worker = threading.Thread(target=acquire)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            worker.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(entered.wait(0.15))
        finally:
            process.communicate("release\n", timeout=5)
            if worker.ident is not None:
                worker.join(5)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(worker.is_alive())
        self.assertTrue(entered.is_set())


if __name__ == "__main__":
    unittest.main()
