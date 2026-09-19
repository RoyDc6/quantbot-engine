from dataclasses import replace
import unittest

from ..config import Config
from ..decision_diff import classify, diagnose
from ..market import DAY_MS, validate_bars
from ..strategy import Strategy
from .test_pipeline import fixture, rules


class DecisionDiffTests(unittest.TestCase):
    def test_classification_depth(self):
        base = {
            "structure_trigger_count": 0,
            "decision_changed_count": 0,
            "target_weight_changed_count": 0,
            "trade_changed_count": 0,
        }
        self.assertEqual(classify(base), "NO_STRUCTURE_TRIGGERS_IN_WINDOW")
        self.assertEqual(
            classify({**base, "structure_trigger_count": 1}),
            "STRUCTURE_TRIGGERED_BUT_DECISION_UNCHANGED",
        )
        self.assertEqual(
            classify({**base, "structure_trigger_count": 1, "decision_changed_count": 1}),
            "DECISION_CHANGED_BUT_RISK_ALLOCATOR_FLATTENED",
        )
        self.assertEqual(
            classify(
                {
                    **base,
                    "structure_trigger_count": 1,
                    "decision_changed_count": 1,
                    "target_weight_changed_count": 1,
                }
            ),
            "TARGET_CHANGED_BUT_EXECUTION_FLATTENED",
        )
        self.assertEqual(
            classify({**base, "trade_changed_count": 1}),
            "STRUCTURE_REACHED_EXECUTION",
        )

    def test_diagnostic_grain_and_quality(self):
        symbols = ("BTC-USDT", "ETH-USDT")
        config = replace(Config(initial_cash=10_000), symbols=symbols)
        rows = fixture(125)
        now = int(rows[-1][0]) + DAY_MS + 1000
        bars = validate_bars(rows, now)
        bars_by_symbol = {symbol: bars.copy() for symbol in symbols}
        instruments = {symbol: rules(symbol) for symbol in symbols}
        payload = diagnose(
            bars_by_symbol,
            instruments,
            Strategy(config),
            config,
        )
        self.assertEqual(payload["summary"]["evaluation_days"], 5)
        self.assertEqual(payload["summary"]["row_count"], 10)
        self.assertTrue(payload["quality_checks"]["all_pass"])
        self.assertEqual(
            len(
                {
                    (row["signal_close_ms"], row["symbol"])
                    for row in payload["rows"]
                }
            ),
            10,
        )


if __name__ == "__main__":
    unittest.main()
