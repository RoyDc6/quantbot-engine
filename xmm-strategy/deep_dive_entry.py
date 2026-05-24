# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd, numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

CACHE = r'E:\quant\scanner\cache'
with open(f'{CACHE}/SPY_daily_2500.json', 'r', encoding='utf-8') as f:
    df = pd.DataFrame(json.load(f))
df['trade_date'] = pd.to_datetime(df['trade_date'])
df.set_index('trade_date', inplace=True)
df.sort_index(inplace=True)

pc = XMMSonnetPrecomputed(df, verbose=False)
WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)

# Deep dive on entry conditions
buy_with_limit = []
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    if sig['signal'] == 'BUY':
        buy_with_limit.append({
            'date': df.index[i].date(),
            'close': sig['close'],
            'limit': sig['position_limit'],
            'm_trend': sig['monthly_trend'],
            'w_trend': sig['weekly_trend'],
            'd_trend': sig['daily_trend'],
            'd_low_n': sig['d_low_n'],
            'vxx': sig['vxx_status'],
        })

print(f"BUY信号总数: {len(buy_with_limit)}")
print(f"limit >= 0.05: {sum(1 for x in buy_with_limit if x['limit'] >= 0.05)}")
print(f"limit >= 0.10: {sum(1 for x in buy_with_limit if x['limit'] >= 0.10)}")

# 周线分布
w_trend_dist = {}
for x in buy_with_limit:
    w = x['w_trend']
    w_trend_dist[w] = w_trend_dist.get(w, 0) + 1
print("\nBUY信号时的周线状态分布:", w_trend_dist)

# 看 limit 为什么这么低：分析周线状态
w_status_limit = {}
for x in buy_with_limit:
    w = x['w_trend']
    if w not in w_status_limit:
        w_status_limit[w] = []
    w_status_limit[w].append(x['limit'])

for w, limits in sorted(w_status_limit.items()):
    arr = np.array(limits)
    print(f"  周线={w}: count={len(arr)}, avg_limit={arr.mean():.3f}, >=0.05: {sum(arr>=0.05)}")

# 分析 REDUCE 和 EXIT 的触发原因
print("\n=== REDUCE / EXIT 分析 ===")
for i in range(WARMUP, len(df)):
    sig = pc.get(i)
    if sig['signal'] in ('EXIT', 'REDUCE'):
        print(f"  {df.index[i].date()} {sig['signal']}: limit={sig['position_limit']:.3f}, "
              f"m={sig['monthly_trend']}, w={sig['weekly_trend']}, d={sig['daily_trend']}, "
              f"d_ld={sig['d_low_n']}, vxx={sig['vxx_status']}")