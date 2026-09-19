from __future__ import annotations

import pandas as pd
import pytest

from hk_daily_selection.contracts import (
    normalize_symbol,
    prepare_bars,
    prepare_membership,
    validate_point_in_time_coverage,
)


def test_bounded_ohlc_envelope_repair_preserves_source_values() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-02-26",
                "symbol": "02611.HK",
                "open": 17.28,
                "high": 17.26,
                "low": 15.88,
                "close": 15.98,
                "volume": 1_000_000,
                "turnover": 16_000_000,
            }
        ]
    )
    bars = prepare_bars(frame, repair_ohlc_envelope=True)

    assert bars.loc[0, "high"] == 17.28
    assert bars.loc[0, "source_high"] == 17.26
    assert bool(bars.loc[0, "ohlc_envelope_repaired"])


def test_large_ohlc_envelope_gap_is_not_repaired() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-02-26",
                "symbol": "02611.HK",
                "open": 20.00,
                "high": 17.00,
                "low": 15.00,
                "close": 16.00,
                "volume": 1_000_000,
                "turnover": 16_000_000,
            }
        ]
    )
    with pytest.raises(ValueError, match="exceeds repair tolerance"):
        prepare_bars(frame, repair_ohlc_envelope=True)


def test_hk_symbol_normalization() -> None:
    assert normalize_symbol("HK.700") == "00700.HK"
    assert normalize_symbol("700.HK") == "00700.HK"
    assert normalize_symbol("800701.HK") == "800701.HK"


def test_duplicate_bars_fail_closed() -> None:
    row = {
        "date": "2026-07-29",
        "symbol": "HK.00700",
        "open": 500.0,
        "high": 505.0,
        "low": 495.0,
        "close": 501.0,
        "volume": 1_000_000,
        "turnover": 501_000_000,
    }
    with pytest.raises(ValueError, match="duplicate date-symbol"):
        prepare_bars(pd.DataFrame([row, row]))


def test_overlapping_membership_intervals_fail_closed() -> None:
    rows = [
        {
            "symbol": "00700.HK",
            "effective_from": "2025-01-01",
            "effective_to": "2025-06-30",
            "name": "Example",
            "sector": "Technology",
        },
        {
            "symbol": "00700.HK",
            "effective_from": "2025-06-30",
            "effective_to": "2025-12-31",
            "name": "Example",
            "sector": "Technology",
        },
    ]
    with pytest.raises(ValueError, match="overlapping membership"):
        prepare_membership(pd.DataFrame(rows))


def test_current_snapshot_cannot_be_backfilled_silently() -> None:
    membership = prepare_membership(
        pd.DataFrame(
            [
                {
                    "symbol": "00700.HK",
                    "effective_from": "2026-07-30",
                    "effective_to": "",
                    "name": "Example",
                    "sector": "Technology",
                    "is_current_snapshot": True,
                    "source_asof": "2026-07-30",
                    "membership_source": "CURRENT_FIXTURE",
                }
            ]
        )
    )
    dates = pd.to_datetime(["2026-07-29", "2026-07-30"])

    strict = validate_point_in_time_coverage(membership, dates)
    assert not strict.ok
    assert any("point-in-time" in error for error in strict.errors)

    explicit_bias = validate_point_in_time_coverage(
        membership,
        dates,
        allow_survivorship_bias=True,
    )
    assert explicit_bias.ok
    assert any("SURVIVORSHIP_BIAS" in item for item in explicit_bias.warnings)
