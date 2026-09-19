"""Configuration for the independent Northstar-D1 model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NorthstarD1Config:
    """Model settings; these values do not control any trading account."""

    short_period: int = 25
    long_period: int = 90
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    structure_threshold: float = 1.01
    td_period: int = 4
    minimum_bars: int = 120

    # Internal exposure ladder.
    short_cross_fraction: float = 0.20
    long_cross_fraction: float = 0.40
    double_cross_fraction: float = 0.60
    double_breakdown_fraction: float = 1.00

    # Internal adjustment controls.
    aligned_structure_fraction: float = 0.20
    opposing_structure_trim: float = 0.10

    # Research-only risk cap used in paper intents.
    max_single_position_pct: float = 0.20

    # A same-day Futu daily bar is considered complete only after this local time.
    session_close_hour: int = 16
    session_close_minute: int = 15

    # Live Futu data contract. A scan fails closed when any check is not met.
    snapshot_max_age_seconds: int = 300
    snapshot_future_tolerance_seconds: int = 60
    opend_max_clock_skew_seconds: int = 120
    futu_tcp_timeout_seconds: float = 1.0
    trading_calendar_lookback_days: int = 40

    # Futu marks shortened trading days as MORNING sessions.
    hk_morning_session_cutoff_hour: int = 12
    hk_morning_session_cutoff_minute: int = 15
    us_morning_session_cutoff_hour: int = 13
    us_morning_session_cutoff_minute: int = 15
