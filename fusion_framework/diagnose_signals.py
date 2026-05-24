"""诊断：为何低换手版没有信号"""
import pandas as pd, numpy as np, warnings
warnings.filterwarnings('ignore')

df = pd.read_csv('E:/quant/ml_alpha/hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df = df[df['volume']>0].copy(); df = df[df['close']>0.5].copy()
df = df.sort_values(['symbol','trade_date']).reset_index(drop=True)

# ── 用原始方法计算 weekly_rsi ──────────────────────────────────
print("=== weekly_rsi 计算 ===")
df['weekly_rsi'] = 50.0
for sym, g in df.groupby('symbol'):
    g = g.sort_values('trade_date').reset_index(drop=True)
    close = g['close'].values; n = len(close)
    wrsi = np.full(n, 50.0)
    weekly_close = [close[i*5+4] for i in range(n//5) if i*5+4 < n]
    if len(weekly_close) >= 15:
        wrsi_arr = np.full(len(weekly_close), 50.0)
        d = np.diff(weekly_close)
        g2 = np.where(d > 0, d, 0.0); l2 = np.where(d < 0, -d, 0.0)
        period = 4
        ag, al = np.mean(g2[:period]), np.mean(l2[:period])
        wrsi_arr[period] = 50.0 if al < 1e-10 else 100 - 100/(1+ag/(al+1e-10))
        for i in range(period+1, len(weekly_close)):
            ag = (ag*(period-1)+g2[i-1])/period
            al = (al*(period-1)+l2[i-1])/period
            wrsi_arr[i] = 100-100/(1+ag/(al+1e-10)) if al>1e-10 else 100.0
        # 原始映射
        for i in range(n):
            w = i // 5
            if w < len(wrsi_arr): wrsi[i] = wrsi_arr[w]
    df.loc[g.index, 'weekly_rsi'] = wrsi

recent = df[df['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
from scipy.stats import spearmanr
ic_w, p_w = spearmanr(recent['weekly_rsi'], recent['future_ret_5d'])
print(f"weekly_rsi IC={ic_w:.4f} (n={len(recent)})")

# ── 看一个具体股票 ─────────────────────────────────────────────
sym = '00700.HK'
g = df[df['symbol']==sym].tail(30)
wrsi_vals = g['weekly_rsi'].values
ret5 = g['future_ret_5d'].values
close_vals = g['close'].values
dates = g['trade_date'].values
for i in range(len(g)):
    print(f"  {dates[i]} close={close_vals[i]:.2f} wrsi={wrsi_vals[i]:.1f} ret5={ret5[i]:.4f}" if not np.isnan(ret5[i]) else f"  {dates[i]} close={close_vals[i]:.2f} wrsi={wrsi_vals[i]:.1f} ret5=NaN")

# ── 信号模拟 ─────────────────────────────────────────────────
print("\n=== 信号模拟（最近10天）===")
recent_dates = sorted(df['trade_date'].unique())[-10:]
for date in recent_dates:
    today = df[df['trade_date']==date]
    buy_signals = []
    for _, row in today.iterrows():
        sym2 = row['symbol']
        hist = df[(df['symbol']==sym2)&(df['trade_date']<=date)].tail(60)
        if len(hist)<60: continue
        wrsi = float(row.get('weekly_rsi', 50.0))
        rsi_d = float(row.get('rsi_14', 50.0))
        macd_h = float(row.get('macd_hist', 0.0))
        ma20r = float(row.get('ma20_ratio', 1.0))
        ma60r = float(row.get('ma60_ratio', 1.0))
        res = float(row.get('resonance', 0.0))
        for v in [wrsi, rsi_d, macd_h, ma20r, ma60r, res]:
            if np.isnan(v): v = 0.0
        score = 0.202*(wrsi-50) + (-0.152)*res
        trend_up = bool(ma20r>1.0) and bool(ma60r>1.0)
        above20 = bool(ma20r>1.0); above60 = bool(ma60r>1.0)
        macd_pos = bool(macd_h>0)
        if above20 and above60 and macd_pos and rsi_d<70: ta='BUY'
        elif rsi_d>=78 or (not above20 and rsi_d>65): ta='SELL'
        else: ta='HOLD'
        fm = 'BUY' if score>=3 else ('SELL' if score<=-3 else 'HOLD')
        if trend_up:
            mat={('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'BUY',
                 ('HOLD','BUY'):'BUY',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'HOLD',
                 ('SELL','BUY'):'BUY',('SELL','HOLD'):'HOLD',('SELL','SELL'):'SELL'}
        else:
            mat={('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'REDUCED',
                 ('HOLD','BUY'):'HOLD',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'SELL',
                 ('SELL','BUY'):'REDUCED',('SELL','HOLD'):'SELL',('SELL','SELL'):'SELL'}
        level = mat.get((fm,ta),'HOLD')
        if level in ['BUY','STRONG_BUY']:
            buy_signals.append((sym2, score, wrsi, ta, level))
    buy_signals.sort(key=lambda x: -x[1])
    print(f"  {date}: BUY={len(buy_signals)}只", end="")
    if buy_signals:
        for s2,s,w,t,l in buy_signals[:3]:
            print(f" {s2}(sc={s:.1f},wrsi={w:.0f},ta={t})", end="")
    print()
