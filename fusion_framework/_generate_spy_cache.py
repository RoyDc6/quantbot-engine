"""临时脚本：从 FutuProvider 获取 SPY 数据并缓存为 JSON"""
import sys, json, os
sys.path.insert(0, 'E:/quant')

from research.data.futu_provider import FutuProvider
import pandas as pd

# 获取 600 根日 K 线（约 数据（约 2.5年，可覆盖 500 交易日）
futu = FutuProvider()
kdata = futu.fetch_klines("SPY", 600, "1d")

df = kdata.df.copy()
df['trade_date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
df = df.sort_values('trade_date').reset_index(drop=True)

print(f"获取数据: {len(df)} bars, {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
print(f"价格范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

# 保存到缓存目录
out_path = "E:/quant/scanner/cache/SPY_US.json"
os.makedirs(os.path.dirname(out_path), exist_ok=True)
df.to_json(out_path, orient='records', date_format='epoch', indent=2)
print(f"已保存: {out_path}")