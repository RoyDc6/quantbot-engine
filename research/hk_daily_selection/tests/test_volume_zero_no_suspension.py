"""Tests for the volume=0 no-longer-means-suspended rule.

WORKBUDDY_SYNC §四 (2026-07-30 修正) cancelled the inference that zero
volume / zero turnover implies ``is_suspended=True``. The contract now
requires an explicit suspended flag or an external suspension ledger.
"""

from __future__ import annotations

import pandas as pd

from hk_daily_selection.contracts import prepare_bars, validate_bars


def test_zero_volume_does_not_set_suspended() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-30",
                "symbol": "00700.HK",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 0,
                "turnover": 0,
            }
        ]
    )
    bars = prepare_bars(frame)
    assert pd.isna(bars.loc[0, "is_suspended"])


def test_zero_volume_still_emits_quality_warning() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-30",
                "symbol": "00700.HK",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 0,
                "turnover": 0,
            }
        ]
    )
    bars = prepare_bars(frame)
    quality = validate_bars(bars)
    assert any("zero volume" in warning for warning in quality.warnings)


def test_explicit_suspended_flag_is_preserved() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-30",
                "symbol": "00700.HK",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1_000_000,
                "turnover": 100_500_000,
                "is_suspended": True,
            }
        ]
    )
    bars = prepare_bars(frame)
    assert bool(bars.loc[0, "is_suspended"]) is True
