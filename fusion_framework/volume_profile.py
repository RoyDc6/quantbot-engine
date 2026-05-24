# -*- coding: utf-8 -*-
"""
Volume Profile Box Strategy
替代缠论中枢，利用成交量分布刻画市场的"筹码箱体"（Value Area）。

核心机制:
  1. Volume Smearing — 每根K线成交量均匀平铺在 [low, high] 区间
  2. 双指针扩张 — 从POC向两侧扩张至70%成交量确定 value area
  3. 空间状态判定 — above_box / inside_box / below_box

奇点A (Breakout): 价格放量突破 VAH → 高置信度 BUY (conf=0.8)

数据驱动，无主观推理链。适合作为 DecisionSignal 信号源接入 FusionEngine。
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass, field
from .signal_types import DecisionSignal


@dataclass
class VPBoxRaw:
    """Volume Profile 原始计算结果"""
    val: float          # Value Area Low
    poc: float          # Point of Control
    vah: float          # Value Area High
    total_volume: float
    value_area_volume: float
    num_bins: int
    bin_range: tuple    # (min_price, max_price)


class VolumeProfileBoxStrategy:
    """
    Volume Profile 价值区策略模型

    参数:
        name: 策略名称（用于溯源）
        lookback: 滚动窗口长度（K线根数）
        value_area_pct: 价值区成交量占比（默认70%）
        bins: 价格区间切片数
    """

    def __init__(
        self,
        name: str = "volume_profile_box",
        lookback: int = 120,
        value_area_pct: float = 0.70,
        bins: int = 100,
    ):
        self.name = name
        self.lookback = lookback
        self.value_area_pct = value_area_pct
        self.bins = bins

    # ─── 核心算法 ────────────────────────────────────────

    def _calculate_vp_nodes(self, df: pd.DataFrame) -> VPBoxRaw:
        """
        基于 [low, high] 区间成交量均摊的 Volume Profile 计算。

        流程:
          1. 计算全局价格区间 → 生成价格网格
          2. 每根K线成交量均摊到覆盖的bins
          3. POC = 成交量最大的bin中心
          4. 双指针扩张至 value_area_pct 确定 VAH/VAL
        """
        window_df = df.tail(self.lookback)

        min_price = float(window_df['low'].min())
        max_price = float(window_df['high'].max())

        # 防呆：极端情况（一字板或停牌）
        if max_price <= min_price:
            max_price = min_price * 1.001

        # 价格网格
        bin_edges = np.linspace(min_price, max_price, self.bins + 1)
        hist = np.zeros(self.bins, dtype=np.float64)

        # ── Volume Smearing (vectorized) ──
        lows = window_df['low'].values.astype(np.float64)
        highs = window_df['high'].values.astype(np.float64)
        volumes = window_df['volume'].values.astype(np.float64)

        # 一次性计算所有行的 bin 索引
        start_idxs = np.clip(np.digitize(lows, bin_edges, right=False) - 1, 0, self.bins - 1)
        end_idxs = np.clip(np.digitize(highs, bin_edges, right=False) - 1, 0, self.bins - 1)
        num_bins_arr = end_idxs - start_idxs + 1

        # 只处理有效区间 (排除零宽度K线)
        mask = num_bins_arr > 0
        if mask.any():
            s_arr = start_idxs[mask]
            e_arr = end_idxs[mask]
            v_arr = volumes[mask] / num_bins_arr[mask]

            # 展开为 flat indices + flat values，用 np.add.at 累加
            bin_indices = np.concatenate([np.arange(s, e + 1) for s, e in zip(s_arr, e_arr)])
            vol_values = np.concatenate([np.full(e - s + 1, v) for s, e, v in zip(s_arr, e_arr, v_arr)])
            np.add.at(hist, bin_indices, vol_values)

        # ── POC ──
        total_volume = float(np.sum(hist))
        poc_idx = int(np.argmax(hist))
        poc_price = float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2.0)

        # ── Value Area 双指针扩张 ──
        target_volume = total_volume * self.value_area_pct
        lower_idx = poc_idx
        upper_idx = poc_idx
        current_volume = float(hist[poc_idx])

        while current_volume < target_volume:
            can_go_down = lower_idx > 0
            can_go_up = upper_idx < self.bins - 1

            if not can_go_down and not can_go_up:
                break

            if can_go_down and can_go_up:
                if hist[lower_idx - 1] > hist[upper_idx + 1]:
                    lower_idx -= 1
                    current_volume += float(hist[lower_idx])
                else:
                    upper_idx += 1
                    current_volume += float(hist[upper_idx])
            elif can_go_down:
                lower_idx -= 1
                current_volume += float(hist[lower_idx])
            elif can_go_up:
                upper_idx += 1
                current_volume += float(hist[upper_idx])

        val = float(bin_edges[lower_idx])
        vah = float(bin_edges[upper_idx + 1])

        return VPBoxRaw(
            val=val, poc=poc_price, vah=vah,
            total_volume=total_volume,
            value_area_volume=current_volume,
            num_bins=self.bins,
            bin_range=(min_price, max_price)
        )

    # ─── 信号生成 ────────────────────────────────────────

    def analyze(self, df: pd.DataFrame, ticker: str = "") -> DecisionSignal:
        """
        标准化分析接口：返回 DecisionSignal 供 FusionEngine 消费。

        Args:
            df: OHLCV DataFrame (含 open/high/low/close/volume)
            ticker: 标的代码，用于 DecisionSignal.ticker

        Returns:
            DecisionSignal 对象
        """
        date_str = ""
        if 'timestamp' in df.columns:
            date_str = str(df['timestamp'].iloc[-1])
        else:
            date_str = str(df.index[-1]) if hasattr(df.index, 'dtype') else ""

        # 数据校验
        if len(df) < self.lookback:
            return DecisionSignal(
                ticker=ticker,
                direction="HOLD",
                confidence=0.0,
                date=date_str,
                signal_level="HOLD",
                source=self.name,
                warnings=[f"insufficient data: {len(df)} < {self.lookback}"]
            )

        # ── 计算 VP ──
        raw = self._calculate_vp_nodes(df)
        current_price = float(df['close'].iloc[-1])

        # ── 状态判定 ──
        if current_price > raw.vah:
            state = "above_box"
            direction = "BUY"
            confidence = 0.8
            signal_level = "VP_BUY"

        elif current_price < raw.val:
            state = "below_box"
            direction = "SELL"
            confidence = 0.8
            signal_level = "VP_SELL"

        else:
            state = "inside_box"
            direction = "BUY" if current_price >= raw.poc else "SELL"
            confidence = 0.2  # 震荡区低置信度
            signal_level = "HOLD"

        # ── 构建 factor_details ──
        factor_details = {
            "VAL": round(raw.val, 2),
            "POC": round(raw.poc, 2),
            "VAH": round(raw.vah, 2),
            "current_price": round(current_price, 2),
            "state": state,
            "va_width": round(raw.vah - raw.val, 2),
            "total_volume": f"{raw.total_volume:.0f}",
            "value_area_vol_pct": f"{raw.value_area_volume / raw.total_volume * 100:.1f}%",
            "lookback": self.lookback,
            "bins": self.bins,
            "bin_range": f"[{raw.bin_range[0]:.2f}, {raw.bin_range[1]:.2f}]",
        }

        return DecisionSignal(
            ticker=ticker,
            direction=direction,
            confidence=confidence,
            date=date_str,
            factor_score=0.0,
            factor_details=factor_details,
            llm_factor_score=0.0,
            event_sentiment_score=0.0,
            event_type="none",
            fusion_score=0.0,
            signal_level=signal_level,
            source=self.name,
            warnings=[],
        )


# ─── 快捷入口 ─────────────────────────────────────────

def quick_vp_signal(df: pd.DataFrame, ticker: str = "",
                    lookback: int = 120) -> DecisionSignal:
    """
    快速调用 Volume Profile 分析的单行入口。

    Usage:
        signal = quick_vp_signal(df, ticker="09988.HK")
        print(signal.direction, signal.confidence, signal.signal_level)
    """
    strategy = VolumeProfileBoxStrategy(lookback=lookback)
    return strategy.analyze(df, ticker=ticker)