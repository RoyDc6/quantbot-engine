# -*- coding: utf-8 -*-
"""
徐小明结构/钝化模块
基于同花顺指标公式实现

生命周期：
  底部钝化形成 → 底部钝化持续 → 底部结构形成（确认） → 底部结构持续 → 消失
  顶部钝化形成 → 顶部钝化持续 → 顶部结构形成（确认） → 顶部结构持续 → 消失
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """计算MACD三值"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    diff = ema_fast - ema_slow
    dea = diff.ewm(span=signal, adjust=False).mean()
    macd = (diff - dea) * 2
    return diff, dea, macd


def _bars_since(condition: pd.Series) -> pd.Series:
    """
    BARSLAST的Python实现
    返回上一次condition=True到当前的bar数
    如果当前不满足条件，返回距离上一个True的天数
    """
    result = pd.Series(-1, index=condition.index, dtype=float)
    true_pos = np.where(condition.values)[0]
    if len(true_pos) == 0:
        return result

    last_true_pos = true_pos[-1]
    for i in range(len(condition)):
        if i > last_true_pos:
            result.iloc[i] = i - last_true_pos
    return result


def _bars_since_neg(condition: pd.Series) -> pd.Series:
    """
    返回上一次condition=True到当前的bar数
    （当前也满足条件时返回0）
    """
    result = pd.Series(-1, index=condition.index, dtype=float)
    true_pos = np.where(condition.values)[0]
    if len(true_pos) == 0:
        return result

    last_true_pos = true_pos[-1]
    for i in range(len(condition)):
        # 当前i以及之前最后True的位置
        if i >= last_true_pos:
            result.iloc[i] = i - last_true_pos
    return result


