# -*- coding: utf-8 -*-
"""
segment.py - 线段划分
缠论核心：至少3笔构成线段
"""
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Segment:
    """线段的数据结构"""
    start_idx: int          # 起笔索引（在笔列表中的索引）
    end_idx: int            # 终笔索引
    direction: int          # 方向：1=向上，-1=向下
    strokes: List           # 包含的笔列表
    high: float             # 线段最高价
    low: float              # 线段最低价
    start_price: float      # 起点价格
    end_price: float        # 终点价格
    start_date: str         # 起始日期
    end_date: str           # 结束日期
    
    def bars(self) -> int:
        """线段包含的笔数"""
        return self.end_idx - self.start_idx + 1
    
    def is_valid(self, min_strokes: int = 3) -> bool:
        """线段是否有效（至少3笔）"""
        return self.bars() >= min_strokes


def find_segments(strokes: List, min_strokes: int = 3) -> List[Segment]:
    """
    从笔列表中划分线段
    
    简化算法：
    1. 至少3笔构成线段
    2. 同向笔合并，遇到破坏（反向笔超过前极端点）则新线段开始
    3. 新线段必须破坏前一线段的极端点
    
    参数:
        strokes: 笔的列表（Stroke对象）
        min_strokes: 最小笔数（默认3）
    
    返回:
        线段的列表
    """
    if len(strokes) < min_strokes:
        return []
    
    segments = []
    i = 0
    
    while i <= len(strokes) - min_strokes:
        # 尝试从当前笔开始构建线段
        first_stroke = strokes[i]
        direction = first_stroke.direction
        
        # 寻找破坏点
        segment_strokes = [first_stroke]
        extreme_price = first_stroke.end_price
        extreme_idx = i
        
        for j in range(i + 1, len(strokes)):
            current_stroke = strokes[j]
            segment_strokes.append(current_stroke)
            
            # 检查是否破坏
            if direction == 1:  # 向上线段
                if current_stroke.low < extreme_price and len(segment_strokes) >= min_strokes:
                    # 被破坏，线段结束
                    break
                # 更新极端点
                if current_stroke.high > extreme_price:
                    extreme_price = current_stroke.high
                    extreme_idx = j
            else:  # 向下线段
                if current_stroke.high > extreme_price and len(segment_strokes) >= min_strokes:
                    # 被破坏，线段结束
                    break
                # 更新极端点
                if current_stroke.low < extreme_price:
                    extreme_price = current_stroke.low
                    extreme_idx = j
        
        # 验证线段有效性
        if len(segment_strokes) >= min_strokes:
            # 计算线段属性
            segment_high = max(s.high for s in segment_strokes)
            segment_low = min(s.low for s in segment_strokes)
            
            segment = Segment(
                start_idx=i,
                end_idx=i + len(segment_strokes) - 1,
                direction=direction,
                strokes=segment_strokes,
                high=segment_high,
                low=segment_low,
                start_price=first_stroke.start_price,
                end_price=segment_strokes[-1].end_price,
                start_date=first_stroke.start_date,
                end_date=segment_strokes[-1].end_date,
            )
            
            segments.append(segment)
            i = i + len(segment_strokes) - 1  # 跳到下一个可能起点
        else:
            i += 1
    
    return segments


def segments_to_dataframe(segments: List[Segment]) -> pd.DataFrame:
    """
    将线段列表转换为DataFrame
    """
    if len(segments) == 0:
        return pd.DataFrame()
    
    data = [{
        'start_idx': seg.start_idx,
        'end_idx': seg.end_idx,
        'direction': seg.direction,
        'strokes': seg.bars(),
        'high': seg.high,
        'low': seg.low,
        'start_price': seg.start_price,
        'end_price': seg.end_price,
        'start_date': seg.start_date,
        'end_date': seg.end_date,
    } for seg in segments]
    
    return pd.DataFrame(data)


if __name__ == '__main__':
    # 测试需要先获取笔
    print("Use test_chan.py for full testing")
