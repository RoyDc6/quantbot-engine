# -*- coding: utf-8 -*-
"""
fractal.py - 分型识别
缠论核心：顶分型、底分型
"""
import pandas as pd
import numpy as np
from typing import Tuple


def is_top_fractal(high: pd.Series, low: pd.Series, i: int) -> bool:
    """
    判断第i根K线是否为顶分型
    定义：high[i-1] < high[i] > high[i+1]
          且 low[i-1] < low[i] 或 low[i] > low[i+1]
    """
    if i < 1 or i >= len(high) - 1:
        return False
    
    # 高点是中间最高
    high_middle = high[i] > high[i-1] and high[i] > high[i+1]
    
    # 低点两侧至少有一侧低于中间
    low_condition = (low[i-1] < low[i]) or (low[i+1] < low[i])
    
    return high_middle and low_condition


def is_bottom_fractal(high: pd.Series, low: pd.Series, i: int) -> bool:
    """
    判断第i根K线是否为底分型
    定义：low[i-1] > low[i] < low[i+1]
          且 high[i-1] > high[i] 或 high[i+1] > high[i]
    """
    if i < 1 or i >= len(low) - 1:
        return False
    
    # 低点是中间最低
    low_middle = low[i] < low[i-1] and low[i] < low[i+1]
    
    # 高点两侧至少有一侧高于中间
    high_condition = (high[i-1] > high[i]) or (high[i+1] > high[i])
    
    return low_middle and high_condition


def find_fractals(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """
    识别所有顶分型和底分型
    
    参数:
        df: K线数据，需包含列 'high', 'low'
    
    返回:
        (top_fractals, bottom_fractals): 两个布尔Series
            top_fractals[i] = True 表示第i根K线是顶分型
            bottom_fractals[i] = True 表示第i根K线是底分型
    """
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    
    n = len(df)
    top_fractals = pd.Series([False] * n, index=df.index)
    bottom_fractals = pd.Series([False] * n, index=df.index)
    
    for i in range(1, n - 1):
        if is_top_fractal(high, low, i):
            top_fractals.iloc[i] = True
        elif is_bottom_fractal(high, low, i):
            bottom_fractals.iloc[i] = True
    
    return top_fractals, bottom_fractals


def mark_fractals(df: pd.DataFrame) -> pd.DataFrame:
    """
    标记分型，返回带分型标记的DataFrame
    
    新增列:
        fractal: 1=顶分型, -1=底分型, 0=无
        fractal_price: 分型价格（顶分型取high，底分型取low）
    
    参数:
        df: K线数据
    
    返回:
        带分型标记的DataFrame
    """
    df = df.copy()
    top_fractals, bottom_fractals = find_fractals(df)
    
    df['fractal'] = 0
    df.loc[top_fractals, 'fractal'] = 1
    df.loc[bottom_fractals, 'fractal'] = -1
    
    df['fractal_price'] = np.nan
    df.loc[top_fractals, 'fractal_price'] = df.loc[top_fractals, 'high']
    df.loc[bottom_fractals, 'fractal_price'] = df.loc[bottom_fractals, 'low']
    
    return df


def get_fractal_extremes(df: pd.DataFrame) -> pd.DataFrame:
    """
    提取分型极值点（用于笔划分）
    
    返回:
        仅包含分型点的DataFrame，列: date, idx, type, price
            type: 'top' 或 'bottom'
            price: 分型价格
    """
    df_marked = mark_fractals(df)
    fractals = df_marked[df_marked['fractal'] != 0].copy()
    
    if len(fractals) == 0:
        return pd.DataFrame(columns=['date', 'idx', 'type', 'price'])
    
    result = pd.DataFrame({
        'date': fractals['date'].values if 'date' in fractals.columns else fractals.index,
        'idx': fractals.index,
        'type': ['top' if f == 1 else 'bottom' for f in fractals['fractal']],
        'price': fractals['fractal_price'].values,
    })
    
    return result.reset_index(drop=True)


# ============================================================
# 跳过连续同类型分型（取最高/最低）
# ============================================================
def filter_fractals(fractals_df: pd.DataFrame) -> pd.DataFrame:
    """
    过滤连续的同类型分型，只保留极值
    
    规则：连续多个顶分型，只保留最高的
         连续多个底分型，只保留最低的
    
    参数:
        fractals_df: get_fractal_extremes() 的输出
    
    返回:
        过滤后的分型DataFrame
    """
    if len(fractals_df) == 0:
        return fractals_df
    
    result = []
    current_type = None
    current_group = []
    
    for idx, row in fractals_df.iterrows():
        if row['type'] != current_type:
            # 类型切换，处理前一组
            if current_group:
                if current_type == 'top':
                    best = max(current_group, key=lambda x: x['price'])
                else:
                    best = min(current_group, key=lambda x: x['price'])
                result.append(best)
            
            current_type = row['type']
            current_group = [row.to_dict()]
        else:
            current_group.append(row.to_dict())
    
    # 处理最后一组
    if current_group:
        if current_type == 'top':
            best = max(current_group, key=lambda x: x['price'])
        else:
            best = min(current_group, key=lambda x: x['price'])
        result.append(best)
    
    return pd.DataFrame(result)


if __name__ == '__main__':
    # 测试代码
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from data_fetcher import FutuDataFetcher
    
    fetcher = FutuDataFetcher()
    df = fetcher.get_kline('HK.00700', '2024-01-01', '2026-04-13')
    
    if df is not None:
        df_marked = mark_fractals(df)
        fractals = get_fractal_extremes(df)
        fractals_filtered = filter_fractals(fractals)
        
        print(f"总K线数: {len(df)}")
        print(f"识别分型: {len(fractals)}")
        print(f"过滤后分型: {len(fractals_filtered)}")
        print("\n最近10个分型:")
        print(fractals_filtered.tail(10).to_string(index=False))
