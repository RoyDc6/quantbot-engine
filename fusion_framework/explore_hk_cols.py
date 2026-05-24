import pandas as pd, os

path = 'E:/quant/ml_alpha/hk_features_full.csv'
df = pd.read_csv(path)
print(f'全部列名（66个）:')
for i, c in enumerate(df.columns):
    print(f'  {i+1:>2}. {c}')
print()
# 看数据分布
print(f'股票列表（前20）: {sorted(df["symbol"].unique())[:20]}')
print(f'总股票数: {df["symbol"].nunique()}')
# 看最近数据
recent = df[df['trade_date'] > '2026-01-01'].sort_values('trade_date')
print(f'\n最近数据: {recent["trade_date"].min()} ~ {recent["trade_date"].max()}')
print(f'最近行数: {len(recent)}')
# 检查因子完整性
sample = df[df['symbol'] == 'HK.00700'].tail(10)
print(f'\n00700最近10行关键因子:')
key = ['trade_date','close','rsi_14','macd','macd_hist','fractal_top_5','fractal_bottom_5']
avail = [c for c in key if c in df.columns]
print(sample[avail].to_string())
