# -*- coding: utf-8 -*-
"""
TD序列模块 (Tom DeMark Sequential)
计算TD9计数，用于辅助判断反转点
"""
import pandas as pd
import numpy as np


def calc_td9(close: pd.Series, period: int = 4) -> pd.Series:
    """
    计算TD9序列

    TD买入计数: 连续N根K线收盘价 < N根前收盘价
    TD卖出计数: 连续N根K线收盘价 > N根前收盘价

    Args:
        close: 收盘价序列
        period: 比较周期（默认4，即比较当前收盘价与4天前收盘价）

    Returns:
        pd.Series: 正数=买入计数，负数=卖出计数，0=无计数
    """
    n = len(close)
    count = pd.Series(0, index=close.index, dtype=float)
    direction = None  # 'buy' or 'sell' or None

    for i in range(period, n):
        curr = close.iloc[i]
        prev = close.iloc[i - period]

        if curr > prev:
            if direction == 'buy':
                count.iloc[i] = 0
                direction = 'sell'
            elif direction == 'sell':
                count.iloc[i] = count.iloc[i - 1] + 1
            else:
                direction = 'sell'
                count.iloc[i] = 1
        elif curr < prev:
            if direction == 'sell':
                count.iloc[i] = 0
                direction = 'buy'
            elif direction == 'buy':
                count.iloc[i] = count.iloc[i - 1] + 1
            else:
                direction = 'buy'
                count.iloc[i] = 1
        else:
            # 相等则计数中断
            count.iloc[i] = 0
            direction = None

    return count


def calc_td_seq(close: pd.Series, period: int = 4) -> pd.DataFrame:
    """
    计算TD序列详细数据

    Args:
        close: 收盘价序列
        period: 比较周期（默认4，即比较当前收盘价与N天前收盘价）

    Returns:
        DataFrame 含:
        - td_count: 计数（正=买入，负=卖出）
        - td_phase: 当前相位 'buy' / 'sell' / 'none'
        - td_near: 是否接近9（7,8,9任一）
        - td_reached: 是否达到9
    """
    td_count = calc_td9(close, period=period)

    result = pd.DataFrame(index=close.index)
    result['td_count'] = td_count
    result['td_near'] = abs(td_count) >= 7
    result['td_reached'] = abs(td_count) >= 9

    return result


def get_td_state(td_df: pd.DataFrame) -> dict:
    """
    返回最新TD序列状态
    """
    i = -1
    return {
        'td_count': int(td_df['td_count'].iloc[i]),
        'td_near': bool(td_df['td_near'].iloc[i]),
        'td_reached': bool(td_df['td_reached'].iloc[i]),
        'is_buy_seq': td_df['td_count'].iloc[i] > 0,
        'is_sell_seq': td_df['td_count'].iloc[i] < 0,
    }
