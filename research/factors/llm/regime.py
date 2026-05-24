"""
research/factors/llm/regime.py — LLM 市场状态因子

LLM 作为 Regime Detector（市场状态检测器）：
- 从价格行为中识别市场状态（趋势/震荡/高波动/反转）
- 输出 -1 (强空头趋势) 到 +1 (强多头趋势) 的连续值
- 0 附近表示震荡/无方向

参考 SOUL.md 架构哲学：
- Regime-Dependent LLM Weight 的核心输入
- 高波动/政策市场景下权重最高 (45-50%)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData
from .base import BaseLLMFactor, _DEFAULT_LLM_MODEL


class LLMRegimeFactor(BaseLLMFactor):
    """LLM 市场状态因子

    识别市场处于什么状态，输出连续值：
    +0.5 ~ +1.0: 强多头趋势
    +0.1 ~ +0.5: 弱多头/反弹
    -0.1 ~ +0.1: 震荡/无方向
    -0.5 ~ -0.1: 弱空头/回调
    -1.0 ~ -0.5: 强空头趋势
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="llm_regime",
            category="llm",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="LLM 市场状态因子: 识别趋势/震荡/反转, 输出 -1~+1",
            default_params={"llm_window": 10, "llm_model": _DEFAULT_LLM_MODEL},
        )

    def _build_prompt(self, features: str) -> str:
        return f"""你是一个量化市场状态分析专家。分析以下技术特征，判断当前市场状态。

技术特征:
{features}

请输出一个 -1 到 +1 之间的分数，表示市场状态：
+1.0 = 强多头趋势（趋势明确向上，动能强劲）
+0.5 = 弱多头/反弹（短期向上，但趋势不明确）
 0.0 = 震荡/无方向（横盘整理，无明显趋势）
-0.5 = 弱空头/回调（短期向下，但非趋势性）
-1.0 = 强空头趋势（趋势明确向下，动能强劲）

只输出一行: SCORE: X.XX
不要其他文字。"""

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似：基于 EMA 趋势 + RSI 的简化 regime 判断"""
        close = data.df["close"]
        ema7 = close.ewm(span=7, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema63 = close.ewm(span=63, adjust=False).mean()

        # 趋势方向：多周期 EMA 排列
        trend_short = (ema7 - ema21) / ema21 * 100
        trend_long = (ema21 - ema63) / ema63 * 100

        # RSI 作为动量修正
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta).clip(lower=0).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_norm = (rsi - 50) / 50  # 归一化到 -1~+1

        # 综合评分
        score = (trend_short * 0.4 + trend_long * 0.3 + rsi_norm * 0.3) / 100
        score = score.clip(-1.0, 1.0)
        return score


class LLMRegimeConfidenceFactor(BaseLLMFactor):
    """LLM 市场状态置信度因子

    评估 LLM 对自己 regime 判断的置信度。
    高置信度 = 市场信号清晰（强趋势或明确反转）
    低置信度 = 市场混沌（多空胶着）
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="llm_regime_confidence",
            category="llm",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="LLM 市场状态置信度: 0~1, 越高表示信号越清晰",
            default_params={"llm_window": 10, "llm_model": _DEFAULT_LLM_MODEL},
        )

    def _build_prompt(self, features: str) -> str:
        return f"""你是一个量化市场分析专家。分析以下技术特征，评估市场信号的清晰度。

技术特征:
{features}

请输出一个 0 到 1 之间的置信度分数：
1.0 = 信号极其清晰（强趋势/明确反转/关键突破）
0.7 = 信号较清晰（趋势明确，但有少量噪音）
0.5 = 信号模糊（多空力量均衡）
0.3 = 信号混乱（频繁反转/无方向）
0.0 = 完全混沌（无法判断方向）

只输出一行: SCORE: X.XX
不要其他文字。"""

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似：基于 ADX 的置信度估计"""
        high = data.df["high"]
        low = data.df["low"]
        close = data.df["close"]

        # ADX 计算
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = pd.Series(0.0, index=data.df.index)
        minus_dm = pd.Series(0.0, index=data.df.index)
        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

        tr_smooth = tr.ewm(span=14, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(span=14, adjust=False).mean() / tr_smooth.replace(0, np.nan)
        minus_di = 100 * minus_dm.ewm(span=14, adjust=False).mean() / tr_smooth.replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(span=14, adjust=False).mean()

        # ADX > 25 高置信度，< 20 低置信度
        confidence = adx / 50  # 归一化到 ~0~1
        confidence = confidence.clip(0, 1)
        return confidence