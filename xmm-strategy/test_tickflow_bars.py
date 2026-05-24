# -*- coding: utf-8 -*-
import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['TICKFLOW_API_KEY'] = 'REDACTED_NAMED_CREDENTIAL'
import tickflow

client = tickflow.Client()

# Test 10y monthly SPY
print("=== 测试月线数据 ===")
df_m = client.bars('SPY.US', period='10y', interval='1month')
print(f"月线: {len(df_m)} 条")
print(f"时间: {df_m.iloc[0]['time'][:10]} ~ {df_m.iloc[-1]['time'][:10]}")
print(df_m.tail(3)[['time','close']].to_string())

print("\n=== 测试周线数据 ===")
df_w = client.bars('SPY.US', period='3y', interval='1week')
print(f"周线: {len(df_w)} 条")
print(f"时间: {df_w.iloc[0]['time'][:10]} ~ {df_w.iloc[-1]['time'][:10]}")

print("\n=== 测试日线数据 ===")
df_d = client.bars('SPY.US', period='2y', interval='1day')
print(f"日线: {len(df_d)} 条")
print(f"时间: {df_d.iloc[0]['time'][:10]} ~ {df_d.iloc[-1]['time'][:10]}")