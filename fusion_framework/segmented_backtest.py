# -*- coding: utf-8 -*-
import sys, io, os, json, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, 'E:/quant'); sys.path.insert(0, 'E:/quant/fusion_framework')
from signal_types import FusionModelSignal, XMMSignal

# Load SPY
df = pd.read_json('E:/quant/scanner/cache/SPY_daily_2500.json').sort_values('trade_date').reset_index(drop=True)
print(f"SPY: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]} ({len(df)} days)")

# Indicators
def compute_rsi(close, n=14):
    d = np.diff(close); g = np.where(d>0,d,0.0); l = np.where(d<0,-d,0.0)
    r = np.full(len(close), 50.0)
    if len(close) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l[:n])
    r[n] = 50.0 if al < 1e-10 else 100 - 100/(1+ag/(al+1e-10))
    for i in range(n+1, len(close)):
        ag = (ag*(n-1)+g[i-1])/n; al = (al*(n-1)+l[i-1])/n
        r[i] = 50.0 if al < 1e-10 else 100 - 100/(1+ag/(al+1e-10))
    return r

def compute_weekly_rsi(close, period=14):
    weekly = [close[i*5+4] for i in range(len(close)//5) if i*5+4 < len(close)]
    wrsi = compute_rsi(np.array(weekly), period)
    result = np.full(len(close), 50.0)
    for i in range(len(close)):
        w = i // 5
        result[i] = wrsi[w] if w < len(wrsi) else wrsi[-1]
    return result

def compute_sma(close, n):
    s = np.full(len(close), np.nan)
    for i in range(n-1, len(close)): s[i] = np.mean(close[i-n+1:i+1])
    return s

def compute_macd(close):
    s = pd.Series(close)
    ef = s.ewm(span=12, adjust=False).mean().values
    es = s.ewm(span=26, adjust=False).mean().values
    macd = ef - es
    sig = pd.Series(macd).ewm(span=9, adjust=False).mean().values
    return macd - sig

# Pre-compute signals for entire dataset
close = df['close'].values.astype(float)
dts = pd.to_datetime(df['trade_date'])
rsi_d = compute_rsi(close, 14)
rsi_w = compute_weekly_rsi(close, 14)
sma20 = compute_sma(close, 20)
sma50 = compute_sma(close, 50)
mh = compute_macd(close)

fm_acts = []; xmm_acts = []
for i in range(len(close)):
    c = close[i]; w = rsi_w[i]; d = rsi_d[i]
    above20 = not np.isnan(sma20[i]) and c > sma20[i]
    above50 = not np.isnan(sma50[i]) and c > sma50[i]
    mp = mh[i] > 0
    res = 0.0
    if d < 30: res += 1
    if d > 70: res -= 1
    if w < 30: res += 0.5
    if w > 70: res -= 0.5
    score = 0.202*(50-w) + 0.152*res*50
    thr = 3 if above20 else 8
    fm_acts.append('BUY' if score >= thr else ('SELL' if score <= -thr else 'HOLD'))
    if above20 and above50 and mp: xmm_acts.append('BUY')
    elif not above20 or d >= 75: xmm_acts.append('SELL')
    else: xmm_acts.append('HOLD')

# Segments
segments = [
    ("2016-2017 Bull",     "2016-06-01", "2017-12-31"),
    ("2018 VIX+Corr",      "2018-01-01", "2018-12-31"),
    ("2019 Rebound",       "2019-01-01", "2019-12-31"),
    ("2020 COVID Crash",   "2020-01-01", "2020-12-31"),
    ("2021 LowVol Bull",   "2021-01-01", "2021-12-31"),
    ("2022 Bear Market",   "2022-01-01", "2022-12-31"),
    ("2023 AI Bull",       "2023-01-01", "2023-12-31"),
    ("2024-2026 Now",      "2024-01-01", "2026-04-22"),
]

c_rate, slip = 0.001, 0.0005

print()
print("=" * 80)
print("FUSION v3 SEGMENTED BACKTEST: FM(BUY) entry, FM(SELL) exit, XMM(+10d) safety")
print("=" * 80)
hdr = "{:<22} {:>8} {:>7} {:>8} {:>7} {:>8} {:>8}".format(
    "Period", "BH", "Trades", "Fusion", "Sharpe", "MaxDD", "Alpha")
print(hdr)
print("-" * 72)

for label, start, end in segments:
    mask = (dts >= start) & (dts <= end)
    idx = np.where(mask)[0]
    if len(idx) < 30:
        continue
    seg_close = close[idx]
    bh = (seg_close[-1]/seg_close[0] - 1) * 100

    capital = 100000.0; pos = 0; entry_price = 0.0; entry_i = 0; nt = 0
    values = []
    for j, ii in enumerate(idx):
        i = ii; c = close[i]
        days_held = j - entry_i if pos > 0 else 0
        fm_act = fm_acts[i]; xmm_act = xmm_acts[i]
        if pos == 0:
            if fm_act == 'BUY':
                shares = int(capital * 0.6 / c)
                if shares > 0:
                    capital -= shares * c * (1 + c_rate + slip)
                    pos = shares; entry_price = c; entry_i = j; nt += 1
        else:
            fm_exit = fm_act == 'SELL'
            xmm_safety = xmm_act == 'SELL' and days_held > 10
            if fm_exit or xmm_safety:
                capital += pos * c * (1 - c_rate - slip)
                pos = 0; nt += 1
        values.append(capital + pos * c)

    if not values: continue
    ret = (values[-1]/100000 - 1) * 100
    alpha = ret - bh
    vals = np.array(values)
    rets = np.diff(vals)/vals[:-1]; rets = rets[np.isfinite(rets)]
    sh = np.mean(rets)/np.std(rets)*np.sqrt(252) if len(rets)>1 and np.std(rets)>1e-10 else 0
    peak = np.maximum.accumulate(vals)
    dd = np.min((vals - peak)/(peak + 1e-10)) * 100

    print("{:<22} {:>+7.1f}% {:>7} {:>+7.1f}% {:>+6.2f} {:>+7.1f}% {:>+7.1f}%".format(
        label, bh, nt, ret, sh, dd, alpha))

print("-" * 72)
print("Alpha = Fusion return minus Buy&Hold. Positive = beating SPY.")
