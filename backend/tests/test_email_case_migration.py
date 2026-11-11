import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy import String
from sqlalchemy.dialects.mysql import dialect
from backend.scripts.migrate_email_case import plan, EMAIL_COMMENT


class EmailCaseMigrationTest(unittest.TestCase):
    def test_plan_preserves_definition_aligns_comment_and_does_not_write(self):
        connection = MagicMock(dialect=dialect())
        connection.scalar.return_value = 'utf8mb4_0900_ai_ci'
        column = {'name': 'email', 'type': String(254), 'nullable': False,
                  'default': None, 'comment': 'Original email comment'}
        with patch('backend.scripts.migrate_email_case.inspect') as inspect:
            inspect.return_value.get_columns.return_value = [column]
            statements = plan(connection)
            self.assertEqual(len(statements), 1)
            self.assertIn('COLLATE utf8mb4_bin NOT NULL', statements[0])
            self.assertIn(f"COMMENT '{EMAIL_COMMENT}'", statements[0])
            connection.exec_driver_sql.assert_not_called()
            connection.scalar.return_value = 'utf8mb4_bin'
            self.assertEqual(len(plan(connection)), 1)  # Old comment still needs migration.
            column['comment'] = EMAIL_COMMENT
            self.assertEqual(plan(connection), [])
            column['nullable'] = True
            with self.assertRaises(RuntimeError):
                plan(connection)
