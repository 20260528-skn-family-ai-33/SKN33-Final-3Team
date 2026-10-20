"""바깥 예외 처리와 잠금 작업 자체의 실패를 구분하는 회귀 테스트."""
import unittest
from unittest import mock

from backend.app.scheduler.collectors.un_data.locking import db_lock


class LockCleanupContextTest(unittest.TestCase):
    def engine(self, release_error=None, close_error=None):
        engine = mock.Mock()
        engine.dialect.name = 'mysql'
        connection = engine.connect.return_value
        connection.closed = connection.invalidated = False
        connection.scalar.side_effect = [10, 1, release_error or 1]
        connection.execute.return_value.one.return_value = (10, 10)
        connection.close.side_effect = close_error
        return engine, connection

    def test_cleanup_failure_inside_outer_except_is_reported(self):
        # 이미 처리 중인 바깥 오류는 정상 종료한 잠금 작업의 실패가 아닙니다.
        for phase in ('release', 'close'):
            with self.subTest(phase=phase):
                failure = OSError(phase + ' failed')
                engine, connection = self.engine(**{phase + '_error': failure})
                try:
                    raise ValueError('outer handled error')
                except ValueError:
                    with self.assertRaises(OSError) as caught:
                        with db_lock(engine):
                            pass
                    self.assertIs(caught.exception, failure)
                self.assertEqual(connection.close.call_count, 2 if phase == 'close' else 1)

    def test_body_failure_is_preserved_even_when_both_cleanup_steps_fail(self):
        for original in (ValueError('body failed'), KeyboardInterrupt('cancelled')):
            with self.subTest(error=type(original).__name__):
                engine, connection = self.engine(OSError('release'), OSError('close'))
                with self.assertRaises(type(original)) as caught:
                    with db_lock(engine):
                        raise original
                self.assertIs(caught.exception, original)
                self.assertEqual(connection.invalidate.call_count, 2)
                self.assertEqual(connection.close.call_count, 2)

    def test_acquisition_failure_is_preserved(self):
        engine, connection = self.engine(close_error=OSError('close failed'))
        connection.scalar.side_effect = [10, 0]
        with self.assertRaisesRegex(RuntimeError, '다른 UN 갱신'):
            with db_lock(engine):
                self.fail('잠금 획득 실패 후 본문이 실행되면 안 됩니다')
        self.assertEqual(connection.close.call_count, 2)

    def test_success_inside_outer_except_remains_success(self):
        engine, connection = self.engine()
        try:
            raise ValueError('handled')
        except ValueError:
            with db_lock(engine) as guard:
                self.assertIs(guard.connection, connection)
        connection.close.assert_called_once()
        connection.invalidate.assert_not_called()

    def test_cleanup_cancellation_matrix(self):
        # 취소 위치와 원래 작업 실패 유무를 교차 검증합니다.
        for phase in ('release', 'invalidate', 'close'):
            for cancellation_type in (KeyboardInterrupt, SystemExit):
                for body_failed in (False, True):
                    with self.subTest(phase=phase, cancel=cancellation_type.__name__, body_failed=body_failed):
                        cancel = cancellation_type('cleanup cancelled')
                        original = ValueError('original') if body_failed else None
                        engine, connection = self.engine()
                        if phase == 'release':
                            connection.scalar.side_effect = [10, 1, cancel]
                        elif phase == 'invalidate':
                            connection.scalar.side_effect = [10, 1, OSError('release')]
                            connection.invalidate.side_effect = cancel
                        else:
                            connection.close.side_effect = [cancel, None]
                        expected = original if original is not None else cancel
                        with self.assertRaises(type(expected)) as caught:
                            with db_lock(engine):
                                if original is not None:
                                    raise original
                        self.assertIs(caught.exception, expected)
                        self.assertGreaterEqual(connection.close.call_count, 1)
                        connection.invalidate.assert_called_once()
                        if phase == 'close':
                            self.assertEqual(connection.close.call_count, 2)

    def test_repeated_close_and_invalidate_cancellation_is_bounded(self):
        engine, connection = self.engine()
        original = ValueError('original')
        connection.close.side_effect = KeyboardInterrupt('close')
        connection.invalidate.side_effect = KeyboardInterrupt('invalidate')
        with self.assertRaises(ValueError) as caught:
            with db_lock(engine):
                raise original
        self.assertIs(caught.exception, original)
        self.assertEqual(connection.close.call_count, 2)
        connection.invalidate.assert_called_once()


    def test_detach_precedes_lock_acquisition(self):
        engine, connection = self.engine()
        with db_lock(engine):
            pass
        names=[call[0] for call in connection.mock_calls]
        self.assertEqual(names[0], 'detach')
        self.assertLess(names.index('detach'), names.index('scalar'))

    def test_detach_cancellation_never_acquires_lock(self):
        engine, connection = self.engine()
        cancellation=KeyboardInterrupt('detach cancelled')
        connection.detach.side_effect=cancellation
        with self.assertRaises(KeyboardInterrupt) as caught:
            with db_lock(engine):
                self.fail('분리 실패 후 본문 실행 금지')
        self.assertIs(caught.exception,cancellation)
        connection.scalar.assert_not_called()
        connection.close.assert_called_once()
