import unittest
from backend.app.agents.budget import new_round, reserve, settle, recover, seconds
from backend.app.api.common import ApiError
from backend.app.api.config import Settings


class BudgetTest(unittest.TestCase):
    def reserve(self, ledger, call='c', claim=1, timeout=50, finish=35):
        return reserve(ledger, round_id='r', claim_token=claim, call_id=call,
                       timeout=timeout, finish_reserve=finish)

    def test_reservation_and_settlement_are_idempotent(self):
        original = new_round('r', 1)
        ledger = self.reserve(original)
        self.assertEqual(original['charged_seconds'], 0)
        self.assertEqual(self.reserve(ledger), ledger)
        ledger = settle(ledger, round_id='r', claim_token=1, call_id='c', elapsed=7)
        self.assertEqual(ledger['charged_seconds'], 7)
        self.assertEqual(settle(ledger, round_id='r', claim_token=1, call_id='c', elapsed=9), ledger)

    def test_crash_keeps_full_reservation_and_rejects_old_worker_refund(self):
        ledger = self.reserve(new_round('r', 1))
        ledger = recover(ledger, round_id='r', previous_claim=1, claim_token=2)
        self.assertEqual(ledger['charged_seconds'], 50)
        for claim in (1, 2):
            with self.assertRaises(ApiError) as error:
                settle(ledger, round_id='r', claim_token=claim, call_id='c', elapsed=1)
            self.assertEqual(error.exception.code, 'JOB_CLAIM_LOST')

    def test_retry_preserves_finish_budget(self):
        ledger = self.reserve(new_round('r', 1), 'c1')
        ledger = settle(ledger, round_id='r', claim_token=1, call_id='c1', elapsed=50)
        ledger = self.reserve(ledger, 'c2')
        ledger = settle(ledger, round_id='r', claim_token=1, call_id='c2', elapsed=50)
        with self.assertRaises(ApiError) as error: self.reserve(ledger, 'c3')
        self.assertEqual(error.exception.code, 'RUN_TIMEOUT')
        self.assertEqual(ledger['charged_seconds'], 100)

    def test_recovery_limit_and_round_identity(self):
        ledger = new_round('r', 1)
        for old in (1, 2): ledger = recover(ledger, round_id='r', previous_claim=old, claim_token=old+1)
        with self.assertRaises(ApiError) as error:
            recover(ledger, round_id='r', previous_claim=3, claim_token=4)
        self.assertEqual(error.exception.code, 'RECOVERY_EXHAUSTED')
        with self.assertRaises(ApiError):
            reserve(ledger, round_id='different', claim_token=3, call_id='c', timeout=1)

    def test_overrun_is_charged_and_invalid_settings_rejected(self):
        ledger = self.reserve(new_round('r', 1))
        ledger = settle(ledger, round_id='r', claim_token=1, call_id='c', elapsed=55)
        self.assertEqual(ledger['charged_seconds'], 55)
        with self.assertRaises(ValueError): seconds(Settings({'API_CHAT_SQL_SECONDS': 'nan'}), 'sql')
        self.assertEqual(seconds(Settings({}), 'sql'), 30)
