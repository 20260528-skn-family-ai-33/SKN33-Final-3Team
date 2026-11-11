"""Report Agent 평가기 자체 검증: 점수가 실패·오류·건너뜀을 숨기지 않는지, 증거 해시와 출처 표시가 믿을 만한지.

팀 평가기(test_agent_sql_visualization_evaluation.py)와 같은 기준을 Report Agent 평가기에 적용한다.
"""
import socket
import unittest
from unittest.mock import patch

from backend.scripts import evaluate_report_agent as evaluator
from backend.scripts.evaluate_report_agent import CASES, digest_bytes, offline, revision_metadata, run_case, summarize


class ReportEvaluationTest(unittest.TestCase):
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
        with patch('unittest.defaultTestLoader.loadTestsFromName', return_value=unittest.TestSuite([Fixture()])):
            return run_case({'id': 'fixture', 'group': '승인', 'test': 'fixture'})

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
        outcomes = [{'group': '승인', 'status': status} for status in ('PASS', 'FAIL', 'ERROR', 'SKIP', 'XFAIL')]
        score = summarize(outcomes)
        self.assertEqual((score['승인']['total'], score['승인']['pass'], score['승인']['pass_rate_pct']), (5, 1, 20))
        self.assertEqual(score['합계']['total'], 5)

    def test_registry_has_unique_resolvable_cases(self):
        self.assertEqual(len({case['id'] for case in CASES}), len(CASES))
        self.assertEqual(len({case['test'] for case in CASES}), len(CASES))
        for case in CASES:
            with self.subTest(case=case['id']):
                loader = unittest.TestLoader()
                suite = loader.loadTestsFromName(case['test'])
                self.assertEqual(loader.errors, [])
                self.assertEqual(suite.countTestCases(), 1)

    def test_network_guard_blocks_external_hosts_but_allows_loopback(self):
        """Windows asyncio는 127.0.0.1 소켓 쌍을 쓰므로 루프백만 허용하고 외부 주소는 막는다."""
        calls = []
        guarded = offline(lambda *args, **kwargs: calls.append(args) or 'connected', 0)
        self.assertEqual(guarded(('127.0.0.1', 8000)), 'connected')
        self.assertEqual(guarded('localhost'), 'connected')
        for target in (('api.openai.com', 443), ('10.0.0.5', 3306), 'example.org'):
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                guarded(target)
        self.assertEqual(len(calls), 2)
        method = offline(socket.socket.connect, 1)  # 소켓 메서드는 self 다음 인자가 주소다
        with self.assertRaises(RuntimeError):
            method(object(), ('api.openai.com', 443))

    def test_historical_comparison_swaps_only_registered_sources(self):
        """작업 전 비교는 SOURCES에 등록한 운영 모듈만 바꾼다. 테스트·fixture는 바꾸지 않는다."""
        self.assertTrue(all(path.startswith('backend/app/') and path.endswith('.py') for path in evaluator.SOURCES))
        self.assertFalse(any('tests/' in path for path in evaluator.SOURCES))


if __name__ == '__main__':
    unittest.main()