def calc_xmm_structure(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                        signal: int = 9, threshold: float = 1.01) -> pd.DataFrame:
    """
    计算徐小明结构/钝化全部变量

    Args:
        df: DataFrame 含 close/high/low/volume
        fast, slow, signal: MACD参数
        threshold: 结构确认阈值（默认1.01，ref_diff需比当前diff大threshold倍）

    Returns:
        pd.DataFrame 含所有中间变量和信号
    """
    close = df['close']
    high = df['high']
    low = df['low']

    diff, dea, macd = calc_macd(close, fast, slow, signal)
    n = len(df)

    # 预分配
    res = pd.DataFrame(index=df.index)
    res['close'] = close
    res['high'] = high
    res['low'] = low
    res['diff'] = diff
    res['dea'] = dea
    res['macd'] = macd

    # ---- 辅助变量 ----
    N1 = pd.Series(-1, index=df.index, dtype=float)  # 上一次 MACD>=0 后，当前处于 MACD<0 区间的bar数
    M1 = pd.Series(-1, index=df.index, dtype=float)  # 上一次 MACD<=0 后，当前处于 MACD>0 区间的bar数

    CL1 = pd.Series(np.nan, index=df.index)
    CL2 = pd.Series(np.nan, index=df.index)
    CL3 = pd.Series(np.nan, index=df.index)
    CH1 = pd.Series(np.nan, index=df.index)
    CH2 = pd.Series(np.nan, index=df.index)
    CH3 = pd.Series(np.nan, index=df.index)

    DIFL1 = pd.Series(np.nan, index=df.index)
    DIFL2 = pd.Series(np.nan, index=df.index)
    DIFL3 = pd.Series(np.nan, index=df.index)
    DIFH1 = pd.Series(np.nan, index=df.index)
    DIFH2 = pd.Series(np.nan, index=df.index)
    DIFH3 = pd.Series(np.nan, index=df.index)

    for i in range(1, n):
        # 当前MACD为负（下MACD）
        if macd.iloc[i] < 0:
            # N1: 从上一次MACD>=0的bar到当前（不含当前）
            # 即：在MACD<0区间内，经过了多少bar
            n1 = 0
            for j in range(i - 1, -1, -1):
                if macd.iloc[j] >= 0:
                    break
                n1 += 1
            N1.iloc[i] = n1

            w = int(n1) + 1
            start = max(0, i - w + 1)
            CL1.iloc[i] = close.iloc[start:i + 1].min()
            DIFL1.iloc[i] = diff.iloc[start:i + 1].min()

            # 找前一个正区间
            pos_start = -1
            for j in range(i - 1, -1, -1):
                if macd.iloc[j] > 0:
                    pos_start = j
                    break
            if pos_start >= 0:
                w2 = pos_start + 1
                CL2.iloc[i] = close.iloc[max(0, pos_start - w2 + 1):pos_start + 1].min()
                DIFL2.iloc[i] = diff.iloc[max(0, pos_start - w2 + 1):pos_start + 1].min()

                # 再找前前一个正区间
                pos_start2 = -1
                for j in range(pos_start - 1, -1, -1):
                    if macd.iloc[j] > 0:
                        pos_start2 = j
                        break
                if pos_start2 >= 0:
                    w3 = pos_start - pos_start2 + 1
                    CL3.iloc[i] = close.iloc[max(0, pos_start2 - w3 + 1):pos_start2 + 1].min()
                    DIFL3.iloc[i] = diff.iloc[max(0, pos_start2 - w3 + 1):pos_start2 + 1].min()

        # 当前MACD为正（上MACD）
        if macd.iloc[i] > 0:
            m1 = 0
            for j in range(i - 1, -1, -1):
                if macd.iloc[j] <= 0:
                    break
                m1 += 1
            M1.iloc[i] = m1

            w = int(m1) + 1
            start = max(0, i - w + 1)
            CH1.iloc[i] = high.iloc[start:i + 1].max()
            DIFH1.iloc[i] = diff.iloc[start:i + 1].max()

            # 找前一个负区间
            neg_start = -1
            for j in range(i - 1, -1, -1):
                if macd.iloc[j] < 0:
                    neg_start = j
                    break
            if neg_start >= 0:
                w2 = neg_start + 1
                CH2.iloc[i] = high.iloc[max(0, neg_start - w2 + 1):neg_start + 1].max()
                DIFH2.iloc[i] = diff.iloc[max(0, neg_start - w2 + 1):neg_start + 1].max()

                # 再找前前一个负区间
                neg_start2 = -1
                for j in range(neg_start - 1, -1, -1):
                    if macd.iloc[j] < 0:
                        neg_start2 = j
                        break
                if neg_start2 >= 0:
                    w3 = neg_start - neg_start2 + 1
                    CH3.iloc[i] = high.iloc[max(0, neg_start2 - w3 + 1):neg_start2 + 1].max()
                    DIFH3.iloc[i] = diff.iloc[max(0, neg_start2 - w3 + 1):neg_start2 + 1].max()

    res['N1'] = N1
    res['M1'] = M1
    res['CL1'] = CL1
    res['CL2'] = CL2
    res['CL3'] = CL3
    res['CH1'] = CH1
    res['CH2'] = CH2
    res['CH3'] = CH3
    res['DIFL1'] = DIFL1
    res['DIFL2'] = DIFL2
    res['DIFL3'] = DIFL3
    res['DIFH1'] = DIFH1
    res['DIFH2'] = DIFH2
    res['DIFH3'] = DIFH3

    # ---- 底部结构/钝化 ----
    ref_macd_1 = macd.shift(1)
    ref_diff_1 = diff.shift(1)
    ref_diff_2 = diff.shift(2)

    # 直接底钝化: 价格新低，DIF未新低
    直接底钝化 = (CL1 < CL2) & (DIFL1 > DIFL2) & (ref_macd_1 < 0) & (diff < 0)

    # 隔峰底钝化: 价格三次区间新低，DIF三峰形态
    隔峰底钝化 = (CL1 < CL3) & (DIFL1 < DIFL2) & (DIFL1 > DIFL3) & (ref_macd_1 < 0) & (diff < 0)

    # 底部钝化
    底部钝化 = (直接底钝化 | 隔峰底钝化) & (diff < 0)

    # 底钝化：首次出现底部钝化（从0变1）
    底钝化_new = pd.Series(False, index=df.index)
    prev_div = False
    for i in range(1, n):
        if 底部钝化.iloc[i] and not prev_div:
            底钝化_new.iloc[i] = True
        prev_div = 底部钝化.iloc[i]

    # 底钝化消失
    底钝化消失 = (
        (直接底钝化.shift(1).fillna(False) & (DIFL1 <= DIFL2) & (diff < dea)) |
        (隔峰底钝化.shift(1).fillna(False) & (DIFL1 <= DIFL3) & (diff < dea))
    )

    # 底部结构: 底部钝化形成后，DIF开始放大
    底部结构 = 底部钝化.shift(1).fillna(False) & (abs(ref_diff_1) >= abs(diff) * threshold)

    # 底再次钝化: 结构形成后再次出现钝化
    底再次钝化_new = pd.Series(False, index=df.index)
    prev_struct = False
    for i in range(1, n):
        if 底部结构.iloc[i] and not prev_struct:
            prev_struct = True
        elif 底部钝化.iloc[i] and prev_struct and (abs(ref_diff_1.iloc[i]) * threshold <= abs(diff.iloc[i])):
            底再次钝化_new.iloc[i] = True
    底再次钝化 = 底再次钝化_new

    # 底结构形成（首次出现）
    底结构形成_new = pd.Series(False, index=df.index)
    prev_bstruct = False
    for i in range(1, n):
        if 底部结构.iloc[i] and not prev_bstruct:
            底结构形成_new.iloc[i] = True
        prev_bstruct = 底部结构.iloc[i]

    # 底结构消失: 价格跌破关键位
    底结构消失_new = pd.Series(False, index=df.index)
    for i in range(1, n):
        if 底部结构.iloc[i]:
            # 找上一个正区间
            m1_val = int(M1.iloc[i]) if pd.notna(M1.iloc[i]) and M1.iloc[i] >= 0 else 1
            prev_struct_bar = i - m1_val - 1
            prev_struct_bar2 = i - 1
            had_prev_struct = (prev_struct_bar >= 0 and 底部结构.iloc[prev_struct_bar]) or (prev_struct_bar2 >= 0 and 底部结构.iloc[prev_struct_bar2])
            prev_div_was = 底钝化_new.iloc[i - 1] if i > 0 else False
            count_struct = 底部结构.iloc[max(0, i - 23):i + 1].sum()
            if (close.iloc[i] < CL2.iloc[i] or close.iloc[i] < CL1.iloc[i]) and had_prev_struct and not prev_div_was and count_struct >= 1:
                底结构消失_new.iloc[i] = True

    # 底消失 = 底钝化消失 OR 底结构消失
    底消失 = (底钝化消失 | 底结构消失_new) & (~底部钝化)

    res['直接底钝化'] = 直接底钝化
    res['隔峰底钝化'] = 隔峰底钝化
    res['底部钝化'] = 底部钝化
    res['底钝化'] = 底钝化_new
    res['底钝化消失'] = 底钝化消失
    res['底部结构'] = 底部结构
    res['底再次钝化'] = 底再次钝化
    res['底结构形成'] = 底结构形成_new
    res['底结构消失'] = 底结构消失_new
    res['底消失'] = 底消失

    # ---- 顶部结构/钝化 ----
    直接顶钝化 = (CH1 > CH2) & (DIFH1 < DIFH2) & (ref_macd_1 > 0) & (diff > 0)
    隔峰顶钝化 = (CH1 > CH3) & (DIFH1 > DIFH2) & (DIFH1 < DIFH3) & (ref_macd_1 > 0) & (diff > 0)
    顶部钝化 = (直接顶钝化 | 隔峰顶钝化) & (diff > 0)

    顶钝化_new = pd.Series(False, index=df.index)
    prev_top_div = False
    for i in range(1, n):
        if 顶部钝化.iloc[i] and not prev_top_div:
            顶钝化_new.iloc[i] = True
        prev_top_div = 顶部钝化.iloc[i]

    顶钝化消失 = (
        (直接顶钝化.shift(1).fillna(False) & (DIFH1 >= DIFH2) & (diff > dea)) |
        (隔峰顶钝化.shift(1).fillna(False) & (DIFH1 >= DIFH3) & (diff > dea))
    )

    顶部结构 = 顶部钝化.shift(1).fillna(False) & (ref_diff_1 >= diff * threshold)

    顶再次钝化_new = pd.Series(False, index=df.index)
    prev_top_struct = False
    for i in range(1, n):
        if 顶部结构.iloc[i] and not prev_top_struct:
            prev_top_struct = True
        elif 顶部钝化.iloc[i] and prev_top_struct and (ref_diff_1.iloc[i] * threshold <= diff.iloc[i]):
            顶再次钝化_new.iloc[i] = True
    顶再次钝化 = 顶再次钝化_new

    顶结构形成_new = pd.Series(False, index=df.index)
    prev_tstruct = False
    for i in range(1, n):
        if 顶部结构.iloc[i] and not prev_tstruct:
            顶结构形成_new.iloc[i] = True
        prev_tstruct = 顶部结构.iloc[i]

    顶结构消失_new = pd.Series(False, index=df.index)
    for i in range(1, n):
        if 顶部结构.iloc[i]:
            n1_val = int(N1.iloc[i]) if pd.notna(N1.iloc[i]) and N1.iloc[i] >= 0 else 1
            prev_struct_bar = i - n1_val - 1
            prev_struct_bar2 = i - 1
            had_prev_struct = (prev_struct_bar >= 0 and 顶部结构.iloc[prev_struct_bar]) or (prev_struct_bar2 >= 0 and 顶部结构.iloc[prev_struct_bar2])
            prev_div_was = 顶钝化_new.iloc[i - 1] if i > 0 else False
            count_struct = 顶部结构.iloc[max(0, i - 22):i + 1].sum()
            if (close.iloc[i] > CH2.iloc[i] or close.iloc[i] > CH1.iloc[i]) and had_prev_struct and not prev_div_was and count_struct >= 1:
                顶结构消失_new.iloc[i] = True

    顶消失 = (顶钝化消失 | 顶结构消失_new) & (~顶部钝化)

    res['直接顶钝化'] = 直接顶钝化
    res['隔峰顶钝化'] = 隔峰顶钝化
    res['顶部钝化'] = 顶部钝化
    res['顶钝化'] = 顶钝化_new
    res['顶钝化消失'] = 顶钝化消失
    res['顶部结构'] = 顶部结构
    res['顶再次钝化'] = 顶再次钝化
    res['顶结构形成'] = 顶结构形成_new
    res['顶结构消失'] = 顶结构消失_new
    res['顶消失'] = 顶消失

    return res


