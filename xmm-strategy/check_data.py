# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os, json, pandas as pd

# 检查 CSI300 成分股列表
csi = 'E:/quant/ml_alpha/csi300_cache/csi300_list.json'
df = pd.read_json(csi, convert_dates=False)
print(f"CSI300列表: {len(df)}条")
print(f"列名: {list(df.columns)}")
print(df.head(5).to_string())

# 检查 SPY_1y 时间戳格式
spy = 'E:/quant/scanner/cache/SPY_US_1y.json'
with open(spy, 'r') as f:
    raw = json.load(f)
print(f"\nSPY_1y: {len(raw)}条")
print(f"第一条: {raw[0]}")
print(f"最后一条: {raw[-1]}")
