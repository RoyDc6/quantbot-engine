"""
research/factors/technical/volume.py — 成交量类因子

包含:
- OBV (On-Balance Volume)
- Volume Ratio (当前量 / 均值)
- Volume Price Trend (VPT)
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


class OBVFactor(BaseFactor):
    """能量潮 OBV — 量价关系因子

    OBV 趋势先于价格变化，背离信号有价值。
    返回归一化 Z-Score，跨标的可比。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="obv_zscore",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="On-Balance Volume Z-Score, 默认窗口 20",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        close = data.df["close"]
        volume = data.df["volume"]

        # OBV 计算
        price_change = close.diff()
        obv = (price_change.apply(np.sign) * volume).fillna(0).cumsum()

        # Z-Score 归一化
        obv_mean = obv.rolling(window=window).mean()
        obv_std = obv.rolling(window=window).std()
        obv_z = (obv - obv_mean) / obv_std.replace(0, np.nan)

        return obv_z.rename(f"OBV_z_{window}")


class VolumeRatioFactor(BaseFactor):
    """成交量比率 — 当前量 / N日均量

    > 1.5 放量, < 0.5 缩量
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="volume_ratio",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "5m", "15m"],
            description="Volume / MA Volume, 默认窗口 20",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        volume = data.df["volume"]
        vol_ma = volume.rolling(window=window).mean()
        ratio = volume / vol_ma.replace(0, np.nan)
        return ratio.rename(f"VolRatio_{window}")


class VPTFactor(BaseFactor):
    """量价趋势 VPT (Volume Price Trend)

    VPT = 累积( Volume * (close - prev_close) / prev_close )
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="vpt",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="Volume Price Trend, 累积量价动量",
            default_params={},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        close = data.df["close"]
        volume = data.df["volume"]
        pct_change = close.pct_change()
        vpt = (pct_change * volume).fillna(0).cumsum()
        # Z-Score 归一化
        vpt_std = vpt.std()
        vpt_z = (vpt - vpt.mean()) / vpt_std if vpt_std != 0 else pd.Series(0.0, index=vpt.index)
        return vpt_z.rename("VPT")