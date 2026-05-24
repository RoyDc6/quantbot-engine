"""
research/factors/technical/trend.py — 趋势类因子

包含:
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)
- ADX (Average Directional Index)
- 趋势强度 (EMA 间距)
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


class EMAFactor(BaseFactor):
    """指数移动平均 EMA(n) 及价格偏离度"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="ema_deviation",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="价格对 EMA(n) 的偏离百分比, 默认 n=20",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        close = data.df["close"]
        ema = close.ewm(span=window, adjust=False).mean()
        deviation = (close - ema) / ema * 100
        return deviation.rename(f"EMA_dev_{window}")


class SMAFactor(BaseFactor):
    """简单移动平均 SMA(n) 及价格偏离度"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="sma_deviation",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="价格对 SMA(n) 的偏离百分比, 默认 n=20",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        close = data.df["close"]
        sma = close.rolling(window=window).mean()
        deviation = (close - sma) / sma * 100
        return deviation.rename(f"SMA_dev_{window}")


class ADXFactor(BaseFactor):
    """平均趋向指数 ADX(n) — 趋势强度度量

    ADX > 25 表示强趋势，ADX < 20 表示弱趋势/震荡
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="adx_14",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="Average Directional Index, 默认周期 14",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Directional Movement
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = pd.Series(0.0, index=data.df.index)
        minus_dm = pd.Series(0.0, index=data.df.index)

        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

        # Smoothed
        tr_smooth = tr.ewm(span=window, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(span=window, adjust=False).mean() / tr_smooth.replace(0, np.nan)
        minus_di = 100 * minus_dm.ewm(span=window, adjust=False).mean() / tr_smooth.replace(0, np.nan)

        # ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(span=window, adjust=False).mean()

        return adx.rename(f"ADX_{window}")


class TrendStrengthFactor(BaseFactor):
    """趋势强度因子 — 多周期 EMA 间距

    计算快/慢 EMA 之间的归一化间距，衡量趋势强度。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="trend_strength",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="快慢EMA间距百分比, 默认 fast=7, slow=21",
            default_params={"fast": 7, "slow": 21},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        fast = params.get("fast", 7)
        slow = params.get("slow", 21)
        close = data.df["close"]

        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        spread = (ema_fast - ema_slow) / ema_slow * 100

        return spread.rename(f"TrendStr_{fast}_{slow}")