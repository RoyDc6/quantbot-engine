"""Self-contained internal calculations for Northstar-D1.

The model does not import any production strategy or controller package.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd


def calc_dual_trend(
    df: pd.DataFrame,
    short_period: int = 25,
    long_period: int = 90,
) -> pd.DataFrame:
    """Calculate the copied EMA(high/low) trend bands and one-bar crosses."""

    high = df["high"]
    low = df["low"]
    close = df["close"]

    short_top = high.ewm(span=short_period, adjust=False, min_periods=5).mean()
    short_bottom = low.ewm(span=short_period, adjust=False, min_periods=5).mean()
    long_top = high.ewm(span=long_period, adjust=False, min_periods=10).mean()
    long_bottom = low.ewm(span=long_period, adjust=False, min_periods=10).mean()

    result = df.copy()
    result["short_top"] = short_top
    result["short_bottom"] = short_bottom
    result["long_top"] = long_top
    result["long_bottom"] = long_bottom
    result["cross_short_up"] = (close.shift(1) < short_top.shift(1)) & (close > short_top)
    result["cross_short_down"] = (close.shift(1) > short_bottom.shift(1)) & (close < short_bottom)
    result["cross_long_up"] = (close.shift(1) < long_top.shift(1)) & (close > long_top)
    result["cross_long_down"] = (close.shift(1) > long_bottom.shift(1)) & (close < long_bottom)
    return result


def get_trend_state(trend: pd.DataFrame) -> Dict:
    """Return the latest trend state and event fields."""

    last = trend.iloc[-1]
    close = float(last["close"])
    short_top = float(last["short_top"])
    short_bottom = float(last["short_bottom"])
    long_top = float(last["long_top"])
    long_bottom = float(last["long_bottom"])

    if close > short_top and close > long_top:
        market = "UP"
    elif close < short_bottom and close < long_bottom:
        market = "DOWN"
    else:
        market = "SIDEWAYS"

    return {
        "market": market,
        "close": close,
        "short_top": short_top,
        "short_bottom": short_bottom,
        "long_top": long_top,
        "long_bottom": long_bottom,
        "cross_short_up": bool(last["cross_short_up"]),
        "cross_short_down": bool(last["cross_short_down"]),
        "cross_long_up": bool(last["cross_long_up"]),
        "cross_long_down": bool(last["cross_long_down"]),
    }


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate DIFF, DEA and the doubled MACD histogram."""

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    diff = ema_fast - ema_slow
    dea = diff.ewm(span=signal, adjust=False).mean()
    macd = (diff - dea) * 2
    return diff, dea, macd


