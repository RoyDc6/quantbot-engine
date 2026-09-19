"""Causal daily features for the Hong Kong stock-selection prototype."""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "mom_20_5",
    "mom_60_5",
    "return_20",
    "relative_strength_20",
    "trend_strength",
    "trend_efficiency_20",
    "turnover_ratio_5_20",
    "up_turnover_share_20",
    "median_turnover_60",
    "active_days_20",
    "realized_vol_20",
    "drawdown_20",
    "extension_atr_20",
    "entry_quality",
]


def _per_symbol_features(
    frame: pd.DataFrame,
    annualization_days: int,
) -> pd.DataFrame:
    data = frame.sort_values("date").copy()
    close = data["close"].astype(float)
    high = data["high"].astype(float)
    low = data["low"].astype(float)
    turnover = data["turnover"].astype(float)

    data["history_bars"] = np.arange(1, len(data) + 1)
    data["return_1"] = close.pct_change(fill_method=None)
    data["return_5"] = close.div(close.shift(5)).sub(1.0)
    data["return_20"] = close.div(close.shift(20)).sub(1.0)
    data["mom_20_5"] = close.shift(5).div(close.shift(20)).sub(1.0)
    data["mom_60_5"] = close.shift(5).div(close.shift(60)).sub(1.0)

    data["ma_20"] = close.rolling(20, min_periods=20).mean()
    data["ma_60"] = close.rolling(60, min_periods=60).mean()
    data["trend_strength"] = (
        close.div(data["ma_20"]).sub(1.0)
        + data["ma_20"].div(data["ma_60"]).sub(1.0)
    )

    path_length = (
        data["return_1"].abs().rolling(20, min_periods=20).sum()
    )
    data["trend_efficiency_20"] = data["return_20"].div(
        path_length.replace(0.0, np.nan)
    )

    data["median_turnover_5"] = turnover.rolling(
        5, min_periods=5
    ).median()
    data["median_turnover_20"] = turnover.rolling(
        20, min_periods=20
    ).median()
    data["median_turnover_60"] = turnover.rolling(
        60, min_periods=60
    ).median()
    active_trade = (
        (turnover > 0.0) & (data["volume"].astype(float) > 0.0)
    ).astype(float)
    data["active_days_20"] = active_trade.rolling(
        20, min_periods=20
    ).sum()
    data["turnover_ratio_5_20"] = data["median_turnover_5"].div(
        data["median_turnover_20"].replace(0.0, np.nan)
    )
    up_turnover = turnover.where(data["return_1"] > 0.0, 0.0)
    data["up_turnover_share_20"] = (
        up_turnover.rolling(20, min_periods=20).sum()
        .div(turnover.rolling(20, min_periods=20).sum().replace(0.0, np.nan))
    )

    data["realized_vol_20"] = (
        data["return_1"].rolling(20, min_periods=20).std(ddof=0)
        * np.sqrt(annualization_days)
    )
    rolling_high = close.rolling(20, min_periods=20).max()
    data["drawdown_20"] = close.div(rolling_high).sub(1.0)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high.sub(low),
            high.sub(previous_close).abs(),
            low.sub(previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr_20"] = true_range.rolling(20, min_periods=20).mean()
    data["extension_atr_20"] = close.sub(data["ma_20"]).div(
        data["atr_20"].replace(0.0, np.nan)
    )
    extension = data["extension_atr_20"]
    # Highest at a controlled 0.5 ATR extension; heavily penalize chasing.
    data["entry_quality"] = -extension.sub(0.5).abs().clip(upper=5.0)
    return data


def compute_features(
    bars: pd.DataFrame,
    benchmark_symbol: str,
    annualization_days: int = 252,
) -> pd.DataFrame:
    """Compute features using only observations available on each row's date."""

    pieces: List[pd.DataFrame] = []
    for _, group in bars.groupby("symbol", sort=False):
        pieces.append(_per_symbol_features(group, annualization_days))
    features = pd.concat(pieces, ignore_index=True)

    benchmark = features[features["symbol"] == benchmark_symbol][
        ["date", "return_20", "close", "ma_20", "ma_60"]
    ].copy()
    if benchmark.empty:
        features["benchmark_return_20"] = np.nan
        features["benchmark_close"] = np.nan
        features["benchmark_ma_20"] = np.nan
        features["benchmark_ma_60"] = np.nan
    else:
        benchmark = benchmark.rename(
            columns={
                "return_20": "benchmark_return_20",
                "close": "benchmark_close",
                "ma_20": "benchmark_ma_20",
                "ma_60": "benchmark_ma_60",
            }
        )
        features = features.merge(benchmark, on="date", how="left")

    features["relative_strength_20"] = (
        features["return_20"] - features["benchmark_return_20"]
    )
    return features.sort_values(["date", "symbol"]).reset_index(drop=True)
