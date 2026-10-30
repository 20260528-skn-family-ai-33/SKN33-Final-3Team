import unittest
from backend.scripts.migrate_integration_cleanup import without_file_sizes


class CleanupTest(unittest.TestCase):
    def test_only_size_is_removed_from_each_format(self):
        original = {'PDF': {'key': '1/report.pdf', 'size': 42, 'sha256': 'checksum'},
                    'DOCX': {'size': 100, 'storage': 'local', 'created_at': '2026-10-28T00:00:00Z'},
                    'FUTURE': {'size': 7, 'custom_field': {'size': 'keep'}}}
        cleaned = without_file_sizes(original)
        self.assertEqual(cleaned['PDF'], {'key': '1/report.pdf', 'sha256': 'checksum'})
        self.assertEqual(cleaned['DOCX'], {'storage': 'local', 'created_at': '2026-10-28T00:00:00Z'})
        self.assertEqual(cleaned['FUTURE'], {'custom_field': {'size': 'keep'}})
        self.assertEqual(original['PDF']['size'], 42)
        self.assertEqual(without_file_sizes(cleaned), cleaned)
        self.assertIsNone(without_file_sizes(None))
        self.assertEqual(without_file_sizes({}), {})

    def test_unknown_shape_stops_migration(self):
        for value in ([], 'file', {'PDF': None}, {'PDF': 'path'}):
            with self.assertRaises(RuntimeError):
                without_file_sizes(value)
