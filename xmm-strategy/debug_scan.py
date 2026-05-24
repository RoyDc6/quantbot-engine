# -*- coding: utf-8 -*-
"""不同阈值下的信号对比"""
import sys, io, json, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
from collections import Counter
from modules.engine import XMMStrategy

with open('E:/quant/scanner/cache/SPY_US.json', 'r', encoding='utf-8') as f:
    raw = json.load(f)
df = pd.DataFrame(raw)
df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
df = df[['trade_date','open','high','low','close','volume']].sort_values('trade_date').reset_index(drop=True)
df.set_index('trade_date', inplace=True)

def run_scan(threshold):
    engine = XMMStrategy(short_period=25, long_period=90, threshold=threshold)
    results = []
    for i in range(60, len(df)):
        w = df.iloc[:i+1].copy()
        try:
            sig = engine.analyze(w)
            sig['date'] = df.index[i]
            results.append(sig)
        except:
            break
    return results

print("=" * 100)
for th in [1.0, 1.5, 2.0]:
    r = run_scan(th)
    s = Counter(rr['signal'] for rr in r)
    key = [rr for rr in r if rr['signal'] in ('BUY','SELL')]
    print(f"\n阈值={th}: BUY={s['BUY']} SELL={s['SELL']} HOLD={s['HOLD']}")
    for kk in key:
        tl = kk.get('trend_layer', {})
        sl = kk.get('structure_layer', {})
        struct = [x for x in sl if sl[x] and x not in ('diff','dea','macd')]
        tcross = []
        if tl.get('cross_short_up'): tcross.append('短突')
        if tl.get('cross_short_down'): tcross.append('短跌')
        if tl.get('cross_long_up'): tcross.append('长突')
        if tl.get('cross_long_down'): tcross.append('长跌')
        print(f"  {kk['date']} {kk['signal']} 净分={kk['net_score']:+.1f} 趋势={tcross or '无'} 结构={struct or '无'} TD={kk['td_count']}×{kk['td_amplified']}")
