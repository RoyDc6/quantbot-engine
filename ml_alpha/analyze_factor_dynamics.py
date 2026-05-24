"""
复杂度因子时序特性分析
- 因子稳定性（IC随时间变化）
- 因子衰减（IC半衰期）
- 与缠论因子相关性
- 分层回测
"""

import sys, io
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 加载数据
df = pd.read_csv(r'E:\quant\ml_alpha\complexity_factor_data.csv', parse_dates=['date'])
print(f"数据: {len(df)}条, {df['symbol'].nunique()}只股票, {df['date'].min().date()} ~ {df['date'].max().date()}")

# 强因子列表
strong_factors = ['apen_60', 'hurst_60', 'apen_20', 'entropy_60', 'higuchi_fd_60']

# ── 1. 月度IC稳定性 ─────────────────────────────────────
print("\n" + "="*60)
print("1. 月度IC稳定性分析")
print("="*60)

df['year_month'] = df['date'].dt.to_period('M')
monthly_ic = []

for ym, group in df.groupby('year_month'):
    row = {'month': str(ym)}
    for f in strong_factors:
        from scipy.stats import spearmanr
        valid = group[[f, 'future_5d']].dropna()
        if len(valid) >= 20:
            ic, _ = spearmanr(valid[f], valid['future_5d'])
            row[f] = ic
        else:
            row[f] = np.nan
    monthly_ic.append(row)

monthly_df = pd.DataFrame(monthly_ic)
print("\n月度RankIC均值与标准差:")
for f in strong_factors:
    valid = monthly_df[f].dropna()
    if len(valid) > 0:
        print(f"  {f:15s}: 均值={valid.mean():+.4f}, 标准差={valid.std():.4f}, "
              f"胜率={(valid>0).mean()*100:.1f}%, 月份数={len(valid)}")

# ── 2. IC衰减分析（不同持有期） ─────────────────────────
print("\n" + "="*60)
print("2. IC衰减分析（不同持有期）")
print("="*60)

# 需要重新计算不同持有期的收益
# 这里用已有数据近似：检查不同lag的IC
import tickflow_env  # noqa: auto-inject TICKFLOW_API_KEY
from tickflow import TickFlow
tf = TickFlow() if os.environ.get('TICKFLOW_API_KEY') else TickFlow.free()

# 取几只代表性股票重新计算多期收益
test_symbols = ['00700.HK', '09988.HK', '00941.HK', '00939.HK', '02318.HK']
print("\n测试股票多期收益IC衰减...")

multi_period_data = []
for symbol in test_symbols:
    try:
        kline = tf.klines.get(symbol, period='1d', count=200, as_dataframe=True)
        kline['date'] = pd.to_datetime(kline['trade_date'])
        kline['ret'] = kline['close'].pct_change()
        
        # 计算多期收益
        for horizon in [1, 3, 5, 10, 20]:
            kline[f'future_{horizon}d'] = kline['close'].shift(-horizon) / kline['close'] - 1
        
        # 计算因子
        for i in range(65, len(kline) - 20):
            window = kline['ret'].iloc[:i+1].dropna().values
            if len(window) < 60:
                continue
            
            from complexity_factors import compute_complexity_factors
            factors = compute_complexity_factors(window)
            
            row = {'symbol': symbol, 'date': kline['date'].iloc[i], **factors}
            for h in [1, 3, 5, 10, 20]:
                row[f'future_{h}d'] = kline[f'future_{h}d'].iloc[i]
            multi_period_data.append(row)
    except Exception as e:
        print(f"  {symbol} error: {e}")

