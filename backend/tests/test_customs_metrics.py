"""설계서의 이력 창·중앙값·부분 규칙·판본 분리를 검증한다."""
from datetime import date
from decimal import Decimal
import unittest

from backend.app.services.customs_metrics import calculate_series, validate_rules
from backend.app.services.customs_stage import ValidationError


def observation(year, month, amount, weight):
    return date(year, month, 1), Decimal(str(amount)), None if weight is None else Decimal(str(weight))


class CalculationTest(unittest.TestCase):
    def calculate(self, rows, code="0101210000", rules=None, month=None):
        month = month or rows[-1][0]
        return next(calculate_series(rows, code, validate_rules(rules or {}), month, month))

    def test_current_excluded_gaps_and_actual_zero_retained(self):
        rows = [observation(2025, 1, 0, 0), observation(2025, 3, 120, 10), observation(2025, 5, 999, 1)]
        result = self.calculate(rows)
        self.assertEqual(result[1:5], (2, Decimal("60.00"), Decimal("12.000000"), None))

    def test_only_previous_twelve_calendar_months(self):
        rows = [observation(2024, 1, 9999, 1), observation(2024, 2, 10, 1),
                observation(2025, 1, 20, 1), observation(2025, 2, 9999, 1)]
        result = self.calculate(rows)
        self.assertEqual(result[1:4], (2, Decimal("15.00"), Decimal("15.000000")))

    def test_even_median_missing_and_zero_weight(self):
        rows = [observation(2025, 1, 10, 2), observation(2025, 2, 30, 2),
                observation(2025, 3, 50, 0), observation(2025, 4, 80, None), observation(2025, 5, 10, 2)]
        self.assertEqual(self.calculate(rows)[1:4], (4, Decimal("42.50"), Decimal("10.000000")))

    def test_no_history_is_not_zero_baseline(self):
        result = self.calculate([observation(2025, 1, 100, 1)])
        self.assertEqual(result[1:5], (0, None, None, None))

    def test_combined_rules_and_exact_next_month_required(self):
        rules={"r1":{"spike_ratio":3,"return_pct":20},"r2":{"hs_prefixes":["89"]},"r3":{"unit_ratio":3}}
        rows=[observation(2025,m,100,10) for m in (1,2,3)]
        rows += [observation(2025,4,1000,10),observation(2025,5,120,10)]
        result=self.calculate(rows,"8901000000",rules,date(2025,4,1))
        self.assertEqual(result[4],"R1_R2_R3")
        result=self.calculate(rows[:-1],"8901000000",rules,date(2025,4,1))
        self.assertEqual(result[4],"R2_R3")
        self.assertIn("R1_NEXT_MONTH_MISSING",result[5])

    def test_short_history_r2_only_and_zero_trade(self):
        rules={"r1":{"spike_ratio":3,"return_pct":20},"r2":{"hs_prefixes":["88"]},"r3":{"unit_ratio":3}}
        result=self.calculate([observation(2025,1,1000,10)],"8802000000",rules)
        self.assertEqual(result[4],"R2")
        self.assertEqual(self.calculate([observation(2025,1,0,0)],"8802000000",rules)[4],"NONE")

    def test_low_price_and_zero_median_no_division(self):
        rules={"r3":{"unit_ratio":5}}
        rows=[observation(2025,m,100,10) for m in (1,2,3)]+[observation(2025,4,10,10)]
        self.assertEqual(self.calculate(rows,rules=rules)[4],"R3")
        rows=[observation(2025,m,0,10) for m in (1,2,3)]+[observation(2025,4,100,10)]
        result=self.calculate(rows,rules=rules)
        self.assertEqual(result[4],"NONE")
        self.assertIn("R3_ZERO_MEDIAN",result[5])

    def test_zero_amount_is_not_a_low_unit_price_anomaly(self):
        rows=[observation(2025,m,100,10) for m in (1,2,3)]+[observation(2025,4,0,10)]
        result=self.calculate(rows,rules={"r3":{"unit_ratio":5}})
        self.assertEqual(result[1:5],(3,Decimal("100.00"),Decimal("10.000000"),"NONE"))
        self.assertIn("R3_CURRENT_AMOUNT_ZERO",result[5])
        self.assertEqual(result[6:],(Decimal("0"),Decimal("10")))

    def test_r1_confirmed_threshold_and_return_boundaries(self):
        rules={"r1":{"spike_ratio":3,"return_pct":20}}
        for current,following,expected in ((300,80,"R1"),(300,120,"R1"),
                ("299.99",100,"NONE"),(300,"79.99","NONE"),(300,"120.01","NONE")):
            with self.subTest(current=current,following=following):
                rows=[observation(2025,m,100,10) for m in (1,2,3)]
                rows += [observation(2025,4,current,10),observation(2025,5,following,10)]
                self.assertEqual(self.calculate(rows,rules=rules,month=date(2025,4,1))[4],expected)

    def test_r3_confirmed_high_and_low_boundaries(self):
        for amount,expected in ((500,"R3"),(20,"R3"),("499.99","NONE"),("20.01","NONE")):
            with self.subTest(amount=amount):
                rows=[observation(2025,m,100,10) for m in (1,2,3)]+[observation(2025,4,amount,10)]
                self.assertEqual(self.calculate(rows,rules={"r3":{"unit_ratio":5}})[4],expected)

    def test_decimal_overflow_is_explicit(self):
        rows=[observation(2025,1,10**19,"0.001"),observation(2025,2,1,1)]
        result=self.calculate(rows)
        self.assertIsNone(result[3])
        self.assertIn("MEDIAN_DECIMAL_RANGE",result[5])

    def test_invalid_or_unconfirmed_rule_config(self):
        for rules in ({"r1":{}},{"r3":{"unit_ratio":"NaN"}},{"r2":{"hs_prefixes":[89]}},
                      {"r2":{"hs_prefixes":["89"],"typo":1}},{"unknown":{}}):
            with self.assertRaises(ValidationError): validate_rules(rules)


class RuleConfigurationTest(unittest.TestCase):
    def setUp(self):
        from backend.app.db.session import make_engine
        from backend.app.models.base import Base
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def stored_rules(self):
        from backend.app.db.crud import CRUD
        from backend.app.db.session import session_scope
        from backend.app.models.batch import ServiceOption
        with session_scope(self.engine) as session:
            row = CRUD(ServiceOption).get(session, "CUSTOMS_ANOMALY")
            return row.value_json if row else None

    def test_partial_configuration_preserves_existing_rules(self):
        from backend.app.services.customs_metrics import configure_rules
        configure_rules(self.engine, r1_spike_ratio=3, r1_return_pct=20, r2_hs_prefixes=["88", "89"])
        configure_rules(self.engine, r3_unit_ratio=5)
        self.assertEqual(self.stored_rules(), {
            "r1": {"spike_ratio": 3, "return_pct": 20},
            "r2": {"hs_prefixes": ["88", "89"]}, "r3": {"unit_ratio": 5}})
        configure_rules(self.engine)
        self.assertEqual(self.stored_rules()["r3"], {"unit_ratio": 5})

    def test_invalid_configuration_keeps_previous_values(self):
        from backend.app.services.customs_metrics import configure_rules
        configure_rules(self.engine, r2_hs_prefixes=["88", "89"])
        for values in ({"r3_unit_ratio": 0}, {"r1_spike_ratio": 3}):
            with self.assertRaises(ValidationError):
                configure_rules(self.engine, **values)
        self.assertEqual(self.stored_rules(), {"r2": {"hs_prefixes": ["88", "89"]}})


if __name__ == "__main__": unittest.main()
