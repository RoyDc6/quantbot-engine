# check_price.py
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import numpy as np

with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
    raw = json.load(f)
df = pd.DataFrame(raw)
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
df = df.sort_values('datetime').set_index('datetime')
for c in ['open','high','low','close','volume']:
    df[c] = pd.to_numeric(df[c], errors='coerce')

# 2025年3-5月价格走势
sub = df.loc['2025-03-01':'2025-05-31']
print('2025年3-5月 SPY 价格走势:')
print(f'  3月初: ${sub["close"].iloc[0]:.2f}')
print(f'  4月最低: ${sub["low"].min():.2f} ({sub["low"].idxmin().date()})')
print(f'  5月底: ${sub.loc["2025-05-31", "close"] if "2025-05-31" in sub.index else sub["close"].iloc[-1]:.2f}')

# 全年最低点
print(f'\n全年最低: ${df["low"].min():.2f} ({df["low"].idxmin().date()})')
print(f'全年最高: ${df["high"].max():.2f} ({df["high"].idxmax().date()})')
