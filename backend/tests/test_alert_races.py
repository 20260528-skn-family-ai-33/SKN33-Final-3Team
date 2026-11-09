"""PR #68 10차 리뷰: 같은 사건을 두 판정 작업이 함께 저장할 때, 같은 회원의 두 창이 팝업을 함께 조회할 때의 경합.

SQLite 테스트 스키마에는 고유 제약이 없어, 운영 스키마와 같은 uq_event_key·uq_user_event를 테스트에서 만든다.
실제 두 연결의 경합(MySQL 잠금)은 로컬 MySQL 검증으로 따로 확인한다.
"""
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import Index, event, func, select, update

from backend.app.api.common import digest, now
from backend.app.scheduler.jobs import major_issue_detect as job
from backend.tests.test_major_issue_detect import T
from backend.tests import test_major_issue_detect as detect_tests


class AlertRaceTest(unittest.TestCase):
    setUp_base = detect_tests.DetectTest.setUp
    consent = detect_tests.DetectTest.consent
    article = detect_tests.DetectTest.article

    def setUp(self):
        self.setUp_base()
        tables = self.meta.tables
        Index('uq_event_key', tables['alert_event'].c.event_key, unique=True).create(self.engine)
        Index('uq_user_event', tables['user_alert'].c.user_id, tables['user_alert'].c.event_id, unique=True).create(self.engine)
        # pysqlite는 기본 설정에서 BEGIN을 첫 쓰기까지 미뤄 SAVEPOINT가 MySQL처럼 동작하지 않는다(바깥 트랜잭션 없이 RELEASE가 커밋).
        # SQLAlchemy 문서의 방식대로 이 테스트 엔진만 트랜잭션 시작 때 BEGIN을 직접 보낸다.
        with self.engine.connect() as c:
            c.connection.driver_connection.isolation_level = None
        event.listen(self.engine, 'begin', lambda conn: conn.exec_driver_sql('BEGIN'))

    def count(self, name):
        with self.engine.connect() as c:
            return c.scalar(select(func.count()).select_from(self.meta.tables[name]))

    # ── 1. 사건 등록 경합 ───────────────────────────────────────
    def test_job_that_loses_the_event_key_race_skips_it_and_continues(self):
        """다른 작업이 존재 확인 뒤·저장 전에 같은 사건을 먼저 등록하면, 이 작업은 오류 없이 그 사건만 건너뛰고
        뒤의 다른 사건은 정상 등록·전달한다. 사건과 회원 알림은 각각 1건이다."""
        self.consent()
        self.article(1, T, category='통상·규제', title='첫 사건')
        self.article(2, T - timedelta(days=1), category='통상·규제', title='둘째 사건')
        first_key = digest(['MAJOR_ISSUE', 'KOTRA_TRADE_REGULATION', 1])
        raced = []

        def other_job_registers_first(conn, cursor, statement, *args):
            """이 작업이 존재 확인을 마치고 첫 사건을 저장하려는 순간(SAVEPOINT 또는 INSERT 직전) 다른 작업이 같은 사건을
            등록·전달해 둔 상태를 만든다. SQLite 테스트 엔진은 연결 하나를 공유하므로 같은 연결에 DB 드라이버로 바로 쓴다
            (실제 두 연결의 경합은 로컬 MySQL 검증)."""
            if raced or not statement.lstrip().upper().startswith(('SAVEPOINT', 'INSERT INTO ALERT_EVENT')):
                return
            raced.append(True)
            raw = conn.connection.driver_connection.cursor()
            stamp = now().isoformat(sep=' ')
            raw.execute('INSERT INTO alert_event (event_type, article_id, event_key, payload_json, created_at) VALUES (?, 1, ?, ?, ?)',
                        ('MAJOR_ISSUE', first_key, '{"schema_version": 1, "label": "첫 사건", "reason": "KOTRA_TRADE_REGULATION", '
                         f'"target": {{}}, "metric_date": "{T.isoformat()}", "article_ids": [1]}}', stamp))
            raw.execute('INSERT INTO user_alert (user_id, event_id, is_interest_match, created_at) VALUES (?, ?, 0, ?)',
                        (self.uid, raw.lastrowid, stamp))

        event.listen(self.engine, 'before_cursor_execute', other_job_registers_first)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', other_job_registers_first)
        summary = job.detect(self.database, T)
        self.assertEqual(raced, [True])
        self.assertEqual((summary['events'], summary['deliveries']), (1, 1))  # 둘째 사건만 이 작업이 등록·전달
        self.assertEqual(self.count('alert_event'), 2)
        self.assertEqual(self.count('user_alert'), 2)
        with self.engine.connect() as c:
            events = self.meta.tables['alert_event']
            self.assertEqual(c.scalar(select(func.count()).select_from(events).where(events.c.event_key == first_key)), 1)

    def test_other_integrity_errors_are_not_hidden(self):
        """사건 키 중복이 아닌 무결성 오류는 '이미 처리된 사건'으로 숨기지 않는다."""
        from sqlalchemy.exc import IntegrityError
        self.consent()
        self.article(1, T, category='통상·규제')
        with patch.object(job, 'deliver_event', side_effect=IntegrityError('insert', {}, Exception('other'))):
            with self.assertRaises(IntegrityError):
                job.detect(self.database, T)
        self.assertEqual(self.count('alert_event'), 0)

    # ── 2. 팝업 예약 ───────────────────────────────────────────
    def popups(self):
        return self.client.get('/api/v1/notifications/popups').json()['groups']

    def ack(self, delivery_id):
        return self.client.post(f'/api/v1/notifications/popups/{delivery_id}/ack', headers={'X-CSRF-Token': self.csrf}, json={})

    def ids(self, groups):
        return [n['user_alert_id'] for g in groups for n in g['notifications']]

    def test_same_alert_is_issued_to_one_window_until_reservation_expires(self):
        """두 창이 확인 전에 조회해도 같은 알림은 한 창에만 발급된다. 그 창이 확인 없이 닫히면 예약 만료 뒤 다시 발급되고,
        확인한 알림은 다시 발급되지 않는다."""
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)
        first = self.popups()
        self.assertEqual(len(self.ids(first)), 1)
        self.assertEqual(self.popups(), [])  # 다른 창: 예약 중인 알림은 받지 않는다
        snapshots = self.meta.tables['api_snapshot']
        with self.engine.begin() as c:  # 첫 창이 확인 없이 닫히고 예약이 끝났다
            c.execute(update(snapshots).where(snapshots.c.kind == 'DELIVERY').values(expires_at=now() - timedelta(seconds=1)))
        again = self.popups()
        self.assertEqual(self.ids(again), self.ids(first))
        self.assertNotEqual(again[0]['delivery_id'], first[0]['delivery_id'])
        self.assertEqual(self.ack(again[0]['delivery_id']).status_code, 204)
        with self.engine.begin() as c:
            c.execute(update(snapshots).where(snapshots.c.kind == 'DELIVERY').values(expires_at=now() - timedelta(seconds=1)))
        self.assertEqual(self.popups(), [])  # 확인한 알림은 예약이 끝나도 다시 오지 않는다

    def test_one_bundle_per_request_so_unshown_kinds_are_not_held(self):
        """화면은 한 번에 한 묶음만 표시한다. 서버도 한 묶음만 발급·예약해, 표시하지 않은 다른 종류가 예약에 묶여 5분 늦어지지 않는다.
        두 창이 조회하면 서로 다른 묶음을 받고, 같은 알림을 함께 받지 않는다."""
        self.consent()
        self.article(1, T, category='통상·규제')
        job.detect(self.database, T)  # 주요 이슈 알림
        tables = self.meta.tables
        with self.engine.begin() as c:  # 리포트 발행 알림(다른 종류)
            rid = c.execute(tables['report'].insert().values(user_id=self.uid, report_type='RPT_WEEKLY', generation_key='qa-race',
                            conditions_json={}, status='READY', retry_count=0, created_at=now())).inserted_primary_key[0]
            event_id = c.execute(tables['alert_event'].insert().values(event_type='REPORT', report_id=rid, event_key=digest(['REPORT', rid]),
                                 payload_json={'destination': 'REPORT_DETAIL'}, created_at=now())).inserted_primary_key[0]
            c.execute(tables['user_alert'].insert().values(user_id=self.uid, event_id=event_id, is_interest_match=False, created_at=now()))
        window1, window2 = self.popups(), self.popups()
        self.assertEqual((len(window1), len(window2)), (1, 1))
        self.assertNotEqual(window1[0]['event_type'], window2[0]['event_type'])
        self.assertFalse(set(self.ids(window1)) & set(self.ids(window2)))
        self.assertEqual(self.popups(), [])

    def test_new_alert_is_issued_while_older_one_is_reserved(self):
        """예약은 그 알림에만 걸린다. 다른 창은 예약 뒤에 새로 온 알림을 받는다."""
        self.consent()
        self.article(1, T, category='통상·규제', title='첫 사건')
        job.detect(self.database, T)
        first = self.ids(self.popups())
        self.article(2, T + timedelta(days=1), category='통상·규제', title='새 사건')
        job.detect(self.database, T + timedelta(days=1))
        second = self.ids(self.popups())
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second, first)


if __name__ == '__main__':
    unittest.main()
