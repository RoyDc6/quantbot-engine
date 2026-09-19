"""Tests for the screen / backtest PIT split.

WORKBUDDY_SYNC §四 (2026-07-30 修正) requires that the daily screen and
the backtest use different PIT validation rules:

* ``mode='screen'`` validates only the SESSION_DATE supplied by the caller.
* ``mode='backtest'`` validates every trading date implied by the bars.
"""

from __future__ import annotations

import pandas as pd
import pytest

from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.contracts import (
    prepare_membership,
    validate_pit_for_backtest,
    validate_pit_for_screen,
    validate_point_in_time_coverage,
)
from hk_daily_selection.selector import run_selection


def _membership_only_for_session_date() -> pd.DataFrame:
    return prepare_membership(
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
                    "observed_at_hkt": "2026-07-30T16:30:00+08:00",
                    "membership_source": "CURRENT_FIXTURE",
                }
            ]
        )
    )


def test_screen_mode_accepts_single_date() -> None:
    membership = _membership_only_for_session_date()
    screen = validate_pit_for_screen(membership, pd.Timestamp("2026-07-30"))
    assert screen.ok
    assert not screen.errors


def test_screen_mode_rejects_earlier_date() -> None:
    membership = _membership_only_for_session_date()
    screen = validate_pit_for_screen(membership, pd.Timestamp("2026-07-29"))
    assert not screen.ok
    assert any("point-in-time" in error for error in screen.errors)


def test_screen_mode_rejects_blank_source_asof() -> None:
    membership = _membership_only_for_session_date().copy()
    membership["source_asof"] = pd.NaT
    screen = validate_pit_for_screen(membership, pd.Timestamp("2026-07-30"))
    assert not screen.ok
    assert any("missing source_asof" in error for error in screen.errors)


def test_screen_mode_rejects_later_source_asof_per_row() -> None:
    membership = _membership_only_for_session_date().copy()
    later = membership.iloc[[0]].copy()
    later["symbol"] = "00005.HK"
    later["source_asof"] = pd.Timestamp("2026-07-31")
    later["observed_at_hkt"] = pd.Timestamp(
        "2026-07-31T16:30:00+08:00"
    )
    mixed = pd.concat([membership, later], ignore_index=True)
    screen = validate_pit_for_screen(mixed, pd.Timestamp("2026-07-30"))
    assert not screen.ok
    assert any("later than SESSION_DATE" in error for error in screen.errors)


def test_screen_mode_rejects_missing_observed_at() -> None:
    membership = _membership_only_for_session_date().copy()
    membership["observed_at_hkt"] = pd.NaT
    screen = validate_pit_for_screen(membership, pd.Timestamp("2026-07-30"))
    assert not screen.ok
    assert any("missing observed_at_hkt" in error for error in screen.errors)


def test_backtest_mode_rejects_historical_unguarded_run() -> None:
    membership = _membership_only_for_session_date()
    dates = pd.to_datetime(["2026-07-29", "2026-07-30"])
    backtest = validate_pit_for_backtest(membership, dates)
    assert not backtest.ok
    assert any("point-in-time" in error for error in backtest.errors)


def test_explicit_mode_rejects_invalid_value() -> None:
    membership = _membership_only_for_session_date()
    with pytest.raises(ValueError, match="mode must be"):
        validate_point_in_time_coverage(
            membership, pd.to_datetime(["2026-07-30"]), mode="diagonal"
        )


def test_run_selection_in_screen_mode_passes_with_same_day_snapshot() -> None:
    """Synthetic bundle covers the whole window; the screen mode should
    succeed when SESSION_DATE equals the latest bar date even if the snapshot
    only labels that date. The real PIT split behaviour is exercised by the
    membership fixtures in the tests above."""

    from hk_daily_selection.synthetic import make_synthetic_bundle

    bars, membership = make_synthetic_bundle(StrategyConfig(), periods=180)
    run = run_selection(
        bars,
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=pd.Timestamp(bars["date"].max()),
    )
    assert run.quality.ok
    assert not run.scores.empty


def test_run_selection_in_backtest_mode_requires_full_history() -> None:
    """Backtest mode with the same-day-only snapshot should fail-closed."""

    bars_rows = []
    dates = pd.bdate_range("2026-01-02", periods=30)
    for date in dates:
        bars_rows.append(
            {
                "date": date,
                "symbol": "800701.HK",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1_000_000,
                "turnover": 100_000_000,
                "is_suspended": False,
            }
        )
    from hk_daily_selection.contracts import prepare_bars

    bars = prepare_bars(pd.DataFrame(bars_rows))
    membership = _membership_only_for_session_date()
    with pytest.raises(ValueError, match="point-in-time"):
        run_selection(
            bars,
            membership,
            StrategyConfig(),
            mode="backtest",
        )
