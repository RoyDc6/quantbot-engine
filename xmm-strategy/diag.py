# -*- coding: utf-8 -*-
import sys, io, os, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'E:/quant/xmm-strategy')

import pandas as pd
import numpy as np
from xmm_model import XMMModel, calc_dual_trend, calc_xmm_structure, calc_td_seq
import time

# 加载数据
print("加载数据...")
with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
    raw = json.load(f)
df = pd.DataFrame(raw)
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
df = df.sort_values('datetime').reset_index(drop=True)
df = df.set_index('datetime')
for c in ['open','high','low','close','volume']:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df.dropna()
print(f"数据: {len(df)}条")

# 测试单次analyze
print("测试单次analyze...")
model = XMMModel(short_period=25, long_period=90, threshold=2.0)
t0 = time.time()
sig = model.analyze(df.iloc[:200])
t1 = time.time()
print(f"单次耗时: {t1-t0:.2f}秒")
print(f"信号: {sig['signal']} net={sig['net_score']}")

# 测试多次信号（限时）
print("\n预计算信号（限时30秒）...")
signals = []
t0 = time.time()
for i in range(95, min(200, len(df)-1)):
    sig = model.analyze(df.iloc[:i+1])
    sig['date'] = df.index[i].strftime('%Y-%m-%d')
    signals.append(sig)
    if time.time()-t0 > 10:
        print(f"  10秒超时，仅完成{i-95+1}次")
        break
    if i % 50 == 0:
        print(f"  i={i} 耗时={time.time()-t0:.1f}s")
t2 = time.time()
print(f"完成{len(signals)}次信号计算，耗时{t2-t0:.1f}秒")

buys = sum(1 for s in signals if s['signal']=='BUY')
sells = sum(1 for s in signals if s['signal']=='SELL')
print(f"信号: BUY={buys} SELL={sells}")