def calc_structure_layer(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    threshold: float = 1.01,
) -> pd.DataFrame:
    """Calculate the model's internal refinement layer."""

    close = df["close"]
    high = df["high"]
    low = df["low"]
    diff, dea, macd = calc_macd(close, fast, slow, signal)
    n = len(df)

    result = pd.DataFrame(index=df.index)
    result["close"] = close
    result["high"] = high
    result["low"] = low
    result["diff"] = diff
    result["dea"] = dea
    result["macd"] = macd

    n1_series = pd.Series(-1, index=df.index, dtype=float)
    m1_series = pd.Series(-1, index=df.index, dtype=float)
    cl1 = pd.Series(np.nan, index=df.index)
    cl2 = pd.Series(np.nan, index=df.index)
    cl3 = pd.Series(np.nan, index=df.index)
    ch1 = pd.Series(np.nan, index=df.index)
    ch2 = pd.Series(np.nan, index=df.index)
    ch3 = pd.Series(np.nan, index=df.index)
    difl1 = pd.Series(np.nan, index=df.index)
    difl2 = pd.Series(np.nan, index=df.index)
    difl3 = pd.Series(np.nan, index=df.index)
    difh1 = pd.Series(np.nan, index=df.index)
    difh2 = pd.Series(np.nan, index=df.index)
    difh3 = pd.Series(np.nan, index=df.index)

    for i in range(1, n):
        if macd.iloc[i] < 0:
            n1 = 0
            for j in range(i - 1, -1, -1):
                if macd.iloc[j] >= 0:
                    break
                n1 += 1
            n1_series.iloc[i] = n1
            width = int(n1) + 1
            start = max(0, i - width + 1)
            cl1.iloc[i] = close.iloc[start : i + 1].min()
            difl1.iloc[i] = diff.iloc[start : i + 1].min()

            positive_bar = next(
                (j for j in range(i - 1, -1, -1) if macd.iloc[j] > 0),
                -1,
            )
            if positive_bar >= 0:
                width2 = positive_bar + 1
                start2 = max(0, positive_bar - width2 + 1)
                cl2.iloc[i] = close.iloc[start2 : positive_bar + 1].min()
                difl2.iloc[i] = diff.iloc[start2 : positive_bar + 1].min()

                previous_positive = next(
                    (
                        j
                        for j in range(positive_bar - 1, -1, -1)
                        if macd.iloc[j] > 0
                    ),
                    -1,
                )
                if previous_positive >= 0:
                    width3 = positive_bar - previous_positive + 1
                    start3 = max(0, previous_positive - width3 + 1)
                    cl3.iloc[i] = close.iloc[start3 : previous_positive + 1].min()
                    difl3.iloc[i] = diff.iloc[start3 : previous_positive + 1].min()

        if macd.iloc[i] > 0:
            m1 = 0
            for j in range(i - 1, -1, -1):
                if macd.iloc[j] <= 0:
                    break
                m1 += 1
            m1_series.iloc[i] = m1
            width = int(m1) + 1
            start = max(0, i - width + 1)
            ch1.iloc[i] = high.iloc[start : i + 1].max()
            difh1.iloc[i] = diff.iloc[start : i + 1].max()

            negative_bar = next(
                (j for j in range(i - 1, -1, -1) if macd.iloc[j] < 0),
                -1,
            )
            if negative_bar >= 0:
                width2 = negative_bar + 1
                start2 = max(0, negative_bar - width2 + 1)
                ch2.iloc[i] = high.iloc[start2 : negative_bar + 1].max()
                difh2.iloc[i] = diff.iloc[start2 : negative_bar + 1].max()

                previous_negative = next(
                    (
                        j
                        for j in range(negative_bar - 1, -1, -1)
                        if macd.iloc[j] < 0
                    ),
                    -1,
                )
                if previous_negative >= 0:
                    width3 = negative_bar - previous_negative + 1
                    start3 = max(0, previous_negative - width3 + 1)
                    ch3.iloc[i] = high.iloc[start3 : previous_negative + 1].max()
                    difh3.iloc[i] = diff.iloc[start3 : previous_negative + 1].max()

    result["N1"] = n1_series
    result["M1"] = m1_series
    result["CL1"] = cl1
    result["CL2"] = cl2
    result["CL3"] = cl3
    result["CH1"] = ch1
    result["CH2"] = ch2
    result["CH3"] = ch3
    result["DIFL1"] = difl1
    result["DIFL2"] = difl2
    result["DIFL3"] = difl3
    result["DIFH1"] = difh1
    result["DIFH2"] = difh2
    result["DIFH3"] = difh3

    ref_macd_1 = macd.shift(1)
    ref_diff_1 = diff.shift(1)

    direct_bottom_plateau = (
        (cl1 < cl2) & (difl1 > difl2) & (ref_macd_1 < 0) & (diff < 0)
    )
    separated_bottom_plateau = (
        (cl1 < cl3)
        & (difl1 < difl2)
        & (difl1 > difl3)
        & (ref_macd_1 < 0)
        & (diff < 0)
    )
    bottom_plateau = (direct_bottom_plateau | separated_bottom_plateau) & (diff < 0)
    bottom_plateau_new = _first_true_in_run(bottom_plateau)
    bottom_plateau_disappeared = (
        (
            direct_bottom_plateau.shift(1).fillna(False)
            & (difl1 <= difl2)
            & (diff < dea)
        )
        | (
            separated_bottom_plateau.shift(1).fillna(False)
            & (difl1 <= difl3)
            & (diff < dea)
        )
    )
    bottom_structure = bottom_plateau.shift(1).fillna(False) & (
        abs(ref_diff_1) >= abs(diff) * threshold
    )
    bottom_structure_new = _first_true_in_run(bottom_structure)

    direct_top_plateau = (
        (ch1 > ch2) & (difh1 < difh2) & (ref_macd_1 > 0) & (diff > 0)
    )
    separated_top_plateau = (
        (ch1 > ch3)
        & (difh1 > difh2)
        & (difh1 < difh3)
        & (ref_macd_1 > 0)
        & (diff > 0)
    )
    top_plateau = (direct_top_plateau | separated_top_plateau) & (diff > 0)
    top_plateau_new = _first_true_in_run(top_plateau)
    top_plateau_disappeared = (
        (
            direct_top_plateau.shift(1).fillna(False)
            & (difh1 >= difh2)
            & (diff > dea)
        )
        | (
            separated_top_plateau.shift(1).fillna(False)
            & (difh1 >= difh3)
            & (diff > dea)
        )
    )
    top_structure = top_plateau.shift(1).fillna(False) & (
        ref_diff_1 >= diff * threshold
    )
    top_structure_new = _first_true_in_run(top_structure)

    result["direct_bottom_plateau"] = direct_bottom_plateau
    result["separated_bottom_plateau"] = separated_bottom_plateau
    result["bottom_plateau"] = bottom_plateau
    result["bottom_plateau_new"] = bottom_plateau_new
    result["bottom_plateau_disappeared"] = bottom_plateau_disappeared
    result["bottom_structure"] = bottom_structure
    result["bottom_structure_new"] = bottom_structure_new
    result["direct_top_plateau"] = direct_top_plateau
    result["separated_top_plateau"] = separated_top_plateau
    result["top_plateau"] = top_plateau
    result["top_plateau_new"] = top_plateau_new
    result["top_plateau_disappeared"] = top_plateau_disappeared
    result["top_structure"] = top_structure
    result["top_structure_new"] = top_structure_new
    return result


