"""
research/factors/llm/base.py — LLM 因子基类

LLM 因子架构设计（遵循 SOUL.md 三层架构哲学）：
- LLM 是 Market Cognition Layer（市场意识层）
- 擅长：模糊信号、非线性关联、市场叙事
- 输出必须量化为数值，才能进入 FactorRegistry 做 IC 分析

设计原则：
1. 每个 LLM 因子调用 LLM 分析技术特征，输出 -1~+1 的连续值
2. 为控制成本，仅对最近 N 根 K 线调用 LLM，历史数据用规则近似
3. 内置速率限制（9 req/min），避免 429
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


# ============================================================
# NVIDIA NIM API 集成
# ============================================================

_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_LLM_MODEL = "meta/llama-4-maverick-17b-128e-instruct"  # 0.59s, 中文OK
_QUALITY_LLM_MODEL = "mistralai/mistral-small-4-119b-2603"  # 0.69s, 119B

# 速率限制：9 req/min
import time
_last_call_time: float = 0.0
_MIN_INTERVAL = 60.0 / 9  # ~6.67s between calls


def _rate_limited_call(prompt: str, model: str = _DEFAULT_LLM_MODEL,
                       max_tokens: int = 200, temperature: float = 0.3) -> str:
    """带速率限制的 LLM 调用"""
    global _last_call_time

    # 速率限制
    elapsed = time.time() - _last_call_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

    import requests
    api_key = os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        return "[ERROR missing NVIDIA_API_KEY]"

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.post(
        f"{_NVIDIA_BASE_URL}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=120,
    )
    _last_call_time = time.time()

    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"]
    else:
        return f"[ERROR {resp.status_code}]"


def _parse_score(text: str) -> Optional[float]:
    """从 LLM 回复中解析数值分数

    支持格式:
    - "SCORE: 0.75"
    - "分数: -0.3"
    - "score=0.5"
    - 纯数字行
    """
    # 尝试匹配 SCORE: X.XX 或 分数: X.XX
    patterns = [
        r"(?:SCORE|score|Score|分数)[:\s=]+([+-]?\d+\.?\d*)",
        r"^([+-]?\d+\.?\d*)$",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            return max(-1.0, min(1.0, val))  # 钳制到 [-1, 1]
    return None


def _extract_technical_features(data: KLineData, n_bars: int = 20) -> str:
    """从 K 线数据中提取技术特征摘要（用于 LLM 分析）"""
    df = data.df.tail(n_bars).copy()
    if len(df) < 10:
        return "数据不足"

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 基础统计
    ret_1d = close.pct_change().tail(5).tolist()
    ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0
    ret_20d = (close.iloc[-1] / close.iloc[0] - 1) * 100

    # 波动率
    hv = close.pct_change().std() * np.sqrt(252) * 100

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).iloc[-1] if not rs.empty else 50

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = (ema12 - ema26).iloc[-1]

    # 布林带位置
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_pos = ((close.iloc[-1] - sma20.iloc[-1]) / (2 * std20.iloc[-1])
              ) if std20.iloc[-1] != 0 else 0

    # 成交量
    vol_ratio = volume.iloc[-1] / volume.tail(20).mean() if len(volume) >= 20 else 1

    # 价格位置（相对 20日高低）
    hh20 = high.tail(20).max()
    ll20 = low.tail(20).min()
    price_pos = ((close.iloc[-1] - ll20) / (hh20 - ll20) * 100
                 ) if (hh20 - ll20) != 0 else 50

    features = (
        f"价格: {close.iloc[-1]:.2f} | "
        f"近5日涨跌幅: {[f'{x*100:.1f}%' if not pd.isna(x) else 'N/A' for x in ret_1d]} | "
        f"近5日: {ret_5d:.1f}% | 近20日: {ret_20d:.1f}% | "
        f"RSI(14): {rsi:.1f} | MACD: {macd:.4f} | "
        f"布林带位置: {bb_pos:.2f}σ | "
        f"波动率(年化): {hv:.1f}% | "
        f"成交量比: {vol_ratio:.2f} | "
        f"价格位置(20日): {price_pos:.0f}%"
    )
    return features


class BaseLLMFactor(BaseFactor):
    """LLM 因子基类 — 封装 LLM 调用和分数解析

    子类只需实现:
    - meta() 类方法
    - _build_prompt(features) 方法: 构建分析 prompt
    """

    # LLM 调用参数
    llm_model: str = _DEFAULT_LLM_MODEL
    llm_max_tokens: int = 200
    llm_temperature: float = 0.3

    # 最近多少根 K 线调用 LLM（其余用规则近似）
    llm_window: int = 10

    def _build_prompt(self, features: str) -> str:
        """构建 LLM 分析 prompt — 子类必须实现"""
        raise NotImplementedError

    def _rule_fallback(self, data: KLineData) -> pd.Series:
        """规则近似 — 当不调用 LLM 时使用"""
        raise NotImplementedError

    def compute(self, data: KLineData, **params) -> pd.Series:
        """计算 LLM 因子值

        策略：
        - 对最近 N 根 K 线调用 LLM
        - 对历史 K 线使用规则近似
        - 返回完整的 pd.Series
        """
        window = params.get("llm_window", self.llm_window)
        model = params.get("llm_model", self.llm_model)

        n = len(data.df)
        if n < 30:
            return pd.Series(index=data.df.index, dtype=float)

        # 1. 规则近似（全量）
        rule_values = self._rule_fallback(data)

        # 2. LLM 增强（最近 window 根）
        llm_values = {}
        for i in range(max(0, n - window), n):
            # 提取该 bar 附近的特征
            subset = KLineData(
                symbol=data.symbol,
                market=data.market,
                timeframe=data.timeframe,
                df=data.df.iloc[:i + 1],
                provider=data.provider,
            )
            features = _extract_technical_features(subset, n_bars=20)
            prompt = self._build_prompt(features)
            response = _rate_limited_call(prompt, model=model,
                                          max_tokens=self.llm_max_tokens,
                                          temperature=self.llm_temperature)
            score = _parse_score(response)
            if score is not None:
                llm_values[data.df.index[i]] = score

        # 3. 合并：LLM 值覆盖规则值
        result = rule_values.copy()
        for idx, val in llm_values.items():
            result[idx] = val

        return result.rename(self.meta().name)
