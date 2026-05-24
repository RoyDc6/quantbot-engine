"""
research/factors/technical/momentum.py — 动量类因子

包含:
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- ROC (Rate of Change)
- Stochastic Oscillator
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


class RSIFactor(BaseFactor):
    """相对强弱指标 RSI(n)"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="rsi_14",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="Relative Strength Index, 默认周期 14",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        close = data.df["close"]
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)

        avg_gain = gain.rolling(window=window, min_periods=window).mean()
        avg_loss = loss.rolling(window=window, min_periods=window).mean()

        # Wilder smoothing (start at window+1 because rolling mean at index=window
        # is valid, and we use it as seed for subsequent iterations)
        for i in range(window + 1, len(avg_gain)):
            avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (window - 1) + gain.iloc[i]) / window
            avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (window - 1) + loss.iloc[i]) / window

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.rename(f"RSI_{window}")


class MACDFactor(BaseFactor):
    """MACD 指标"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="macd",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="MACD (12, 26, 9): MACD线, 信号线, 柱状图",
            default_params={"fast": 12, "slow": 26, "signal": 9},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        fast = params.get("fast", 12)
        slow = params.get("slow", 26)
        signal = params.get("signal", 9)

        close = data.df["close"]
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        # 返回 MACD 柱状图（最常用作因子值）
        return histogram.rename("MACD_histogram")


class ROCFactor(BaseFactor):
    """变动率指标 ROC(n)"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="roc_10",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="Rate of Change, 默认周期 10",
            default_params={"window": 10},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 10)
        close = data.df["close"]
        roc = close.pct_change(periods=window) * 100
        return roc.rename(f"ROC_{window}")


class StochasticFactor(BaseFactor):
    """随机指标 %K(n)"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="stoch_k_14",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="Stochastic Oscillator %K, 默认周期 14",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        lowest_low = low.rolling(window=window).min()
        highest_high = high.rolling(window=window).max()
        k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
        return k.rename(f"StochK_{window}")