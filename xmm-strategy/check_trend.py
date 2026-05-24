import json, sys, io, pandas as pd, numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'E:\quant\xmm-strategy')

from modules.trend import detect_trend

with open(r'E:\quant\scanner\cache\SPY_US.json', 'r') as f:
    raw = json.load(f)
df = pd.DataFrame(raw)
df['trade_date'] = pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d')
df = df[['trade_date', 'open', 'high', 'low', 'close', 'volume']].sort_values('trade_date').reset_index(drop=True)
df.set_index('trade_date', inplace=True)

trend = detect_trend(df)
close = df['close']
ma20 = close.rolling(20, min_periods=5).mean()
ma60 = close.rolling(60, min_periods=10).mean()
ma20_slope = ma20 - ma20.shift(5)

print("日期          收盘     MA20      MA60      slope20  趋势")
print("-" * 70)
for i in range(len(df)):
    if df.index[i] >= '2026-03-01':
        t = trend.iloc[i]
        c = close.iloc[i]
        m20 = ma20.iloc[i]
        m60 = ma60.iloc[i]
        s20 = ma20_slope.iloc[i]
        up_cond = (c > m20) and (m20 > m60) and (s20 > 0)
        print(f"{df.index[i]}  {c:7.2f}  {m20:7.2f}  {m60:7.2f}  {s20:6.2f}  {t}  up_cond={up_cond}")
