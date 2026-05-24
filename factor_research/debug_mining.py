import pandas as pd, sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Test single file extraction
from factor_mining_v2 import extract_all_new_factors

test_files = {
    'CSI300': r'E:\quant\ml_alpha\csi300_cache',
    'SPX': r'E:\quant\ml_alpha\spx_cache',
}

for name, path in test_files.items():
    files = sorted([f for f in os.listdir(path) if f.endswith('.csv')])[:1]
    if not files: continue
    
    fpath = os.path.join(path, files[0])
    print(f'Testing {name}: {files[0]}')
    
    df = pd.read_csv(fpath)
    print(f'  Raw: {len(df)} rows, cols={list(df.columns)[:6]}')
    print(f'  Date range: {df["trade_date"].min()} ~ {df["trade_date"].max()}')
    
    # Filter volume=0
    if 'volume' in df.columns:
        valid_mask = df['volume'] > 0
        print(f'  Volume>0 rows: {valid_mask.sum()}/{len(df)}')
        if valid_mask.sum() == 0:
            print(f'  WARNING: All volume=0!')
            # Check sample values
            print(f'  Volume sample: {df["volume"].head(10).tolist()}')
        else:
            first_valid = df[valid_mask].index.min()
            print(f'  First valid idx: {first_valid}')
            df = df.loc[first_valid:].reset_index(drop=True)
    
    print(f'  After filter: {len(df)} rows')
    
    # Try extract
    try:
        feats, dates = extract_all_new_factors(df, max_bars=2000)
        n = len(feats.get('rsi_14', []))
        print(f'  Extracted factors: {n} bars, {len(feats)} factors')
        print(f'  Factor names: {list(feats.keys())[:10]}...')
        
        # Test IC on one factor
        if 'fwd_ret_5d' in feats:
            valid = [(i, feats['fwd_ret_5d'][i]) for i in range(n) if not (feats['fwd_ret_5d'][i] != feats['fwd_ret_5d'][i] or abs(feats['fwd_ret_5d'][i]) > 1e9)]
            print(f'  Valid fwd_ret: {len(valid)}/{n}')
    except Exception as e:
        print(f'  ERROR: {e}')
        import traceback
        traceback.print_exc()
    
    print()
