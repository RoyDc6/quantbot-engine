# -*- coding: utf-8 -*-
"""
徐小明策略模型 v4.4
三层独立信号 + TD放大器
优化：全量预计算一次，analyze每次O(1)查表
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple, List
import json


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


# ============================================================
# 趋势层
# ============================================================

def calc_dual_trend(df: pd.DataFrame, sp: int = 25, lp: int = 90) -> pd.DataFrame:
    df = df.copy()
    df['短顶'] = _ema(df['high'], sp)
    df['短底'] = _ema(df['low'],  sp)
    df['长顶'] = _ema(df['high'], lp)
    df['长底'] = _ema(df['low'],  lp)
    df['cross_short_up']   = (df['close'].shift(1) <= df['短顶'].shift(1)) & (df['close'] > df['短顶'])
    df['cross_short_down'] = (df['close'].shift(1) >= df['短底'].shift(1)) & (df['close'] < df['短底'])
    df['cross_long_up']    = (df['close'].shift(1) <= df['长顶'].shift(1)) & (df['close'] > df['长顶'])
    df['cross_long_down']  = (df['close'].shift(1) >= df['长底'].shift(1)) & (df['close'] < df['长底'])
    return df


# ============================================================
# MACD结构层（直接逐行O(n²)但已是最简）
# ============================================================

def calc_xmm_structure(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    df = df.copy()
    df['ema_fast'] = _ema(df['close'], fast)
    df['ema_slow'] = _ema(df['close'], slow)
    df['diff'] = df['ema_fast'] - df['ema_slow']
    df['dea']  = _ema(df['diff'], signal)
    df['histogram'] = (df['diff'] - df['dea']) * 2

    m = df['histogram'].values
    d = df['diff'].values
    dea = df['dea'].values
    close = df['close'].values
    n = len(m)

    N1 = np.zeros(n, dtype=np.int32)
    M1 = np.zeros(n, dtype=np.int32)
    cur_n1, cur_m1 = 0, 0
    for i in range(1, n):
        cur_n1 = 0 if (m[i-1] >= 0 and m[i] < 0) else cur_n1 + 1
        N1[i] = cur_n1
        cur_m1 = 0 if (m[i-1] <= 0 and m[i] > 0) else cur_m1 + 1
        M1[i] = cur_m1

    records = []
    for i in range(n):
        n1 = int(N1[i])
        m1 = int(M1[i])

        lb1 = max(0, i - n1);   w1 = i - lb1 + 1
        lb2 = max(0, lb1 - m1) if m1 > 0 else max(0, lb1 - 60); w2 = lb1 - lb2 + 1
        lb3 = max(0, lb2 - m1) if m1 > 0 else max(0, lb2 - 60); w3 = lb2 - lb3 + 1

        def wmin(arr, idx, w):
            return np.min(arr[max(0,idx-w+1):idx+1])
        def wmax(arr, idx, w):
            return np.max(arr[max(0,idx-w+1):idx+1])

        cl1 = wmin(close, i, w1);   difl1 = wmin(d, i, w1)
        cl2 = wmin(close, lb1, w2) if lb1 > 0 else np.nan; difl2 = wmin(d, lb1, w2) if lb1 > 0 else np.nan
        cl3 = wmin(close, lb2, w3) if lb2 > 0 else np.nan; difl3 = wmin(d, lb2, w3) if lb2 > 0 else np.nan
        cb1 = wmax(close, i, w1);   difh1 = wmax(d, i, w1)
        cb2 = wmax(close, lb1, w2) if lb1 > 0 else np.nan; difh2 = wmax(d, lb1, w2) if lb1 > 0 else np.nan
        cb3 = wmax(close, lb2, w3) if lb2 > 0 else np.nan; difh3 = wmax(d, lb2, w3) if lb2 > 0 else np.nan

        prev = records[i-1] if records else {}

        direct_bot = (i > 0 and m[i-1] < 0 and d[i] < 0 and cl1 < cl2 and difl1 > difl2)
        skip_bot   = (i > 0 and m[i-1] < 0 and d[i] < 0 and cl1 < cl3 and difl1 < difl2 and difl1 > difl3)
        bot_div    = direct_bot or skip_bot
        bot_struct = (prev.get('底部钝化') and prev.get('diff') is not None and
                       abs(prev['diff']) >= abs(d[i]) * 1.01)
        bot_dis    = ((difl1 <= difl2 and d[i] < dea[i]) if direct_bot else
                      (difl1 <= difl3 and d[i] < dea[i]) if skip_bot else False)
        bot_repeat = (prev.get('底部结构') and bot_div and prev.get('diff') is not None and
                       abs(prev['diff']) * 1.01 <= abs(d[i]))

        direct_top = (i > 0 and m[i-1] > 0 and d[i] > 0 and cb1 > cb2 and difh1 < difh2)
        skip_top   = (i > 0 and m[i-1] > 0 and d[i] > 0 and cb1 > cb3 and difh1 > difh2 and difh1 < difh3)
        top_div    = direct_top or skip_top
        top_struct = (prev.get('顶部钝化') and prev.get('diff') is not None and
                        prev['diff'] >= d[i] * 1.01)
        top_dis    = ((difh1 >= difh2 and d[i] > dea[i]) if direct_top else
                      (difh1 >= difh3 and d[i] > dea[i]) if skip_top else False)
        top_repeat = (prev.get('顶部结构') and top_div and prev.get('diff') is not None and
                        prev['diff'] * 1.01 <= d[i])

        records.append({
            'diff': d[i], 'dea': dea[i], 'histogram': m[i],
            '直接底钝化': bool(direct_bot), '隔峰底钝化': bool(skip_bot),
            '底部钝化': bool(bot_div), '底部结构': bool(bot_struct),
            '底钝化消失': bool(bot_dis), '底再次钝化': bool(bot_repeat),
            '直接顶钝化': bool(direct_top), '隔峰顶钝化': bool(skip_top),
            '顶部钝化': bool(top_div), '顶部结构': bool(top_struct),
            '顶钝化消失': bool(top_dis), '顶再次钝化': bool(top_repeat),
        })

    result = pd.DataFrame(records, index=df.index)
    result['diff'] = d; result['dea'] = dea; result['histogram'] = m
    return result


# ============================================================
# TD序列
# ============================================================

def calc_td_seq(close: pd.Series, period: int = 4) -> pd.Series:
    n = len(close)
    td = np.zeros(n, dtype=np.int32)
    count = 0
    c = close.values
    for i in range(n):
        if i >= period and c[i] < c[i-period]: count += 1
        elif i >= period and c[i] > c[i-period]: count = 0
        td[i] = count
    return pd.Series(td, index=close.index)


# ============================================================
# 预计算器（一次性全量计算，多次analyze时复用）
# ============================================================

class XMMPrecomputed:
    """预计算全量指标，后续analyze只需O(1)查表"""

    def __init__(self, df: pd.DataFrame,
                 short_period=25, long_period=90,
                 macd_fast=12, macd_slow=26, macd_signal=9):
        self.short_period = short_period
        self.long_period  = long_period
        self.macd_fast    = macd_fast
        self.macd_slow    = macd_slow
        self.macd_signal  = macd_signal
        print(f"  预计算趋势层...", end='', flush=True)
        self.trend_df  = calc_dual_trend(df, short_period, long_period)
        print(f"  MACD结构层...", end='', flush=True)
        self.macd_df   = calc_xmm_structure(df, macd_fast, macd_slow, macd_signal)
        print(f"  TD序列...", end='', flush=True)
        self.td_s      = calc_td_seq(df['close'])
        print(f" 完成！")
        self.close_arr = df['close'].values
        self.n = len(df)

    def get(self, index: int) -> Dict:
        """获取指定索引的所有指标（O(1)）"""
        t_last = self.trend_df.iloc[index]
        s_last = self.macd_df.iloc[index]
        c = float(t_last['close'])
        st, sb = float(t_last['短顶']), float(t_last['短底'])
        lt, lb = float(t_last['长顶']), float(t_last['长底'])
        csu = bool(t_last['cross_short_up']); csd = bool(t_last['cross_short_down'])
        clu = bool(t_last['cross_long_up']);  cld = bool(t_last['cross_long_down'])
        if c > st and c > lt:   market = 'UP'
        elif c < sb and c < lb: market = 'DOWN'
        else:                    market = 'SIDEWAYS'
        td_count = int(self.td_s.iloc[index])
        td_amp   = 6 <= td_count <= 9
        mult     = 1.5 if td_amp else 1.0

        bull = float(int(csu) + int(clu))
        bear = float(int(csd) + int(cld))
        breasons, sreasons = [], []
        if csu and clu:  bull += 2.0; breasons.append('趋势双突')
        elif csu:         bull += 1.0; breasons.append('短突')
        elif clu:         bull += 1.0; breasons.append('长突')
        if csd and cld:  bear += 2.0; sreasons.append('趋势双跌')
        elif csd:         bear += 1.0; sreasons.append('短跌')
        elif cld:         bear += 1.0; sreasons.append('长跌')

        def _b(v): return bool(v)
        if _b(s_last['底部结构']):   bull += 3.0*mult; breasons.append('底结构')
        if _b(s_last['底部钝化']):   bull += 1.5*mult; breasons.append('底钝化')
        if _b(s_last['底钝化消失']): bull += 1.0;     breasons.append('底钝消')
        if _b(s_last['底部结构']) and _b(s_last['底再次钝化']): bull += 1.0; breasons.append('底再钝')
        if _b(s_last['顶部结构']):   bear += 3.0*mult; sreasons.append('顶结构')
        if _b(s_last['顶部钝化']):   bear += 1.5*mult; sreasons.append('顶钝化')
        if _b(s_last['顶钝化消失']): bear += 1.0;     sreasons.append('顶钝消')
        if _b(s_last['顶部结构']) and _b(s_last['顶再次钝化']): bear += 1.0; sreasons.append('顶再钝')

        net = bull - bear
        if market == 'SIDEWAYS':
            sig = 'BUY' if net >= 2.0 else ('SELL' if net <= -2.0 else 'HOLD')
            stype = 'trend_sideways'
        else:
            if net >= 2.0:    sig = 'BUY';  stype = 'bull_convergence'
            elif net <= -2.0: sig = 'SELL'; stype = 'bear_convergence'
            else:             sig = 'HOLD'; stype = 'insufficient'

        strength = round(abs(net), 1)
        pos = min(0.15 + strength * 0.08, 1.0) if sig in ('BUY','SELL') else 0.0
        parts = []
        if breasons: parts.append('多:'+'+'.join(breasons))
        if sreasons: parts.append('空:'+'+'.join(sreasons))
        if td_amp: parts.append(f'TD{td_count}x1.5')
        reason = ' | '.join(parts) if parts else '无信号'

        return {
            'signal': sig, 'bull_score': round(bull,2), 'bear_score': round(bear,2),
            'net_score': round(net,2), 'strength': strength,
            'position_size': round(pos,2), 'signal_type': stype, 'reason': reason,
            'market': market, 'td_count': td_count, 'td_amplified': td_amp,
            'close': c, 'index': index,
            'structure': {k: _b(v) for k,v in s_last.items() if k not in ('diff','dea','histogram')},
        }


# ============================================================
# 策略模型（兼容旧接口）
# ============================================================

class XMMModel:
    def __init__(self,
                 short_period: int = 25, long_period: int = 90,
                 macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9,
                 threshold: float = 2.0, name: str = "徐小明三层信号"):
        self.name = name
        self.short_period = short_period; self.long_period = long_period
        self.macd_fast = macd_fast; self.macd_slow = macd_slow
        self.macd_signal = macd_signal; self.threshold = threshold

    def precompute(self, df: pd.DataFrame) -> XMMPrecomputed:
        return XMMPrecomputed(df,
                             self.short_period, self.long_period,
                             self.macd_fast, self.macd_slow, self.macd_signal)

    def analyze(self, df: pd.DataFrame) -> Dict:
        if df is None or len(df) < max(self.long_period + 5, 60):
            return self._default('数据不足')
        pc = self.precompute(df)
        return pc.get(len(df)-1)

    def _default(self, reason: str) -> Dict:
        return {'signal':'HOLD','bull_score':0,'bear_score':0,'net_score':0,
                'strength':0,'position_size':0.0,'signal_type':'none','reason':reason,
                'market':'UNKNOWN','td_count':0,'td_amplified':False,
                'close':0,'date':'N/A','structure':{}}

    def to_json(self) -> str:
        return json.dumps({'name':self.name,'params':{
            'short_period':self.short_period,'long_period':self.long_period,
            'macd_fast':self.macd_fast,'macd_slow':self.macd_slow,
            'macd_signal':self.macd_signal,'threshold':self.threshold,
        }}, ensure_ascii=False, indent=2)
