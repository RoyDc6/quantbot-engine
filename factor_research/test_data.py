import sys, io, warnings, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
warnings.filterwarnings('ignore')

from datetime import datetime
import pandas as pd

CSI300_DIR = r'E:\quant\ml_alpha\csi300_cache'
SPX_DIR = r'E:\quant\ml_alpha\spx_cache'

csi_files = sorted([f for f in os.listdir(CSI300_DIR) if f.endswith('.csv')])[:5]
spx_files = sorted([f for f in os.listdir(SPX_DIR) if f.endswith('.csv')])[:5]

print(f'CSI300: {len(csi_files)} test files')
print(f'SPX: {len(spx_files)} test files')

for fname in csi_files[:2]:
    df = pd.read_csv(os.path.join(CSI300_DIR, fname))
    vol_pos = (df['volume'] > 0).sum()
    first_date = str(df['trade_date'].iloc[0])
    print(f'  {fname}: {len(df)} rows, vol>0: {vol_pos}, first_date: {first_date}')

print('\nDONE')
