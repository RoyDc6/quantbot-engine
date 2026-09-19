# -*- coding: utf-8 -*-
"""Independent Chan Center (ZS) factor.

Pure OHLC structure factor derived from confirmed Chan-style pens and fixed
three-pen overlap centers.  It deliberately excludes RSI, MACD and volume so
that its information remains separate from XMM and Volume Profile.

Only confirmed pivots may create centers.  This module has no dependency on
FusionController and is evaluated only when callers invoke it directly.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from fusion_framework.signal_types import DecisionSignal


MIN_MERGED_CENTER_GAP = 3
MIN_RAW_ENDPOINT_GAP = 4


class ChanCenterFactor:
    """Independent ZS structure factor producing a standard DecisionSignal."""

    name = "ZS"
    minimum_bars = 50

    def analyze(self, df: pd.DataFrame, ticker: str = "") -> DecisionSignal:
        _validate_ohlc(df)
        date = _last_date(df)

        if len(df) < self.minimum_bars:
            return _empty_signal(ticker, date, "数据不足", confidence=0.0)

        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)

        merged_h, merged_l, high_pos, low_pos = _merge_inclusion(high, low)
        candidate_k, candidate_x, candidate_type, candidate_price = _find_fractals(
            merged_h, merged_l, high_pos, low_pos
        )
        pivot_x, pivot_type, pivot_price = _build_pivots(
            candidate_k, candidate_x, candidate_type, candidate_price
        )

        # The latest pivot is provisional until a later opposite pivot locks it.
        confirmed_count = max(0, len(pivot_x) - 1)
        centers = _find_centers(
            pivot_x, pivot_type, pivot_price, confirmed_count, len(df)
        )
        if not centers:
            return _empty_signal(ticker, date, "尚未形成确认中枢", confidence=0.20)

        atr_value = float(_atr(high, low, close, 14)[-1])
        score = _score_latest_center(close, atr_value, centers)
        details = score["details"]

        return DecisionSignal(
            ticker=ticker,
            direction=score["direction"],
            confidence=score["confidence"],
            date=date,
            factor_score=score["factor_score"],
            factor_details=details,
            fusion_score=0.0,
            signal_level=score["direction"],
            source="ZS",
        )


def _validate_ohlc(df: pd.DataFrame) -> None:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    missing = [name for name in ("high", "low", "close") if name not in df.columns]
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    if len(df) == 0:
        raise ValueError("empty OHLC data")


def _last_date(df: pd.DataFrame) -> str:
    value = df["date"].iloc[-1] if "date" in df.columns else df.index[-1]
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _empty_signal(ticker: str, date: str, reason: str,
                  confidence: float) -> DecisionSignal:
    return DecisionSignal(
        ticker=ticker,
        direction="HOLD",
        confidence=confidence,
        date=date,
        factor_score=0.0,
        factor_details={
            "phase": "no_center",
            "event": "NONE",
            "reason": reason,
            "active": False,
        },
        fusion_score=0.0,
        signal_level="HOLD",
        source="ZS",
    )


def _initial_direction(high: Sequence[float], low: Sequence[float]) -> int:
    for i in range(1, len(high)):
        included = (
            (high[i] >= high[i - 1] and low[i] <= low[i - 1])
            or (high[i] <= high[i - 1] and low[i] >= low[i - 1])
        )
        if not included:
            return 1 if high[i] > high[i - 1] and low[i] > low[i - 1] else -1
    return 1


def _merge_inclusion(high: Sequence[float], low: Sequence[float]) -> Tuple[List, List, List, List]:
    direction = _initial_direction(high, low)
    merged_h: List[float] = []
    merged_l: List[float] = []
    high_pos: List[int] = []
    low_pos: List[int] = []

    for i in range(len(high)):
        if not merged_h:
            merged_h.append(float(high[i]))
            merged_l.append(float(low[i]))
            high_pos.append(i)
            low_pos.append(i)
            continue

        last = len(merged_h) - 1
        ph, pl = merged_h[last], merged_l[last]
        included = (
            (high[i] >= ph and low[i] <= pl)
            or (high[i] <= ph and low[i] >= pl)
        )
        if included:
            if direction > 0:
                if high[i] >= ph:
                    merged_h[last] = float(high[i])
                    high_pos[last] = i
                if low[i] >= pl:
                    merged_l[last] = float(low[i])
                    low_pos[last] = i
            else:
                if high[i] <= ph:
                    merged_h[last] = float(high[i])
                    high_pos[last] = i
                if low[i] <= pl:
                    merged_l[last] = float(low[i])
                    low_pos[last] = i
            continue

        direction = 1 if high[i] > ph and low[i] > pl else -1
        merged_h.append(float(high[i]))
        merged_l.append(float(low[i]))
        high_pos.append(i)
        low_pos.append(i)

    return merged_h, merged_l, high_pos, low_pos


def _find_fractals(merged_h: Sequence[float], merged_l: Sequence[float],
                   high_pos: Sequence[int], low_pos: Sequence[int]) -> Tuple[List, List, List, List]:
    candidate_k: List[int] = []
    candidate_x: List[int] = []
    candidate_type: List[int] = []
    candidate_price: List[float] = []

    for i in range(1, len(merged_h) - 1):
        is_top = (
            merged_h[i] > merged_h[i - 1]
            and merged_h[i] > merged_h[i + 1]
            and merged_l[i] > merged_l[i - 1]
            and merged_l[i] > merged_l[i + 1]
        )
        is_bottom = (
            merged_l[i] < merged_l[i - 1]
            and merged_l[i] < merged_l[i + 1]
            and merged_h[i] < merged_h[i - 1]
            and merged_h[i] < merged_h[i + 1]
        )
        if is_top:
            candidate_k.append(i)
            candidate_x.append(int(high_pos[i]))
            candidate_type.append(1)
            candidate_price.append(float(merged_h[i]))
        elif is_bottom:
            candidate_k.append(i)
            candidate_x.append(int(low_pos[i]))
            candidate_type.append(-1)
            candidate_price.append(float(merged_l[i]))

    return candidate_k, candidate_x, candidate_type, candidate_price


def _build_pivots(candidate_k: Sequence[int], candidate_x: Sequence[int],
                  candidate_type: Sequence[int], candidate_price: Sequence[float]) -> Tuple[List, List, List]:
    pivot_k: List[int] = []
    pivot_x: List[int] = []
    pivot_type: List[int] = []
    pivot_price: List[float] = []

    for ck, cx, ct, cp in zip(candidate_k, candidate_x, candidate_type, candidate_price):
        if not pivot_k:
            pivot_k.append(int(ck)); pivot_x.append(int(cx))
            pivot_type.append(int(ct)); pivot_price.append(float(cp))
            continue

        last = len(pivot_k) - 1
        if ct == pivot_type[last]:
            more_extreme = (ct == 1 and cp >= pivot_price[last]) or (ct == -1 and cp <= pivot_price[last])
            if more_extreme:
                pivot_k[last] = int(ck)
                pivot_x[last] = int(cx)
                pivot_price[last] = float(cp)
            continue

        far_enough = (
            ck - pivot_k[last] >= MIN_MERGED_CENTER_GAP
            and cx - pivot_x[last] >= MIN_RAW_ENDPOINT_GAP
        )
        correct_price = (
            (pivot_type[last] == -1 and cp > pivot_price[last])
            or (pivot_type[last] == 1 and cp < pivot_price[last])
        )
        if far_enough and correct_price:
            pivot_k.append(int(ck)); pivot_x.append(int(cx))
            pivot_type.append(int(ct)); pivot_price.append(float(cp))

    return pivot_x, pivot_type, pivot_price


def _find_centers(pivot_x: Sequence[int], pivot_type: Sequence[int],
                  pivot_price: Sequence[float], pivot_count: int,
                  data_len: int) -> List[Dict]:
    centers: List[Dict] = []
    i = 0
    while i + 3 < pivot_count:
        highs = [max(pivot_price[j], pivot_price[j + 1]) for j in range(i, i + 3)]
        lows = [min(pivot_price[j], pivot_price[j + 1]) for j in range(i, i + 3)]
        zg = float(min(highs))
        zd = float(max(lows))
        if zg <= zd:
            i += 1
            continue

        end_pivot = i + 3
        scan = i + 3
        active = True
        while scan + 1 < pivot_count:
            segment_high = max(pivot_price[scan], pivot_price[scan + 1])
            segment_low = min(pivot_price[scan], pivot_price[scan + 1])
            if segment_high >= zd and segment_low <= zg:
                end_pivot = scan + 1
                scan += 1
            else:
                active = False
                break

        overlap = False
        if centers:
            previous = centers[-1]
            overlap = min(previous["zg"], zg) > max(previous["zd"], zd)

        centers.append({
            "start": int(pivot_x[i]),
            "formation_end": int(pivot_x[i + 3]),
            "end": int(data_len - 1 if active else pivot_x[end_pivot]),
            "zg": zg,
            "zd": zd,
            "entry_direction": 1 if pivot_type[i] == -1 else -1,
            "active": bool(active),
            "overlap": bool(overlap),
            "extension_segments": max(0, int(end_pivot - (i + 3))),
        })

        if active:
            break
        i = end_pivot

    return centers


def _score_latest_center(close: np.ndarray, atr_value: float,
                         centers: Sequence[Dict]) -> Dict:
    center = dict(centers[-1])
    previous = centers[-2] if len(centers) > 1 else None
    current = float(close[-1])
    zg, zd = float(center["zg"]), float(center["zd"])
    mid = (zg + zd) / 2.0
    half = max((zg - zd) / 2.0, 1e-9)
    atr = max(float(atr_value), abs(current) * 1e-4, 1e-9)

    position_score = 100.0 * math.tanh((current - mid) / (half + 0.25 * atr))
    if current > zg:
        event = "UP_BREAK"
        hold_bars = _consecutive_outside(close, zg, above=True)
        distance = (current - zg) / atr
        breakout_score = 100.0 * math.tanh(distance) * (0.4 + 0.6 * min(hold_bars / 2.0, 1.0))
    elif current < zd:
        event = "DOWN_BREAK"
        hold_bars = _consecutive_outside(close, zd, above=False)
        distance = (zd - current) / atr
        breakout_score = -100.0 * math.tanh(distance) * (0.4 + 0.6 * min(hold_bars / 2.0, 1.0))
    else:
        event = "INSIDE"
        hold_bars = 0
        breakout_score = 0.0

    if previous:
        previous_mid = (float(previous["zg"]) + float(previous["zd"])) / 2.0
        shift_score = 100.0 * math.tanh((mid - previous_mid) / atr)
    else:
        previous_mid = None
        shift_score = 0.0

    entry_score = 100.0 * int(center["entry_direction"])
    raw_score = (
        0.25 * position_score
        + 0.40 * breakout_score
        + 0.25 * shift_score
        + 0.10 * entry_score
    )
    if event == "INSIDE":
        raw_score = max(-25.0, min(25.0, raw_score))
    elif hold_bars < 2:
        raw_score = max(-40.0, min(40.0, raw_score))

    bars_since_exit = 0 if center["active"] else max(0, len(close) - 1 - int(center["end"]))
    decay = 1.0 if center["active"] else math.exp(-bars_since_exit / 10.0)
    factor_score = round(max(-100.0, min(100.0, raw_score * decay)), 1)

    confidence = 0.55 + min(int(center["extension_segments"]), 3) * 0.10
    if center["active"]:
        confidence = min(confidence, 0.70)
    else:
        confidence = min(confidence + 0.15, 1.0)
    if hold_bars >= 2:
        confidence = min(confidence + 0.10, 1.0)
    confidence = round(max(0.0, min(1.0, confidence * max(decay, 0.25))), 3)

    direction = "BUY" if factor_score >= 25 else "SELL" if factor_score <= -25 else "HOLD"
    phase = "extending" if center["active"] and center["extension_segments"] > 0 else "forming" if center["active"] else "exited"
    reason = _build_reason(direction, phase, event, hold_bars, factor_score)

    details = {
        "phase": phase,
        "event": event,
        "zg": round(zg, 6),
        "zd": round(zd, 6),
        "mid": round(mid, 6),
        "width_atr": round((zg - zd) / atr, 4),
        "position_score": round(position_score, 2),
        "breakout_score": round(breakout_score, 2),
        "shift_score": round(shift_score, 2),
        "entry_score": round(entry_score, 2),
        "entry_direction": "UP" if center["entry_direction"] > 0 else "DOWN",
        "hold_bars": int(hold_bars),
        "age_bars": int(len(close) - int(center["start"])),
        "bars_since_exit": int(bars_since_exit),
        "extension_segments": int(center["extension_segments"]),
        "overlap": bool(center["overlap"]),
        "active": bool(center["active"]),
        "previous_mid": round(previous_mid, 6) if previous_mid is not None else None,
        "decay": round(decay, 4),
        "reason": reason,
    }
    return {
        "direction": direction,
        "confidence": confidence,
        "factor_score": factor_score,
        "details": details,
    }


def analyze_chan_center(df: pd.DataFrame, ticker: str = "") -> DecisionSignal:
    """Convenience entry point for one-off independent ZS evaluation."""
    return ChanCenterFactor().analyze(df, ticker=ticker)


def _consecutive_outside(close: np.ndarray, boundary: float, above: bool) -> int:
    count = 0
    for value in close[::-1]:
        condition = value > boundary if above else value < boundary
        if not condition:
            break
        count += 1
    return count


def _build_reason(direction: str, phase: str, event: str,
                  hold_bars: int, score: float) -> str:
    phase_map = {"forming": "中枢形成", "extending": "中枢延伸", "exited": "中枢已离开"}
    if event == "UP_BREAK":
        structure = f"向上离开并站稳{hold_bars}根K线"
    elif event == "DOWN_BREAK":
        structure = f"向下离开并站稳{hold_bars}根K线"
    else:
        structure = "价格仍在中枢内部"
    action_map = {"BUY": "偏多", "SELL": "偏空", "HOLD": "观望"}
    return f"{phase_map.get(phase, phase)}；{structure}；ZS {action_map[direction]}({score:+.1f})"


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         period: int) -> np.ndarray:
    true_range = np.empty(len(close), dtype=float)
    true_range[0] = high[0] - low[0]
    for i in range(1, len(close)):
        true_range[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    result = np.empty(len(close), dtype=float)
    running = 0.0
    for i, value in enumerate(true_range):
        if i == 0:
            running = value
        else:
            running = (running * (period - 1) + value) / period
        result[i] = running
    return result
