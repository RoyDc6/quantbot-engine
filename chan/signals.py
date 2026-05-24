# -*- coding: utf-8 -*-
"""
signals.py - 缠论买卖点信号
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple
from enum import Enum


class SignalType(Enum):
    """信号类型"""
    BUY_1 = "buy1"     # 一买：趋势底背驰
    SELL_1 = "sell1"   # 一卖：趋势顶背驰
    BUY_2 = "buy2"     # 二买：回踩不破一买
    SELL_2 = "sell2"   # 二卖：反弹不破一卖
    BUY_3 = "buy3"     # 三买：突破中枢后回踩
    SELL_3 = "sell3"   # 三卖：跌破中枢后反弹


@dataclass
class ChanSignal:
    """缠论信号"""
    date: str           # 信号日期
    signal_type: str    # 信号类型
    price: float        # 信号价格
    strength: int       # 信号强度 1-5
    reason: str        # 信号原因
    
    def to_dict(self) -> dict:
        return {
            'date': self.date,
            'signal_type': self.signal_type,
            'price': self.price,
            'strength': self.strength,
            'reason': self.reason,
        }


def compute_macd_divergence(prices: pd.Series, window: int = 12) -> Tuple[bool, bool]:
    """
    简化版MACD背驰判断
    
    返回:
        (top_divergence, bottom_divergence)
    """
    if len(prices) < window + 26:
        return False, False
    
    # 简化：比较最近两段趋势的价格高低与动能
    recent = prices.tail(window)
    earlier = prices.tail(window * 2).head(window)
    
    # 底背驰：价格新低但跌势减缓
    bottom_div = (recent.min() < earlier.min()) and \
                 (recent.pct_change().std() < earlier.pct_change().std())
    
    # 顶背驰：价格新高但涨势减缓
    top_div = (recent.max() > earlier.max()) and \
              (recent.pct_change().std() < earlier.pct_change().std())
    
    return top_div, bottom_div


def find_chan_signals(
    merged_df: pd.DataFrame,
    strokes: List,
    segments: List,
    pivots: List,
) -> List[ChanSignal]:
    """
    识别缠论买卖点信号
    
    参数:
        merged_df: 合并后的K线数据
        strokes: 笔列表
        segments: 线段列表
        pivots: 中枢列表
    
    返回:
        信号列表
    """
    signals = []
    
    if len(merged_df) < 30 or len(strokes) < 5:
        return signals
    
    # 获取最新数据
    latest_price = float(merged_df['close'].iloc[-1])
    latest_date = str(merged_df['date'].iloc[-1])
    
    # 检查背驰
    close = merged_df['close'].astype(float)
    top_div, bottom_div = compute_macd_divergence(close)
    
    # 一类买卖点（趋势背驰）
    if bottom_div and len(strokes) >= 2:
        last_stroke = strokes[-1]
        if last_stroke.direction == -1:  # 最后一笔向下
            signals.append(ChanSignal(
                date=latest_date,
                signal_type='buy1',
                price=latest_price,
                strength=4 if len(strokes) >= 10 else 3,
                reason='底背驰+下跌笔结束',
            ))
    
    if top_div and len(strokes) >= 2:
        last_stroke = strokes[-1]
        if last_stroke.direction == 1:  # 最后一笔向上
            signals.append(ChanSignal(
                date=latest_date,
                signal_type='sell1',
                price=latest_price,
                strength=4 if len(strokes) >= 10 else 3,
                reason='顶背驰+上涨笔结束',
            ))
    
    # 二类买卖点（中枢确认）
    if len(pivots) >= 1:
        last_pivot = pivots[-1]
        
        # 二买：回踩中枢不破
        if latest_price > last_pivot.zg and len(strokes) >= 3:
            prev_stroke = strokes[-2] if len(strokes) >= 2 else strokes[-1]
            if prev_stroke.direction == -1:  # 前一笔向下回踩
                signals.append(ChanSignal(
                    date=latest_date,
                    signal_type='buy2',
                    price=latest_price,
                    strength=3,
                    reason=f'回踩中枢[ZD={last_pivot.zd:.2f}, ZG={last_pivot.zg:.2f}]不破',
                ))
        
        # 二卖：反弹中枢不破
        if latest_price < last_pivot.zd and len(strokes) >= 3:
            prev_stroke = strokes[-2] if len(strokes) >= 2 else strokes[-1]
            if prev_stroke.direction == 1:  # 前一笔向上反弹
                signals.append(ChanSignal(
                    date=latest_date,
                    signal_type='sell2',
                    price=latest_price,
                    strength=3,
                    reason=f'反弹中枢[ZD={last_pivot.zd:.2f}, ZG={last_pivot.zg:.2f}]不破',
                ))
    
    # 三类买卖点（中枢突破）
    if len(pivots) >= 1:
        last_pivot = pivots[-1]
        
        # 三买：突破中枢后回踩不回中枢
        if latest_price > last_pivot.gg:
            signals.append(ChanSignal(
                date=latest_date,
                signal_type='buy3',
                price=latest_price,
                strength=4,
                reason=f'突破中枢上沿GG={last_pivot.gg:.2f}',
            ))
        
        # 三卖：跌破中枢后反弹不回中枢
        if latest_price < last_pivot.dd:
            signals.append(ChanSignal(
                date=latest_date,
                signal_type='sell3',
                price=latest_price,
                strength=4,
                reason=f'跌破中枢下沿DD={last_pivot.dd:.2f}',
            ))
    
    return signals


def generate_trading_signal(symbol: str, signals: List[ChanSignal]) -> dict:
    """
    将缠论信号转换为交易信号（兼容现有框架）
    
    参数:
        symbol: 标的代码
        signals: 缠论信号列表
    
    返回:
        交易信号字典
    """
    if len(signals) == 0:
        return {
            'bucket': 'observe',
            'position_pct': 0.0,
            'direction': 'NEUTRAL',
            'reason': '无缠论信号',
        }
    
    # 取最新信号
    latest = signals[-1]
    
    # 买卖点映射
    if 'buy' in latest.signal_type:
        bucket = 'structural'
        direction = 'BULL_CHAN'
        base_pct = {
            'buy1': 0.15,  # 一买谨慎
            'buy2': 0.12,  # 二买适中
            'buy3': 0.18,  # 三买激进
        }.get(latest.signal_type, 0.10)
        
        position_pct = base_pct * (latest.strength / 5)
        
        return {
            'bucket': bucket,
            'position_pct': round(position_pct, 4),
            'direction': direction,
            'reason': latest.reason,
            'chan_signal': latest.signal_type,
        }
    
    elif 'sell' in latest.signal_type:
        bucket = 'risk'
        direction = 'BEAR_CHAN'
        
        return {
            'bucket': bucket,
            'position_pct': 0.0,
            'direction': direction,
            'reason': latest.reason,
            'chan_signal': latest.signal_type,
        }
    
    else:
        return {
            'bucket': 'observe',
            'position_pct': 0.0,
            'direction': 'NEUTRAL',
            'reason': '无明确信号',
        }


if __name__ == '__main__':
    print("Use test_chan.py for full testing")
