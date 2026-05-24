# -*- coding: utf-8 -*-
"""
stroke.py - 笔划分
缠论核心：顶分型到底分型（或反过来）为1笔
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class Stroke:
    """笔的数据结构"""
    start_idx: int        # 起点索引（在合并后K线中的索引）
    end_idx: int          # 终点索引
    direction: int        # 方向：1=向上，-1=向下
    start_price: float    # 起点价格
    end_price: float      # 终点价格
    high: float           # 笔内最高价
    low: float            # 笔内最低价
    start_date: str       # 起点日期
    end_date: str         # 终点日期
    
    def length(self) -> float:
        """笔的长度（价格）"""
        return abs(self.end_price - self.start_price)
    
    def bars(self) -> int:
        """笔的K线数量"""
        return self.end_idx - self.start_idx + 1
    
    def is_valid(self, min_bars: int = 4) -> bool:
        """
        笔是否有效
        缠论规定：一笔至少5根K线（含分型）
        这里用 min_bars=4 表示分型间隔至少4根
        """
        return self.bars() >= min_bars


def find_strokes(merged_df: pd.DataFrame, min_bars: int = 4) -> List[Stroke]:
    """
    在合并后的K线中划分笔
    
    算法：
    1. 识别所有分型
    2. 过滤连续同类型分型
    3. 连接相邻的顶底分型形成笔
    4. 验证笔的有效性（至少5根K线）
    
    参数:
        merged_df: 合并后的K线数据
        min_bars: 笔的最小K线数（含分型，默认4表示间隔至少4根）
    
    返回:
        笔的列表
    """
    from .fractal import find_fractals, filter_fractals, get_fractal_extremes
    
    if len(merged_df) < 5:
        return []
    
    # 识别分型
    top_fractals, bottom_fractals = find_fractals(merged_df)
    
    # 提取分型点
    df_marked = merged_df.copy()
    df_marked['fractal'] = 0
    df_marked.loc[top_fractals, 'fractal'] = 1
    df_marked.loc[bottom_fractals, 'fractal'] = -1
    
    fractals = df_marked[df_marked['fractal'] != 0].copy()
    
    if len(fractals) < 2:
        return []
    
    # 过滤连续同类型分型
    filtered = []
    last_type = 0
    
    for idx, row in fractals.iterrows():
        current_type = row['fractal']
        
        if last_type == 0:
            # 第一个分型
            filtered.append({
                'idx': idx,
                'type': 'top' if current_type == 1 else 'bottom',
                'price': row['high'] if current_type == 1 else row['low'],
                'date': row['date'] if 'date' in row else str(idx),
            })
            last_type = current_type
        elif current_type == -last_type:
            # 类型切换，直接加入
            filtered.append({
                'idx': idx,
                'type': 'top' if current_type == 1 else 'bottom',
                'price': row['high'] if current_type == 1 else row['low'],
                'date': row['date'] if 'date' in row else str(idx),
            })
            last_type = current_type
        else:
            # 同类型，取极值（顶取高，底取低）
            if current_type == 1:  # 顶分型
                if row['high'] > filtered[-1]['price']:
                    filtered[-1] = {
                        'idx': idx,
                        'type': 'top',
                        'price': row['high'],
                        'date': row['date'] if 'date' in row else str(idx),
                    }
            else:  # 底分型
                if row['low'] < filtered[-1]['price']:
                    filtered[-1] = {
                        'idx': idx,
                        'type': 'bottom',
                        'price': row['low'],
                        'date': row['date'] if 'date' in row else str(idx),
                    }
    
    if len(filtered) < 2:
        return []
    
    # 连接相邻分型形成笔
    strokes = []
    
    for i in range(len(filtered) - 1):
        start = filtered[i]
        end = filtered[i + 1]
        
        # 方向：顶→底为向下，底→顶为向上
        if start['type'] == 'top' and end['type'] == 'bottom':
            direction = -1
            start_price = start['price']
            end_price = end['price']
        elif start['type'] == 'bottom' and end['type'] == 'top':
            direction = 1
            start_price = start['price']
            end_price = end['price']
        else:
            continue
        
        # 计算笔内极值
        segment = merged_df.iloc[start['idx']:end['idx']+1]
        high = segment['high'].max()
        low = segment['low'].min()
        
        stroke = Stroke(
            start_idx=start['idx'],
            end_idx=end['idx'],
            direction=direction,
            start_price=start_price,
            end_price=end_price,
            high=high,
            low=low,
            start_date=str(start['date']),
            end_date=str(end['date']),
        )
        
        # 验证笔的有效性
        if stroke.bars() >= min_bars:
            strokes.append(stroke)
    
    return strokes


def strokes_to_dataframe(strokes: List[Stroke]) -> pd.DataFrame:
    """
    将笔列表转换为DataFrame
    
    返回:
        DataFrame，列: start_idx, end_idx, direction, start_price, end_price,
               high, low, start_date, end_date, length, bars
    """
    if len(strokes) == 0:
        return pd.DataFrame()
    
    data = [{
        'start_idx': s.start_idx,
        'end_idx': s.end_idx,
        'direction': s.direction,
        'start_price': s.start_price,
        'end_price': s.end_price,
        'high': s.high,
        'low': s.low,
        'start_date': s.start_date,
        'end_date': s.end_date,
        'length': s.length(),
        'bars': s.bars(),
    } for s in strokes]
    
    return pd.DataFrame(data)


# ============================================================
# 测试
# ============================================================
if __name__ == '__main__':
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from data_fetcher import FutuDataFetcher
    from chan.merge import merge_klines
    
    fetcher = FutuDataFetcher()
    df = fetcher.get_kline('HK.00700', '2024-01-01', '2026-04-13')
    
    if df is not None:
        print(f"原始K线数: {len(df)}")
        
        # 合并包含关系
        merged_df, _ = merge_klines(df)
        print(f"合并后K线数: {len(merged_df)}")
        
        # 划分笔
        strokes = find_strokes(merged_df)
        print(f"识别笔数: {len(strokes)}")
        
        if len(strokes) > 0:
            print("\n最近10笔:")
            strokes_df = strokes_to_dataframe(strokes)
            print(strokes_df.tail(10).to_string(index=False))
