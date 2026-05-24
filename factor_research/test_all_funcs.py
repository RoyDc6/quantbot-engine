"""Test all factor functions on the actual data"""
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

# Load data
df = pd.read_csv(r'E:\quant\ml_alpha\csi300_cache\000001_SZ.csv')
c = df['close'].values.astype(np.float64)[-1500:]
h = df['high'].values.astype(np.float64)[-1500:]
l = df['low'].values.astype(np.float64)[-1500:]
v = df['volume'].values.astype(np.float64)[-1500:]
n = 1500
print(f'Test data: n={n}')

# Import all helper functions from mining_v4
exec(open(r'E:\quant\factor_research\mining_v4.py', encoding='utf-8').read()
     .split('# ═══════════════════════════════════════════════════════════\n# IC计算')[0]
     .split('\ndef calc_ic(')[0]
     .split('\ndef calc_monthly(')[0])

# Test each function
funcs = [
    ('_ma', lambda: _ma(c, 20)),
    ('_ema', lambda: _ema(c, 12)),
    ('_atr', lambda: _atr(h, l, c, 14)),
    ('_rsi', lambda: _rsi(c, 14)),
    ('_williams_r', lambda: _williams_r(h, l, c, 14)),
    ('_cci', lambda: _cci(h, l, c, 20)),
    ('_cci14', lambda: _cci(h, l, c, 14)),
    ('_cci28', lambda: _cci(h, l, c, 28)),
    ('_adx', lambda: _adx(h, l, c, 14)),
    ('_stoch', lambda: _stoch(h, l, c, 14)),
    ('_obv', lambda: _obv(c, v)),
    ('_mfi', lambda: _mfi(h, l, c, v, 14)),
    ('_cons', lambda: _cons(c)),
]

for name, fn in funcs:
    try:
        result = fn()
        if isinstance(result, tuple):
            print(f'  {name}: OK ({[len(x) for x in result]})')
        else:
            print(f'  {name}: OK (len={len(result)}, last={result[-1]:.4f})')
    except Exception as e:
        print(f'  {name}: ERROR - {e}')
        traceback.print_exc()

# Test full extraction
print('\nFull extraction test...')
ohlcv = {'close': df['close'].values, 'high': df['high'].values, 'low': df['low'].values,
         'volume': df['volume'].values, 'trade_date': df['trade_date'].values}
try:
    feats, dates = extract_factors(ohlcv, max_bars=1500)
    print(f'Full extraction: SUCCESS ({len(feats)} factors, dates len={len(dates)})')
    print(f'fwd_ret_5d non-nan: {np.sum(~np.isnan(feats["fwd_ret_5d"]))}')
    # Try building rows
    rows = []
    for j in range(30, n - 1):
        row = {'symbol': 'TEST', 'trade_date': str(dates[j])[:10] if dates[j] is not None and j < len(dates) else ''}
        for fname, fvals in feats.items():
            if j < len(fvals):
                v = fvals[j]
                if not (np.isnan(v) or np.isinf(v)):
                    row[fname] = v
        rows.append(row)
    print(f'Rows built: {len(rows)}')
    print(f'First row keys: {list(rows[0].keys())[:5]}...')
except Exception as e:
    print(f'Full extraction: ERROR - {e}')
    traceback.print_exc()