def get_structure_state(res: pd.DataFrame) -> Dict:
    """
    返回当前结构状态（最新一根K线），含连续钝化天数
    """
    i = -1

    # 连续底部钝化天数
    连续底部钝化天数 = 0
    for j in range(len(res) - 1, -1, -1):
        if bool(res['底部钝化'].iloc[j]):
            连续底部钝化天数 += 1
        else:
            break

    # 连续顶部钝化天数
    连续顶部钝化天数 = 0
    for j in range(len(res) - 1, -1, -1):
        if bool(res['顶部钝化'].iloc[j]):
            连续顶部钝化天数 += 1
        else:
            break

    return {
        '直接底钝化': bool(res['直接底钝化'].iloc[i]),
        '隔峰底钝化': bool(res['隔峰底钝化'].iloc[i]),
        '底部钝化': bool(res['底部钝化'].iloc[i]),
        '底钝化': bool(res['底钝化'].iloc[i]),
        '底部结构': bool(res['底部结构'].iloc[i]),
        '底结构形成': bool(res['底结构形成'].iloc[i]),
        '底再次钝化': bool(res['底再次钝化'].iloc[i]),
        '底消失': bool(res['底消失'].iloc[i]),
        '直接顶钝化': bool(res['直接顶钝化'].iloc[i]),
        '隔峰顶钝化': bool(res['隔峰顶钝化'].iloc[i]),
        '顶部钝化': bool(res['顶部钝化'].iloc[i]),
        '顶钝化': bool(res['顶钝化'].iloc[i]),
        '顶部结构': bool(res['顶部结构'].iloc[i]),
        '顶结构形成': bool(res['顶结构形成'].iloc[i]),
        '顶再次钝化': bool(res['顶再次钝化'].iloc[i]),
        '顶消失': bool(res['顶消失'].iloc[i]),
        '连续底部钝化天数': 连续底部钝化天数,
        '连续顶部钝化天数': 连续顶部钝化天数,
        'diff': float(res['diff'].iloc[i]),
        'dea': float(res['dea'].iloc[i]),
        'macd': float(res['macd'].iloc[i]),
    }
