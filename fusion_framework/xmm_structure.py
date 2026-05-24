"""
徐小明结构/钝化公式 - Python实现
解析同花顺/通达信公式，用Python实现
"""

import pandas as pd
import numpy as np

# ============================================================
# 核心MACD计算
# ============================================================
def calc_macd(close, fast=12, slow=26, signal=9):
    """
    计算MACD三值
    DIFF = EMA(fast) - EMA(slow)
    DEA  = EMA(DIFF, signal)
    MACD = (DIFF - DEA) * 2
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    diff = ema_fast - ema_slow
    dea = diff.ewm(span=signal, adjust=False).mean()
    macd = (diff - dea) * 2
    return diff, dea, macd

# ============================================================
# 公式辅助函数
# ============================================================

def barslast(condition):
    """
    BARSLAST: 上一次条件成立到现在的bar数
    BARSLAST(condition) = 满足condition的bar之后有多少根bar未满足
    实际上是: 从最近一次True的位置到当前的bar数
    """
    # 找到所有True的位置，然后计算每个位置到当前的距离
    result = pd.Series(-1, index=condition.index)
    true_positions = np.where(condition.values)[0]
    if len(true_positions) == 0:
        return result
    
    last_true_pos = true_positions[-1]
    for i in range(len(condition)):
        if i > last_true_pos:
            result.iloc[i] = i - last_true_pos
    return result

def barslast_count(condition):
    """计数: 从上一次True到现在经过了多少根bar"""
    return barslast(condition)

def ref(series, n):
    """REF: n天前的值"""
    return series.shift(n)

def llv(series, window):
    """LLV: window周期内的最低值"""
    return series.rolling(window=window, min_periods=1).min()

def hhv(series, window):
    """HHV: window周期内的最高值"""
    return series.rolling(window=window, min_periods=1).max()

# ============================================================
# 结构/钝化核心公式解析
# ============================================================

def calc_xmm_structure(close, diff, dea, macd, fast=12, slow=26, signal=9):
    """
    计算徐小明结构/钝化信号
    
    核心逻辑：
    - N1: 上一次MACD从正转负后，经过了多少根bar（当前在负区间）
    - M1: 上一次MACD从负转正后，经过了多少根bar（当前在正区间）
    """
    df = pd.DataFrame({'close': close, 'diff': diff, 'dea': dea, 'macd': macd})
    
    # N1: 上一次MACD从正转负后（当前MACD<0），经过的bar数
    # 条件: REF(MACD,1) >= 0 AND MACD < 0
    macd_pos_to_neg = (ref(macd, 1) >= 0) & (macd < 0)
    N1 = barslast(macd_pos_to_neg)  # 当前处于负区间，经过的bar数
    
    # M1: 上一次MACD从负转正后（当前MACD>0），经过的bar数
    macd_neg_to_pos = (ref(macd, 1) <= 0) & (macd > 0)
    M1 = barslast(macd_neg_to_pos)  # 当前处于正区间，经过的bar数
    
    # 价格系列
    # CL1: 当前负区间内的最低收盘价
    CL1 = pd.Series(np.nan, index=close.index)
    # CL2: 前一次正区间的最低收盘价
    CL2 = pd.Series(np.nan, index=close.index)
    # CL3: 前前一次正区间的最低收盘价
    CL3 = pd.Series(np.nan, index=close.index)
    
    # CH1: 当前正区间内的最高收盘价
    CH1 = pd.Series(np.nan, index=close.index)
    # CH2: 前一次负区间的最高收盘价
    CH2 = pd.Series(np.nan, index=close.index)
    # CH3: 前前一次负区间的最高收盘价
    CH3 = pd.Series(np.nan, index=close.index)
    
    # DIF系列
    DIFL1 = pd.Series(np.nan, index=close.index)
    DIFL2 = pd.Series(np.nan, index=close.index)
    DIFL3 = pd.Series(np.nan, index=close.index)
    DIFH1 = pd.Series(np.nan, index=close.index)
    DIFH2 = pd.Series(np.nan, index=close.index)
    DIFH3 = pd.Series(np.nan, index=close.index)
    
    # 逐行计算（简化版）
    for i in range(len(df)):
        # 负区间: MACD < 0
        if macd.iloc[i] < 0:
            n1 = N1.iloc[i]
            # 当前负区间内的最低收盘价 (窗口 = n1+1)
            window = int(n1) + 1
            CL1.iloc[i] = close.iloc[max(0, i-window+1):i+1].min()
            
            # 前一次正区间的最低收盘价
            # 找前一个正区间
            pos_starts = np.where((macd.iloc[:i].shift(1).fillna(0) <= 0) & (macd.iloc[:i] > 0))[0]
            if len(pos_starts) >= 1:
                last_pos_start = pos_starts[-1]
                m1_prev = i - last_pos_start  # 前一次正区间的持续bar数
                CL2.iloc[i] = close.iloc[max(0, last_pos_start-m1_prev):last_pos_start+1].min()
            if len(pos_starts) >= 2:
                second_pos_start = pos_starts[-2]
                m1_prev2 = pos_starts[-1] - second_pos_start
                CL3.iloc[i] = close.iloc[max(0, second_pos_start-m1_prev2):second_pos_start+1].min()
            
            # DIFL系列
            DIFL1.iloc[i] = diff.iloc[max(0, i-window+1):i+1].min()
            if len(pos_starts) >= 1:
                DIFL2.iloc[i] = diff.iloc[max(0, last_pos_start-m1_prev):last_pos_start+1].min()
            if len(pos_starts) >= 2:
                DIFL3.iloc[i] = diff.iloc[max(0, second_pos_start-m1_prev2):second_pos_start+1].min()
        
        # 正区间: MACD > 0
        if macd.iloc[i] > 0:
            m1 = M1.iloc[i]
            window = int(m1) + 1
            CH1.iloc[i] = close.iloc[max(0, i-window+1):i+1].max()
            
            # 前一次负区间的最高收盘价
            neg_starts = np.where((macd.iloc[:i].shift(1).fillna(0) >= 0) & (macd.iloc[:i] < 0))[0]
            if len(neg_starts) >= 1:
                last_neg_start = neg_starts[-1]
                n1_prev = i - last_neg_start
                CH2.iloc[i] = close.iloc[max(0, last_neg_start-n1_prev):last_neg_start+1].max()
            if len(neg_starts) >= 2:
                second_neg_start = neg_starts[-2]
                n1_prev2 = neg_starts[-1] - second_neg_start
                CH3.iloc[i] = close.iloc[max(0, second_neg_start-n1_prev2):second_neg_start+1].max()
            
            # DIFH系列
            DIFH1.iloc[i] = diff.iloc[max(0, i-window+1):i+1].max()
            if len(neg_starts) >= 1:
                DIFH2.iloc[i] = diff.iloc[max(0, last_neg_start-n1_prev):last_neg_start+1].max()
            if len(neg_starts) >= 2:
                DIFH3.iloc[i] = diff.iloc[max(0, second_neg_start-n1_prev2):second_neg_start+1].max()
    
    df['N1'] = N1
    df['M1'] = M1
    df['CL1'] = CL1
    df['CL2'] = CL2
    df['CL3'] = CL3
    df['CH1'] = CH1
    df['CH2'] = CH2
    df['CH3'] = CH3
    df['DIFL1'] = DIFL1
    df['DIFL2'] = DIFL2
    df['DIFL3'] = DIFL3
    df['DIFH1'] = DIFH1
    df['DIFH2'] = DIFH2
    df['DIFH3'] = DIFH3
    
    return df


def calc_xmm_signals(df):
    """
    根据df数据计算结构/钝化信号
    """
    close = df['close']
    diff = df['diff']
    dea = df['dea']
    macd = df['macd']
    N1 = df['N1']
    M1 = df['M1']
    CL1 = df['CL1']
    CL2 = df['CL2']
    CL3 = df['CL3']
    CH1 = df['CH1']
    CH2 = df['CH2']
    CH3 = df['CH3']
    DIFL1 = df['DIFL1']
    DIFL2 = df['DIFL2']
    DIFL3 = df['DIFL3']
    DIFH1 = df['DIFH1']
    DIFH2 = df['DIFH2']
    DIFH3 = df['DIFH3']
    
    # ============================================================
    # 底部结构/钝化
    # ============================================================
    
    # 直接底钝化: 价格新低但DIF未新低（底背离）
    # 条件: CL1 < CL2 AND DIFL1 > DIFL2 AND REF(MACD,1)<0 AND DIFF<0
    直接底钝化 = (CL1 < CL2) & (DIFL1 > DIFL2) & (ref(macd, 1) < 0) & (diff < 0)
    
    # 隔峰底钝化: 价格新低但DIF没有创新低（隔峰底背离）
    # 条件: CL1 < CL3 AND DIFL1 < DIFL2 AND DIFL1 > DIFL3
    隔峰底钝化 = (CL1 < CL3) & (DIFL1 < DIFL2) & (DIFL1 > DIFL3) & (ref(macd, 1) < 0) & (diff < 0)
    
    # 底部钝化 = 直接底钝化 OR 隔峰底钝化，且 DIFF < 0
    底部钝化 = (直接底钝化 | 隔峰底钝化) & (diff < 0)
    
    # 底钝化: 第一次出现底部钝化（从0变1）
    底钝化 = (ref(底部钝化, 1) == 0) & 底部钝化
    
    # 底钝化消失
    底钝化消失 = (
        (ref(直接底钝化, 1) & (DIFL1 <= DIFL2) & (diff < dea)) |
        (ref(隔峰底钝化, 1) & (DIFL1 <= DIFL3) & (diff < dea))
    )
    
    # 底部结构: 底部钝化形成后，DIFF开始放大（结构形成）
    # 条件: REF(底部钝化,1) AND |REF(DIFF,1)| >= |DIFF| * 1.01
    底部结构 = ref(底部钝化, 1) & (abs(ref(diff, 1)) >= abs(diff) * 1.01)
    
    # 底再次钝化: 结构形成后再次出现钝化
    底再次钝化 = ref(底部结构, 1) & 底部钝化 & (abs(ref(diff, 1)) * 1.01 <= abs(diff))
    
    # 底结构形成: 第一次出现底部结构
    底结构形成 = (ref(底部结构, 1) == 0) & 底部结构
    
    # 底结构消失
    底结构消失 = (
        ((close < CL2) | (close < CL1)) &
        (ref(底部结构, int(M1.iloc[0] if pd.notna(M1.iloc[0]) else 0) + 1) | ref(底部结构, int(M1.iloc[0] if pd.notna(M1.iloc[0]) else 0))) &
        (~ref(底钝化, 1)) &
        (底部结构.rolling(24).sum() >= 1)
    )
    
    # 底消失 = 底钝化消失 OR 底结消失，且不再是底部钝化
    底消失 = (底钝化消失 | 底结构消失) & (~底部钝化)
    
    # ============================================================
    # 顶部结构/钝化
    # ============================================================
    
    # 直接顶钝化: 价格新高但DIF未新高（顶背离）
    直接顶钝化 = (CH1 > CH2) & (DIFH1 < DIFH2) & (ref(macd, 1) > 0) & (diff > 0)
    
    # 隔峰顶钝化
    隔峰顶钝化 = (CH1 > CH3) & (DIFH1 > DIFH2) & (DIFH1 < DIFH3) & (ref(macd, 1) > 0) & (diff > 0)
    
    # 顶部钝化
    顶部钝化 = (直接顶钝化 | 隔峰顶钝化) & (diff > 0)
    
    # 顶钝化: 第一次出现顶部钝化
    顶钝化 = (ref(顶部钝化, 1) == 0) & 顶部钝化 & (diff > dea)
    
    # 顶钝化消失
    顶钝化消失 = (
        (ref(直接顶钝化, 1) & (DIFH1 >= DIFH2) & (diff > dea)) |
        (ref(隔峰顶钝化, 1) & (DIFH1 >= DIFH3) & (diff > dea))
    )
    
    # 顶部结构: 顶部钝化形成后，DIFF开始缩小（结构形成）
    顶部结构 = ref(顶部钝化, 1) & (ref(diff, 1) >= diff * 1.01)
    
    # 顶再次钝化
    顶再次钝化 = ref(顶部结构, 1) & 顶部钝化 & (ref(diff, 1) * 1.01 <= diff)
    
    # 顶结构形成
    顶结构形成 = (ref(顶部结构, 1) == 0) & 顶部结构
    
    # 顶结构消失
    顶结构消失 = (
        ((close > CH2) | (close > CH1)) &
        (ref(顶部结构, int(N1.iloc[0] if pd.notna(N1.iloc[0]) else 0) + 1) | ref(顶部结构, int(N1.iloc[0] if pd.notna(N1.iloc[0]) else 0))) &
        (~ref(顶钝化, 1)) &
        (顶部结构.rolling(23).sum() >= 1)
    )
    
    # 顶消失
    顶消失 = (顶钝化消失 | 顶结构消失) & (~顶部钝化)
    
    return {
        '直接底钝化': 直接底钝化,
        '隔峰底钝化': 隔峰底钝化,
        '底部钝化': 底部钝化,
        '底钝化': 底钝化,
        '底部结构': 底部结构,
        '底结构形成': 底结构形成,
        '底消失': 底消失,
        '直接顶钝化': 直接顶钝化,
        '隔峰顶钝化': 隔峰顶钝化,
        '顶部钝化': 顶部钝化,
        '顶钝化': 顶钝化,
        '顶部结构': 顶部结构,
        '顶结构形成': 顶结构形成,
        '顶消失': 顶消失,
    }


if __name__ == '__main__':
    # 测试
    print("徐小明结构/钝化公式解析完成")
    print("=" * 60)
    print("底部结构形成条件:")
    print("  1. 先有底部钝化 (CL1<CL2 AND DIFL1>DIFL2) 或 (CL1<CL3 AND DIFL1<DIFL2 AND DIFL1>DIFL3)")
    print("  2. 底部钝化后，DIFF开始放大 (|REF(DIFF,1)| >= |DIFF| * 1.01)")
    print("  3. 顶背离/底背离形成，结构成立")
    print()
    print("顶部结构形成条件:")
    print("  1. 先有顶部钝化 (CH1>CH2 AND DIFH1<DIFH2) 或 (CH1>CH3 AND DIFH1>DIFH2 AND DIFH1<DIFH3)")
    print("  2. 顶部钝化后，DIFF开始缩小 (REF(DIFF,1) >= DIFF * 1.01)")
    print()
    print("钝化消失条件:")
    print("  底钝化消失: DIFL1 <= DIFL2 AND DIFF < DEA")
    print("  顶钝化消失: DIFH1 >= DIFH2 AND DIFF > DEA")
    print("  结构消失: 价格跌破/升破关键位")