def _first_true_in_run(values: pd.Series) -> pd.Series:
    result = pd.Series(False, index=values.index)
    previous = False
    for i in range(1, len(values)):
        current = bool(values.iloc[i])
        if current and not previous:
            result.iloc[i] = True
        previous = current
    return result


def _trailing_true_count(values: pd.Series) -> int:
    count = 0
    for i in range(len(values) - 1, -1, -1):
        if bool(values.iloc[i]):
            count += 1
        else:
            break
    return count


def get_structure_state(result: pd.DataFrame) -> Dict:
    """Return the latest copied structure state."""

    last = result.iloc[-1]
    return {
        "bottom_plateau": bool(last["bottom_plateau"]),
        "bottom_plateau_new": bool(last["bottom_plateau_new"]),
        "bottom_plateau_disappeared": bool(
            last["bottom_plateau_disappeared"]
        ),
        "bottom_structure": bool(last["bottom_structure"]),
        "bottom_structure_new": bool(last["bottom_structure_new"]),
        "top_plateau": bool(last["top_plateau"]),
        "top_plateau_new": bool(last["top_plateau_new"]),
        "top_plateau_disappeared": bool(last["top_plateau_disappeared"]),
        "top_structure": bool(last["top_structure"]),
        "top_structure_new": bool(last["top_structure_new"]),
        "bottom_plateau_days": _trailing_true_count(result["bottom_plateau"]),
        "top_plateau_days": _trailing_true_count(result["top_plateau"]),
        "diff": float(last["diff"]),
        "dea": float(last["dea"]),
        "macd": float(last["macd"]),
    }


def calc_td_sequence(close: pd.Series, period: int = 4) -> pd.DataFrame:
    """Calculate TD setup counts with the documented sign convention.

    Positive counts are buy setups (close below ``period`` bars ago). Negative
    counts are sell setups (close above ``period`` bars ago).
    """

    counts = pd.Series(0, index=close.index, dtype=int)
    phase = "none"
    for i in range(period, len(close)):
        current = close.iloc[i]
        reference = close.iloc[i - period]
        if current < reference:
            counts.iloc[i] = counts.iloc[i - 1] + 1 if phase == "buy" else 1
            phase = "buy"
        elif current > reference:
            counts.iloc[i] = counts.iloc[i - 1] - 1 if phase == "sell" else -1
            phase = "sell"
        else:
            counts.iloc[i] = 0
            phase = "none"

    result = pd.DataFrame(index=close.index)
    result["td_count"] = counts
    result["td_near"] = counts.abs().between(7, 8)
    result["td_reached"] = counts.abs() >= 9
    return result


def get_td_state(result: pd.DataFrame) -> Dict:
    """Return the latest TD state."""

    count = int(result["td_count"].iloc[-1])
    return {
        "td_count": count,
        "td_near": bool(result["td_near"].iloc[-1]),
        "td_reached": bool(result["td_reached"].iloc[-1]),
        "is_buy_seq": count > 0,
        "is_sell_seq": count < 0,
    }
