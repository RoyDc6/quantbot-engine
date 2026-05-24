"""
research/factors/technical/advanced.py -- 进阶技术因子 (Phase 2)

新增 8 个因子，补充现有 14 因子的覆盖盲区:
- 资金流: MFI (Money Flow Index)
- 极端值: Williams %R
- 周期偏离: CCI
- 趋势确认: Ichimoku Base Line
- 量价锚定: VWAP Deviation
- 趋势时机: Aroon Oscillator
- 通道位置: Keltner Channel Position
- 突破信号: Donchian Channel Position
"""

from __future__ import annotations

import pandas as pd
import numpy as np

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


class MFIFactor(BaseFactor):
    """资金流量指标 MFI(n) -- 量价版 RSI

    MFI 结合价格和成交量，比纯 RSI 多了资金流向信息。
    > 80 超买, < 20 超卖
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="mfi_14",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "15m"],
            description="Money Flow Index, volume-weighted RSI, default period 14",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]
        volume = data.df["volume"]

        # Typical Price
        tp = (high + low + close) / 3.0
        raw_mf = tp * volume

        # Positive / Negative Money Flow
        tp_diff = tp.diff()
        pos_mf = raw_mf.where(tp_diff > 0, 0.0)
        neg_mf = raw_mf.where(tp_diff < 0, 0.0)

        # Rolling sum
        pos_sum = pos_mf.rolling(window=window, min_periods=window).sum()
        neg_sum = neg_mf.rolling(window=window, min_periods=window).sum()

        mf_ratio = pos_sum / neg_sum.replace(0, np.nan)
        mfi = 100.0 - (100.0 / (1.0 + mf_ratio))
        return mfi.rename(f"MFI_{window}")


class WilliamsRFactor(BaseFactor):
    """威廉指标 Williams %R(n)

    与 Stochastic 互补，衡量收盘价在 N 日高低价范围中的位置。
    范围 [-100, 0]，> -20 超买, < -80 超卖。
    输出归一化到 [-1, +1] 便于融合。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="williams_r_14",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "15m"],
            description="Williams %R, normalized to [-1,+1], default period 14",
            default_params={"window": 14},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 14)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        hh = high.rolling(window=window).max()
        ll = low.rolling(window=window).min()
        wr = -100.0 * (hh - close) / (hh - ll).replace(0, np.nan)
        # 归一化到 [-1, +1]: WR 原始范围 [-100, 0] -> (WR + 50) / 50
        wr_norm = (wr + 50.0) / 50.0
        return wr_norm.rename(f"WilliamsR_{window}")


class CCIFactor(BaseFactor):
    """顺势指标 CCI(n)

    衡量价格偏离统计均值的程度。
    > +100 强势, < -100 弱势。
    归一化: CCI / 100 裁剪到 [-2, +2]。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="cci_20",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="Commodity Channel Index, default period 20",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        tp = (high + low + close) / 3.0
        sma = tp.rolling(window=window).mean()
        mad = tp.rolling(window=window).apply(
            lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
        )
        cci = (tp - sma) / (0.015 * mad).replace(0, np.nan)
        # 归一化: 除以 100 裁剪
        cci_norm = (cci / 100.0).clip(-2.0, 2.0)
        return cci_norm.rename(f"CCI_{window}")


class IchimokuBaseFactor(BaseFactor):
    """一目均衡表基准线 (Kijun-sen)

    Kijun = (N-period High + N-period Low) / 2
    价格在基准线上方 = 多头, 下方 = 空头。
    输出: (Close - Kijun) / Close 的百分比偏差。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="ichimoku_base",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="Ichimoku Kijun-sen deviation, default period 26",
            default_params={"window": 26},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 26)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        kijun = (high.rolling(window=window).max() + low.rolling(window=window).min()) / 2.0
        deviation = (close - kijun) / kijun.replace(0, np.nan) * 100.0
        return deviation.rename(f"IchimokuBase_{window}")


