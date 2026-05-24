# -*- coding: utf-8 -*-
"""
诊断：检查底背离是否被正确识别
"""
import json
import sys
import io
import pandas as pd
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'E:\quant\xmm-strategy')

from modules.structure import calc_xmm_structure
from modules.trend import detect_trend

with open(r'E:\quant\scanner\cache\SPY_US.json', 'r') as f:
    raw = json.load(f)
df = pd.DataFrame(raw)
df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
df = df[['trade_date', 'open', 'high', 'low', 'close', 'volume']].sort_values('trade_date').reset_index(drop=True)
df.set_index('trade_date', inplace=True)

trend = detect_trend(df)
res = calc_xmm_structure(df)

print("=" * 100)
print(f"{'日期':<12}{'收盘':>8}{'趋势':<10}{'DIF':>8}{'MACD':>8}{'直接底':>6}{'隔峰底':>6}{'底部钝化':>8}{'底部结构':>8}{'底消失':>6}")
print("-" * 100)

for i in range(len(res)):
    date = res.index[i]
    close = res['close'].iloc[i]
    d = res['diff'].iloc[i]
    m = res['macd'].iloc[i]
    t = trend.iloc[i]
    dd = '✅' if res['直接底钝化'].iloc[i] else ''
    gd = '✅' if res['隔峰底钝化'].iloc[i] else ''
    bd = '✅' if res['底部钝化'].iloc[i] else ''
    bs = '⭐' if res['底部结构'].iloc[i] else ''
    bx = '❌' if res['底消失'].iloc[i] else ''
    print(f"{date:<12}{close:>8.2f}{t:<10}{d:>8.3f}{m:>8.3f}{dd:>6}{gd:>6}{bd:>8}{bs:>8}{bx:>6}")
