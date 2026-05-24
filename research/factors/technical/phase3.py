"""
research/factors/technical/phase3.py -- Phase 3 Technical Factors (v3.0 Candidate)

8 new factors covering two NEW dimensions not in Tech17:
- Volume-Price Microstructure: vwmomentum, close_location_value, gap_factor, vp_corr
- Volatility/Trend Regime: vol_regime, vol_of_vol, efficiency_ratio, price_accel

Design constraint: Each factor must capture information orthogonal to existing Tech17.
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


_MARKETS = ["US", "HK", "CN", "CRYPTO"]
_FREQS = ["1d", "1h", "15m"]


class VolumeWeightedMomentum(BaseFactor):
    """Volume-Weighted Momentum (VWMomentum)

    ROC weighted by volume surge ratio. Unlike VPT (cumulative), this measures
    "volume-confirmed directional momentum" per period.
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="vwmomentum_10",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="10-day ROC weighted by volume ratio",
            default_params={"window": 10},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 10)
        close = data.df["close"]
        volume = data.df["volume"]

        roc = close.pct_change(window)
        vol_ratio = volume / volume.rolling(window * 2, min_periods=5).mean()
        vol_ratio = vol_ratio.clip(0.1, 5.0)
        return (roc * vol_ratio).rename("vwmomentum_10")


class CloseLocationValue(BaseFactor):
    """Close Location Value (CLV)

    Where price closes relative to the day's range, smoothed over window.
    Different from Williams %R (window extremes). CLV is per-bar intraday pressure.
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="close_location_value",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Smoothed close location value within daily range",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        day_range = (high - low).replace(0, np.nan)
        clv = ((close - low) / day_range) * 2 - 1
        return clv.rolling(window, min_periods=1).mean().rename("close_location_value")


class VolatilityRegime(BaseFactor):
    """Volatility Regime Ratio

    Short-term vol / long-term vol. Identifies regime shifts.
    Orthogonal to ATR/BB_Width (absolute vol levels).
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="vol_regime_20",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Short-term vol / long-term vol ratio",
            default_params={"short": 20, "long": 60},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        short_w = params.get("short", 20)
        long_w = params.get("long", 60)
        returns = data.df["close"].pct_change()

        short_vol = returns.rolling(short_w, min_periods=5).std()
        long_vol = returns.rolling(long_w, min_periods=20).std().replace(0, np.nan)
        return (short_vol / long_vol).rename("vol_regime_20")


class VolatilityOfVolatility(BaseFactor):
    """Volatility of Volatility (VoV)

    Rolling std of ATR / ATR. Measures volatility stability.
    Orthogonal to ATR (vol level vs vol stability).
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="vol_of_vol_20",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Rolling std of ATR normalized by ATR (volatility stability)",
            default_params={"atr_window": 14, "vov_window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        atr_window = params.get("atr_window", 14)
        vov_window = params.get("vov_window", 20)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(atr_window, min_periods=1).mean()
        return (atr.rolling(vov_window, min_periods=5).std() / atr).rename("vol_of_vol_20")


class EfficiencyRatio(BaseFactor):
    """Kaufman Efficiency Ratio

    Net change / Sum of abs changes. Range [0,1].
    High = smooth trend, Low = choppy.
    Different from ADX (directional strength vs trend quality).
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="efficiency_ratio_10",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Kaufman efficiency ratio (trend quality)",
            default_params={"window": 10},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 10)
        close = data.df["close"]

        direction = (close - close.shift(window)).abs()
        volatility = close.diff().abs().rolling(window, min_periods=1).sum().replace(0, np.nan)
        return (direction / volatility).rename("efficiency_ratio_10")


class PriceAcceleration(BaseFactor):
    """Price Acceleration (2nd derivative)

    ROC of ROC. Positive = accelerating, Negative = decelerating.
    Orthogonal to ROC (1st derivative) and MACD (EMA momentum).
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="price_accel_10",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Price acceleration (ROC of ROC)",
            default_params={"window": 10},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 10)
        close = data.df["close"]

        roc = close.pct_change(window)
        return (roc - roc.shift(window)).rename("price_accel_10")


class GapFactor(BaseFactor):
    """Overnight Gap Factor

    Gap = Open / PrevClose - 1, smoothed over window.
    Captures overnight/inter-session alpha not in intraday factors.
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="gap_factor_10",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Smoothed overnight gap (open vs prev close)",
            default_params={"window": 10},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 10)
        open_price = data.df["open"]
        close = data.df["close"]

        gap = open_price / close.shift(1) - 1
        return gap.rolling(window, min_periods=1).mean().rename("gap_factor_10")


class VolumePriceCorrelation(BaseFactor):
    """Volume-Price Correlation (VPC)

    Rolling correlation of returns and volume change.
    Positive = confirmed moves, Negative = divergence.
    Orthogonal to Volume Ratio (level) and VPT (cumulative).
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="vp_corr_20",
            category="technical",
            markets=_MARKETS,
            frequencies=_FREQS,
            description="Rolling correlation of returns and volume change",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        returns = data.df["close"].pct_change()
        vol_change = data.df["volume"].pct_change()

        return returns.rolling(window, min_periods=5).corr(vol_change).rename("vp_corr_20")
