# -*- coding: utf-8 -*-
"""
双趋势线判断模块（基于同花顺指标公式 v2.0）

趋势线:
  短顶 = EMA(H, 25)   短底 = EMA(L, 25)
  长顶 = EMA(H, 90)   长底 = EMA(L, 90)

交叉信号:
  短多: 今天突破短顶（上穿）  前一根 < 前一根短顶 AND 今天 > 今天短顶
  短空: 今天跌破短底（下破）  前一根 > 前一根短底 AND 今天 < 今天短底
  长多: 今天突破长顶（上穿）  前一根 < 前一根长顶 AND 今天 > 今天长顶
  长空: 今天跌破长底（下破）  前一根 > 前一根长底 AND 今天 < 今天长底

仓位分配（424规则）:
  突破短顶 → BUY 10%     跌破短底 → SELL 10%
  突破长顶（未突破短顶）→ BUY 40%   跌破长底（未跌破短底）→ SELL 40%
  双突破（短+长同向） → BUY/SELL 20%
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple


def calc_short_trend(df: pd.DataFrame, period: int = 25) -> pd.DataFrame:
    """
    计算短期趋势线（基于同花顺公式）
    
    短顶 = EMA(H, period)  高点均线（压力）
    短底 = EMA(L, period)  低点均线（支撑）
    
    短多 = 前一根 < 前一根短顶 AND 今天 > 今天短顶（上穿短顶）
    短空 = 前一根 > 前一根短底 AND 今天 < 今天短底（下破短底）
    """
    high = df['high']
    low = df['low']
    close = df['close']

    短顶 = high.ewm(span=period, adjust=False, min_periods=5).mean()
    短底 = low.ewm(span=period, adjust=False, min_periods=5).mean()

    # 前一根值
    短顶_1 = 短顶.shift(1)
    短底_1 = 短底.shift(1)
    close_1 = close.shift(1)

    # 上穿短顶（今天突破短顶）
    短多 = (close_1 < 短顶_1) & (close > 短顶)
    # 下破短底（今天跌破短底）
    短空 = (close_1 > 短底_1) & (close < 短底)

    return pd.DataFrame({
        '短顶': 短顶,
        '短底': 短底,
        '短多': 短多,
        '短空': 短空,
    })


def calc_long_trend(df: pd.DataFrame, period: int = 90) -> pd.DataFrame:
    """
    计算长期趋势线

    长顶 = EMA(H, period)  高点均线（压力）
    长底 = EMA(L, period)  低点均线（支撑）
    
    长多 = 前一根 < 前一根长顶 AND 今天 > 今天长顶（上穿长顶）
    长空 = 前一根 > 前一根长底 AND 今天 < 今天长底（下破长底）
    """
    high = df['high']
    low = df['low']
    close = df['close']

    长顶 = high.ewm(span=period, adjust=False, min_periods=10).mean()
    长底 = low.ewm(span=period, adjust=False, min_periods=10).mean()

    # 前一根值
    长顶_1 = 长顶.shift(1)
    长底_1 = 长底.shift(1)
    close_1 = close.shift(1)

    # 上穿长顶
    长多 = (close_1 < 长顶_1) & (close > 长顶)
    # 下破长底
    长空 = (close_1 > 长底_1) & (close < 长底)

    return pd.DataFrame({
        '长顶': 长顶,
        '长底': 长底,
        '长多': 长多,
        '长空': 长空,
    })


def calc_dual_trend(df: pd.DataFrame, short_period: int = 25, long_period: int = 90) -> pd.DataFrame:
    """
    计算双趋势线（短+长）

    Returns DataFrame with:
      短顶, 短底, 短多, 短空,
      长顶, 长底, 长多, 长空,
      cross_short_up, cross_short_down,   # 短顶突破/短底跌破
      cross_long_up, cross_long_down,       # 长顶突破/长底跌破
    """
    short = calc_short_trend(df, short_period)
    long = calc_long_trend(df, long_period)

    # ✅ 保留原始列（high/low/close/...）
    result = df.copy()
    result = pd.concat([result, short, long], axis=1)

    # 交叉信号（今天是否发生）
    result['cross_short_up'] = result['短多']   # 突破短顶
    result['cross_short_down'] = result['短空']  # 跌破短底
    result['cross_long_up'] = result['长多']    # 突破长顶
    result['cross_long_down'] = result['长空']  # 跌破长底

    return result


def get_trend_signals(df: pd.DataFrame,
                      short_period: int = 25,
                      long_period: int = 90) -> Tuple[pd.DataFrame, Dict]:
    """
    获取双趋势信号（向量版本 + 最新日标量）

    624仓位规则（BUY）:
      情况1: 短多+长多     → BUY  60%  (双突破，最强)
      情况2: 短多+非长多   → BUY  20%  (仅突破短顶)
      情况3: 非短多+长多   → BUY  40%  (仅突破长顶)

    仓位规则（SELL）:
      情况4: 短空+长空     → SELL 100%  (双跌破，全清仓)
      情况5: 短空+非长空   → SELL  20%  (仅跌破短底)
      情况6: 非短空+长空   → SELL  40%  (仅跌破长底)
    """
    trend = calc_dual_trend(df, short_period, long_period)

    close = df['close']

    # ========== 424信号矩阵 ==========
    # 向上交叉
    cond_buy_both = trend['cross_short_up'] & trend['cross_long_up']    # 情况1: 短多+长多
    cond_buy_short_only = trend['cross_short_up'] & ~trend['cross_long_up']  # 情况2: 仅短多
    cond_buy_long_only = ~trend['cross_short_up'] & trend['cross_long_up']   # 情况3: 仅长多

    # 向下交叉
    cond_sell_both = trend['cross_short_down'] & trend['cross_long_down']    # 情况4: 短空+长空
    cond_sell_short_only = trend['cross_short_down'] & ~trend['cross_long_down']  # 情况5: 仅短空
    cond_sell_long_only = ~trend['cross_short_down'] & trend['cross_long_down']   # 情况6: 仅长空

    trend['signal'] = 'HOLD'
    trend.loc[cond_buy_both, 'signal'] = 'BUY'
    trend.loc[cond_buy_short_only, 'signal'] = 'BUY'
    trend.loc[cond_buy_long_only, 'signal'] = 'BUY'      # 情况3: 长顶突破
    trend.loc[cond_sell_both, 'signal'] = 'SELL'
    trend.loc[cond_sell_short_only, 'signal'] = 'SELL'
    trend.loc[cond_sell_long_only, 'signal'] = 'SELL'     # 情况6: 长底跌破

    # 仓位
    trend['position_size'] = 0.0
    trend.loc[cond_buy_both, 'position_size'] = 0.60      # 情况1: 双突破 60%
    trend.loc[cond_buy_short_only, 'position_size'] = 0.20  # 情况2: 仅短多 20%
    trend.loc[cond_buy_long_only, 'position_size'] = 0.40   # 情况3: 仅长多 40%
    trend.loc[cond_sell_both, 'position_size'] = 1.00     # 情况4: 双跌破 100% 全清仓！
    trend.loc[cond_sell_short_only, 'position_size'] = 0.20  # 情况5: 仅短空 20%
    trend.loc[cond_sell_long_only, 'position_size'] = 0.40   # 情况6: 仅长空 40%

    # 信号描述
    trend['signal_type'] = 'none'
    trend.loc[cond_buy_both, 'signal_type'] = '双突破做多(60%)'
    trend.loc[cond_buy_short_only, 'signal_type'] = '突破短顶做多(20%)'
    trend.loc[cond_buy_long_only, 'signal_type'] = '突破长顶做多(40%)'
    trend.loc[cond_sell_both, 'signal_type'] = '双跌破做空(100%全清!)'
    trend.loc[cond_sell_short_only, 'signal_type'] = '跌破短底做空(20%)'
    trend.loc[cond_sell_long_only, 'signal_type'] = '跌破长底做空(40%)'

    # 市场趋势判断
    c = float(close.iloc[-1])
    st = float(trend['短顶'].iloc[-1])
    sb = float(trend['短底'].iloc[-1])
    lt = float(trend['长顶'].iloc[-1])
    lb = float(trend['长底'].iloc[-1])

    # 趋势定义：价格在均线对之间/之上/之下
    if c > st and c > lt:
        market_trend = 'UP'
    elif c < sb and c < lb:
        market_trend = 'DOWN'
    else:
        market_trend = 'SIDEWAYS'

    # 最新日信号
    latest = {col: trend[col].iloc[-1] for col in trend.columns}
    latest['close'] = c
    latest['short_top'] = st
    latest['short_bot'] = sb
    latest['long_top'] = lt
    latest['long_bot'] = lb
    latest['market_trend'] = market_trend

    return trend, latest
