"""
research/factors/llm/sentiment.py — LLM 市场情绪因子

LLM 作为 Narrative Engine（市场叙事引擎）：
- 从量价关系中提取市场情绪信号
- 识别价格与成交量/动量的背离
- 捕捉传统因子难以量化的非线性情绪变化

因子设计：
1. llm_sentiment: 综合市场情绪评分
2. llm_divergence: 量价背离检测（RSI/Volume/Price 背离）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData
from .base import BaseLLMFactor, _DEFAULT_LLM_MODEL


class LLMSentimentFactor(BaseLLMFactor):
    """LLM 市场情绪因子

    从量价行为中提取市场情绪：
    +1.0 = 极度乐观（放量突破/趋势加速）
    +0.5 = 温和乐观（稳步上涨/量价配合）
     0.0 = 中性（震荡/无方向）
    -0.5 = 温和悲观（缩量下跌/反弹乏力）
    -1.0 = 极度悲观（放量暴跌/恐慌抛售）
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="llm_sentiment",
            category="llm",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="LLM 市场情绪因子: 从量价行为提取情绪, 输出 -1~+1",
            default_params={"llm_window": 10, "llm_model": _DEFAULT_LLM_MODEL},
        )

    def _build_prompt(self, features: str) -> str:
        return f"""你是一个市场情绪分析专家。分析以下技术特征，判断当前市场情绪。

技术特征:
{features}

请输出一个 -1 到 +1 之间的分数，表示市场情绪：
+1.0 = 极度乐观（放量突破新高，趋势加速，RSI>70但未背离）
+0.5 = 温和乐观（稳步上涨，量价配合，RSI 50-70）
 0.0 = 中性（震荡整理，成交量萎缩，无明显方向）
-0.5 = 温和悲观（缩量下跌，反弹无力，RSI 30-50）
-1.0 = 极度悲观（放量暴跌，恐慌抛售，RSI<30）

只输出一行: SCORE: X.XX
不要其他文字。"""

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似：基于 RSI + 量价关系的情绪估计"""
        close = data.df["close"]
        volume = data.df["volume"]

        # RSI 情绪
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta).clip(lower=0).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_sentiment = (rsi - 50) / 50  # -1~+1

        # 量价配合
        ret = close.pct_change()
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma.replace(0, np.nan)

        # 放量上涨 = 积极，放量下跌 = 消极
        volume_sentiment = (ret * np.sign(vol_ratio - 1)).fillna(0)
        volume_sentiment = volume_sentiment.rolling(5).mean() * 5  # 放大

        # 综合
        score = rsi_sentiment * 0.6 + volume_sentiment * 0.4
        score = score.clip(-1.0, 1.0)
        return score


class LLMDivergenceFactor(BaseLLMFactor):
    """LLM 背离检测因子

    检测价格与指标之间的背离：
    - 顶背离：价格创新高，但 RSI/OBV 未创新高 → 看空信号
    - 底背离：价格创新低，但 RSI/OBV 未创新低 → 看多信号
    - 隐藏背离：趋势中的短暂反向

    输出：
    +0.5 ~ +1.0: 强底背离（强烈看多）
    +0.1 ~ +0.5: 弱底背离/潜在反转
    -0.1 ~ +0.1: 无背离
    -0.5 ~ -0.1: 弱顶背离/潜在回调
    -1.0 ~ -0.5: 强顶背离（强烈看空）
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="llm_divergence",
            category="llm",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="LLM 背离检测: 识别价格与RSI/成交量的顶底背离, 输出 -1~+1",
            default_params={"llm_window": 10, "llm_model": _DEFAULT_LLM_MODEL},
        )

    def _build_prompt(self, features: str) -> str:
        return f"""你是一个技术分析专家，专门检测价格与指标之间的背离。分析以下技术特征。

技术特征:
{features}

请判断是否存在背离信号，输出 -1 到 +1 之间的分数：
+1.0 = 强底背离（价格新低，RSI/OBV 未新低，强烈看多反转信号）
+0.5 = 弱底背离（价格略低，指标略高，潜在反转）
 0.0 = 无背离（价格与指标同步运动）
-0.5 = 弱顶背离（价格略高，指标略低，潜在回调）
-1.0 = 强顶背离（价格新高，RSI/OBV 未新高，强烈看空反转信号）

只输出一行: SCORE: X.XX
不要其他文字。"""

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似：基于 RSI 背离的简化检测"""
        close = data.df["close"]
        high = data.df["high"]
        low = data.df["low"]

        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta).clip(lower=0).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        # 寻找最近的高低点
        window = 10
        divergence = pd.Series(0.0, index=close.index)

        for i in range(window * 2, len(close)):
            # 近 window 根的最高/最低
            recent_high = high.iloc[i - window:i].max()
            recent_low = low.iloc[i - window:i].min()
            current_close = close.iloc[i]
            current_rsi = rsi.iloc[i]

            # 前 window 根的最高/最低
            prev_high = high.iloc[i - window * 2:i - window].max()
            prev_low = low.iloc[i - window * 2:i - window].min()
            prev_rsi = rsi.iloc[i - window:i - window // 2].mean()

            # 顶背离：价格更高，RSI 更低
            if current_close > prev_high and current_rsi < prev_rsi - 5:
                divergence.iloc[i] = -0.5
            # 底背离：价格更低，RSI 更高
            elif current_close < prev_low and current_rsi > prev_rsi + 5:
                divergence.iloc[i] = 0.5

        return divergence