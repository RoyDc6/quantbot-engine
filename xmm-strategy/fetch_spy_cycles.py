# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['TICKFLOW_API_KEY'] = 'REDACTED_TICKFLOW_KEY_FILE'
import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
import pandas as pd

tf = TickFlow(api_key=os.environ['TICKFLOW_API_KEY'])
print("=== 获取 SPY 三周期数据 ===")

# Daily 600 bars
d = tf.klines.get('SPY.US', period='1d', count=600)
df_d = pd.DataFrame({
    'trade_date': pd.to_datetime(d['timestamp'], unit='ms'),
    'open': d['open'], 'high': d['high'],
    'low': d['low'], 'close': d['close'],
    'volume': d['volume']
})
df_d['trade_date'] = df_d['trade_date'].dt.strftime('%Y-%m-%d')
df_d = df_d.sort_values('trade_date').reset_index(drop=True)
print(f"日线: {len(df_d)} 条, {df_d['trade_date'].iloc[0]} ~ {df_d['trade_date'].iloc[-1]}")

# Weekly 500 bars
w = tf.klines.get('SPY.US', period='1w', count=500)
df_w = pd.DataFrame({
    'trade_date': pd.to_datetime(w['timestamp'], unit='ms'),
    'open': w['open'], 'high': w['high'],
    'low': w['low'], 'close': w['close'],
    'volume': w['volume']
})
df_w['trade_date'] = df_w['trade_date'].dt.strftime('%Y-%m-%d')
df_w = df_w.sort_values('trade_date').reset_index(drop=True)
print(f"周线: {len(df_w)} 条, {df_w['trade_date'].iloc[0]} ~ {df_w['trade_date'].iloc[-1]}")

# Monthly 120 bars
m = tf.klines.get('SPY.US', period='1M', count=120)
df_m = pd.DataFrame({
    'trade_date': pd.to_datetime(m['timestamp'], unit='ms'),
    'open': m['open'], 'high': m['high'],
    'low': m['low'], 'close': m['close'],
    'volume': m['volume']
})
df_m['trade_date'] = df_m['trade_date'].dt.strftime('%Y-%m-%d')
df_m = df_m.sort_values('trade_date').reset_index(drop=True)
print(f"月线: {len(df_m)} 条, {df_m['trade_date'].iloc[0]} ~ {df_m['trade_date'].iloc[-1]}")

# 保存
cache = r'E:\quant\scanner\cache'
df_d.to_json(f'{cache}/SPY_daily_600.json', orient='records', force_ascii=False)
df_w.to_json(f'{cache}/SPY_weekly_500.json', orient='records', force_ascii=False)
df_m.to_json(f'{cache}/SPY_monthly_120.json', orient='records', force_ascii=False)
print("\n已保存到 cache 目录")