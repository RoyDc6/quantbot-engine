"""
research/factors/llm/pattern.py — LLM 图表形态识别因子

LLM 作为 Pattern Recognizer（图表形态识别器）：
- 识别经典图表形态（头肩顶/底、双顶/底、三角形、旗形等）
- 传统因子难以量化这些非线性形态
- LLM 擅长从价格序列中识别这些模式

因子设计：
1. llm_pattern: 图表形态信号强度
2. llm_anomaly: 异常价格行为检测
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData
from .base import BaseLLMFactor, _DEFAULT_LLM_MODEL


class LLMPatternFactor(BaseLLMFactor):
    """LLM 图表形态识别因子

    识别经典图表形态并输出信号强度：
    +1.0 = 强看多形态（头肩底/双底/上升三角形突破）
    +0.5 = 弱看多形态（旗形/楔形/潜在反转）
     0.0 = 无明确形态
    -0.5 = 弱看空形态（旗形/楔形/潜在回调）
    -1.0 = 强看空形态（头肩顶/双顶/下降三角形跌破）
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="llm_pattern",
            category="llm",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="LLM 图表形态识别: 头肩顶底/双顶底/三角形等, 输出 -1~+1",
            default_params={"llm_window": 10, "llm_model": _DEFAULT_LLM_MODEL},
        )

    def _build_prompt(self, features: str) -> str:
        return f"""你是一个图表形态分析专家。分析以下技术特征，识别图表形态。

技术特征:
{features}

请判断是否存在以下图表形态，输出 -1 到 +1 之间的分数：
+1.0 = 强看多形态（头肩底/双底/W底/上升三角形/看涨旗形）
+0.5 = 弱看多形态（潜在反转/楔形/圆底雏形）
 0.0 = 无明确形态（随机波动/横盘）
-0.5 = 弱看空形态（潜在回调/楔形/圆顶雏形）
-1.0 = 强看空形态（头肩顶/双顶/M顶/下降三角形/看跌旗形）

只输出一行: SCORE: X.XX
不要其他文字。"""

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似：基于价格形态的简化检测"""
        close = data.df["close"]
        high = data.df["high"]
        low = data.df["low"]

        # 检测双顶/双底（简化版）
        pattern = pd.Series(0.0, index=close.index)

        for i in range(40, len(close)):
            # 取最近 20 根 K 线
            seg_high = high.iloc[i - 20:i]
            seg_low = low.iloc[i - 20:i]
            seg_close = close.iloc[i - 20:i]

            # 找两个高点
            h1 = seg_high.iloc[:10].max()
            h1_idx = seg_high.iloc[:10].idxmax()
            h2 = seg_high.iloc[10:].max()
            h2_idx = seg_high.iloc[10:].idxmax()

            # 找两个低点
            l1 = seg_low.iloc[:10].min()
            l1_idx = seg_low.iloc[:10].idxmin()
            l2 = seg_low.iloc[10:].min()
            l2_idx = seg_low.iloc[10:].idxmin()

            # 双顶：两个高点接近，中间有回调
            if abs(h1 - h2) / h1 < 0.03 and h1_idx < h2_idx:
                # 检查中间是否有回调
                mid_low = seg_low.loc[h1_idx:h2_idx].min()
                if mid_low < h1 * 0.97:
                    pattern.iloc[i] = -0.6

            # 双底：两个低点接近，中间有反弹
            if abs(l1 - l2) / l1 < 0.03 and l1_idx < l2_idx:
                mid_high = seg_high.loc[l1_idx:l2_idx].max()
                if mid_high > l1 * 1.03:
                    pattern.iloc[i] = 0.6

        return pattern


class LLMAnomalyFactor(BaseLLMFactor):
    """LLM 异常检测因子

    检测异常的价格/成交量行为：
    - 异常放量/缩量
    - 价格跳空
    - 日内振幅异常
    - 趋势加速/减速

    输出：
    0.0 ~ 0.3: 正常市场行为
    0.3 ~ 0.6: 轻度异常（关注）
    0.6 ~ 0.8: 中度异常（警惕）
    0.8 ~ 1.0: 严重异常（潜在风险/机会）
    """

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="llm_anomaly",
            category="llm",
            markets=["US", "HK", "CN", "CRYPTO"],
            frequencies=["1d"],
            description="LLM 异常检测: 识别异常量价行为, 输出 0~1",
            default_params={"llm_window": 10, "llm_model": _DEFAULT_LLM_MODEL},
        )

    def _build_prompt(self, features: str) -> str:
        return f"""你是一个市场异常检测专家。分析以下技术特征，检测异常行为。

技术特征:
{features}

请输出一个 0 到 1 之间的异常分数：
0.0 ~ 0.2 = 正常（市场行为符合预期）
0.2 ~ 0.4 = 轻微异常（成交量略高/略低，价格小幅跳空）
0.4 ~ 0.6 = 中度异常（放量/缩量明显，价格跳空较大）
0.6 ~ 0.8 = 显著异常（极端放量/缩量，大幅跳空，趋势突变）
0.8 ~ 1.0 = 严重异常（恐慌/狂热，极端行为，潜在转折点）

只输出一行: SCORE: X.XX
不要其他文字。"""

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似：基于统计的异常检测"""
        close = data.df["close"]
        volume = data.df["volume"]
        high = data.df["high"]
        low = data.df["low"]

        # 成交量异常
        vol_ma = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std()
        vol_z = ((volume - vol_ma) / vol_std.replace(0, np.nan)).abs()
        vol_anomaly = (vol_z / 3).clip(0, 1)  # 3σ 以上为异常

        # 收益率异常
        ret = close.pct_change()
        ret_ma = ret.rolling(20).mean()
        ret_std = ret.rolling(20).std()
        ret_z = ((ret - ret_ma) / ret_std.replace(0, np.nan)).abs()
        ret_anomaly = (ret_z / 3).clip(0, 1)

        # 日内振幅异常
        daily_range = (high - low) / close
        range_ma = daily_range.rolling(20).mean()
        range_std = daily_range.rolling(20).std()
        range_z = ((daily_range - range_ma) / range_std.replace(0, np.nan)).abs()
        range_anomaly = (range_z / 3).clip(0, 1)

        # 综合
        score = (vol_anomaly * 0.4 + ret_anomaly * 0.3 + range_anomaly * 0.3)
        score = score.clip(0, 1)
        return score