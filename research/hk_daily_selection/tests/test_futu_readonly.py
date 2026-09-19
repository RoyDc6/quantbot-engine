from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hk_daily_selection import futu_readonly
from hk_daily_selection.futu_readonly import (
    _require_completed_session,
    fetch_current_hsci_membership,
)


def test_future_session_is_rejected() -> None:
    tomorrow = pd.Timestamp.now(tz="Asia/Hong_Kong").normalize() + pd.Timedelta(
        days=1
    )
    with pytest.raises(ValueError, match="future"):
        _require_completed_session(tomorrow)


def test_current_snapshot_cannot_be_backdated() -> None:
    fake_now = datetime(
        2026, 7, 30, 16, 35, tzinfo=ZoneInfo("Asia/Hong_Kong")
    )
    with patch.object(futu_readonly, "_now_hkt", return_value=fake_now):
        with pytest.raises(ValueError, match="cannot be backdated"):
            fetch_current_hsci_membership(
                observed_asof=pd.Timestamp("2026-07-29")
            )


@pytest.mark.skipif(
    os.getenv("RUN_FUTU_READONLY") != "1",
    reason="set RUN_FUTU_READONLY=1 for the local OpenD read-only smoke test",
)
def test_live_current_hsci_snapshot_is_labeled() -> None:
    membership = fetch_current_hsci_membership()

    assert len(membership) >= 500
    assert membership["is_current_snapshot"].all()
    assert membership["source_asof"].notna().all()
    assert membership["observed_at_hkt"].notna().all()
    assert membership["membership_source"].str.startswith(
        "FUTU_CURRENT_INDEX:"
    ).all()
    assert set(membership["security_type"]) <= {"STOCK", "ETF", "REIT"}