class VWAPDeviationFactor(BaseFactor):
    """VWAP 偏差因子

    VWAP = 累积(Typical Price * Volume) / 累积(Volume)
    偏差 = (Close - VWAP) / VWAP * 100
    价格高于 VWAP = 当日买入成本偏高，反之偏低。
    日内锚定信号，对短线有意义。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="vwap_deviation",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "15m"],
            description="VWAP deviation (%), rolling VWAP over N bars",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]
        volume = data.df["volume"]

        tp = (high + low + close) / 3.0
        cum_tp_vol = (tp * volume).rolling(window=window).sum()
        cum_vol = volume.rolling(window=window).sum()
        vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        deviation = (close - vwap) / vwap.replace(0, np.nan) * 100.0
        return deviation.rename(f"VWAPDev_{window}")


class AroonOscillatorFactor(BaseFactor):
    """Aroon 振荡器

    Aroon Up = ((N - days_since_N_high) / N) * 100
    Aroon Down = ((N - days_since_N_low) / N) * 100
    Oscillator = Up - Down, 范围 [-100, +100]
    > 0 多头趋势, < 0 空头趋势。
    归一化到 [-1, +1]。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="aroon_osc",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="Aroon Oscillator (Up - Down), normalized, default period 25",
            default_params={"window": 25},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 25)
        high = data.df["high"]
        low = data.df["low"]

        def _days_since_extreme(series, window, mode="max"):
            """计算窗口内极值距今的天数"""
            result = pd.Series(np.nan, index=series.index)
            for i in range(window, len(series)):
                chunk = series.iloc[i - window:i + 1]
                if mode == "max":
                    idx = chunk.idxmax()
                else:
                    idx = chunk.idxmin()
                # idx 是 DataFrame index 值，需要找位置
                pos = chunk.index.get_loc(idx)
                result.iloc[i] = window - pos
            return result

        days_high = _days_since_extreme(high, window, "max")
        days_low = _days_since_extreme(low, window, "min")

        aroon_up = ((window - days_high) / window) * 100.0
        aroon_down = ((window - days_low) / window) * 100.0
        aroon_osc = (aroon_up - aroon_down) / 100.0  # 归一化到 [-1, +1]
        return aroon_osc.rename(f"AroonOsc_{window}")


class KeltnerPositionFactor(BaseFactor):
    """Keltner 通道位置因子

    中轨 = EMA(20)
    上轨 = EMA + 2 * ATR(14)
    下轨 = EMA - 2 * ATR(14)
    位置 = (Close - 下轨) / (上轨 - 下轨), 范围 [0, 1]
    归一化到 [-1, +1] (0.5 -> 0)。
    与 BB 类似但用 ATR 替代标准差，更平滑。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="keltner_position",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h", "15m"],
            description="Price position within Keltner Channel (EMA+2ATR), normalized",
            default_params={"ema_window": 20, "atr_window": 14, "atr_mult": 2.0},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        ema_window = params.get("ema_window", 20)
        atr_window = params.get("atr_window", 14)
        atr_mult = params.get("atr_mult", 2.0)

        close = data.df["close"]
        high = data.df["high"]
        low = data.df["low"]

        # EMA 中轨
        mid = close.ewm(span=ema_window, adjust=False).mean()

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_window).mean()

        upper = mid + atr_mult * atr
        lower = mid - atr_mult * atr
        width = (upper - lower).replace(0, np.nan)

        # 位置 [0, 1] -> 归一化 [-1, +1]
        pos = (close - lower) / width
        pos_norm = (pos - 0.5) * 2.0
        return pos_norm.rename(f"KeltnerPos_{ema_window}")


class DonchianPositionFactor(BaseFactor):
    """Donchian 通道位置因子

    上轨 = N-period High
    下轨 = N-period Low
    位置 = (Close - 下轨) / (上轨 - 下轨), 范围 [0, 1]
    归一化到 [-1, +1]。
    经典趋势跟踪信号，与 ADX 互补。
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="donchian_position",
            category="technical",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d", "1h"],
            description="Price position within Donchian Channel (N-period), normalized",
            default_params={"window": 20},
        )

    def compute(self, data: KLineData, **params) -> pd.Series:
        window = params.get("window", 20)
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        upper = high.rolling(window=window).max()
        lower = low.rolling(window=window).min()
        width = (upper - lower).replace(0, np.nan)

        pos = (close - lower) / width
        pos_norm = (pos - 0.5) * 2.0  # [0,1] -> [-1,+1]
        return pos_norm.rename(f"DonchianPos_{window}")
