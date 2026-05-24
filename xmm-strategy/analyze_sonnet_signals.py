# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json, pandas as pd, numpy as np
sys.path.insert(0, r'E:\quant\xmm-strategy')
from xmm_sonnet_model import XMMSonnetPrecomputed, TREND_LONG, TD_COMPLETE, TD_REF

CACHE = r'E:\quant\scanner\cache'

with open(f'{CACHE}/SPY_daily_2500.json', 'r', encoding='utf-8') as f:
    df_daily = pd.DataFrame(json.load(f))
df_daily['trade_date'] = pd.to_datetime(df_daily['trade_date'])
df_daily.set_index('trade_date', inplace=True)
df_daily.sort_index(inplace=True)

pc = XMMSonnetPrecomputed(df_daily, verbose=False)
WARMUP = max(TREND_LONG, TD_COMPLETE + TD_REF)

# Signal distribution
from collections import Counter
sig_counter = Counter()
limit_dist = []
monthly_status = Counter()
buy_count = 0

for i in range(WARMUP, len(df_daily)):
    sig = pc.get(i)
    sig_counter[sig['signal']] += 1
    limit_dist.append(sig['position_limit'])
    monthly_status[sig['monthly_trend']] += 1
    if sig['signal'] == 'BUY':
        buy_count += 1

print("=== 信号分布 ===")
print(f"总天数: {len(df_daily) - WARMUP}")
print(f"BUY次数: {buy_count}")
print(dict(sig_counter))
print("\n=== 月线状态分布 ===")
print(dict(monthly_status))
print("\n=== position_limit 统计 ===")
arr = np.array(limit_dist)
print(f"均值: {arr.mean():.3f}, 中位数: {np.median(arr):.3f}")
print(f">=0.05: {(arr>=0.05).sum()}, >=0.10: {(arr>=0.10).sum()}, >=0.30: {(arr>=0.30).sum()}")
print(f">=0.50: {(arr>=0.50).sum()}, >=0.70: {(arr>=0.70).sum()}")

# Benchmark
entry_bench = float(df_daily['close'].iloc[WARMUP])
exit_bench = float(df_daily['close'].iloc[-1])
bench_ret = (exit_bench - entry_bench) / entry_bench * 100
print(f"\n基准: {entry_bench:.2f} → {exit_bench:.2f} = +{bench_ret:.1f}%")