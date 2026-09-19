"""End-to-end guards for the daily screen data contract."""

from __future__ import annotations

from argparse import Namespace

import pandas as pd
import pytest

from hk_daily_selection.cli import _cmd_screen
from hk_daily_selection.config import StrategyConfig
from hk_daily_selection.contracts import (
    prepare_bars,
    validate_screen_bar_freshness,
)
from hk_daily_selection.selector import run_selection
from hk_daily_selection.synthetic import make_synthetic_bundle


def _bundle() -> tuple[pd.DataFrame, pd.DataFrame]:
    return make_synthetic_bundle(StrategyConfig(), periods=180)


def test_prepare_bars_normalizes_fetch_timestamp_to_hkt() -> None:
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
                "fetched_at_hkt": "2026-07-30T08:30:00Z",
            }
        ]
    )
    bars = prepare_bars(frame)
    stamp = bars.loc[0, "fetched_at_hkt"]
    assert stamp.isoformat() == "2026-07-30T16:30:00+08:00"


def test_screen_freshness_rejects_missing_timestamp() -> None:
    bars, _ = _bundle()
    anchor = pd.Timestamp(bars["date"].max())
    bars = bars.drop(columns=["fetched_at_hkt"])
    quality = validate_screen_bar_freshness(bars, anchor)
    assert not quality.ok
    assert any("missing fetched_at_hkt" in error for error in quality.errors)


def test_screen_freshness_rejects_same_day_before_16_30() -> None:
    bars, _ = _bundle()
    anchor = pd.Timestamp(bars["date"].max())
    bars = bars.copy()
    bars.loc[bars["date"] == anchor, "fetched_at_hkt"] = pd.Timestamp(
        f"{anchor.date()}T16:29:00+08:00"
    )
    quality = validate_screen_bar_freshness(bars, anchor)
    assert not quality.ok
    assert any("before 16:30 HKT" in error for error in quality.errors)


def test_screen_freshness_accepts_later_historical_refetch() -> None:
    bars, _ = _bundle()
    anchor = pd.Timestamp(bars["date"].max())
    bars = bars.copy()
    later = (anchor + pd.Timedelta(days=1, hours=9)).tz_localize(
        "Asia/Hong_Kong"
    )
    bars.loc[bars["date"] == anchor, "fetched_at_hkt"] = later
    quality = validate_screen_bar_freshness(bars, anchor)
    assert quality.ok


def test_screen_freshness_rejects_later_adjusted_refetch() -> None:
    bars, _ = _bundle()
    anchor = pd.Timestamp(bars["date"].max())
    bars = bars.copy()
    later = (anchor + pd.Timedelta(days=1, hours=9)).tz_localize(
        "Asia/Hong_Kong"
    )
    bars.loc[bars["date"] == anchor, "fetched_at_hkt"] = later
    bars.loc[bars["date"] == anchor, "adjustment"] = "QFQ"
    quality = validate_screen_bar_freshness(bars, anchor)
    assert not quality.ok
    assert any("future corporate actions" in error for error in quality.errors)


def test_screen_emits_only_requested_session_date() -> None:
    bars, membership = _bundle()
    dates = sorted(pd.to_datetime(bars["date"].unique()))
    anchor = pd.Timestamp(dates[-2])
    run = run_selection(
        bars,
        membership,
        StrategyConfig(),
        mode="screen",
        session_date=anchor,
    )
    assert set(pd.to_datetime(run.scores["date"])) == {anchor}
    assert not (run.scores["date"] > anchor).any()


def test_cli_screen_rejects_csv_without_fetch_provenance(
    tmp_path,
) -> None:
    bars, membership = _bundle()
    bars = bars.drop(columns=["fetched_at_hkt"])
    bars_path = tmp_path / "bars.csv"
    membership_path = tmp_path / "membership.csv"
    bars.to_csv(bars_path, index=False)
    membership.to_csv(membership_path, index=False)
    args = Namespace(
        bars=str(bars_path),
        membership=str(membership_path),
        config=None,
        output_dir=str(tmp_path / "output"),
        allow_survivorship_bias=False,
        as_of=str(pd.Timestamp(bars["date"].max()).date()),
    )
    with pytest.raises(ValueError, match="missing fetched_at_hkt"):
        _cmd_screen(args)


def test_cli_screen_writes_only_anchor_date(tmp_path) -> None:
    bars, membership = _bundle()
    dates = sorted(pd.to_datetime(bars["date"].unique()))
    anchor = pd.Timestamp(dates[-2])
    bars_path = tmp_path / "bars.csv"
    membership_path = tmp_path / "membership.csv"
    output_dir = tmp_path / "output"
    bars.to_csv(bars_path, index=False)
    membership.to_csv(membership_path, index=False)
    args = Namespace(
        bars=str(bars_path),
        membership=str(membership_path),
        config=None,
        output_dir=str(output_dir),
        allow_survivorship_bias=False,
        as_of=str(anchor.date()),
    )
    assert _cmd_screen(args) == 0
    scores = pd.read_csv(output_dir / "daily_scores.csv")
    assert set(pd.to_datetime(scores["date"])) == {anchor}
    assert (output_dir / f"daily_candidates_{anchor.date()}.md").exists()
