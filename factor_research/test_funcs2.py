"""Test all factor functions - no stdout manipulation"""
import numpy as np
import pandas as pd
import traceback

# Load data
df = pd.read_csv(r'E:\quant\ml_alpha\csi300_cache\000001_SZ.csv')
c = df['close'].values.astype(np.float64)[-1500:]
h = df['high'].values.astype(np.float64)[-1500:]
l = df['low'].values.astype(np.float64)[-1500:]
v = df['volume'].values.astype(np.float64)[-1500:]
n = 1500
print(f'Test data: n={n}')

# Import from mining_v4
src = open(r'E:\quant\factor_research\mining_v4.py', encoding='utf-8').read()
helper_end = src.find('\ndef calc_ic(')
exec(src[:helper_end])

funcs = [
    ('_ma(20)', lambda: _ma(c, 20)),
    ('_rsi(14)', lambda: _rsi(c, 14)),
    ('_cci(14)', lambda: _cci(h, l, c, 14)),
    ('_cci(28)', lambda: _cci(h, l, c, 28)),
    ('_adx(14)', lambda: _adx(h, l, c, 14)),
    ('_stoch', lambda: _stoch(h, l, c, 14)),
    ('_obv', lambda: _obv(c, v)),
    ('_mfi(14)', lambda: _mfi(h, l, c, v, 14)),
    ('_cons', lambda: _cons(c)),
]

for name, fn in funcs:
    try:
        result = fn()
        if isinstance(result, tuple):
            print(f'  {name}: OK {[len(x) for x in result]}')
        else:
            val = float(result[-1])
            print(f'  {name}: OK (len={len(result)}, last={val:.4f})')
    except Exception as e:
        print(f'  {name}: ERROR {e}')
        traceback.print_exc()

print('\nFull extraction test...')
ohlcv = {
    'close': df['close'].values,
    'high': df['high'].values,
    'low': df['low'].values,
    'volume': df['volume'].values,
    'trade_date': df['trade_date'].values if 'trade_date' in df.columns else None,
}

try:
    feats, dates = extract_factors(ohlcv, max_bars=1500)
    print(f'SUCCESS: {len(feats)} factors, dates len={len(dates)}')

    rows = []
    for j in range(30, n - 1):
        row = {'symbol': 'TEST', 'trade_date': str(dates[j])[:10] if j < len(dates) else ''}
        for fname, fvals in feats.items():
            if j < len(fvals):
                val = fvals[j]
                if not (np.isnan(val) or np.isinf(val)):
                    row[fname] = float(val)
        rows.append(row)

    print(f'Rows: {len(rows)}, first row keys: {list(rows[0].keys())[:5]}')
except Exception as e:
    print(f'FAILED: {e}')
    traceback.print_exc()
