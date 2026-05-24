"""Check HK features and test new factors"""
import pandas as pd, numpy as np
from scipy.stats import spearmanr

df = pd.read_csv(r'E:\quant\ml_alpha\hk_features_full.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')
df = df.dropna(subset=['trade_date'])
print(f'HK: {len(df)} rows, {df["symbol"].nunique()} stocks')
print(f'Columns: {list(df.columns)[:15]}')
print(f'Range: {df["trade_date"].min().date()} ~ {df["trade_date"].max().date()}')

# Check what labels exist
ret_cols = [c for c in df.columns if 'ret' in c.lower() or 'fwd' in c.lower() or 'label' in c.lower()]
print(f'Return cols: {ret_cols}')

# Recent 2yr
cutoff = df['trade_date'].max() - pd.Timedelta(days=730)
recent = df[df['trade_date'] >= cutoff].copy()
print(f'Recent: {len(recent)} rows, {recent["symbol"].nunique()} stocks')

# Test factors - need to compute them
# For now, test existing factors
new_factors = ['frac_balance_20','frac_balance_10','fractal_top_5','rsi_14',
               'macd','vol_ratio','mom_5d','atr_ratio','top_count_10']
for col in new_factors:
    if col in recent.columns:
        d = recent.dropna(subset=[col, 'fwd_ret_5d'])
        if len(d) > 60:
            ric, _ = spearmanr(d[col], d['fwd_ret_5d'])
            print(f'  {col:20s} RankIC={ric:+.4f}')
