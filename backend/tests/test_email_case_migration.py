import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy import String
from sqlalchemy.dialects.mysql import dialect
from backend.scripts.migrate_email_case import plan


class EmailCaseMigrationTest(unittest.TestCase):
    def test_plan_preserves_column_and_does_not_write(self):
        connection = MagicMock(dialect=dialect())
        connection.scalar.return_value = 'utf8mb4_0900_ai_ci'
        column = {'name': 'email', 'type': String(254), 'nullable': False,
                  'default': None, 'comment': 'Original email comment'}
        with patch('backend.scripts.migrate_email_case.inspect') as inspect:
            inspect.return_value.get_columns.return_value = [column]
            statements = plan(connection)
            self.assertEqual(len(statements), 1)
            self.assertIn('COLLATE utf8mb4_bin NOT NULL', statements[0])
            self.assertIn("COMMENT 'Original email comment'", statements[0])
            connection.exec_driver_sql.assert_not_called()
            connection.scalar.return_value = 'utf8mb4_bin'
            self.assertEqual(plan(connection), [])
            column['nullable'] = True
            with self.assertRaises(RuntimeError):
                plan(connection)
