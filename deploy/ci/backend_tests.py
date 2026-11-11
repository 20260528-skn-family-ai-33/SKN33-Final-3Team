"""Run the entire unittest suite. Only exact, recorded failure signatures are tolerated."""
import json
import linecache
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = Path(__file__).with_name("known-failures.json")


class CIResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.allowed = []

    def matches(self, test, error):
        expected = self.policy.get(test.id())
        if expected is None:
            return False
        method = getattr(test, test._testMethodName).__func__.__code__
        tb = error[2]
        while tb and tb.tb_frame.f_code is not method:
            tb = tb.tb_next
        if tb is None:
            return False
        statement = linecache.getline(method.co_filename, tb.tb_lineno).strip()
        lines = str(error[1]).splitlines()
        return [error[0].__name__, lines[0] if lines else "", statement] == expected

    def record(self, test, error, fallback):
        if self.matches(test, error):
            self.allowed.append(test.id())
            self.stream.writeln(f"\nKNOWN {test.id()}: {error[1]}")
        else:
            fallback(test, error)

    def addFailure(self, test, err):
        self.record(test, err, super().addFailure)

    def addError(self, test, err):
        self.record(test, err, super().addError)


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover("backend/tests")
    count = suite.countTestCases()
    result = unittest.TextTestRunner(resultclass=CIResult).run(suite)
    ok = count > 0 and count == result.testsRun and result.wasSuccessful()
    summary = [f"Backend tests: {result.testsRun}/{count}, skipped: {len(result.skipped)}, "
               f"known exceptions: {len(result.allowed)}, gate: {'PASS' if ok else 'FAIL'}"]
    summary.extend(f"- Known: `{name}`" for name in result.allowed)
    summary.extend(f"- Failed: `{test.id()}`" for test, _ in result.failures + result.errors)
    print("\n".join(summary))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("\n".join(summary) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
