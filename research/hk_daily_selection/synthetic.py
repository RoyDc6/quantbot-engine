"""Deterministic synthetic data for tests and an end-to-end demo.

Nothing generated here represents a real security or investment result.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .contracts import prepare_bars, prepare_membership


def make_synthetic_bundle(
    config: StrategyConfig | None = None,
    *,
    periods: int = 280,
    seed: int = 20260730,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    cfg = config or StrategyConfig()
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=periods)
    fetched_at_hkt = (
        dates + pd.Timedelta(hours=16, minutes=30)
    ).tz_localize("Asia/Hong_Kong")
    symbols = [f"{index:05d}.HK" for index in range(1, 11)]
    sectors = [
        "Financials",
        "Financials",
        "Information Technology",
        "Information Technology",
        "Consumer Discretionary",
        "Consumer Discretionary",
        "Industrials",
        "Industrials",
        "Utilities",
        "Healthcare",
    ]

    market_returns = rng.normal(0.00025, 0.008, size=periods)
    bar_frames = []

    benchmark_close = 100.0 * np.cumprod(1.0 + market_returns)
    benchmark_open = np.r_[benchmark_close[0], benchmark_close[:-1]] * (
        1.0 + rng.normal(0.0, 0.0015, periods)
    )
    benchmark_high = np.maximum(benchmark_open, benchmark_close) * (
        1.0 + np.abs(rng.normal(0.002, 0.001, periods))
    )
    benchmark_low = np.minimum(benchmark_open, benchmark_close) * (
        1.0 - np.abs(rng.normal(0.002, 0.001, periods))
    )
    bar_frames.append(
        pd.DataFrame(
            {
                "date": dates,
                "symbol": cfg.benchmark_symbol,
                "open": benchmark_open,
                "high": benchmark_high,
                "low": benchmark_low,
                "close": benchmark_close,
                "volume": 20_000_000.0,
                "turnover": benchmark_close * 20_000_000.0,
                "is_suspended": False,
                "suspension_source": "SYNTHETIC_EXPLICIT",
                "fetched_at_hkt": fetched_at_hkt,
            }
        )
    )

    membership_rows = []
    for index, (symbol, sector) in enumerate(zip(symbols, sectors)):
        drift = (index - 4.5) * 0.000035
        idiosyncratic = rng.normal(0.0, 0.010 + index * 0.0003, periods)
        # Introduce persistent but changing cross-sectional leadership.
        regime_alpha = np.zeros(periods)
        regime_alpha[80:180] = (5 - index) * 0.00008
        regime_alpha[180:] = (index - 4) * 0.00006
        returns = 0.75 * market_returns + drift + regime_alpha + idiosyncratic
        close = (35.0 + index * 7.0) * np.cumprod(1.0 + returns)
        open_price = np.r_[close[0], close[:-1]] * (
            1.0 + rng.normal(0.0, 0.0025, periods)
        )
        high = np.maximum(open_price, close) * (
            1.0 + np.abs(rng.normal(0.004, 0.002, periods))
        )
        low = np.minimum(open_price, close) * (
            1.0 - np.abs(rng.normal(0.004, 0.002, periods))
        )
        base_volume = 650_000 + index * 90_000
        volume = np.maximum(
            rng.lognormal(np.log(base_volume), 0.25, periods),
            10_000,
        )
        turnover = close * volume
        bar_frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": symbol,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "turnover": turnover,
                    "is_suspended": False,
                    "suspension_source": "SYNTHETIC_EXPLICIT",
                    "fetched_at_hkt": fetched_at_hkt,
                }
            )
        )
        membership_rows.append(
            {
                "symbol": symbol,
                "effective_from": dates[0],
                "effective_to": "",
                "name": f"SYNTHETIC_{index + 1:02d}",
                "sector": sector,
                "security_type": "STOCK",
                "issuer_id": f"SYNTHETIC_ISSUER_{index + 1:02d}",
                "lot_size": 100,
                "is_current_snapshot": False,
                "source_asof": dates[0],
                "membership_source": "SYNTHETIC_POINT_IN_TIME_FIXTURE",
                "total_market_val": float((20 - index) * 10_000_000_000),
                "screen_universe_rank": index + 1,
                "screen_universe_source": "SYNTHETIC_TOP_N",
                "sector_source": "SYNTHETIC_SECTOR",
                "snapshot_is_suspended": False,
                "suspension_source": "SYNTHETIC_SNAPSHOT",
                "market_snapshot_observed_at_hkt": (
                    dates[0] + pd.Timedelta(hours=16, minutes=30)
                ).tz_localize("Asia/Hong_Kong"),
                "corporate_action_status": "CLEAR",
                "has_recent_corporate_action": False,
                "corporate_action_observed_at_hkt": (
                    dates[0] + pd.Timedelta(hours=16, minutes=30)
                ).tz_localize("Asia/Hong_Kong"),
            }
        )

    return (
        prepare_bars(pd.concat(bar_frames, ignore_index=True)),
        prepare_membership(pd.DataFrame(membership_rows)),
    )