if multi_period_data:
    mp_df = pd.DataFrame(multi_period_data)
    print(f"\n多期数据: {len(mp_df)}条")
    
    print("\n不同持有期RankIC:")
    print(f"{'Factor':15s} {'1d':>8s} {'3d':>8s} {'5d':>8s} {'10d':>8s} {'20d':>8s}")
    print("-"*60)
    for f in strong_factors:
        ics = []
        for h in [1, 3, 5, 10, 20]:
            valid = mp_df[[f, f'future_{h}d']].dropna()
            if len(valid) >= 30:
                from scipy.stats import spearmanr
                ic, _ = spearmanr(valid[f], valid[f'future_{h}d'])
                ics.append(f"{ic:+.4f}")
            else:
                ics.append("N/A")
        print(f"{f:15s} {ics[0]:>8s} {ics[1]:>8s} {ics[2]:>8s} {ics[3]:>8s} {ics[4]:>8s}")

# ── 3. 与缠论因子相关性 ─────────────────────────────────
print("\n" + "="*60)
print("3. 与缠论因子相关性分析")
print("="*60)

# 加载缠论特征数据
try:
    chan_df = pd.read_csv(r'E:\quant\scanner2\hk_features.csv', parse_dates=['date'])
    print(f"缠论数据: {len(chan_df)}条")
    
    # 合并
    merged = df.merge(chan_df, on=['symbol', 'date'], how='inner')
    print(f"合并后: {len(merged)}条")
    
    # 缠论主要因子
    chan_factors = ['top_fractal', 'bottom_fractal', 'trend_strength', 
                    'fractal_intensity', 'breakout_signal']
    
    print("\n复杂度因子 vs 缠论因子 相关性矩阵:")
    all_factors = strong_factors + [f for f in chan_factors if f in merged.columns]
    if len(all_factors) > len(strong_factors):
        corr = merged[all_factors].corr()
        print(f"\n{'':15s}", end="")
        for cf in chan_factors:
            if cf in corr.columns:
                print(f"{cf[:10]:>10s}", end=" ")
        print()
        
        for f in strong_factors:
            print(f"{f:15s}", end=" ")
            for cf in chan_factors:
                if cf in corr.columns and f in corr.index:
                    print(f"{corr.loc[f, cf]:+9.3f}", end=" ")
            print()
    else:
        print("缠论因子列名不匹配，可用列:", chan_df.columns.tolist()[:10])
except FileNotFoundError:
    print("缠论特征文件未找到，跳过相关性分析")

# ── 4. 因子分层回测 ─────────────────────────────────────
print("\n" + "="*60)
print("4. 因子分层回测（5层）")
print("="*60)

def quantile_return(df, factor, q=5):
    """分层收益"""
    df = df.copy()
    df['group'] = pd.qcut(df[factor], q, labels=False, duplicates='drop')
    returns = df.groupby('group')['future_5d'].mean()
    return returns

for f in strong_factors[:3]:  # 只看最强的3个
    print(f"\n{f} 分层收益:")
    try:
        rets = quantile_return(df, f, 5)
        spread = rets.iloc[-1] - rets.iloc[0] if len(rets) >= 2 else 0
        print(f"  Q1(低): {rets.iloc[0]*100:+.3f}%  Q5(高): {rets.iloc[-1]*100:+.3f}%  Spread: {spread*100:+.3f}%")
        print(f"  单调性: {'✓' if rets.is_monotonic_increasing or rets.is_monotonic_decreasing else '✗'}")
    except Exception as e:
        print(f"  Error: {e}")

# ── 5. 因子自相关性（稳定性） ───────────────────────────
print("\n" + "="*60)
print("5. 因子自相关性（1期滞后）")
print("="*60)

df_sorted = df.sort_values(['symbol', 'date'])
for f in strong_factors:
    autocorr = []
    for sym, group in df_sorted.groupby('symbol'):
        if len(group) >= 10:
            corr = group[f].autocorr(lag=1)
            if not np.isnan(corr):
                autocorr.append(corr)
    if autocorr:
        print(f"  {f:15s}: 自相关={np.mean(autocorr):+.4f} (越高越稳定)")

print("\n" + "="*60)
print("分析完成")
print("="*60)
