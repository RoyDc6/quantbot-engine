# check_timing.py
# 检查入场时机

import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, 'E:/quant/xmm-strategy')

import pandas as pd
from xmm_sonnet_model import XMMSonnetModel

def load_spy():
    with open('E:/quant/scanner/cache/SPY_US_1y.json', 'r') as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.sort_values('datetime').set_index('datetime')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna()

df = load_spy()
model = XMMSonnetModel()
pc = model.precompute(df, verbose=False)

# 检查 2024-09-09 附近的信号
print('=' * 100)
print('2024-09-09 附近信号（趋势跟随入场点）')
print('=' * 100)

for i in range(len(df)):
    if df.index[i] >= pd.Timestamp('2024-09-01') and df.index[i] <= pd.Timestamp('2024-09-15'):
        sig = pc.get(i)
        dt = df.index[i].strftime('%Y-%m-%d')
        print(f'{dt}  信号:{sig["signal"]:<8} 趋势:{sig["daily_trend"]:<12} 月:{sig["monthly_trend"]:<12} 周:{sig["weekly_trend"]:<12} 原因:{sig["layer2"]}')

print('\n' + '=' * 100)
print('2025-04-04 附近信号（EXIT点）')
print('=' * 100)

for i in range(len(df)):
    if df.index[i] >= pd.Timestamp('2025-03-25') and df.index[i] <= pd.Timestamp('2025-04-15'):
        sig = pc.get(i)
        dt = df.index[i].strftime('%Y-%m-%d')
        print(f'{dt}  信号:{sig["signal"]:<8} 趋势:{sig["daily_trend"]:<12} 月:{sig["monthly_trend"]:<12} 周:{sig["weekly_trend"]:<12} 原因:{sig["layer2"]}')
