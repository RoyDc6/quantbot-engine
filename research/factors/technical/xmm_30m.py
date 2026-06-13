"""
research/factors/technical/xmm_30m.py - 30m XMM research factor.

This factor wraps the protected production XMM engine in read-only mode and
turns its latest per-bar state into a continuous score in [-100, 100].
It is intended for HK/US research on 30-minute bars, not direct execution.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

from ..base import BaseFactor
from ..registry import FactorMeta
from ...data.base import KLineData


BASE = Path(__file__).resolve().parents[3]
XMM_PATH = str(BASE / "xmm-strategy")
if XMM_PATH not in sys.path:
    sys.path.insert(0, XMM_PATH)

from modules.engine import XMMStrategy  # noqa: E402


class XMM30mFactor(BaseFactor):
    """Independent 30-minute XMM factor for HK/US research."""

    @classmethod
    def meta(cls) -> FactorMeta:
        return FactorMeta(
            name="xmm_30m",
            category="technical",
            markets=["US", "HK"],
            frequencies=["30m"],
            description=(
                "30-minute XMM factor score in [-100, 100]. Uses production "
                "XMM rules read-only, with soft trend/structure/TD state "
                "preserved while HOLD."
            ),
            default_params={
                "min_bars": 100,
                "lookback": 160,
                "short_period": 25,
                "long_period": 90,
                "soft_score": True,
            },
            version="0.1.0",
        )

    def validate(self, data: KLineData) -> bool:
        if not super().validate(data):
            return False
        if data.market not in {"US", "HK"}:
            return False
        return data.timeframe == "30m"

    def compute(self, data: KLineData, **params) -> pd.Series:
        min_bars = int(params.get("min_bars", 100))
        lookback = int(params.get("lookback", 160))
        short_period = int(params.get("short_period", 25))
        long_period = int(params.get("long_period", 90))
        soft_score = bool(params.get("soft_score", True))

        df = self._prepare_df(data.df)
        scores = pd.Series(np.nan, index=data.df.index, dtype=float, name="XMM_30m_score")
        if len(df) < min_bars:
            return scores

        strategy = XMMStrategy(short_period=short_period, long_period=long_period)
        for end in range(min_bars - 1, len(df)):
            start = max(0, end + 1 - lookback)
            window = df.iloc[start:end + 1]
            if window.isna().any().any():
                continue
            result = strategy.analyze(window)
            scores.iloc[end] = self._score_result(result, soft_score=soft_score)

        return scores.clip(-100, 100)

    @staticmethod
    def _prepare_df(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        if "date" in out.columns:
            out = out.set_index(pd.to_datetime(out["date"]))
        cols = ["open", "high", "low", "close", "volume"]
        return out[cols].astype(float)

    @classmethod
    def _score_result(cls, result: Dict[str, Any], soft_score: bool) -> float:
        signal = result.get("signal", "HOLD")
        direction = 1.0 if signal == "BUY" else -1.0 if signal == "SELL" else 0.0
        position = float(result.get("position_size", 0.0) or 0.0)
        hard_score = direction * position * 100.0
        if hard_score != 0.0 or not soft_score:
            return hard_score

        trend = result.get("trend_layer", {})
        structure = result.get("structure_layer", {})
        td = result.get("td_layer", {})

        score = cls._trend_score(trend)
        score += cls._structure_score(structure)
        score += cls._td_score(td)
        return float(score)

    @staticmethod
    def _trend_score(trend: Dict[str, Any]) -> float:
        market = trend.get("market")
        if market == "UP":
            return 20.0
        if market == "DOWN":
            return -20.0
        return 0.0

    @staticmethod
    def _structure_score(structure: Dict[str, Any]) -> float:
        score = 0.0
        if structure.get("底部结构"):
            score += 30.0
        if structure.get("顶部结构"):
            score -= 30.0
        if structure.get("底部钝化"):
            score += 15.0
        if structure.get("顶部钝化"):
            score -= 15.0
        return score

    @staticmethod
    def _td_score(td: Dict[str, Any]) -> float:
        td_count = int(td.get("td_count", 0) or 0)
        if td_count == 0:
            return 0.0
        sign = 1.0 if td_count > 0 else -1.0
        magnitude = min(abs(td_count), 9) / 9.0
        return sign * 15.0 * magnitude
