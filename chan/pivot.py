# -*- coding: utf-8 -*-
"""
pivot.py - 中枢识别
缠论核心：至少3个连续线段的重叠区间
"""
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Pivot:
    """中枢的数据结构"""
    start_idx: int          # 起始线段索引
    end_idx: int            # 结束线段索引
    zg: float               # 中枢高点 ZG = min(线段高点)
    zd: float               # 中枢低点 ZD = max(线段低点)
    gg: float               # 中枢最高 GG = max(线段高点)
    dd: float               # 中枢最低 DD = min(线段低点)
    level: int              # 中枢级别（暂未实现多级别递归）
    start_date: str         # 起始日期
    end_date: str           # 结束日期
    
    def height(self) -> float:
        """中枢高度"""
        return self.zg - self.zd
    
    def center(self) -> float:
        """中枢中心价格"""
        return (self.zg + self.zd) / 2
    
    def contains_price(self, price: float) -> bool:
        """价格是否在中枢区间内"""
        return self.zd <= price <= self.zg
    
    def bars(self) -> int:
        """中枢包含的线段数"""
        return self.end_idx - self.start_idx + 1


def find_pivots(segments: List, min_segments: int = 3) -> List[Pivot]:
    """
    从线段列表中识别中枢
    
    算法：
    1. 遍历连续的线段（至少3个）
    2. 计算重叠区间：ZG = min(线段高点), ZD = max(线段低点)
    3. 如果 ZG > ZD，则形成有效中枢
    4. 检查中枢延伸：新线段是否在中枢区间内
    
    参数:
        segments: 线段列表（Segment对象）
        min_segments: 构成中枢的最小线段数（默认3）
    
    返回:
        中枢的列表
    """
    if len(segments) < min_segments:
        return []
    
    pivots = []
    i = 0
    
    while i <= len(segments) - min_segments:
        # 尝试从当前线段开始构建中枢
        segment_window = segments[i:i + min_segments]
        
        # 计算潜在中枢区间
        zg = min(seg.high for seg in segment_window)
        zd = max(seg.low for seg in segment_window)
        
        # 验证中枢有效性：ZG > ZD
        if zg > zd:
            gg = max(seg.high for seg in segment_window)
            dd = min(seg.low for seg in segment_window)
            
            # 中枢有效，尝试延伸
            pivot_segments = list(segment_window)
            end_idx = i + min_segments - 1
            
            # 检查后续线段是否在中枢内延伸
            for j in range(i + min_segments, len(segments)):
                next_seg = segments[j]
                
                # 新线段是否在中枢区间内
                if next_seg.low >= zd and next_seg.high <= zg:
                    # 在中枢内，延伸中枢
                    pivot_segments.append(next_seg)
                    end_idx = j
                    # 更新GG/DD
                    gg = max(gg, next_seg.high)
                    dd = min(dd, next_seg.low)
                else:
                    # 突破中枢
                    break
            
            pivot = Pivot(
                start_idx=i,
                end_idx=end_idx,
                zg=zg,
                zd=zd,
                gg=gg,
                dd=dd,
                level=1,
                start_date=segments[i].start_date,
                end_date=pivot_segments[-1].end_date,
            )
            
            pivots.append(pivot)
            i = end_idx + 1  # 跳过已处理线段
        else:
            i += 1
    
    return pivots


def pivots_to_dataframe(pivots: List[Pivot]) -> pd.DataFrame:
    """
    将中枢列表转换为DataFrame
    """
    if len(pivots) == 0:
        return pd.DataFrame()
    
    data = [{
        'start_idx': p.start_idx,
        'end_idx': p.end_idx,
        'zg': p.zg,
        'zd': p.zd,
        'gg': p.gg,
        'dd': p.dd,
        'height': p.height(),
        'center': p.center(),
        'segments': p.bars(),
        'start_date': p.start_date,
        'end_date': p.end_date,
    } for p in pivots]
    
    return pd.DataFrame(data)


def check_pivot_break(pivot: Pivot, price: float, direction: str) -> str:
    """
    检查价格是否突破中枢
    
    参数:
        pivot: 中枢对象
        price: 当前价格
        direction: 'up' 或 'down'
    
    返回:
        'up_break': 向上突破
        'down_break': 向下突破
        'inside': 在中枢内
        'extend': 中枢延伸
    """
    if price > pivot.zg:
        return 'up_break'
    elif price < pivot.zd:
        return 'down_break'
    else:
        return 'inside'


if __name__ == '__main__':
    print("Use test_chan.py for full testing")
