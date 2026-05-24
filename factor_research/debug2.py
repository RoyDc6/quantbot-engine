"""Debug: run the actual extraction from mining_v4.py"""
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

# ── Read and exec only the extract_factors function ──────
src = open(r'E:\quant\factor_research\mining_v4.py', encoding='utf-8').read()
# Find the extract_factors function
start = src.find('\ndef extract_factors(')
end = src.find('\n\n# ═══════════════════════════════════════════════════════════\n# IC计算')
func_src = src[start:end]
print(f'Function found: {len(func_src)} chars')

# Patch: add try/except inside to catch the specific error
func_src = func_src.replace(
    'def extract_factors(ohlcv, max_bars=1500):',
    'def extract_factors(ohlcv, max_bars=1500):\n    _debug_errors = []'
)

exec(func_src, globals())

# Now test
df = pd.read_csv(r'E:\quant\ml_alpha\csi300_cache\000001_SZ.csv')
ohlcv = {
    'close': df['close'].values,
    'high': df['high'].values,
    'low': df['low'].values,
    'volume': df['volume'].values,
    'trade_date': df['trade_date'].values if 'trade_date' in df.columns else None,
}

print('Testing extract_factors...')
try:
    feats, dates = extract_factors(ohlcv, max_bars=1500)
    print(f'SUCCESS! {len(feats)} keys')
    print(f'dates type: {type(dates)}, len: {len(dates)}')
except Exception as e:
    print(f'FAILED: {e}')
    traceback.print_exc()
