import os, sys, warnings
os.chdir(r'E:\quant\fusion_framework')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
print('基础库 OK')

# 加载数据
df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df[df['volume'] > 0].copy()
df = df[df['close'] > 0.5].copy()
print(f'数据: {len(df)}行, {df["symbol"].nunique()}只股票')

# 计算周RSI
print('计算周RSI...')
weekly_list = []
for sym, g in df.groupby('symbol'):
    g = g.sort_values('trade_date')
    close = g['close'].values
    rsi_d = g['rsi_14'].values
    n = len(close)
    wrsi = np.full(n, np.nan)
    weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    weekly_rsi = np.full(len(weekly_close), 50.0)
    if len(weekly_close) >= 15:
        d = np.diff(weekly_close)
        g2 = np.where(d > 0, d, 0.0)
        l2 = np.where(d < 0, -d, 0.0)
        period = 4
        ag, al = np.mean(g2[:period]), np.mean(l2[:period])
        weekly_rsi[period] = 50.0 if al < 1e-10 else 100 - 100 / (1 + ag / (al + 1e-10))
        for i in range(period+1, len(weekly_close)):
            ag = (ag * (period-1) + g2[i-1]) / period
            al = (al * (period-1) + l2[i-1]) / period
            weekly_rsi[i] = 100 - 100 / (1 + ag / (al + 1e-10)) if al > 1e-10 else 100.0
    for i in range(n):
        w = i // 5
        if w < len(weekly_rsi): wrsi[i] = weekly_rsi[w]
    tmp = g.copy()
    tmp['weekly_rsi'] = wrsi
    weekly_list.append(tmp)

df2 = pd.concat(weekly_list, ignore_index=True)
print(f'周RSI完成: {df2["weekly_rsi"].notna().sum()}条有效')

# IC验证
recent_dates = sorted(df2['trade_date'].unique())[-500:]
vals = df2[df2['trade_date'].isin(recent_dates)].dropna(subset=['weekly_rsi', 'future_ret_5d'])
ic, p = spearmanr(vals['weekly_rsi'], vals['future_ret_5d'], nan_policy='omit')
print(f'weekly_rsi IC={ic:.4f} (n={len(vals)})')

# 信号生成测试
date = recent_dates[-10]
today = df2[df2['trade_date'] == date]
print(f'测试日期: {date.date()}, 股票数: {len(today)}')
# 取3只股票测试
for _, row in today.head(3).iterrows():
    sym = row['symbol']
    hist = df2[(df2['symbol'] == sym) & (df2['trade_date'] <= date)].tail(60)
    if len(hist) < 60:
        print(f'  {sym}: 历史不足{len(hist)}天')
        continue
    wrsi = row.get('weekly_rsi', 50.0)
    if np.isnan(wrsi): wrsi = 50.0
    resonance_val = row.get('resonance', 0.0)
    if np.isnan(resonance_val): resonance_val = 0.0
    score = 0.202 * (wrsi - 50) + (-0.152) * resonance_val
    rsi_d = row.get('rsi_14', 50.0)
    macd_h = row.get('macd_hist', 0.0)
    ma20r = row.get('ma20_ratio', 1.0)
    ma60r = row.get('ma60_ratio', 1.0)
    score = 0.202 * (50 - wrsi) + (-0.152) * row.get('resonance', 0.0)
    trend_up = ma20r > 1.0 and ma60r > 1.0
    fm_sig = 'BUY' if score >= 5 else ('SELL' if score <= -5 else 'HOLD')
    above20 = ma20r > 1.0
    above60 = ma60r > 1.0
    macd_pos = macd_h > 0
    if above20 and above60 and macd_pos and rsi_d < 70:
        xmm_sig = 'BUY'
    elif rsi_d >= 78 or (not above20 and rsi_d > 65):
        xmm_sig = 'SELL'
    else:
        xmm_sig = 'HOLD'
    print(f'  {sym}: score={score:.2f} wrsi={wrsi:.1f} rsi_d={rsi_d:.1f} FM={fm_sig} XMM={xmm_sig} trend={trend_up}')

print('测试完成!')
