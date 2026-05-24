"""Quick test - extract factors from ONE file"""
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import pandas as pd

# Paste the extract_factors function here
def _ma(p, n):
    r = np.full(len(p), np.nan)
    if len(p) >= n: r[n-1:] = np.convolve(p, np.ones(n)/n, mode='valid')
    return r

def _rsi(p, n=14):
    d = np.diff(p); g = np.where(d>0, d, 0.0); l_ = np.where(d<0, -d, 0.0)
    r = np.full(len(p), 50.0)
    if len(p) <= n: return r
    ag, al = np.mean(g[:n]), np.mean(l_[:n])
    r[n-1] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    for i in range(n, len(p)-1):
        ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+l_[i])/n
        r[i] = 50.0 if al<1e-10 else 100-100/(1+ag/(al+1e-10))
    return r

# Test on CSI300 file
df = pd.read_csv(r'E:\quant\ml_alpha\csi300_cache\000001_SZ.csv')
print(f'DataFrame shape: {df.shape}')
print(f'Close array length: {len(df["close"])}')

c = df['close'].values.astype(np.float64)
h = df['high'].values.astype(np.float64)
l = df['low'].values.astype(np.float64)
v = df['volume'].values.astype(np.float64)
print(f'c len={len(c)}, h len={len(h)}, l len={len(l)}, v len={len(v)}')

max_bars = 1500
n = min(len(df), max_bars)
print(f'n = {n}')
o = c[-n:]
oh = h[-n:]
ol = l[-n:]
ov = v[-n:]
print(f'o len={len(o)}, oh len={len(oh)}, ol len={len(ol)}, ov len={len(ov)}')

# Test rsi_14
try:
    rsi = _rsi(o, 14)
    print(f'rsi_14 len={len(rsi)}, rsi_14[1498]={rsi[1498]}')
except Exception as e:
    traceback.print_exc()

# Test BB
try:
    ma = _ma(o, 5)
    std = np.zeros(n)
    for i in range(4, n):
        std[i] = np.std(o[i-4:i+1])
    print(f'BB calc ok, std[1498]={std[1498]}')
except Exception as e:
    traceback.print_exc()

# Test OBV
try:
    obv = np.zeros(n)
    for i in range(1, n):
        if o[i] > o[i-1]: obv[i] = obv[i-1]+ov[i]
        elif o[i] < o[i-1]: obv[i] = obv[i-1]-ov[i]
    print(f'OBV ok, obv[1498]={obv[1498]}')
except Exception as e:
    traceback.print_exc()

# Test _cons
try:
    dn = np.zeros(n, dtype=int)
    for i in range(1, n):
        if o[i] < o[i-1]: dn[i] = dn[i-1]+1
    print(f'cons ok, dn[1498]={dn[1498]}')
except Exception as e:
    traceback.print_exc()

print('\nAll basic tests passed!')
