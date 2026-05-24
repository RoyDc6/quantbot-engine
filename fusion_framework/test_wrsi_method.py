"""诊断 weekly_rsi 计算"""
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')

df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df[df['volume']>0].copy(); df = df[df['close']>0.5].copy()
df = df.sort_values(['symbol','trade_date']).reset_index(drop=True)

# 方法A：逐股for循环 + pd.concat（复刻原始成功脚本）
print("方法A (for+concat)...")
weekly_list_A = []
for sym, g in df.groupby('symbol'):
    g = g.sort_values('trade_date').reset_index(drop=True)
    close = g['close'].values; n = len(close)
    wrsi = np.full(n, 50.0)
    weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    if len(weekly_close) >= 15:
        d = np.diff(weekly_close)
        g2 = np.where(d > 0, d, 0.0)
        l2 = np.where(d < 0, -d, 0.0)
        period = 4
        ag, al = np.mean(g2[:period]), np.mean(l2[:period])
        weekly_rsi_arr = np.full(len(weekly_close), 50.0)
        weekly_rsi_arr[period] = 50.0 if al < 1e-10 else 100 - 100/(1+ag/(al+1e-10))
        for i in range(period+1, len(weekly_close)):
            ag = (ag*(period-1)+g2[i-1])/period
            al = (al*(period-1)+l2[i-1])/period
            weekly_rsi_arr[i] = 100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
        for i in range(n):
            w = i // 5
            if w < len(weekly_rsi_arr): wrsi[i] = weekly_rsi_arr[w]
    tmp = g.copy()
    tmp['weekly_rsi'] = wrsi
    weekly_list_A.append(tmp)
dfA = pd.concat(weekly_list_A, ignore_index=True)

# 方法B：先添加列，再逐股修改
print("方法B (df['wrsi']=50; df.loc[g.index])...")
df['weekly_rsi'] = 50.0
for sym, g in df.groupby('symbol'):
    g = g.sort_values('trade_date').reset_index(drop=True)
    close = g['close'].values; n = len(close)
    wrsi = np.full(n, 50.0)
    weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    if len(weekly_close) >= 15:
        d = np.diff(weekly_close)
        g2 = np.where(d > 0, d, 0.0)
        l2 = np.where(d < 0, -d, 0.0)
        period = 4
        ag, al = np.mean(g2[:period]), np.mean(l2[:period])
        weekly_rsi_arr = np.full(len(weekly_close), 50.0)
        weekly_rsi_arr[period] = 50.0 if al < 1e-10 else 100 - 100/(1+ag/(al+1e-10))
        for i in range(period+1, len(weekly_close)):
            ag = (ag*(period-1)+g2[i-1])/period
            al = (al*(period-1)+l2[i-1])/period
            weekly_rsi_arr[i] = 100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
        for i in range(n):
            w = i // 5
            if w < len(weekly_rsi_arr): wrsi[i] = weekly_rsi_arr[w]
    # 方法B1: 用 df.loc[g.index, 'weekly_rsi']
    for orig_idx in g.index:
        row_idx = list(g.index).index(orig_idx)
        df.loc[orig_idx, 'weekly_rsi'] = wrsi[row_idx]

df['future_ret_5d'] = df.groupby('symbol')['close'].pct_change(5)
dfA['future_ret_5d'] = dfA.groupby('symbol')['close'].pct_change(5)

from scipy.stats import spearmanr
recentA = dfA[dfA['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
recentB = df[df['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
icA, pA = spearmanr(recentA['weekly_rsi'], recentA['future_ret_5d'])
icB, pB = spearmanr(recentB['weekly_rsi'], recentB['future_ret_5d'])
print(f"方法A IC={icA:.4f} (n={len(recentA)})")
print(f"方法B IC={icB:.4f} (n={len(recentB)})")

# 检查00700
g007A = dfA[dfA['symbol']=='00700.HK'].tail(10)
g007B = df[df['symbol']=='00700.HK'].tail(10)
print("\n方法A 00700 tail10 wrsi:")
print(g007A['weekly_rsi'].values)
print("方法B 00700 tail10 wrsi:")
print(g007B['weekly_rsi'].values)
