"""Tests for the regime gate when market breadth is undefined.

WORKBUDDY_SYNC §五 (2026-07-30 修正): missing breadth must be reported as
``UNKNOWN`` rather than silently falling through to ``OFF``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.selector import _regime_for_date


def _benchmark_row(
    close: float, ma_20: float, ma_60: float
) -> pd.Series:
    return pd.Series(
        {
            "benchmark_close": close,
            "benchmark_ma_20": ma_20,
            "benchmark_ma_60": ma_60,
        }
    )


def test_nan_breadth_returns_unknown() -> None:
    config = StrategyConfig(breadth_on=0.55, breadth_caution=0.45)
    # Empty dated frame means breadth cannot be computed; this is the
    # canonical "no constituents have a complete MA20 yet" case.
    dated = pd.DataFrame({"close": [], "ma_20": []})
    benchmark = _benchmark_row(close=100.0, ma_20=98.0, ma_60=95.0)
    regime, breadth = _regime_for_date(dated, benchmark, config)
    assert regime == "UNKNOWN"
    assert not np.isfinite(breadth)


def test_breadth_sample_with_too_few_rows_is_unknown() -> None:
    config = StrategyConfig(breadth_on=0.55, breadth_caution=0.45)
    # All ma_20 entries are NaN -> breadth_sample is empty -> breadth is NaN
    # -> the regime must be UNKNOWN, never ON/CAUTION/OFF, even if the
    # benchmark trio would otherwise look bullish.
    dated = pd.DataFrame(
        {
            "close": [101.0, 102.0],
            "ma_20": [np.nan, np.nan],
        }
    )
    benchmark = _benchmark_row(close=100.0, ma_20=99.0, ma_60=95.0)
    regime, breadth = _regime_for_date(dated, benchmark, config)
    assert regime == "UNKNOWN"
    assert not np.isfinite(breadth)


def test_nan_benchmark_does_not_reach_on_or_off() -> None:
    config = StrategyConfig(breadth_on=0.55, breadth_caution=0.45)
    dated = pd.DataFrame(
        {
            "close": [101.0],
            "ma_20": [99.0],
        }
    )
    # Benchmark MA fields missing -> UNKNOWN per the original branch too.
    benchmark = pd.Series(
        {"benchmark_close": np.nan, "benchmark_ma_20": np.nan, "benchmark_ma_60": np.nan}
    )
    regime, _ = _regime_for_date(dated, benchmark, config)
    assert regime == "UNKNOWN"
