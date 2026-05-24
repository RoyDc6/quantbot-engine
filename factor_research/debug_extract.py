"""Debug the actual mining_v4.py extraction"""
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import numpy as np
import pandas as pd

# Import extract_factors from mining_v4.py
exec(open(r'E:\quant\factor_research\mining_v4.py', encoding='utf-8').read().split('# ═══════════════════════════════════════════════════════════\n# IC计算')[0])

# Test
df = pd.read_csv(r'E:\quant\ml_alpha\csi300_cache\000001_SZ.csv')
print(f'Loaded: {df.shape}')

ohlcv = {
    'close': df['close'].values,
    'high': df['high'].values,
    'low': df['low'].values,
    'volume': df['volume'].values,
    'trade_date': df['trade_date'].values if 'trade_date' in df.columns else None,
}
print(f'ohlcv close len: {len(ohlcv["close"])}')

try:
    feats, dates = extract_factors(ohlcv, max_bars=1500)
    print(f'SUCCESS! feats has {len(feats)} keys, dates len={len(dates)}')
    for k, v in feats.items():
        if isinstance(v, np.ndarray):
            print(f'  {k}: shape={v.shape}')
except Exception as e:
    traceback.print_exc()
    print(f'FAILED: {e}')
