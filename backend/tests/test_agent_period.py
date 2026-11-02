"""D-94: distinguish out-of-range requests from holes in published data."""
import unittest
from datetime import date
from unittest.mock import patch
from backend.app.api import trade
from backend.app.api.common import ApiError


class AgentPeriodTest(unittest.TestCase):
    def test_before_first_month_is_range_error(self):
        with patch.object(trade, 'bounds', return_value=[date(2025, 3, 1)]):
            with self.assertRaises(ApiError) as exc:
                trade.resolve_period(None, start='2025-02', end='2025-03')
        self.assertEqual((exc.exception.code, exc.exception.status), ('PERIOD_NOT_AVAILABLE', 422))
        self.assertIn('2025-03', exc.exception.message)

    def test_internal_gap_is_not_zero_or_silently_shortened(self):
        with patch.object(trade, 'bounds', return_value=[date(2025, 1, 1), date(2025, 3, 1)]):
            with self.assertRaises(ApiError) as exc:
                trade.resolve_period(None, start='2025-01', end='2025-03')
        self.assertEqual(exc.exception.code, 'DATA_NOT_READY')

    def test_relative_period_uses_the_same_lower_bound(self):
        with patch.object(trade, 'bounds', return_value=[date(2025, 3, 1)]):
            with self.assertRaises(ApiError) as exc:
                trade.resolve_period(None, months=2)
            self.assertEqual(trade.resolve_period(None, months=1), (date(2025, 3, 1),) * 2)
        self.assertEqual(exc.exception.code, 'PERIOD_NOT_AVAILABLE')
