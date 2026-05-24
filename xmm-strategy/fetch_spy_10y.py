# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['TICKFLOW_API_KEY'] = 'REDACTED_TICKFLOW_KEY_FILE'
import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
import pandas as pd, json

tf = TickFlow(api_key=os.environ['TICKFLOW_API_KEY'])
CACHE = r'E:\quant\scanner\cache'

print("=== 获取 SPY 三周期数据 ===")

# Daily 2500 bars (~10 years)
d = tf.klines.get('SPY.US', period='1d', count=2500)
df_d = pd.DataFrame({
    'trade_date': pd.to_datetime(d['timestamp'], unit='ms'),
    'open': d['open'], 'high': d['high'],
    'low': d['low'], 'close': d['close'],
    'volume': d['volume']
})
df_d = df_d.drop_duplicates('trade_date').sort_values('trade_date').reset_index(drop=True)
df_d['trade_date'] = df_d['trade_date'].dt.strftime('%Y-%m-%d')
print(f"日线: {len(df_d)} 条, {df_d['trade_date'].iloc[0]} ~ {df_d['trade_date'].iloc[-1]}")

# Weekly 520 bars
w = tf.klines.get('SPY.US', period='1w', count=520)
df_w = pd.DataFrame({
    'trade_date': pd.to_datetime(w['timestamp'], unit='ms'),
    'open': w['open'], 'high': w['high'],
    'low': w['low'], 'close': w['close'],
    'volume': w['volume']
})
df_w = df_w.drop_duplicates('trade_date').sort_values('trade_date').reset_index(drop=True)
df_w['trade_date'] = df_w['trade_date'].dt.strftime('%Y-%m-%d')
print(f"周线: {len(df_w)} 条, {df_w['trade_date'].iloc[0]} ~ {df_w['trade_date'].iloc[-1]}")

# Monthly 240 bars (~20 years)
m = tf.klines.get('SPY.US', period='1M', count=240)
df_m = pd.DataFrame({
    'trade_date': pd.to_datetime(m['timestamp'], unit='ms'),
    'open': m['open'], 'high': m['high'],
    'low': m['low'], 'close': m['close'],
    'volume': m['volume']
})
df_m = df_m.drop_duplicates('trade_date').sort_values('trade_date').reset_index(drop=True)
df_m['trade_date'] = df_m['trade_date'].dt.strftime('%Y-%m-%d')
print(f"月线: {len(df_m)} 条, {df_m['trade_date'].iloc[0]} ~ {df_m['trade_date'].iloc[-1]}")

# 保存
df_d.to_json(f'{CACHE}/SPY_daily_2500.json', orient='records', force_ascii=False)
df_w.to_json(f'{CACHE}/SPY_weekly_520.json', orient='records', force_ascii=False)
df_m.to_json(f'{CACHE}/SPY_monthly_240.json', orient='records', force_ascii=False)
print("\n已保存")

# 验证月线能否支持 EMA(25,90)
nm = len(df_m)
print(f"\n月线 {nm} 条 / EMA(25,90): 需 nm//2 = {nm//2}, 需要 {90} -> {'✅ OK' if nm >= 180 else '❌ 数据不足（需 180+ 条）'}")