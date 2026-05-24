import pathlib, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
cache = pathlib.Path(r'E:\quant\ml_alpha\kline_cache')
files = list(cache.glob('*.csv'))
valid = [f for f in files if f.stat().st_size > 500]
print(f'Cache: {len(valid)}/{len(files)} files valid')
result_file = r'E:\quant\ml_alpha\complexity_partial_results.csv'
if os.path.exists(result_file):
    import pandas as pd
    df = pd.read_csv(result_file)
    print(f'Partial results: {len(df)} records, {df["symbol"].nunique()} stocks')
