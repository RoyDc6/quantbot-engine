"""Tests for the post-close fetch window (WORKBUDDY_SYNC §四 2026-07-30 修正).

Same-day HK bars may only be fetched at or after 16:30 HKT. Any fetch whose
wall time is before 16:15 HKT is rejected outright. Historical sessions (i.e.
sessions before today) have no wall-clock restriction.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hk_daily_selection import futu_readonly
from hk_daily_selection.futu_readonly import (
    HK_FETCH_EARLIEST_HKT,
    HK_FETCH_SAME_DAY_HKT,
    _require_fetch_window,
    _require_completed_session,
)


HKT = ZoneInfo("Asia/Hong_Kong")


def _fake_now(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=HKT)


def test_same_day_before_16_15_is_rejected() -> None:
    today = pd.Timestamp("2026-07-30")
    fake = _fake_now(2026, 7, 30, 16, 14)
    with patch.object(futu_readonly, "_now_hkt", return_value=fake):
        with pytest.raises(ValueError, match="before 16:15 HKT"):
            _require_fetch_window(today)


def test_same_day_16_15_to_16_29_is_still_rejected() -> None:
    today = pd.Timestamp("2026-07-30")
    fake = _fake_now(2026, 7, 30, 16, 20)
    with patch.object(futu_readonly, "_now_hkt", return_value=fake):
        with pytest.raises(ValueError, match="16:30 HKT"):
            _require_fetch_window(today)


def test_same_day_16_30_is_accepted() -> None:
    today = pd.Timestamp("2026-07-30")
    fake = _fake_now(2026, 7, 30, 16, 30)
    with patch.object(futu_readonly, "_now_hkt", return_value=fake):
        returned = _require_fetch_window(today)
    assert returned.tzinfo is not None


def test_historical_session_has_no_wall_clock_restriction() -> None:
    past = pd.Timestamp("2026-07-29")
    fake = _fake_now(2026, 7, 30, 9, 0)  # 09:00 HKT next morning
    with patch.object(futu_readonly, "_now_hkt", return_value=fake):
        # 09:00 HKT is before 16:15, but the session is historical, so we
        # only require the 16:15 floor for the wall clock, not the 16:30 cap.
        _require_fetch_window(past)


def test_future_session_is_rejected() -> None:
    tomorrow = pd.Timestamp("2026-07-31")
    fake = _fake_now(2026, 7, 30, 17, 0)
    with patch.object(futu_readonly, "_now_hkt", return_value=fake):
        with pytest.raises(ValueError, match="future"):
            _require_fetch_window(tomorrow)


def test_completed_session_legacy_check_still_rejects_intraday() -> None:
    today = pd.Timestamp("2026-07-30")
    fake = _fake_now(2026, 7, 30, 15, 0)
    with patch.object(futu_readonly, "_now_hkt", return_value=fake):
        with pytest.raises(ValueError, match="16:15 HKT"):
            _require_completed_session(today)


def test_window_constants_are_stable() -> None:
    assert HK_FETCH_EARLIEST_HKT == (16, 15)
    assert HK_FETCH_SAME_DAY_HKT == (16, 30)
