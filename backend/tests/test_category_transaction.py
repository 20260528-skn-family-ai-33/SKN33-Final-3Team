"""이름 잠금 획득/해제 오류와 성공 커밋의 응답을 구분한다."""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from sqlalchemy.exc import SQLAlchemyError
from backend.app.api.category_transaction import category_transaction
from backend.app.api.common import ApiError


class CategoryTransactionTest(unittest.TestCase):
    def connection(self, results):
        c=Mock(closed=False,invalidated=False)
        c.scalar.side_effect=results
        @contextmanager
        def begin():
            try: yield
            except BaseException: c.rollback(); raise
            else: c.commit()
        c.begin.side_effect=begin
        db=SimpleNamespace(engine=Mock(dialect=SimpleNamespace(name='mysql'),url=SimpleNamespace(database='test')))
        db.engine.connect.return_value=c
        return db,c

    def test_timeout_is_retryable_409_and_null_or_exception_is_db_error(self):
        for value,status in [(0,409),(None,503),(SQLAlchemyError('failed'),503)]:
            db,c=self.connection([value])
            with self.subTest(value=value),self.assertRaises(ApiError) as caught:
                with category_transaction(db): self.fail('must not execute')
            self.assertEqual(caught.exception.status,status)
            self.assertTrue(caught.exception.retryable)
            c.close.assert_called_once();c.begin.assert_not_called();c.detach.assert_called_once()

    def test_committed_success_survives_release_errors(self):
        for failure in [0,None,SQLAlchemyError('release failed')]:
            db,c=self.connection([1,failure])
            with self.assertLogs('backend.app.api.category_transaction',level='ERROR'):
                with category_transaction(db) as actual: self.assertIs(actual,c)
            c.commit.assert_called_once();c.rollback.assert_called_once()
            c.invalidate.assert_called_once();c.close.assert_called_once()

    def test_work_error_rolls_back_and_releases_without_masking_error(self):
        db,c=self.connection([1,1])
        with self.assertRaisesRegex(ValueError,'original'):
            with category_transaction(db): raise ValueError('original')
        c.commit.assert_not_called()
        self.assertEqual(c.rollback.call_count,2)
        self.assertEqual(c.scalar.call_count,2)
        c.close.assert_called_once()
