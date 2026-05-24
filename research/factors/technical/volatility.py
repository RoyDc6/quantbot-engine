"""
research/factors/technical/volatility.py — 波动率类因子

包含:
- ATR (Average True Range)
- Bollinger Band Width
- 历史波动率
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


class ATRFactor(BaseFactor):
    """平均真实波幅 ATR(n) — 波动率度量

    归一化为 ATR / Close 百分比，跨标的可比。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="atr_14",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="Average True Range (ATR/Close%), 默认周期 14",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        atr = tr.rolling(window=window).mean()
        atr_pct = atr / close * 100
        return atr_pct.rename(f"ATRpct_{window}")


class BollingerBandWidthFactor(BaseFactor):
    """布林带宽度 — 波动率因子

    Band Width = (Upper - Lower) / Middle
    宽度扩大 → 波动率上升，宽度收窄 → 波动率下降
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="bb_width",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="Bollinger Band Width (UPPER-LOWER)/MA, 默认 n=20, k=2",
            default_params={"window": 20, "std_dev": 2},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        std_dev = params.get("std_dev", 2)

        close = data.df["close"]
        sma = close.rolling(window=window).mean()
        std = close.rolling(window=window).std()

        upper = sma + std_dev * std
        lower = sma - std_dev * std
        width = (upper - lower) / sma * 100

        return width.rename(f"BB_width_{window}")

    def validate(self, data: KLineData) -> bool:
        return super().validate(data) and len(data.df) >= 30


class HistoricalVolatilityFactor(BaseFactor):
    """历史波动率 — 对数收益率的标准差 (年化)"""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="hist_vol_20",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="历史波动率 (对数收益率标准差, 年化), 默认周期 20",
            default_params={"window": 20, "annual_factor": 252},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        annual = params.get("annual_factor", 252)

        close = data.df["close"]
        log_ret = np.log(close / close.shift(1))
        hv = log_ret.rolling(window=window).std() * np.sqrt(annual) * 100

        return hv.rename(f"HV_{window}")