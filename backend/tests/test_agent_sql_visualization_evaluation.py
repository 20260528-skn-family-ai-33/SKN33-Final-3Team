"""Ensure evaluation scores cannot hide failures, errors or skipped evidence."""
import unittest
from unittest.mock import patch

from backend.scripts.evaluate_sql_visualization import CASES, digest_bytes, evaluate_case, revision_metadata, summarize


class EvaluationTest(unittest.TestCase):
    def test_text_hash_is_stable_across_windows_and_unix_newlines(self):
        self.assertEqual(digest_bytes('한글\nnext\n'.encode()), digest_bytes('한글\r\nnext\r\n'.encode()))
        self.assertNotEqual(digest_bytes(b'a\nb'), digest_bytes(b'a\nc'))

    def test_provenance_marks_uncommitted_harness(self):
        with patch('backend.scripts.evaluate_sql_visualization.git', side_effect=[' M harness.py', 'head']):
            self.assertEqual(revision_metadata(['harness.py']),
                             {'revision': 'head', 'dirty': True, 'changes': [' M harness.py']})
        with patch('backend.scripts.evaluate_sql_visualization.git', side_effect=['', 'head']):
            self.assertFalse(revision_metadata(['harness.py'])['dirty'])

    def evaluate(self, body):
        class Fixture(unittest.TestCase):
            def runTest(self):
                body(self)
        with patch('unittest.defaultTestLoader.loadTestsFromName',
                   return_value=unittest.TestSuite([Fixture()])):
            return evaluate_case({'id': 'fixture', 'agent': 'SQL', 'test': 'fixture'})

    def test_one_failed_subtest_fails_the_entire_case(self):
        def body(case):
            for value in (1, 2):
                with case.subTest(value=value):
                    case.assertEqual(value, 1)
        outcome = self.evaluate(body)
        self.assertEqual(outcome['status'], 'FAIL')
        self.assertTrue(outcome['details'])

    def test_skip_and_error_are_not_passes(self):
        def skipped(case):
            case.skipTest('no evidence')
        def errored(case):
            raise ValueError('invalid fixture')
        self.assertEqual(self.evaluate(skipped)['status'], 'SKIP')
        self.assertEqual(self.evaluate(errored)['status'], 'ERROR')
        self.assertEqual(self.evaluate(lambda case: case.assertTrue(True))['status'], 'PASS')

    def test_score_denominator_includes_all_registered_cases(self):
        outcomes = [{'agent': 'SQL', 'status': status}
                    for status in ('PASS', 'FAIL', 'ERROR', 'SKIP', 'XFAIL')]
        score = summarize(outcomes)['SQL']
        self.assertEqual((score['total'], score['pass'], score['pass_rate_pct']), (5, 1, 20))

    def test_registry_has_unique_resolvable_cases(self):
        self.assertEqual(len({case['id'] for case in CASES}), len(CASES))
        self.assertEqual(len({case['test'] for case in CASES}), len(CASES))
        for case in CASES:
            with self.subTest(case=case['id']):
                loader = unittest.TestLoader()
                suite = loader.loadTestsFromName(case['test'])
                self.assertEqual(loader.errors, [])
                self.assertEqual(suite.countTestCases(), 1)


if __name__ == '__main__':
    unittest.main()
