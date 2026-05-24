# -*- coding: utf-8 -*-
"""
merge.py - K线包含关系处理
缠论核心：合并包含关系的K线
"""
import pandas as pd
import numpy as np
from typing import Tuple, List, Dict


def has_inclusion(k1: pd.Series, k2: pd.Series) -> bool:
    """
    判断两根K线是否有包含关系
    
    定义：K线k1和k2有包含关系，当：
        k1的高点 <= k2的高点 且 k1的低点 >= k2的低点（k1被k2包含）
        或
        k1的高点 >= k2的高点 且 k1的低点 <= k2的低点（k2被k1包含）
    """
    h1, l1 = k1['high'], k1['low']
    h2, l2 = k2['high'], k2['low']
    
    # k1被k2包含
    if h1 <= h2 and l1 >= l2:
        return True
    # k2被k1包含
    if h1 >= h2 and l1 <= l2:
        return True
    
    return False


def merge_two_klines(k1: pd.Series, k2: pd.Series, direction: int) -> pd.Series:
    """
    合并两根有包含关系的K线
    
    参数:
        k1, k2: 两根K线（Series，需包含 open, high, low, close, volume）
        direction: 合并方向，1=向上处理，-1=向下处理
    
    返回:
        合并后的K线
    
    向上处理：high = max(h1, h2), low = max(l1, l2)
    向下处理：high = min(h1, h2), low = min(l1, l2)
    """
    merged = k1.copy()
    
    if direction == 1:  # 向上处理
        merged['high'] = max(k1['high'], k2['high'])
        merged['low'] = max(k1['low'], k2['low'])
    else:  # 向下处理
        merged['high'] = min(k1['high'], k2['high'])
        merged['low'] = min(k1['low'], k2['low'])
    
    # 开盘价取较早的K线，收盘价取较晚的
    merged['close'] = k2['close']
    merged['volume'] = k1['volume'] + k2['volume']
    
    return merged


def determine_direction(klines: List[pd.Series], idx: int) -> int:
    """
    确定当前K线的处理方向
    
    规则：
        如果当前K线与前一根K线有包含关系：
            如果前一根K线的低点 < 更前一根K线的低点 → 向下处理（-1）
            如果前一根K线的低点 > 更前一根K线的低点 → 向上处理（1）
            相等则保持前方向
        
    参数:
        klines: 合并后的K线列表
        idx: 当前需要判断方向的索引
    
    返回:
        1 或 -1
    """
    if idx < 1:
        return 1  # 默认向上
    
    if len(klines) < 2:
        return 1
    
    # 比较最后两根合并后K线的低点
    if klines[-1]['low'] < klines[-2]['low']:
        return -1  # 下降趋势，向下处理
    elif klines[-1]['low'] > klines[-2]['low']:
        return 1   # 上升趋势，向上处理
    else:
        # 低点相等，看高点
        if klines[-1]['high'] < klines[-2]['high']:
            return -1
        else:
            return 1


def merge_klines(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[int, int]]:
    """
    处理所有包含关系，返回合并后的K线
    
    参数:
        df: 原始K线数据，需包含列 date, open, high, low, close, volume
    
    返回:
        (merged_df, mapping):
            merged_df: 合并后的K线DataFrame
            mapping: 原始索引 → 合并后索引的映射字典
    """
    if len(df) == 0:
        return df, {}
    
    # 确保列名正确
    required_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"缺少列: {col}")
    
    merged_klines: List[pd.Series] = []
    mapping: Dict[int, int] = {}  # 原始索引 -> 合并后索引
    
    # 第一根K线直接加入
    first_row = df.iloc[0].copy()
    merged_klines.append(first_row)
    mapping[0] = 0
    
    current_direction = 1  # 当前处理方向
    
    for i in range(1, len(df)):
        current_k = df.iloc[i].copy()
        last_merged = merged_klines[-1]
        
        # 检查是否有包含关系
        if has_inclusion(last_merged, current_k):
            # 确定处理方向
            if len(merged_klines) >= 2:
                current_direction = determine_direction(merged_klines, len(merged_klines))
            
            # 合并
            merged = merge_two_klines(last_merged, current_k, current_direction)
            
            # 替换最后一根合并后的K线
            merged_klines[-1] = merged
            mapping[i] = len(merged_klines) - 1
        else:
            # 无包含关系，直接加入
            merged_klines.append(current_k)
            mapping[i] = len(merged_klines) - 1
    
    # 转换为DataFrame
    merged_df = pd.DataFrame(merged_klines).reset_index(drop=True)
    
    # 确保date列存在
    if 'date' not in merged_df.columns:
        merged_df['date'] = merged_df.index
    
    return merged_df, mapping


def process_inclusion(df: pd.DataFrame) -> pd.DataFrame:
    """
    简化接口：返回合并后的K线DataFrame
    
    参数:
        df: 原始K线
    
    返回:
        合并后的K线
    """
    merged_df, _ = merge_klines(df)
    return merged_df


# ============================================================
# 测试
# ============================================================
if __name__ == '__main__':
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from data_fetcher import FutuDataFetcher
    
    fetcher = FutuDataFetcher()
    df = fetcher.get_kline('HK.00700', '2024-01-01', '2026-04-13')
    
    if df is not None:
        print(f"原始K线数: {len(df)}")
        
        merged_df, mapping = merge_klines(df)
        
        print(f"合并后K线数: {len(merged_df)}")
        print(f"合并比例: {len(merged_df) / len(df) * 100:.1f}%")
        
        print("\n最近10根合并后的K线:")
        print(merged_df[['date', 'open', 'high', 'low', 'close']].tail(10).to_string(index=False))
